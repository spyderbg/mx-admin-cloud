"""strategy "work" — heal's exact per-issue loop, minus the check -> sync step:

    select highest-severity status::open issue already in GitLab
      -> status::in-progress
      -> worktree .mxworktrees/<iid> on branch <branch_prefix><iid> off [git] base_branch
      -> fixer.run(task)
      -> common.review_diff(), then testenv.py reset INSIDE that worktree + the full
         check suite — green lands it (commit, push, open PR); red retries with
         feedback, or status::needs-human once [limits] max_fix_attempts is exhausted
      -> next issue

No check runs against the main tree, and nothing gets synced/filed — "work" trusts
GitLab's current status::open backlog as-is and goes straight to fixing it. That backlog
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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gitlab.v4.objects import Project

from mxfix import worktree
from mxfix.config import RunConfig
from mxfix.fixer import Fixer
from mxissues.config import GitLabConfig

from . import common

DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class WorkOutcome:
    issues_attempted: list[common.IssueOutcome] = field(default_factory=list)


def run(
        config: RunConfig, project: Project, fixer: Fixer, *, gitlab_config: GitLabConfig,
        owner_repo: str, github_token: str, repo_root: Path, mode: str | None,
        max_issues: int | None, dry_run: bool,
) -> WorkOutcome:
    # "work" has no natural batch size either (same one-issue-at-a-time design as
    # heal) — --max-issues is the only cap; unset means "keep going until nothing
    # eligible is left".
    limit = max_issues if max_issues is not None else 10_000
    issues = common.select_issues(project, severity_threshold=config.limits.severity_threshold, limit=limit)
    if not issues:
        print(
            f"{DIM}No status::open issue at/above severity::{config.limits.severity_threshold} — nothing for "
            f"work to do. work never files issues itself; run report/heal/batch at least once first, or "
            f"`issues.py open` one by hand, if GitLab has nothing status::open yet.{RESET}",
        )
        return WorkOutcome()

    if not dry_run:
        worktree.fetch_base_branch(config.git.base_branch)

    outcomes = [
        common.work_one_issue(
            issue, config=config, project=project, fixer=fixer, gitlab_config=gitlab_config,
            owner_repo=owner_repo, github_token=github_token, mode=mode, dry_run=dry_run, label="work",
        )
        for issue in issues
    ]
    return WorkOutcome(issues_attempted=outcomes)
