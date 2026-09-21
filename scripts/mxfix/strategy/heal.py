"""strategy "heal" — one status::open issue at a time, sequential, reset between every
attempt (implementation_plan.md §16):

    select highest-severity status::open issue
      -> status::in-progress
      -> worktree .mxworktrees/<iid> on branch <branch_prefix><iid> off [git] base_branch
      -> fixer.run(task)
      -> common.review_diff() — reject a test-only diff for a pytest/e2e issue, or any
         touch of a check-config file (implementation_plan.md risk #15), before either
         one ever reaches a check run
      -> testenv.py reset INSIDE that worktree, then the full check suite
          green -> commit, push, open PR, comment the PR link back onto the issue
          red   -> attempt += 1; feed failures back as FixTask.feedback
                   attempt > max_fix_attempts -> status::needs-human, worktree kept
      -> next issue

The reset runs inside the worktree, not the main tree — the fix exists only there until
it's committed. It matters because earlier E2E specs delete items and archive
categories: without a fresh reset before every attempt, a later attempt's "fix" would be
verified against a fixture an earlier attempt already mutated, not a clean one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gitlab.v4.objects import Project, ProjectIssue

from mxfix import git_target, worktree
from mxfix.config import RunConfig
from mxfix.fixer import Fixer, FixTask
from mxissues import client as mxclient
from mxissues import state as mxstate
from mxissues.config import GitLabConfig

from . import common

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class IssueOutcome:
    iid: int
    status: str  # "fixed" | "needs-human" | "dry-run"
    detail: str
    pr_url: str | None = None


@dataclass
class HealOutcome:
    initial_check: common.CheckRun
    issues_attempted: list[IssueOutcome] = field(default_factory=list)


def _work_one_issue(
        issue: ProjectIssue, *, config: RunConfig, project: Project, fixer: Fixer,
        gitlab_config: GitLabConfig, owner_repo: str, github_token: str, repo_root: Path,
        mode: str | None, dry_run: bool,
) -> IssueOutcome:
    iid = issue.iid
    title = issue.title
    body = issue.description or ""
    branch = f"{config.git.branch_prefix}{iid}"

    print(f"\n{BOLD}=== issue #{iid}: {title} ==={RESET}")

    if dry_run:
        print(
            f"{DIM}  dry-run: would create .mxworktrees/{iid} on branch {branch} off "
            f"{config.git.base_branch}, hand it to fixer '{fixer.name}' for up to "
            f"{config.limits.max_fix_attempts} attempt(s), and on a green check run "
            f"commit" + (", push" if config.git.push else "") +
            (", open a PR" if config.git.push and config.git.open_pr else "") + f".{RESET}",
        )
        return IssueOutcome(iid=iid, status="dry-run", detail="no action taken")

    mxstate.set_status_by_iid(project, iid, "status::in-progress")
    wt_path = worktree.create(str(iid), branch, base_branch=config.git.base_branch)

    feedback: str | None = None
    for attempt in range(1, config.limits.max_fix_attempts + 1):
        task = FixTask(issue_iid=iid, title=title, body=body, worktree=wt_path, attempt=attempt, feedback=feedback)
        print(f"{DIM}  attempt {attempt}/{config.limits.max_fix_attempts}: running fixer '{fixer.name}'...{RESET}")
        result = fixer.run(task)

        if result.status == "no_change":
            detail = f"fixer reported no_change: {result.summary}"
            print(f"{YELLOW}  {detail} — worktree kept at {wt_path} for inspection.{RESET}")
            mxstate.set_status_by_iid(project, iid, "status::needs-human")
            issue.notes.create({"body": f"heal: fixer reported no change needed on attempt {attempt}. {result.summary}\n\nMarking needs-human for a person to look."})
            return IssueOutcome(iid=iid, status="needs-human", detail=detail)

        if result.status != "fixed":
            feedback = f"Fixer itself reported failure: {result.summary}"
            print(f"{YELLOW}  attempt {attempt}: fixer reported failure: {result.summary}{RESET}")
            continue

        rejection = common.review_diff(wt_path, issue_source=mxclient.source_of(issue) or "")
        if rejection is not None:
            feedback = f"Your change was rejected before checks even ran: {rejection}"
            print(f"{YELLOW}  attempt {attempt}: diff rejected — {rejection}{RESET}")
            continue

        check_run = common.run_all_checks(wt_path, mode=mode, workdir=wt_path / ".mxfix-check", reset_stack=True)
        if check_run.green:
            print(f"{GREEN}  attempt {attempt}: fixer succeeded and the full check suite is green.{RESET}")
            try:
                commit_sha = git_target.commit_all(
                    wt_path, title=f"Fix: {title}", summary=result.summary,
                    gitlab_host=gitlab_config.host, gitlab_project=gitlab_config.project, issue_iid=iid,
                )
            except git_target.GitTargetError as exc:
                detail = f"checks were green but nothing to commit ({exc})"
                print(f"{RED}  {detail}{RESET}")
                mxstate.set_status_by_iid(project, iid, "status::needs-human")
                return IssueOutcome(iid=iid, status="needs-human", detail=detail)

            pr_url = None
            if config.git.push:
                git_target.push_branch(wt_path, branch)
                if config.git.open_pr:
                    pr = git_target.open_pr(
                        token=github_token, owner_repo=owner_repo, base=config.git.base_branch, head=branch,
                        title=f"Fix: {title}", gitlab_host=gitlab_config.host, gitlab_project=gitlab_config.project,
                        issue_iid=iid, summary=result.summary,
                    )
                    pr_url = pr.html_url
                    issue.notes.create({"body": f"heal: fix pushed — {pr_url}\n\nStaying `status::in-progress` until that PR merges (see test_environment/README.md's \"Landing the fix\" section)."})

            print(f"{GREEN}  #{iid}: committed {commit_sha[:8]}" + (f", opened {pr_url}" if pr_url else " (not pushed — [git] push = false)") + f"{RESET}")
            try:
                worktree.remove(wt_path)
            except worktree.WorktreeError as exc:
                print(f"{YELLOW}  landed #{iid} but could not remove its worktree: {exc}{RESET}")
            return IssueOutcome(iid=iid, status="fixed", detail=f"commit {commit_sha[:8]}", pr_url=pr_url)

        feedback = check_run.feedback_text()
        print(f"{YELLOW}  attempt {attempt}: checks still red.{RESET}")

    detail = f"exhausted {config.limits.max_fix_attempts} attempt(s) without a green check run"
    print(f"{RED}  #{iid}: {detail} — marking needs-human, worktree kept at {wt_path} for inspection.{RESET}")
    mxstate.set_status_by_iid(project, iid, "status::needs-human")
    issue.notes.create({"body": f"heal: {detail}. Last failures:\n\n{feedback or '(none captured)'}\n\nWorktree kept at `{wt_path}` for inspection."})
    return IssueOutcome(iid=iid, status="needs-human", detail=detail)


def run(
        config: RunConfig, project: Project, fixer: Fixer, *, gitlab_config: GitLabConfig,
        owner_repo: str, github_token: str, repo_root: Path, mode: str | None,
        max_issues: int | None, dry_run: bool,
) -> HealOutcome:
    workdir = repo_root / ".mxfix" / "report"
    initial_check = common.run_all_checks(repo_root, mode=mode, workdir=workdir, reset_stack=False)
    common.sync_findings(project, initial_check, max_new_issues=config.limits.max_new_issues, apply=not dry_run)

    # heal has no natural batch size (it's always exactly one issue at a time by
    # design) — --max-issues is the only thing that caps how many it works through in
    # one `run` invocation; unset means "keep going until nothing eligible is left".
    limit = max_issues if max_issues is not None else 10_000
    issues = common.select_issues(project, severity_threshold=config.limits.severity_threshold, limit=limit)
    if not issues:
        print(f"{DIM}No status::open issue at/above severity::{config.limits.severity_threshold} — nothing for heal to do.{RESET}")
        return HealOutcome(initial_check=initial_check)

    if not dry_run:
        worktree.fetch_base_branch(config.git.base_branch)

    outcomes = [
        _work_one_issue(
            issue, config=config, project=project, fixer=fixer, gitlab_config=gitlab_config,
            owner_repo=owner_repo, github_token=github_token, repo_root=repo_root, mode=mode, dry_run=dry_run,
        )
        for issue in issues
    ]
    return HealOutcome(initial_check=initial_check, issues_attempted=outcomes)
