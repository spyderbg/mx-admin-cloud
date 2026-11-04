"""W1: the Coordinator's Git operations cannot be turned into an execution vector by the repository's
own configuration or attributes (the `.git/config` → `core.fsmonitor` class, plus hooks and filters).

Offline, disposable repositories, harmless marker programs. Each test proves the *attempted execution
is prevented* (the marker file is not created / the operation is refused), and — for the prevention
layer — that the same attack DOES fire without the guard, so the test cannot silently pass. No model,
no network, no real agent; the active Worker's repository is never touched.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from feature_coordinator import native_backend as nb
from feature_coordinator import workspace as workspace_mod
from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.errors import GitConfigExecutionError
from feature_coordinator.gitops import CommitRequest, GitCoordinator, file_digest
from feature_coordinator.gitsafe import (
    assert_no_execution_config,
    execution_findings,
    hardened_env,
    run_git,
)
from feature_coordinator.launchers import role_dirty_paths, run_context
from feature_coordinator.locks import LockSet
from feature_coordinator.native_backend import NativeCliRunner
from feature_coordinator.native_hardening import NativeHardening
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.runners import RunRequest

from fc_helpers import actions_prompt, config_with_agents, install_fake_agent

BRANCH = "feature/gitsafe"


# -- building blocks --------------------------------------------------------------------------------

def marker_program(path: Path, marker: Path, *, passthrough: bool = False) -> str:
    """A harmless program that records it ran by creating *marker*. Returns its absolute path."""
    body = f'#!/bin/sh\ntouch {marker}\n' + ('cat\n' if passthrough else 'exit 0\n')
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def disposable_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_demo_repo(repo, SELF_TEST_FILES)
    return repo


def raw_git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """An UNHARDENED git call — the attacker's-eye baseline that proves the attack is real."""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env=env, check=False)


def plant_fsmonitor(repo: Path, marker: Path) -> None:
    program = marker_program(repo / ".git" / "fsmon.sh", marker)
    subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", program], check=True)


# -- the fail-closed scan ---------------------------------------------------------------------------

def test_scan_clean_repo_has_no_findings(tmp_path: Path) -> None:
    assert execution_findings(disposable_repo(tmp_path)) == []


def test_scan_flags_every_repo_scoped_execution_vector(tmp_path: Path) -> None:
    repo = disposable_repo(tmp_path)

    def cfg(*a: str) -> None:
        subprocess.run(["git", "-C", str(repo), "config", *a], check=True)
    cfg("core.fsmonitor", "/tmp/evil-fsmon")
    cfg("core.sshCommand", "/tmp/evil-ssh")
    cfg("core.hooksPath", ".git/evil-hooks")
    cfg("filter.evil.process", "/tmp/evil-proc")
    cfg("filter.evil.clean", "/tmp/evil-clean")
    cfg("diff.evil.textconv", "/tmp/evil-textconv")
    cfg("credential.helper", "/tmp/evil-cred")
    (repo / ".git/x.inc").write_text("[core]\n    pager = /tmp/evil-pager\n", encoding="utf-8")
    cfg("include.path", "x.inc")  # include-pulled execution must be resolved and flagged
    keys = {f.split()[1].split("=")[0] for f in execution_findings(repo)}
    assert keys == {"core.fsmonitor", "core.sshcommand", "core.hookspath", "filter.evil.process",
                    "filter.evil.clean", "diff.evil.textconv", "credential.helper", "core.pager"}
    assert all(f.startswith("local ") for f in execution_findings(repo))


def test_scan_allows_inert_and_builtin_values(tmp_path: Path) -> None:
    repo = disposable_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", "true"], check=True)   # builtin daemon
    subprocess.run(["git", "-C", str(repo), "config", "credential.helper", ""], check=True)    # reset, not a program
    assert execution_findings(repo) == []


def test_assert_raises_and_names_the_findings(tmp_path: Path) -> None:
    repo = disposable_repo(tmp_path)
    assert_no_execution_config(repo)  # clean: no raise
    plant_fsmonitor(repo, tmp_path / "NEVER")
    with pytest.raises(GitConfigExecutionError, match="core.fsmonitor"):
        assert_no_execution_config(repo)


# -- prevention: fsmonitor / hooks never execute through a Coordinator Git call ---------------------

