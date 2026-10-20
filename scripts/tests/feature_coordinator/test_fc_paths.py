"""Deny-by-default write-scope validation (traversal, symlinks, protected and generated paths)."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import os
from pathlib import Path

import pytest

from feature_coordinator.config import FULL_PROJECT, ProjectConfig
from feature_coordinator.errors import ScopeViolationError
from feature_coordinator.paths import PathPolicy, ScopedWorkspace


@pytest.fixture
def policy(repo: Path, config: ProjectConfig) -> PathPolicy:
    return PathPolicy(repo, config)


@pytest.mark.parametrize("role,path", [
    ("backend", "backend/src/app.py"),
    ("backend", "docs_agents/backend/services/content/README.md"),
    ("cli", "cli/src/new_command.py"),
    ("frontend", "frontend/src/types/contentSchemas.ts"),  # generated output: destination owner writes it
    ("automation", "scripts/generate_ts_client.py"),
    ("automation", "test_environment/e2e/specs/new.spec.ts"),
    ("coordination", "docs_agents/coordination/feature-workflow.md"),
])
def test_owned_paths_are_allowed(policy: PathPolicy, role: str, path: str) -> None:
    decision = policy.check(role, path)
    assert decision.allowed, decision.reason


@pytest.mark.parametrize("role,path,reason", [
    ("backend", "frontend/src/view.ts", "owned by role 'frontend'"),
    ("cli", "backend/src/app.py", "owned by role 'backend'"),
    ("automation", "frontend/src/types/contentSchemas.ts", "generate_ts_schemas.py"),
    ("automation", "frontend/src/api/contentClient.ts", "generate_ts_client.py"),
    ("automation", "scripts/mxfix/fixer.py", "protected"),
    ("automation", "scripts/mxissues/sync.py", "protected"),
    ("automation", "scripts/deploy.sh", "approval"),
    ("backend", "backend/data/users.db", "protected"),
    ("backend", "content/foods/items_ids.csv", "protected"),
    ("backend", ".env", "protected"),
    ("backend", ".git/config", "protected"),
    ("coordination", "docs_agents/architecture/README.md", "approval"),
    ("frontend", "package.json", "deny-by-default"),
    ("frontend", ".github/workflows/ci.yml", "approval"),
])
def test_other_paths_are_denied(policy: PathPolicy, role: str, path: str, reason: str) -> None:
    decision = policy.check(role, path)
    assert not decision.allowed
    assert reason in decision.reason


@pytest.mark.parametrize("path", ["../outside.txt", "backend/../../outside.txt", "/etc/passwd", "", "a\x00b"])
def test_traversal_and_invalid_paths_are_denied(policy: PathPolicy, path: str) -> None:
    assert not policy.check("backend", path).allowed


def test_absolute_path_inside_the_repo_is_normalized(policy: PathPolicy, repo: Path) -> None:
    decision = policy.check("backend", str(repo / "backend" / "x" / ".." / "src" / "app.py"))
    assert decision.allowed and decision.path == "backend/src/app.py"


def test_symlink_escape_out_of_the_repository(policy: PathPolicy, repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, repo / "backend" / "escape")
    decision = policy.check("backend", "backend/escape/file.txt")
    assert not decision.allowed and "symlink escape" in decision.reason


def test_symlink_into_another_roles_tree(policy: PathPolicy, repo: Path) -> None:
    os.symlink(repo / "frontend" / "src", repo / "backend" / "ui")
    decision = policy.check("backend", "backend/ui/view.ts")
    assert not decision.allowed and "resolves via symlink" in decision.reason


def test_symlink_entries_themselves_are_denied(policy: PathPolicy, repo: Path) -> None:
    os.symlink("app.py", repo / "backend" / "src" / "alias.py")
    assert "symlink" in policy.check("backend", "backend/src/alias.py").reason


def test_grants_only_for_approval_paths(policy: PathPolicy) -> None:
    assert policy.check("automation", "scripts/deploy.sh", grants=("scripts/deploy.sh",)).allowed
    assert policy.check("coordination", "docs_agents/README.md", grants=("docs_agents/README.md",)).allowed
    with pytest.raises(ScopeViolationError, match="protected"):
        policy.validate_grants(("content/**",))
    with pytest.raises(ScopeViolationError, match="not an approval-required"):
        policy.validate_grants(("frontend/**",))
    # A grant never opens a protected path, even if passed straight to check().
    assert not policy.check("backend", ".env", grants=(".env",)).allowed


def test_full_project_needs_explicit_grants(policy: PathPolicy) -> None:
    assert not policy.check(FULL_PROJECT, "backend/src/app.py").allowed
    assert policy.check(FULL_PROJECT, "docker/README.md", grants=("docker/**",)).allowed


def test_require_lists_every_denial(policy: PathPolicy) -> None:
    with pytest.raises(ScopeViolationError) as excinfo:
        policy.require("cli", ["cli/a.py", "backend/b.py", "../c"])
    assert "backend/b.py" in str(excinfo.value) and "../c" in str(excinfo.value)
    assert policy.require("cli", ["cli/a.py", "./cli/a.py"]) == ["cli/a.py"]


def test_scoped_workspace_enforces_at_write_time(policy: PathPolicy, repo: Path) -> None:
    ws = ScopedWorkspace(policy, "cli")
    ws.write_text("cli/src/new.py", "x = 1\n")
    assert (repo / "cli" / "src" / "new.py").read_text(encoding="utf-8") == "x = 1\n"
    with pytest.raises(ScopeViolationError):
        ws.write_text("backend/src/app.py", "hacked")
    assert (repo / "backend" / "src" / "app.py").read_text(encoding="utf-8") == "ROUTES = []\n"
    with pytest.raises(ScopeViolationError, match="secrets"):
        ws.read_text(".env")
    assert ws.changed == ["cli/src/new.py"]
