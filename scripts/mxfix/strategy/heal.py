"""strategy "heal" — one status::open issue at a time, sequential, reset between every
attempt (implementation_plan.md §16):

    check -> sync --apply (files anything newly broken, same as report)
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

The per-issue loop itself (worktree -> fixer -> review_diff -> verify -> land) lives in
strategy/common.py's work_one_issue() — shared with strategy/work.py, which is this same
loop minus the check->sync step below. heal always checks first; see work.py for why
that's sometimes exactly what you don't want (re-running the full suite when you already
know what's broken and just want to keep working the backlog).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gitlab.v4.objects import Project

from mxfix import worktree
from mxfix.config import RunConfig
from mxfix.fixer import Fixer
from mxissues.config import GitLabConfig
from mxissues.sync import SyncReport

from . import common

DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class HealOutcome:
    initial_check: common.CheckRun
    sync_report: SyncReport
    issues_attempted: list[common.IssueOutcome] = field(default_factory=list)


def run(
        config: RunConfig, project: Project, fixer: Fixer, *, gitlab_config: GitLabConfig,
        owner_repo: str, github_token: str, repo_root: Path, mode: str | None,
        max_issues: int | None, dry_run: bool,
) -> HealOutcome:
    workdir = repo_root / ".mxfix" / "report"
    initial_check = common.run_all_checks(repo_root, mode=mode, workdir=workdir, reset_stack=False)
    sync_report = common.sync_findings(project, initial_check, max_new_issues=config.limits.max_new_issues, apply=not dry_run)

    # heal has no natural batch size (it's always exactly one issue at a time by
    # design) — --max-issues is the only thing that caps how many it works through in
    # one `run` invocation; unset means "keep going until nothing eligible is left".
    limit = max_issues if max_issues is not None else 10_000
    issues = common.select_issues(project, severity_threshold=config.limits.severity_threshold, limit=limit)
    if not issues:
        print(f"{DIM}No status::open issue at/above severity::{config.limits.severity_threshold} — nothing for heal to do.{RESET}")
        return HealOutcome(initial_check=initial_check, sync_report=sync_report)

    if not dry_run:
        worktree.fetch_base_branch(config.git.base_branch)

    outcomes = [
        common.work_one_issue(
            issue, config=config, project=project, fixer=fixer, gitlab_config=gitlab_config,
            owner_repo=owner_repo, github_token=github_token, mode=mode, dry_run=dry_run, label="heal",
        )
        for issue in issues
    ]
    return HealOutcome(initial_check=initial_check, sync_report=sync_report, issues_attempted=outcomes)