def test_core_fsmonitor_executes_on_a_raw_status_but_not_a_hardened_one(tmp_path: Path) -> None:
    repo = disposable_repo(tmp_path)
    marker = tmp_path / "FSMON"
    plant_fsmonitor(repo, marker)
    raw_git(repo, "status", "--porcelain", env={**hardened_env(), "GIT_OPTIONAL_LOCKS": "0"})  # no -c override
    assert marker.exists(), "attack precondition: a raw status must run the fsmonitor program"
    marker.unlink()
    assert run_git(repo, "status", "--porcelain").returncode == 0
    assert not marker.exists(), "hardened status must not run the planted fsmonitor program"


def test_coordinator_status_helpers_do_not_execute_fsmonitor(tmp_path: Path) -> None:
    repo = disposable_repo(tmp_path)
    marker = tmp_path / "FSMON"
    plant_fsmonitor(repo, marker)
    # the native backend's own git helper, the role dirty-path scan and the workspace check
    nb._git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")  # pylint: disable=protected-access
    role_dirty_paths(repo, ["backend"])
    workspace_mod.git_operation_in_progress(repo)
    assert not marker.exists()


def test_planted_hook_runs_on_a_raw_commit_but_not_a_hardened_one(tmp_path: Path) -> None:
    repo = disposable_repo(tmp_path)
    marker = tmp_path / "HOOK"
    hooks = repo / ".git" / "evil-hooks"
    hooks.mkdir()
    marker_program(hooks / "pre-commit", marker)
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", str(hooks)], check=True)
    raw_git(repo, "commit", "--allow-empty", "-m", "raw", env={**hardened_env(), "GIT_OPTIONAL_LOCKS": "0"})
    assert marker.exists(), "attack precondition: a raw commit must run the planted pre-commit hook"
    marker.unlink()
    assert run_git(repo, "commit", "--allow-empty", "-m", "hardened").returncode == 0
    assert not marker.exists(), "hardened commit must not run the planted hook"


def test_filter_runs_on_a_raw_add_and_the_scan_refuses_it(tmp_path: Path) -> None:
    repo = disposable_repo(tmp_path)
    marker = tmp_path / "FILTER"
    program = marker_program(repo / ".git" / "filt.sh", marker, passthrough=True)
    subprocess.run(["git", "-C", str(repo), "config", "filter.evil.clean", program], check=True)
    (repo / ".gitattributes").write_text("backend/src/app.py filter=evil\n", encoding="utf-8")
    (repo / "backend/src/app.py").write_text("ROUTES = ['/probe']\n", encoding="utf-8")
    raw_git(repo, "add", "backend/src/app.py", env={**hardened_env(), "GIT_OPTIONAL_LOCKS": "0"})
    assert marker.exists(), "attack precondition: a raw add must run the clean filter"
    # filters are name-keyed so -c cannot blanket-disable them; the fail-closed scan is the defense.
    assert execution_findings(repo)
    with pytest.raises(GitConfigExecutionError):
        assert_no_execution_config(repo)


def test_env_injected_execution_cannot_survive_the_hardened_env(tmp_path: Path) -> None:
    repo = disposable_repo(tmp_path)
    marker = tmp_path / "ENVFSMON"
    program = marker_program(repo / ".git" / "envmon.sh", marker)
    hostile = {"GIT_CONFIG_PARAMETERS": f"'core.fsmonitor={program}'",
               "GIT_SSH_COMMAND": program, "GIT_PROXY_COMMAND": program}
    raw_git(repo, "status", "--porcelain", env={**hostile, "GIT_OPTIONAL_LOCKS": "0"})
    assert marker.exists(), "attack precondition: injected GIT_CONFIG_PARAMETERS fsmonitor runs"
    marker.unlink()
    # hardened_env scrubs the injectors; run_git uses it and also wins on -c precedence.
    assert run_git(repo, "status", "--porcelain", env={**hostile}).returncode == 0
    assert not marker.exists()


# -- the Git coordinator refuses to commit a tampered checkout --------------------------------------

@pytest.fixture
def gc(tmp_path: Path, state_dir: Path, config: ProjectConfig) -> GitCoordinator:
    repo = disposable_repo(tmp_path)
    coordinator = GitCoordinator(repo, LockSet(state_dir / "locks"), PathPolicy(repo, config),
                                 tmp_dir=state_dir / "tmp")
    coordinator.create_feature_branch(BRANCH, "dev")
    return coordinator


