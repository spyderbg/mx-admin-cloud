"""strategy "work" — heal's exact per-issue loop, minus the check -> sync step:

    select highest-severity open issue already in the issue tracker
      -> in-progress
      -> worktree .mxworktrees/<iid> on branch <branch_prefix><iid> off [git] base_branch
      -> fixer.run(task)
      -> common.review_diff(), then testenv.py reset INSIDE that worktree + the full
         check suite — green lands it (commit, push, open PR); red retries with
         feedback, or needs-human once [limits] max_fix_attempts is exhausted
      -> next issue

No check runs against the main tree, and nothing gets synced/filed — "work" trusts
the tracker's current open backlog as-is and goes straight to fixing it. That backlog
has to come from somewhere: run report/heal/batch at least once first (or `issues.py
open` by hand) so there's something here to select, or "work" will just report nothing
to do and exit.

Point of the split from heal: heal's opening check->sync run repeats the ENTIRE check
suite (pytest/pylint/frontend build/e2e — several minutes, see
test_environment/README.md's "Verifying a fix" section on why worktrees can't share
.venv/node_modules) before it ever looks at an issue, every single invocation. Once
you're actively working down an already-filed backlog across several `run` calls in a
row, that repeated up-front cost buys nothing you don't already know — "work" skips
straight to the part that matters.

"work" ALSO picks needs-human back up, but only the ones a human has actually
commented on since the last attempt gave up (common.fresh_human_feedback()) — never
blindly, since needs-human exists precisely so a stuck issue doesn't loop
forever on its own. That comment becomes the retry's starting feedback (a fresh
[limits] max_fix_attempts budget, same as any other issue this selects — nothing here
persists an attempt count across separate `run` invocations). A needs-human
issue with no new comment is left exactly alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mxfix import worktree
from mxfix.config import RunConfig
from mxfix.fixer import Fixer
from mxissues.tracker import IssueTracker

from . import common

DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class WorkOutcome:
    issues_attempted: list[common.IssueOutcome] = field(default_factory=list)


def run(
        config: RunConfig, tracker: IssueTracker, fixer: Fixer, *,
        owner_repo: str, github_token: str, repo_root: Path, mode: str | None,
        max_issues: int | None, dry_run: bool,
) -> WorkOutcome:
    # "work" has no natural batch size either (same one-issue-at-a-time design as
    # heal) — --max-issues is the only cap; unset means "keep going until nothing
    # eligible is left". Open issues fill the cap first; whatever's left over goes to
    # commented needs-human issues, so a backlog of fresh open issues is never crowded
    # out by needs-human retries, or vice versa when there's room for both.
    limit = max_issues if max_issues is not None else 10_000
    open_issues = common.select_issues(tracker, severity_threshold=config.limits.severity_threshold, limit=limit)
    remaining = max(limit - len(open_issues), 0)
    needs_human = (
        common.select_needs_human_with_feedback(tracker, severity_threshold=config.limits.severity_threshold, limit=remaining)
        if remaining
        else []
    )

    if not open_issues and not needs_human:
        print(
            f"{DIM}No open issue, and no needs-human issue with a new comment since its last "
            f"attempt, at/above severity::{config.limits.severity_threshold} — nothing for work to do. work "
            f"never files issues itself; run report/heal/batch at least once first, or `issues.py open` one by "
            f"hand, if the tracker has nothing open yet.{RESET}",
        )
        return WorkOutcome()

    if needs_human:
        print(
            f"{DIM}Also picking up {len(needs_human)} needs-human issue(s) with a new comment "
            f"since the last attempt: {', '.join(f'#{issue.number}' for issue, _feedback in needs_human)}.{RESET}",
        )

    if not dry_run:
        worktree.fetch_base_branch(config.git.base_branch)

    outcomes = [
        common.work_one_issue(
            issue, config=config, tracker=tracker, fixer=fixer,
            owner_repo=owner_repo, github_token=github_token, mode=mode, dry_run=dry_run, label="work",
        )
        for issue in open_issues
    ] + [
        common.work_one_issue(
            issue, config=config, tracker=tracker, fixer=fixer,
            owner_repo=owner_repo, github_token=github_token, mode=mode, dry_run=dry_run, label="work",
            initial_feedback=feedback,
        )
        for issue, feedback in needs_human
    ]
    return WorkOutcome(issues_attempted=outcomes)
