"""Shared pipeline for all three strategies: check -> sync -> select
(implementation_plan.md §16 "All three share one pipeline"). report stops after sync;
heal/batch additionally select issues and start fixing — see strategy/heal.py and
strategy/batch.py for what happens after selection.

Checks are validate_build.py, validate_tests.py, validate_frontend_build.py and
testenv.py e2e — the same four scripts a human runs by hand via `/verify` and
`testenv.py e2e`, invoked here as subprocesses so this module has no import-time
dependency on backend/frontend tooling being importable from this process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from gitlab.v4.objects import Project, ProjectIssue

from mxissues import client as mxclient
from mxissues import sync as sync_module
from mxissues.models import Finding
from mxissues.sources import e2e as e2e_source
from mxissues.sources import pylint_source, pytest_source

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


def sync_findings(project: Project, check_run: CheckRun, *, max_new_issues: int, apply: bool) -> sync_module.SyncReport:
    return sync_module.run_sync(project, check_run.findings, apply=apply, max_new_issues=max_new_issues)


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


def select_issues(project: Project, *, severity_threshold: str, limit: int) -> list[ProjectIssue]:
    """status::open, tool-owned issues at/above *severity_threshold*, highest severity
    and (within a severity) oldest iid first. "the single highest-severity status::open
    issue" (heal, limit=1) / "up to concurrency issues above severity_threshold" (batch,
    limit=concurrency) — implementation_plan.md §16.
    """
    min_rank = _SEVERITY_RANK[severity_threshold]
    candidates = project.issues.list(labels=[mxclient.OWNERSHIP_LABEL, "status::open"], all=True)

    def severity_rank(issue: ProjectIssue) -> int:
        ranks = [
            _SEVERITY_RANK[label.removeprefix("severity::")]
            for label in (issue.labels or [])
            if label.startswith("severity::") and label.removeprefix("severity::") in _SEVERITY_RANK
        ]
        return min(ranks, default=99)

    eligible = [issue for issue in candidates if severity_rank(issue) <= min_rank]
    eligible.sort(key=lambda issue: (severity_rank(issue), issue.iid))
    return eligible[:limit]