def commit_request(gc: GitCoordinator, role: str, paths: list[str]) -> CommitRequest:
    return CommitRequest(feature_id="f", task_id=f"f.{role}", role=role, branch=BRANCH,
                         expected_head=gc.rev_parse(BRANCH),
                         files={p: file_digest(gc.root / p) for p in paths}, message=f"{role} work")


def test_commit_succeeds_on_a_clean_checkout(gc: GitCoordinator) -> None:
    (gc.root / "backend/src/app.py").write_text("ROUTES = ['/ok']\n", encoding="utf-8")
    result = gc.commit(commit_request(gc, "backend", ["backend/src/app.py"]))
    assert result.sha and gc.rev_parse(BRANCH) == result.sha


@pytest.mark.parametrize("plant", [
    plant_fsmonitor,
    lambda repo, m: subprocess.run(["git", "-C", str(repo), "config", "filter.evil.clean",
                                    marker_program(repo / ".git/f.sh", m, passthrough=True)], check=True),
    lambda repo, m: subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", ".git/hooks"], check=True),
], ids=["fsmonitor", "filter", "hookspath"])
def test_commit_is_refused_when_the_checkout_config_can_execute(gc: GitCoordinator, tmp_path: Path, plant) -> None:
    (gc.root / "backend/src/app.py").write_text("ROUTES = ['/x']\n", encoding="utf-8")
    head = gc.rev_parse(BRANCH)
    marker = tmp_path / "COMMIT_MARKER"
    plant(gc.root, marker)
    with pytest.raises(GitConfigExecutionError):
        gc.commit(commit_request(gc, "backend", ["backend/src/app.py"]))
    assert gc.rev_parse(BRANCH) == head, "the branch must not move"
    assert not marker.exists(), "no program ran while refusing"


# -- the native-cli backend refuses a tampered checkout, hardened or not ----------------------------

@pytest.fixture
def agent_config(config: ProjectConfig, tmp_path: Path) -> ProjectConfig:
    return config_with_agents(config, install_fake_agent(tmp_path / "agents"))


@pytest.mark.parametrize("hardening_enabled", [False, True], ids=["non-hardened", "hardened"])
def test_native_backend_refuses_a_tampered_checkout_before_running(agent_config: ProjectConfig, tmp_path: Path,
                                                                   fake_home: Path, hardening_enabled: bool) -> None:
    del fake_home
    if hardening_enabled:
        agent_config = dataclasses.replace(agent_config, runtime=dataclasses.replace(
            agent_config.runtime, native_hardening=NativeHardening(enabled=True)))
    repo = disposable_repo(tmp_path)
    marker = tmp_path / "AGENT_OR_MON"
    plant_fsmonitor(repo, marker)
    runner = NativeCliRunner(agent_config.provider_for("backend"), agent_config,  # type: ignore[arg-type]
                             hardening_check=lambda *a, **k: "", enforcement_check=lambda *a, **k: "")
    action = [{"do": "write", "path": "backend/src/created.py", "text": "x = 1\n"}]
    request = RunRequest("f.backend", "backend", actions_prompt(action),
                         ScopedWorkspace(PathPolicy(repo, agent_config), "backend"),
                         context=run_context(tmp_path / "runs", "f.backend"))
    result = runner.run(request)
    assert result.status == "rejected" and "run a program" in result.error
    assert not (repo / "backend/src/created.py").exists(), "the agent must not have run"
    assert not marker.exists(), "the fsmonitor program must not have run"


def test_native_backend_commits_normally_on_a_clean_checkout(agent_config: ProjectConfig, tmp_path: Path,
                                                             fake_home: Path) -> None:
    del fake_home
    repo = disposable_repo(tmp_path)
    runner = NativeCliRunner(agent_config.provider_for("backend"), agent_config)  # type: ignore[arg-type]
    action = [{"do": "write", "path": "backend/src/app.py", "text": "ROUTES = ['/ok']\n"}]
    request = RunRequest("f.backend", "backend", actions_prompt(action),
                         ScopedWorkspace(PathPolicy(repo, agent_config), "backend"),
                         context=run_context(tmp_path / "runs", "f.backend"))
    result = runner.run(request)
    assert result.status == "completed", result.error
    assert "backend/src/app.py" in result.changed_paths
