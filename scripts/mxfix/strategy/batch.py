"""strategy "batch" — up to [limits] concurrency issues fixed in parallel worktrees,
merged onto one integration branch, verified with a SINGLE check run
(implementation_plan.md §16). A red verify can't say which candidate broke it on its
own; `_bisect()` below is the attribution fallback the plan describes: verify the first
half of the merged set, then the second, recursing on whichever is red — log2(N) extra
verify runs instead of one per candidate. It is a heuristic, not a guarantee: a fault
caused by an *interaction* between two individually-fine candidates can, in principle,
survive bisection undetected. That tradeoff is the plan's own, not something this
implementation adds on top.

Deliberate scope boundary, stated plainly rather than half-implemented: batch has no
cross-invocation attempt counter (heal's retry loop lives entirely within one `run`
call; batch's does not carry between separate `run --strategy batch` invocations). A
candidate that batch isolates as the culprit — or one whose fixer attempt itself fails,
one whose diff is rejected by common.review_diff() (implementation_plan.md risk #15 —
a test-only diff for a pytest/e2e issue, or any touch of a check-config file, before it
is ever committed), or one that won't merge cleanly — always goes back to
`status::open` with the failure recorded as an issue comment, for the NEXT `run` (batch
or heal) to pick up. batch never sets `status::needs-human` itself; only heal's counted
retry loop does.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from gitlab.v4.objects import Project, ProjectIssue

from mxfix import git_target, worktree
from mxfix.config import RunConfig
from mxfix.fixer import Fixer, FixTask
from mxissues import client as mxclient
from mxissues import state as mxstate
from mxissues.config import GitLabConfig
from mxissues.sync import SyncReport

from . import common

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class Candidate:
    issue: ProjectIssue
    worktree: Path
    branch: str
    commit_sha: str
    summary: str


@dataclass
class BatchOutcome:
    initial_check: common.CheckRun
    sync_report: SyncReport
    landed: list[int] = field(default_factory=list)
    returned: list[tuple[int, str]] = field(default_factory=list)
    merge_conflicts: list[int] = field(default_factory=list)


def _fix_one(issue: ProjectIssue, *, config: RunConfig, fixer: Fixer, gitlab_config: GitLabConfig) -> Candidate | None:
    """One worktree, one fixer attempt, commit on success. batch does not retry within
    one invocation — see the module docstring. None means "put it back to status::open,
    nothing to land"; the caller does that, since only it knows the issue's status was
    already flipped to in-progress before this ran.
    """
    branch = f"{config.git.branch_prefix}{issue.iid}"
    wt_path = worktree.create(str(issue.iid), branch, base_branch=config.git.base_branch)
    task = FixTask(issue_iid=issue.iid, title=issue.title, body=issue.description or "", worktree=wt_path, attempt=1, feedback=None)

    try:
        result = fixer.run(task)
    except Exception as exc:  # noqa: BLE001 - an unexpected fixer crash must not kill the whole batch round
        issue.notes.create({"body": f"batch: fixer raised an unexpected error: {exc}"})
        worktree.remove(wt_path, force=True)
        return None

    if result.status != "fixed":
        issue.notes.create({"body": f"batch: fixer did not produce a fix ({result.status}): {result.summary}"})
        worktree.remove(wt_path, force=True)
        return None

    rejection = common.review_diff(wt_path, issue_source=mxclient.source_of(issue) or "")
    if rejection is not None:
        issue.notes.create({"body": f"batch: diff rejected before it was even committed — {rejection}"})
        worktree.remove(wt_path, force=True)
        return None

    try:
        commit_sha = git_target.commit_all(
            wt_path, title=f"Fix: {issue.title}", summary=result.summary,
            gitlab_host=gitlab_config.host, gitlab_project=gitlab_config.project, issue_iid=issue.iid,
        )
    except git_target.GitTargetError as exc:
        issue.notes.create({"body": f"batch: fixer reported success but left nothing to commit ({exc})"})
        worktree.remove(wt_path, force=True)
        return None

    return Candidate(issue=issue, worktree=wt_path, branch=branch, commit_sha=commit_sha, summary=result.summary)


def _merge_all(candidates: list[Candidate], *, config: RunConfig, name: str) -> tuple[Path, list[Candidate], list[Candidate]]:
    """Fresh integration worktree off base_branch; merge every candidate's own branch
    onto it in order. Returns (integration_worktree, merged, conflicted).
    """
    integration_branch = f"{config.git.branch_prefix}{name}"
    integration_wt = worktree.create(name, integration_branch, base_branch=config.git.base_branch)
    merged: list[Candidate] = []
    conflicted: list[Candidate] = []
    for candidate in candidates:
        ok = git_target.merge_branch(integration_wt, candidate.branch, message=f"merge {candidate.branch} ({name})")
        (merged if ok else conflicted).append(candidate)
    return integration_wt, merged, conflicted


def _verify_subset(subset: list[Candidate], *, config: RunConfig, mode: str | None, round_id: str) -> tuple[bool, common.CheckRun, Path]:
    """Merge exactly *subset* onto a fresh scratch integration worktree and run the full
    check suite once. Caller owns removing the returned worktree.
    """
    name = f"batch-{round_id}-verify-{id(subset)}"
    integration_wt, _merged, conflicted = _merge_all(subset, config=config, name=name)
    if conflicted:
        # A subset here is a subset of a set that already merged cleanly once — a
        # conflict now would mean git merge is non-deterministic, a much stranger
        # problem than anything else this function handles. Surface it as red plainly
        # rather than silently mis-attributing.
        check_run = common.CheckRun(
            backend_ok=False, tests_ok=False, frontend_ok=False, e2e_ok=False, findings=[],
            junit_xml=integration_wt / "pytest.xml", pylint_json=integration_wt / "pylint.json", e2e_json=integration_wt / "e2e.json",
            infra_detail=f"{len(conflicted)} candidate(s) that merged cleanly before no longer merge cleanly in isolation.",
        )
        return False, check_run, integration_wt
    check_run = common.run_all_checks(integration_wt, mode=mode, workdir=integration_wt / ".mxfix-check", reset_stack=True)
    return check_run.green, check_run, integration_wt


def _bisect(subset: list[Candidate], *, config: RunConfig, mode: str | None, round_id: str) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """Attribution fallback for a red batch verify. Returns (exonerated, [(culprit, failure_text), ...]).
    Assumes the caller already knows *subset* as a whole verifies red — this only
    narrows down which part of it is responsible.
    """
    if len(subset) <= 1:
        return [], [(candidate, "isolated as the sole remaining candidate in a still-red verify") for candidate in subset]

    mid = len(subset) // 2
    green: list[Candidate] = []
    red: list[tuple[Candidate, str]] = []
    for half in (subset[:mid], subset[mid:]):
        is_green, check_run, integration_wt = _verify_subset(half, config=config, mode=mode, round_id=round_id)
        try:
            if is_green:
                green.extend(half)
            elif len(half) == 1:
                red.append((half[0], check_run.feedback_text()))
            else:
                sub_green, sub_red = _bisect(half, config=config, mode=mode, round_id=round_id)
                green.extend(sub_green)
                red.extend(sub_red)
        finally:
            try:
                worktree.remove(integration_wt, force=True)
            except worktree.WorktreeError:
                pass
    return green, red


def _land(candidate: Candidate, *, config: RunConfig, gitlab_config: GitLabConfig, owner_repo: str, github_token: str) -> str | None:
    """Push *candidate*'s own branch and open its own PR — never the integration
    branch, which only ever exists to produce one verify run and is always thrown away.
    Returns the PR URL, or None if [git] push is false.
    """
    pr_url = None
    if config.git.push:
        git_target.push_branch(candidate.worktree, candidate.branch)
        if config.git.open_pr:
            pr = git_target.open_pr(
                token=github_token, owner_repo=owner_repo, base=config.git.base_branch, head=candidate.branch,
                title=f"Fix: {candidate.issue.title}", gitlab_host=gitlab_config.host, gitlab_project=gitlab_config.project,
                issue_iid=candidate.issue.iid, summary=candidate.summary,
            )
            pr_url = pr.html_url
            candidate.issue.notes.create({"body": f"batch: fix pushed — {pr_url}\n\nStaying `status::in-progress` until that PR merges."})
    try:
        worktree.remove(candidate.worktree)
    except worktree.WorktreeError as exc:
        print(f"{YELLOW}  landed #{candidate.issue.iid} but could not remove its worktree: {exc}{RESET}")
    return pr_url


def run(
        config: RunConfig, project: Project, fixer: Fixer, *, gitlab_config: GitLabConfig,
        owner_repo: str, github_token: str, repo_root: Path, mode: str | None,
        max_issues: int | None, dry_run: bool,
) -> BatchOutcome:
    workdir = repo_root / ".mxfix" / "report"
    initial_check = common.run_all_checks(repo_root, mode=mode, workdir=workdir, reset_stack=False)
    sync_report = common.sync_findings(project, initial_check, max_new_issues=config.limits.max_new_issues, apply=not dry_run)

    limit = min(config.limits.concurrency, max_issues) if max_issues is not None else config.limits.concurrency
    issues = common.select_issues(project, severity_threshold=config.limits.severity_threshold, limit=limit)
    if not issues:
        print(f"{DIM}No status::open issue at/above severity::{config.limits.severity_threshold} — nothing for batch to do.{RESET}")
        return BatchOutcome(initial_check=initial_check, sync_report=sync_report)

    round_id = time.strftime("%Y%m%d-%H%M%S")
    print(f"\n{BOLD}=== batch {round_id}: {len(issues)} issue(s) ({', '.join(f'#{i.iid}' for i in issues)}) ==={RESET}")

    if dry_run:
        print(
            f"{DIM}  dry-run: would create one worktree+branch per issue off "
            f"{config.git.base_branch}, run fixer '{fixer.name}' on each in parallel "
            f"(up to {config.limits.concurrency} at once), merge every successful "
            f"candidate onto a {config.git.branch_prefix}batch-{round_id} integration "
            f"branch, reset+verify once, and on green push+PR each issue individually.{RESET}",
        )
        return BatchOutcome(initial_check=initial_check, sync_report=sync_report)

    worktree.fetch_base_branch(config.git.base_branch)
    for issue in issues:
        mxstate.set_status_by_iid(project, issue.iid, "status::in-progress")

    outcome = BatchOutcome(initial_check=initial_check, sync_report=sync_report)

    print(f"{DIM}  running {len(issues)} fixer(s) in parallel (concurrency={config.limits.concurrency})...{RESET}")
    with ThreadPoolExecutor(max_workers=config.limits.concurrency) as pool:
        results = list(pool.map(lambda issue: _fix_one(issue, config=config, fixer=fixer, gitlab_config=gitlab_config), issues))

    for issue, candidate in zip(issues, results):
        if candidate is None:
            mxstate.set_status_by_iid(project, issue.iid, "status::open")
            outcome.returned.append((issue.iid, "fixer did not produce a usable fix this round"))
    candidates = [c for c in results if c is not None]

    if not candidates:
        print(f"{YELLOW}  no candidate produced a committable fix this round.{RESET}")
        return outcome

    integration_wt, merged, conflicted = _merge_all(candidates, config=config, name=f"batch-{round_id}")
    for candidate in conflicted:
        mxstate.set_status_by_iid(project, candidate.issue.iid, "status::open")
        candidate.issue.notes.create({"body": "batch: this fix no longer merges cleanly onto the base branch — returned to status::open for a later run to retry alone."})
        outcome.merge_conflicts.append(candidate.issue.iid)
        try:
            worktree.remove(candidate.worktree, force=True)
        except worktree.WorktreeError:
            pass

    if not merged:
        print(f"{YELLOW}  every candidate had a merge conflict — nothing to verify this round.{RESET}")
        worktree.remove(integration_wt, force=True)
        return outcome

    print(f"{DIM}  verifying {len(merged)} merged candidate(s) on the integration branch...{RESET}")
    check_run = common.run_all_checks(integration_wt, mode=mode, workdir=integration_wt / ".mxfix-check", reset_stack=True)

    if check_run.green:
        print(f"{GREEN}  batch {round_id}: integration verify is green — landing all {len(merged)} candidate(s).{RESET}")
        worktree.remove(integration_wt, force=True)
        for candidate in merged:
            pr_url = _land(candidate, config=config, gitlab_config=gitlab_config, owner_repo=owner_repo, github_token=github_token)
            print(f"{GREEN}  #{candidate.issue.iid}: committed {candidate.commit_sha[:8]}" + (f", opened {pr_url}" if pr_url else "") + f"{RESET}")
            outcome.landed.append(candidate.issue.iid)
        return outcome

    print(f"{YELLOW}  batch {round_id}: integration verify is red — bisecting {len(merged)} candidate(s) to attribute it...{RESET}")
    worktree.remove(integration_wt, force=True)
    green, red = _bisect(merged, config=config, mode=mode, round_id=round_id)

    if green:
        # Landed on the strength of each one's OWN bisected-subset verify, not a fresh
        # combined re-verify of the whole exonerated set — see the module docstring on
        # why that's the plan's tradeoff, not an oversight. A merge conflict at this
        # final, real re-merge (as opposed to a test failure) is still caught below.
        print(f"{GREEN}  {len(green)} candidate(s) exonerated by bisection — landing them.{RESET}")
        final_wt, final_merged, final_conflicted = _merge_all(green, config=config, name=f"batch-{round_id}-final")
        worktree.remove(final_wt, force=True)
        for candidate in final_conflicted:
            red.append((candidate, "merged cleanly during bisection but not during the final re-merge — unexpected."))
        for candidate in final_merged:
            pr_url = _land(candidate, config=config, gitlab_config=gitlab_config, owner_repo=owner_repo, github_token=github_token)
            print(f"{GREEN}  #{candidate.issue.iid}: committed {candidate.commit_sha[:8]}" + (f", opened {pr_url}" if pr_url else "") + f"{RESET}")
            outcome.landed.append(candidate.issue.iid)

    for candidate, failure_text in red:
        print(f"{RED}  #{candidate.issue.iid}: isolated as red — returning to status::open with feedback for the next run.{RESET}")
        mxstate.set_status_by_iid(project, candidate.issue.iid, "status::open")
        candidate.issue.notes.create({
            "body": f"batch: isolated by bisection as (one of) the cause of a red verify:\n\n{failure_text}\n\n"
                    "Returned to `status::open` — the next `run` (batch or heal) will retry it. "
                    "heal is recommended next for a focused, attempt-counted retry.",
        })
        outcome.returned.append((candidate.issue.iid, "isolated as red by bisection"))
        try:
            worktree.remove(candidate.worktree, force=True)
        except worktree.WorktreeError:
            pass

    return outcome
