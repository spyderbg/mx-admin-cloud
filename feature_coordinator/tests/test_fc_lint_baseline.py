"""Baseline-aware Pylint gates (D-19): existing findings reported, new findings block."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from feature_coordinator.config import ConfigError, LintGate, ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.gates import GateContext, SandboxedGateRunner
from feature_coordinator.lint_baseline import compare, fingerprint, lint_report

from fc_helpers import git

MODULE = '''"""A module with one pre-existing finding."""
import os


def keep():
    """Keep."""
    return 1
'''
GATE = LintGate(root="pkg", targets=("src",), pythonpath=("src",))


@pytest.fixture
def lint_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "lint-repo"
    base = init_demo_repo(repo, {"pkg/src/mod.py": MODULE, "pkg/src/other.py": '"""Other."""\n'})
    return repo, base


@pytest.fixture
def lint_config(config: ProjectConfig) -> ProjectConfig:
    return dataclasses.replace(config, lint_gates={"pkg-lint": GATE})


def commit(repo: Path, rel: str, text: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", rel)
    git(repo, "commit", "-q", "-m", f"change {rel}")
    return git(repo, "rev-parse", "HEAD").strip()


def gate(config: ProjectConfig, repo: Path, base: str, head: str, tmp_path: Path):
    return SandboxedGateRunner(config, tmp_path / "logs")("pkg-lint", GateContext(repo, head_sha=head, base_sha=base))


def test_fingerprints_ignore_lines_and_numbers() -> None:
    a = {"path": "src/m.py", "symbol": "too-many-statements", "obj": "f", "line": 10,
         "message": "Too many statements (55/50)"}
    b = {**a, "line": 40, "message": "Too many statements (61/50)"}
    assert fingerprint(a) == fingerprint(b)
    diff = compare([a], [b, {**a, "symbol": "unused-import", "message": "Unused import os"}])
    assert len(diff["new"]) == 1 and diff["new"][0]["symbol"] == "unused-import" and diff["fixed"] == 0


def test_lint_policy_cannot_be_disabled(config: ProjectConfig) -> None:
    for policy in ("report-only", "disabled", "off"):
        with pytest.raises(ConfigError, match="D-19"):
            dataclasses.replace(config, lint_gates={"x": dataclasses.replace(GATE, policy=policy)})


def test_no_changed_files_passes_and_no_base_blocks(lint_config: ProjectConfig, lint_repo, tmp_path: Path) -> None:
    repo, base = lint_repo
    assert gate(lint_config, repo, base, base, tmp_path).status == "passed"
    assert gate(lint_config, repo, "", base, tmp_path).status == "blocked"


@pytest.mark.usefixtures("needs_bwrap")
def test_existing_findings_are_reported_but_do_not_block(lint_config: ProjectConfig, lint_repo, tmp_path: Path) -> None:
    repo, base = lint_repo
    head = commit(repo, "pkg/src/mod.py", '"""A module with one pre-existing finding."""\n# a new comment line\n'
                  + MODULE.split("\n", 1)[1] + '\n\ndef added():\n    """Clean addition."""\n    return 2\n')
    result = gate(lint_config, repo, base, head, tmp_path)
    assert result.status == "passed", result.summary
    assert "1 finding(s) at base, 1 at HEAD, 0 new" in result.summary  # moved, not new


@pytest.mark.usefixtures("needs_bwrap")
def test_new_findings_block(lint_config: ProjectConfig, lint_repo, tmp_path: Path) -> None:
    repo, base = lint_repo
    commit(repo, "pkg/src/mod.py", MODULE.replace("import os\n", "import os\nimport sys\n"))
    head = commit(repo, "pkg/src/brand_new.py", "def f():\n    return 1\n")
    result = gate(lint_config, repo, base, head, tmp_path)
    assert result.status == "failed" and result.exit_code == 1
    assert "unused-import" in result.summary and "Unused import sys" in result.summary
    assert "missing-module-docstring" in result.summary  # the new file's findings are all new
    assert "Unused import os" not in result.summary.split("new: ")[1]


@pytest.mark.usefixtures("needs_bwrap")
def test_fixing_an_existing_finding_is_counted(lint_config: ProjectConfig, lint_repo, tmp_path: Path) -> None:
    repo, base = lint_repo
    head = commit(repo, "pkg/src/mod.py", MODULE.replace("import os\n", ""))
    result = gate(lint_config, repo, base, head, tmp_path)
    assert result.status == "passed" and "1 fixed" in result.summary


@pytest.mark.usefixtures("needs_bwrap")
def test_full_baseline_report(lint_config: ProjectConfig, lint_repo, tmp_path: Path) -> None:
    repo, _ = lint_repo
    counts, result = lint_report(SandboxedGateRunner(lint_config, tmp_path / "logs"), "pkg-lint", GATE,
                                 GateContext(repo))
    assert result.status in ("passed", "failed") and counts["unused-import"] == 1
