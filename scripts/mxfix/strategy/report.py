"""strategy "report" — check -> sync -> stop (implementation_plan.md §16).

Modifies no code, anywhere. The only strategy safe to run unattended or on a schedule,
and testenv.toml's default when [strategy] isn't overridden.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitlab.v4.objects import Project

from mxfix.config import RunConfig
from mxissues.sync import SyncReport

from . import common


@dataclass
class ReportOutcome:
    check_run: common.CheckRun
    sync_report: SyncReport


def run(config: RunConfig, project: Project, *, repo_root: Path, mode: str | None, apply: bool) -> ReportOutcome:
    workdir = repo_root / ".mxfix" / "report"
    check_run = common.run_all_checks(repo_root, mode=mode, workdir=workdir, reset_stack=False)
    sync_report = common.sync_findings(project, check_run, max_new_issues=config.limits.max_new_issues, apply=apply)
    return ReportOutcome(check_run=check_run, sync_report=sync_report)
