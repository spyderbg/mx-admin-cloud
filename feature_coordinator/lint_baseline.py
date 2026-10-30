"""Baseline-aware Pylint gates (D-19).

Existing findings are reported but do not block; findings a feature introduces do.

For a gate `LintGate(root, targets, pythonpath)` and a feature base commit:

  1. the Python files changed between the base and the current HEAD under `root/targets`
     are the files under review (`git diff --diff-filter=AMR`);
  2. Pylint runs on those files twice, both times inside the offline read-only gate
     sandbox: on the checkout, and on a `git archive` snapshot of `root` at the base
     (plus the root-level Pylint configuration files), so both runs use the same rules;
  3. each finding is fingerprinted as (path, symbol, object, message with digits
     normalized) — line numbers are ignored, so moved code is not "new", and "Too many
     statements (55/50)" equals "(52/50)";
  4. new = head findings − base findings (a multiset difference). Any new finding fails
     the gate; the summary always reports the base and head counts.

Pylint itself is never disabled, and no finding is suppressed: the gate only decides which
findings are attributable to the feature. `lint_report()` gives the full baseline.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import LintGate
from .model import EXIT_BLOCKED, EXIT_ERROR, GateResult
from .sandbox import Bind

PYLINT_ARGS = ("-m", "pylint", "--output-format=json2", "--persistent=n", "--score=n")
RC_FILES = (".pylintrc", "pylintrc", "pyproject.toml", "setup.cfg", "tox.ini")
MAX_LISTED = 20


def fingerprint(finding: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Line-independent identity of one finding."""
    message = re.sub(r"\d+", "N", str(finding.get("message", "")))
    return (str(finding.get("path", "")), str(finding.get("symbol", "")), str(finding.get("obj", "")), message)


def compare(base: Iterable[Mapping[str, Any]], head: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """new / fixed / unchanged counts between two finding lists."""
    head = list(head)
    base_counts, head_counts = Counter(map(fingerprint, base)), Counter(map(fingerprint, head))
    new_counts = head_counts - base_counts
    remaining = Counter(new_counts)
    new = []
    for finding in head:
        key = fingerprint(finding)
        if remaining[key] > 0:
            remaining[key] -= 1
            new.append(finding)
    return {"base": sum(base_counts.values()), "head": sum(head_counts.values()), "new": new,
            "fixed": sum((base_counts - head_counts).values())}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          env={"GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}).stdout


def changed_python_files(repo: Path, base: str, head: str, lint: LintGate) -> list[str]:
    """Repository-relative .py files added, modified or renamed under the gate's targets."""
    specs = [f"{lint.root}/{t}" for t in lint.targets]
    out = _git(repo, "diff", "--name-only", "-z", "--diff-filter=AMR", f"{base}..{head}", "--", *specs)
    return sorted(p for p in out.split("\0") if p.endswith(".py"))


def export_snapshot(repo: Path, ref: str, lint: LintGate, dest: Path) -> Path:
    """Extract `root` and the root-level Pylint config files at *ref* into *dest*."""
    present = set(_git(repo, "ls-tree", "--name-only", ref).split("\n"))
    paths = [lint.root, *(rc for rc in RC_FILES if rc in present)]
    data = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", ref, "--", *paths],
                          capture_output=True, check=True).stdout
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(dest, filter="data")
    return dest


def _pylint(runner: Any, name: str, cwd: Path, files: list[str], lint: LintGate, context: Any,
            extra: list[Bind]) -> tuple[list[dict[str, Any]] | None, GateResult]:
    env = {"PYTHONPATH": ":".join(str(cwd / p) for p in lint.pythonpath)} if lint.pythonpath else {}
    result = runner.run_command(name, ["python", *PYLINT_ARGS, *files], context, cwd=cwd, extra=extra, env=env)
    if result.status in ("blocked", "timeout", "error"):
        return None, result
    try:
        report = json.loads((Path(result.log) / "stdout.log").read_text(encoding="utf-8"))
        return list(report.get("messages", [])), result
    except (OSError, ValueError, AttributeError):
        return None, GateResult(name, EXIT_ERROR, result.command, status="error", log=result.log,
                                summary=f"pylint produced no JSON report: {result.summary}")


def run_lint_gate(runner: Any, name: str, lint: LintGate, context: Any) -> GateResult:
    """Run one baseline-aware lint gate (see the module docstring)."""
    repo = Path(context.repo_root)
    base, head = context.base_sha, context.head_sha or "HEAD"
    if not base:
        return GateResult(name, EXIT_BLOCKED, status="blocked", summary="no feature base commit to compare against")
    files = changed_python_files(repo, base, head, lint)
    if not files:
        return GateResult(name, 0, status="passed", summary=f"no Python files changed under {lint.root}")
    rel = [str(Path(f).relative_to(lint.root)) for f in files]
    head_findings, head_result = _pylint(runner, name, repo / lint.root, rel, lint, context, [])
    if head_findings is None:
        return head_result
    snapshot = export_snapshot(repo, base, lint, Path(head_result.log).parent / f"{Path(head_result.log).name}.base")
    existing = [r for r in rel if (snapshot / lint.root / r).exists()]
    base_findings: list[dict[str, Any]] = []
    if existing:
        found, base_result = _pylint(runner, name, snapshot / lint.root, existing, lint, context,
                                     [Bind(str(snapshot), str(snapshot), "ro")])
        if found is None:
            return base_result
        base_findings = found
    diff = compare(base_findings, head_findings)
    listed = [f"{f.get('path')}:{f.get('line')} {f.get('symbol')} {f.get('message')}" for f in diff["new"][:MAX_LISTED]]
    summary = (f"{len(files)} changed file(s): {diff['base']} finding(s) at base, {diff['head']} at HEAD, "
               f"{len(diff['new'])} new, {diff['fixed']} fixed" + (f"; new: {listed}" if listed else ""))
    passed = not diff["new"]
    return GateResult(name, 0 if passed else 1, head_result.command, status="passed" if passed else "failed",
                      duration_seconds=head_result.duration_seconds, log=head_result.log, summary=summary)


def lint_report(runner: Any, name: str, lint: LintGate, context: Any) -> tuple[Counter, GateResult]:
    """The full current findings for the gate's targets, counted by symbol (baseline report)."""
    findings, result = _pylint(runner, name, Path(context.repo_root) / lint.root, list(lint.targets), lint,
                               context, [])
    return Counter(str(f.get("symbol")) for f in findings or ()), result
