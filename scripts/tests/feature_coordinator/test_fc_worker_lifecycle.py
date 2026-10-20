"""Worker lifecycle with real sandboxed runs: stop, timeout, heartbeat, SIGTERM, SIGKILL + recovery."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest

from feature_coordinator.claims import ClaimManager, heartbeat_age
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import RecordingGates, init_demo_repo
from feature_coordinator.errors import ClaimError
from feature_coordinator.model import TaskState
from feature_coordinator.operations import build_coordinator, build_runners
from feature_coordinator.workflow import FeatureSpec, TaskSpec

from fc_helpers import Holder, actions_prompt, git, start_worker, worker_result

SLOW = [{"do": "write", "path": "backend/src/partial.py", "text": "PARTIAL = True\n"},
        {"do": "daemon", "path": "backend/beat.log", "interval": 0.05},
        {"do": "sleep", "seconds": 60}]


@pytest.fixture
def lifecycle(tmp_path: Path, state_dir: Path, agent_config: ProjectConfig, fake_home: Path):
    del fake_home
    repo = tmp_path / "life"
    init_demo_repo(repo)
    coord = build_coordinator(agent_config, repo, state_dir, runners=build_runners(agent_config))
    coord.gate_runner = RecordingGates()
    coord.activate(coord.plan(FeatureSpec("life", "Lifecycle", (TaskSpec("api", "backend", "API"),))).id)
    return repo, coord


def wait_for(predicate, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def stopped_growing(path: Path) -> bool:
    size = path.stat().st_size
    time.sleep(0.5)
    return path.stat().st_size == size


def test_stop_needs_a_running_task(lifecycle) -> None:
    _repo, coord = lifecycle
    with pytest.raises(ClaimError, match="not running"):
        coord.request_cancel("life.api")


@pytest.mark.usefixtures("needs_bwrap")
def test_operator_stop_terminates_the_sandbox_and_yields_the_task(lifecycle, state_dir: Path, fake_agent: Path) -> None:
    repo, coord = lifecycle
    proc = start_worker(repo, state_dir, fake_agent, "life.api", actions_prompt(SLOW))
    assert wait_for((repo / "backend/beat.log").exists)
    coord.request_cancel("life.api", reason="operator stop in test")
    result = worker_result(proc)
    assert result["state"] == "ready" and result["runner"] == "cancelled"
    assert stopped_growing(repo / "backend/beat.log")  # every sandbox process is gone
    task = coord.store.load_task("life.api")
    assert task.state is TaskState.READY and not coord.store.list_claims()
    assert coord.store.get_flag("cancel-life.api") is None  # the stop request was consumed
    # The role's uncommitted work stays in its tree for the next run; nothing was committed.
    assert (repo / "backend/src/partial.py").exists() and git(repo, "log", "--format=%s").splitlines() == ["Seed"]
    assert any(e["kind"] == "run-cancelled" for e in coord.store.events())


@pytest.mark.usefixtures("needs_bwrap")
def test_task_timeout_fails_the_task(lifecycle, state_dir: Path, fake_agent: Path) -> None:
    repo, coord = lifecycle
    proc = start_worker(repo, state_dir, fake_agent, "life.api", actions_prompt(SLOW),
                        {"task_timeout_seconds": 1, "terminate_grace_seconds": 1})
    result = worker_result(proc)
    assert result["state"] == "failed" and result["runner"] == "timeout"
    assert coord.store.load_task("life.api").state is TaskState.FAILED
    assert stopped_growing(repo / "backend/beat.log")


@pytest.mark.usefixtures("needs_bwrap")
def test_heartbeats_and_checkpoint_while_running(lifecycle, state_dir: Path, fake_agent: Path) -> None:
    repo, coord = lifecycle
    proc = start_worker(repo, state_dir, fake_agent, "life.api", actions_prompt(SLOW),
                        {"heartbeat_interval_seconds": 1, "stale_heartbeat_seconds": 3})
    assert wait_for(lambda: (coord.store.load_claim("life.api") or {}).get("checkpoint", {}).get("run_dir"))
    first = coord.store.load_claim("life.api")["heartbeat_at"]
    assert wait_for(lambda: coord.store.load_claim("life.api")["heartbeat_at"] != first, timeout=5)
    assert heartbeat_age(coord.store.load_claim("life.api")) < 3
    coord.request_cancel("life.api")
    assert worker_result(proc)["runner"] == "cancelled"
    del repo


@pytest.mark.usefixtures("needs_bwrap")
def test_sigterm_to_the_worker_is_a_graceful_stop(lifecycle, state_dir: Path, fake_agent: Path) -> None:
    repo, coord = lifecycle
    proc = start_worker(repo, state_dir, fake_agent, "life.api", actions_prompt(SLOW))
    assert wait_for((repo / "backend/beat.log").exists)
    os.kill(proc.pid, signal.SIGTERM)
    result = worker_result(proc)
    assert result["runner"] == "cancelled" and result["state"] == "ready"
    assert not coord.store.list_claims() and stopped_growing(repo / "backend/beat.log")


@pytest.mark.usefixtures("needs_bwrap")
def test_sigkilled_worker_is_recovered_and_its_sandbox_is_gone(lifecycle, state_dir: Path, fake_agent: Path) -> None:
    repo, coord = lifecycle
    proc = start_worker(repo, state_dir, fake_agent, "life.api", actions_prompt(SLOW))
    assert wait_for((repo / "backend/beat.log").exists)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    assert stopped_growing(repo / "backend/beat.log")
    assert coord.store.load_task("life.api").state is TaskState.IN_PROGRESS  # until recovery says otherwise
    report = coord.claims.recover()
    assert [r["task_id"] for r in report.recovered] == ["life.api"]
    assert coord.store.load_task("life.api").state is TaskState.READY
    # Resume: the next run starts from the role's uncommitted work and lands it.
    resumed = start_worker(repo, state_dir, fake_agent, "life.api", actions_prompt([
        {"do": "write", "path": "backend/src/api.py", "text": "API = True\n"}]))
    result = worker_result(resumed)
    assert result["state"] == "verified", result
    assert sorted(result["changed"]) == ["backend/beat.log", "backend/src/api.py", "backend/src/partial.py"]


def test_alive_worker_without_heartbeats_is_reported_unresponsive(lifecycle, state_dir: Path,
                                                                  holders: list[Holder]) -> None:
    _repo, coord = lifecycle
    holder = Holder("import sys\nfrom pathlib import Path\nfrom feature_coordinator.claims import ClaimManager\n"
                    "from feature_coordinator.store import StateStore\n"
                    "c = ClaimManager(StateStore(Path(sys.argv[1]))).claim('life.api', role='backend', "
                    "branch='feature/life', head_sha='a' * 40)", str(state_dir))
    holders.append(holder)
    with coord.store.transaction():
        record = coord.store.load_claim("life.api")
        record["heartbeat_at"] = "2020-01-01T00:00:00.000000Z"
        coord.store.save_claim("life.api", record)
    report = ClaimManager(coord.store).recover(stale_after_seconds=120)
    assert [r["task_id"] for r in report.unresponsive] == ["life.api"] and not report.recovered
    assert coord.store.load_task("life.api").state is TaskState.IN_PROGRESS  # alive: never reclaimed


# -- CLI ----------------------------------------------------------------------------------------

@pytest.mark.usefixtures("needs_bwrap")
def test_cli_sandbox_check_and_gate_commands(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "cli-repo"
    init_demo_repo(repo)
    state = tmp_path / "cli-state"
    assert cli_main(["--repo", str(repo), "--state-dir", str(state), "sandbox-check"]) == 0
    assert "dry run" in capsys.readouterr().out
    assert cli_main(["--repo", str(repo), "--state-dir", str(state), "sandbox-check", "--apply"]) == 0
    assert "containment self-test: PASSED" in capsys.readouterr().out
    assert cli_main(["--repo", str(repo), "--state-dir", str(state), "gate", "backend-tests", "--apply"]) == 1
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "B-7" in out  # refused, with the recorded reason
    assert cli_main(["--repo", str(repo), "--state-dir", str(state), "stop", "nope.task"]) == 1
    assert cli_main(["--repo", str(repo), "--state-dir", str(state), "recover"]) == 0


def test_cli_lifecycle_commands_are_dry_runs_by_default(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "cli-repo"
    init_demo_repo(repo)
    state = tmp_path / "cli-state"
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"slug": "dry", "title": "Dry", "tasks": [{"key": "a", "role": "backend",
                                                                         "title": "A"}]}), encoding="utf-8")
    base = ["--repo", str(repo), "--state-dir", str(state)]
    assert cli_main([*base, "plan", str(spec)]) == 0
    assert not (state / "features").exists()
    assert cli_main([*base, "plan", str(spec), "--apply"]) == 0
    assert cli_main([*base, "activate", "dry"]) == 0
    assert git(repo, "branch", "--list", "feature/dry") == ""
    assert cli_main([*base, "activate", "dry", "--apply"]) == 0
    assert git(repo, "branch", "--show-current").strip() == "feature/dry"
    assert cli_main([*base, "launch", "dry.a"]) == 0 and "dry run" in capsys.readouterr().out
    assert not (state / "runs").exists() or not list((state / "runs").iterdir())  # nothing was launched
