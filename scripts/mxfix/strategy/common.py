"""Shared machinery for all four strategies.

check -> sync -> select (implementation_plan.md §16 "All three share one pipeline" —
now four: report stops after sync; heal/batch/work additionally select issues and start
fixing). Checks are validate_build.py, validate_tests.py, validate_frontend_build.py and
testenv.py e2e — the same four scripts a human runs by hand via `/verify` and
`testenv.py e2e`, invoked here as subprocesses so this module has no import-time
dependency on backend/frontend tooling being importable from this process.

work_one_issue() is the other half: the full per-issue worktree -> fixer -> verify ->
land loop, shared by strategy/heal.py and strategy/work.py (batch's version differs
enough — parallel dispatch, no per-candidate check run, bisect on a shared verify — that
strategy/batch.py keeps its own). heal always calls the check->sync pipeline above
before selecting; work (strategy/work.py) skips straight to selecting whatever is
already open in the issue tracker.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mxfix import git_target, worktree
from mxfix.config import RunConfig
from mxfix.fixer import Fixer, FixTask
from mxissues import sync as sync_module
from mxissues.models import Finding
from mxissues.sources import e2e as e2e_source
from mxissues.sources import pylint_source, pytest_source
from mxissues.tracker import Issue, IssueTracker

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class CheckRun:
    backend_ok: bool
    tests_ok: bool
    frontend_ok: bool
    e2e_ok: bool
    findings: list[Finding]
    junit_xml: Path
    pylint_json: Path
    e2e_json: Path
    infra_detail: str = ""  # set when reset_stack=True and `testenv.py reset` itself failed

    @property
    def green(self) -> bool:
        return self.backend_ok and self.tests_ok and self.frontend_ok and self.e2e_ok

    def feedback_text(self) -> str:
        """A concise failure summary for FixTask.feedback, built from the SAME Finding
        objects `sync` would file — a fixer sees exactly what an issue filed from this
        run would say, not a raw log dump.
        """
        if self.green:
            return ""
        lines = []
        if self.infra_detail:
            lines.append(f"- [infra] {self.infra_detail}")
        lines += [f"- [{f.source}/{f.severity}] {f.title}" for f in self.findings]
        if not lines:
            lines = ["(a check exited non-zero but produced no individual findings — see the run's console output above)"]
        return "\n".join(lines)


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> int:
    print(f"\n{BOLD}$ {' '.join(cmd)}{RESET}")
    return subprocess.run(cmd, cwd=cwd, env=env, check=False).returncode


def _testenv_cmd(repo_root: Path, *, mode: str | None) -> list[str]:
    cmd = [sys.executable, str(repo_root / "scripts" / "testenv.py")]
    if mode:
        cmd += ["--mode", mode]
    return cmd


def reset_stack_fixture(repo_root: Path, *, mode: str | None) -> tuple[bool, str]:
    """`testenv.py reset` inside *repo_root* — the "byte-identical fixture" step
    (implementation_plan.md §16): re-seeds .testenv/ fresh and brings the stack up, so a
    check run is never verified against a fixture an earlier attempt's E2E specs already
    mutated (they delete items, archive categories, ...).
    """
    rc = _run(_testenv_cmd(repo_root, mode=mode) + ["reset"], cwd=repo_root)
    if rc == 0:
        return True, ""
    return False, f"`testenv.py reset` exited {rc} — the sandbox stack itself failed to come up cleanly; see the output above."


def teardown_stack(repo_root: Path, *, mode: str | None) -> None:
    """`testenv.py down --purge` inside *repo_root* — always called after a reset-backed
    verify (success or failure) so the next attempt/issue/round starts from a clean
    slate and doesn't leak a running stack or held ports.
    """
    _run(_testenv_cmd(repo_root, mode=mode) + ["down", "--purge"], cwd=repo_root)


def run_all_checks(repo_root: Path, *, mode: str | None, workdir: Path, reset_stack: bool) -> CheckRun:
    """Run the full check suite against the code at *repo_root*, writing
    machine-readable output under *workdir*.

    `reset_stack=True` (heal/batch's per-attempt/per-round reverify, always against a
    worktree, never the main tree) runs `testenv.py reset` first for a byte-identical
    fixture, then tears the stack down again afterward. `reset_stack=False` (the shared
    opening check->sync->select run, against the checked-out main tree) leaves
    `testenv.py e2e` to bring the stack up on its own if it isn't already running,
    matching how `report` has always run checks — no reset, no teardown, so a stack a
    developer left running for their own use isn't disturbed.
    """
    scripts_dir = repo_root / "scripts"
    workdir.mkdir(parents=True, exist_ok=True)
    junit_xml = workdir / "pytest.xml"
    pylint_json = workdir / "pylint.json"
    e2e_json = workdir / "e2e.json"

    backend_rc = _run([sys.executable, str(scripts_dir / "validate_build.py"), "--json", str(pylint_json)], cwd=repo_root)
    tests_rc = _run([sys.executable, str(scripts_dir / "validate_tests.py"), "--junit-xml", str(junit_xml)], cwd=repo_root)
    frontend_rc = _run([sys.executable, str(scripts_dir / "validate_frontend_build.py")], cwd=repo_root)

    reset_ok, reset_detail = (True, "")
    if reset_stack:
        reset_ok, reset_detail = reset_stack_fixture(repo_root, mode=mode)

    if reset_ok:
        e2e_env = {**os.environ, "PLAYWRIGHT_JSON_OUTPUT_NAME": str(e2e_json)}
        e2e_rc = _run(_testenv_cmd(repo_root, mode=mode) + ["e2e", "--", "--reporter=json"], cwd=repo_root, env=e2e_env)
    else:
        e2e_rc = 1

    if reset_stack:
        teardown_stack(repo_root, mode=mode)

    findings: list[Finding] = []
    if pylint_json.is_file():
        findings.extend(pylint_source.parse(pylint_json))
    if junit_xml.is_file():
        findings.extend(pytest_source.parse(junit_xml))
    if e2e_json.is_file():
        findings.extend(e2e_source.parse(e2e_json))

    return CheckRun(
        backend_ok=backend_rc == 0, tests_ok=tests_rc == 0, frontend_ok=frontend_rc == 0,
        e2e_ok=e2e_rc == 0 and reset_ok, findings=findings,
        junit_xml=junit_xml, pylint_json=pylint_json, e2e_json=e2e_json, infra_detail=reset_detail,
    )


def sync_findings(tracker: IssueTracker, check_run: CheckRun, *, max_new_issues: int, apply: bool) -> sync_module.SyncReport:
    return sync_module.run_sync(tracker, check_run.findings, apply=apply, max_new_issues=max_new_issues)


_CHECK_CONFIG_BASENAMES = {"pylintrc", ".pylintrc", "pytest.ini", "pyproject.toml"}


def _looks_like_test_file(path: str) -> bool:
    p = Path(path)
    return (
        p.name.startswith("test_") or p.name.endswith("_test.py") or p.name.endswith(".spec.ts")
        or "tests" in p.parts or "specs" in p.parts
    )


def review_diff(worktree: Path, *, issue_source: str) -> str | None:
    """implementation_plan.md risk #15: "a fixer can 'fix' by weakening the check —
    deleting an assertion, disabling a test, relaxing a pylint rule. The §15 constraint
    prompt forbids it, but prompts are not enforcement." Inspects the worktree's
    uncommitted diff against HEAD (called before any commit) and returns a
    human-readable rejection reason, or None if the diff passes. Two checks, exactly as
    the plan specifies — no attempt to detect subtler check-weakening, which the prompt
    constraints and the full check suite (heal) / bisect (batch) are the actual defense
    against:

      - every changed file looks like a test file, but the issue that prompted the fix
        came from a source (pytest/e2e) where that shape means "made the test pass by
        changing the test," not by fixing the code the test exercises.
      - any change at all to a check-configuration file (pylintrc/pytest.ini/pyproject.toml)
        — always flagged, regardless of source, since loosening it can silence
        unrelated future failures too.
    """
    diff_result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=worktree, capture_output=True, text=True, check=False)
    untracked_result = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree, capture_output=True, text=True, check=False)
    changed = [line for line in (*diff_result.stdout.splitlines(), *untracked_result.stdout.splitlines()) if line.strip()]
    if not changed:
        return None  # nothing changed — fixer.py's FixResult status already distinguishes "no_change"; not this function's job

    touched_guard = sorted({f for f in changed if Path(f).name in _CHECK_CONFIG_BASENAMES})
    if touched_guard:
        return (
            f"touches a check-configuration file ({', '.join(touched_guard)}) — flagged for human review, "
            "never auto-landed (implementation_plan.md risk #15)."
        )

    if issue_source in ("pytest", "e2e") and all(_looks_like_test_file(f) for f in changed):
        return (
            f"every changed file looks like a test file ({', '.join(sorted(changed))}), but this issue's source is "
            f"{issue_source!r} — a real fix for a failing {issue_source} check should change the code under test, "
            "not just the test itself (implementation_plan.md risk #15)."
        )

    return None


def _severity_rank(issue: Issue) -> int:
    ranks = [
        _SEVERITY_RANK[label.removeprefix("severity::")]
        for label in issue.labels
        if label.startswith("severity::") and label.removeprefix("severity::") in _SEVERITY_RANK
    ]
    return min(ranks, default=99)


def _eligible_sorted(candidates: list[Issue], *, severity_threshold: str) -> list[Issue]:
    min_rank = _SEVERITY_RANK[severity_threshold]
    eligible = [issue for issue in candidates if _severity_rank(issue) <= min_rank]
    eligible.sort(key=lambda issue: (_severity_rank(issue), issue.number))
    return eligible


def select_issues(tracker: IssueTracker, *, severity_threshold: str, limit: int) -> list[Issue]:
    """Open-status, tool-owned, not-closed issues at/above *severity_threshold*, highest
    severity and (within a severity) oldest number first. "the single highest-severity
    open issue" (heal, limit=1) / "up to concurrency issues above severity_threshold"
    (batch, limit=concurrency) — implementation_plan.md §16.
    """
    candidates = tracker.list_issues(status="open", owned_only=True, state="open")
    return _eligible_sorted(candidates, severity_threshold=severity_threshold)[:limit]


_AI_COMMENT_PREFIXES = ("heal:", "work:", "batch:")


def _is_ai_comment(body: str) -> bool:
    return body.strip().startswith(_AI_COMMENT_PREFIXES)


def fresh_human_feedback(tracker: IssueTracker, issue: Issue) -> str | None:
    """Every human (non-system, non-AI-authored) comment on *issue* posted after the
    last AI-authored one, oldest first, joined into one block — or None if there isn't
    one yet (still genuinely needs a human; not retried blindly just because it's
    needs-human).

    "AI-authored" is detected by the `"heal:"`/`"work:"`/`"batch:"` prefix every comment
    work_one_issue()/batch.py already leaves — not by author identity, which isn't
    reliably distinguishable from a human's own account without assuming a specific
    token identity this module has no other reason to know. Tracker-generated system
    notes (GitLab's label changes, state transitions) are excluded via Comment.system;
    they're not comments either way.
    """
    notes = [note for note in tracker.comments(issue.number) if not note.system]
    last_ai_at = next((note.created_at for note in reversed(notes) if _is_ai_comment(note.body)), None)
    human_notes = [
        note for note in notes
        if not _is_ai_comment(note.body) and (last_ai_at is None or note.created_at > last_ai_at)
    ]
    if not human_notes:
        return None
    return "\n\n---\n\n".join(note.body.strip() for note in human_notes)


def select_needs_human_with_feedback(tracker: IssueTracker, *, severity_threshold: str, limit: int) -> list[tuple[Issue, str]]:
    """needs-human, tool-owned issues that have a fresh human comment since the
    last AI attempt — "for status::needs-human tasks, check for a comment and try to use
    it to resolve them." Same ownership guard and severity filter as select_issues(); a
    needs-human issue with no NEW comment is left alone (still genuinely needs a human).
    Returns (issue, feedback_text) pairs, sorted the same way select_issues() sorts.
    """
    candidates = tracker.list_issues(status="needs-human", owned_only=True, state="open")
    eligible = _eligible_sorted(candidates, severity_threshold=severity_threshold)

    result: list[tuple[Issue, str]] = []
    for issue in eligible:
        if len(result) >= limit:
            break
        feedback = fresh_human_feedback(tracker, issue)
        if feedback is not None:
            result.append((issue, feedback))
    return result


@dataclass
class IssueOutcome:
    iid: int
    status: str  # "fixed" | "needs-human" | "dry-run"
    detail: str
    pr_url: str | None = None


def work_one_issue(
        issue: Issue, *, config: RunConfig, tracker: IssueTracker, fixer: Fixer,
        owner_repo: str, github_token: str,
        mode: str | None, dry_run: bool, label: str, initial_feedback: str | None = None,
) -> IssueOutcome:
    """One issue, start to finish: worktree -> fixer.run() -> review_diff() -> reset +
    full check suite -> land on green / retry-or-needs-human on red
    (implementation_plan.md §16 "heal"). Shared by strategy/heal.py and
    strategy/work.py — they differ only in what selects the issue this gets called
    with (heal always runs its own check->sync first; work skips straight to whatever
    is already open in the tracker — see strategy/work.py). *label* tags the prints
    and issue comments this leaves ("heal" or "work") so it's clear afterward which
    strategy actually did the work.

    *initial_feedback*, when given, seeds attempt 1's FixTask.feedback instead of
    starting empty — this is how a needs-human issue with a fresh human comment
    (common.fresh_human_feedback()/select_needs_human_with_feedback()) gets retried: the
    human's own words go to the fixer exactly like a failed check's feedback would on a
    normal retry, with a fresh [limits] max_fix_attempts budget (nothing persists an
    attempt count across separate `run` invocations for ANY strategy — see
    strategy/batch.py's docstring on the same point).
    """
    iid = issue.number
    title = issue.title
    body = issue.description
    branch = f"{config.git.branch_prefix}{iid}"

    print(f"\n{BOLD}=== issue #{iid}: {title} ==={RESET}")
    if initial_feedback is not None:
        print(f"{DIM}  picking this up again — needs-human, but a human left a new comment since the last attempt:{RESET}")
        print(f"{DIM}  {initial_feedback.strip()[:300]}{'...' if len(initial_feedback.strip()) > 300 else ''}{RESET}")

    if dry_run:
        print(
            f"{DIM}  dry-run: would create .mxworktrees/{iid} on branch {branch} off "
            f"{config.git.base_branch}, hand it to fixer '{fixer.name}' for up to "
            f"{config.limits.max_fix_attempts} attempt(s)"
            + (", starting from that comment as feedback," if initial_feedback is not None else "")
            + ", and on a green check run "
            f"commit" + (", push" if config.git.push else "") +
            (", open a PR" if config.git.push and config.git.open_pr else "") + f".{RESET}",
        )
        return IssueOutcome(iid=iid, status="dry-run", detail="no action taken")

    tracker.set_status(iid, "in-progress")
    wt_path = worktree.create(str(iid), branch, base_branch=config.git.base_branch)

    feedback: str | None = initial_feedback
    for attempt in range(1, config.limits.max_fix_attempts + 1):
        task = FixTask(issue_iid=iid, title=title, body=body, worktree=wt_path, attempt=attempt, feedback=feedback)
        print(f"{DIM}  attempt {attempt}/{config.limits.max_fix_attempts}: running fixer '{fixer.name}'...{RESET}")
        result = fixer.run(task)

        if result.status == "no_change":
            detail = f"fixer reported no_change: {result.summary}"
            print(f"{YELLOW}  {detail} — worktree kept at {wt_path} for inspection.{RESET}")
            tracker.set_status(iid, "needs-human")
            tracker.add_comment(iid, f"{label}: fixer reported no change needed on attempt {attempt}. {result.summary}\n\nMarking needs-human for a person to look.")
            return IssueOutcome(iid=iid, status="needs-human", detail=detail)

        if result.status != "fixed":
            feedback = f"Fixer itself reported failure: {result.summary}"
            print(f"{YELLOW}  attempt {attempt}: fixer reported failure: {result.summary}{RESET}")
            continue

        rejection = review_diff(wt_path, issue_source=issue.source or "")
        if rejection is not None:
            feedback = f"Your change was rejected before checks even ran: {rejection}"
            print(f"{YELLOW}  attempt {attempt}: diff rejected — {rejection}{RESET}")
            continue

        check_run = run_all_checks(wt_path, mode=mode, workdir=wt_path / ".mxfix-check", reset_stack=True)
        if check_run.green:
            print(f"{GREEN}  attempt {attempt}: fixer succeeded and the full check suite is green.{RESET}")
            try:
                commit_sha = git_target.commit_all(
                    wt_path, title=f"Fix: {title}", summary=result.summary,
                    closing_reference=tracker.closing_reference(iid),
                )
            except git_target.GitTargetError as exc:
                detail = f"checks were green but nothing to commit ({exc})"
                print(f"{RED}  {detail}{RESET}")
                tracker.set_status(iid, "needs-human")
                return IssueOutcome(iid=iid, status="needs-human", detail=detail)

            pr_url = None
            if config.git.push:
                git_target.push_branch(wt_path, branch)
                if config.git.open_pr:
                    pr = git_target.open_pr(
                        token=github_token, owner_repo=owner_repo, base=config.git.base_branch, head=branch,
                        title=f"Fix: {title}", issue_link=tracker.link_line(iid), summary=result.summary,
                    )
                    pr_url = pr.html_url
                    tracker.add_comment(iid, f"{label}: fix pushed — {pr_url}\n\nStaying in-progress until that PR merges (see test_environment/README.md's \"Landing the fix\" section).")

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
    tracker.set_status(iid, "needs-human")
    tracker.add_comment(iid, f"{label}: {detail}. Last failures:\n\n{feedback or '(none captured)'}\n\nWorktree kept at `{wt_path}` for inspection.")
    return IssueOutcome(iid=iid, status="needs-human", detail=detail)
