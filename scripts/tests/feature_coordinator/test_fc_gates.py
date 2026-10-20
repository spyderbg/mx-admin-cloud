"""Sandboxed gates: timeouts, logs, offline, read-only checkout (B-7), unsafe-gate refusal."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import socket
import time
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.gates import GateContext, SandboxedGateRunner
from feature_coordinator.issues import MockIssueTracker
from feature_coordinator.model import TaskState
from feature_coordinator.runners import MockClaudeCodeRunner
from feature_coordinator.store import StateStore
from feature_coordinator.workflow import FeatureCoordinator, FeatureSpec, TaskSpec

B7_FIXTURE = '''"""Mimics the B-7 backend test: it writes a registry lock file into the real content/ tree."""
from pathlib import Path
lock = Path(__file__).resolve().parents[1] / "content" / "foods" / ".registry.lock"
lock.write_text("locked by a test")
print("wrote", lock)
'''

B7_PYTEST_FIXTURE = '''"""The B-7 fixture as a pytest test, collected by the real backend-tests gate command."""
from pathlib import Path


def test_writes_a_registry_lock_into_content():
    lock = Path(__file__).resolve().parents[1] / "content" / "foods" / ".registry.lock"
    lock.write_text("locked by a test")
'''

UNSAFE_REASON = "unsafe against a real checkout (test fixture)"


@pytest.fixture
def gate_repo(tmp_path: Path) -> Path:
    path = tmp_path / "gate-repo"
    init_demo_repo(path, {"backend/run_fixture_tests.py": B7_FIXTURE, "backend/test_b7_fixture.py": B7_PYTEST_FIXTURE,
                          "content/foods/items.csv": "id\n", "frontend/src/view.ts": "export {}\n",
                          "docs_agents/coordination/README.md": "# c\n"})
    return path


@pytest.fixture
def gate_config(config: ProjectConfig, gate_repo: Path) -> ProjectConfig:
    del gate_repo
    gates = {
        "backend-fixture-tests": ("python", "run_fixture_tests.py"),
        "ok": ("python", "-c", "print('all good')"),
        "chatty": ("python", "-c", "import sys; print('to stdout'); print('to stderr', file=sys.stderr); sys.exit(2)"),
        "slow": ("python", "-c", "import time; time.sleep(30)"),
        "missing-tool": ("definitely-not-an-installed-tool", "--version"),
        "net": ("python", "-c", "import socket, sys; s = socket.socket(); s.settimeout(2); "
                                "sys.exit(0 if s.connect_ex(('127.0.0.1', int(sys.argv[1]))) == 0 else 7)", "0"),
        "unsafe": ("python", "-c", "open('started', 'w').close()"),
    }
    return dataclasses.replace(config, gates=gates, lint_gates={}, unsafe_gates={"unsafe": UNSAFE_REASON})


def run(gate_config: ProjectConfig, repo: Path, name: str, tmp_path: Path, **kwargs):
    return SandboxedGateRunner(gate_config, tmp_path / "logs", **kwargs)(name, GateContext(repo))


def test_an_unsafe_gate_is_blocked_not_run(gate_config: ProjectConfig, gate_repo: Path, tmp_path: Path) -> None:
    result = run(gate_config, gate_repo, "unsafe", tmp_path)
    assert result.status == "blocked" and not result.passed and result.summary == UNSAFE_REASON
    assert not (tmp_path / "logs").exists()  # nothing was started


@pytest.mark.usefixtures("needs_bwrap")
def test_the_real_backend_tests_gate_runs_sandboxed(config: ProjectConfig, gate_repo: Path, tmp_path: Path) -> None:
    assert not config.unsafe_gates  # B-7 is fixed: backend-tests is no longer refused
    result = SandboxedGateRunner(config, tmp_path / "logs")("backend-tests", GateContext(gate_repo))
    assert (result.status, result.exit_code) == ("failed", 1) and "Read-only file system" in result.summary
    assert not (gate_repo / "content/foods/.registry.lock").exists()


@pytest.mark.usefixtures("needs_bwrap")
def test_a_test_writing_into_content_fails_safely(gate_config: ProjectConfig, gate_repo: Path, tmp_path: Path) -> None:
    result = run(gate_config, gate_repo, "backend-fixture-tests", tmp_path)
    assert result.status == "failed" and "Read-only file system" in result.summary
    assert not (gate_repo / "content/foods/.registry.lock").exists()


@pytest.mark.usefixtures("needs_bwrap")
def test_output_is_logged_and_exit_code_kept(gate_config: ProjectConfig, gate_repo: Path, tmp_path: Path) -> None:
    result = run(gate_config, gate_repo, "chatty", tmp_path)
    assert (result.status, result.exit_code) == ("failed", 2)
    log = Path(result.log)
    assert (log / "stdout.log").read_text().strip() == "to stdout"
    assert (log / "stderr.log").read_text().strip() == "to stderr"
    assert "to stderr" in result.summary and result.duration_seconds > 0
    ok = run(gate_config, gate_repo, "ok", tmp_path)
    assert ok.passed and ok.status == "passed"


@pytest.mark.usefixtures("needs_bwrap")
def test_timeout_kills_the_gate(gate_config: ProjectConfig, gate_repo: Path, tmp_path: Path) -> None:
    start = time.monotonic()
    result = run(gate_config, gate_repo, "slow", tmp_path, timeouts={"slow": 1})
    assert result.status == "timeout" and result.exit_code == 124 and not result.passed
    assert time.monotonic() - start < 15


@pytest.mark.usefixtures("needs_bwrap")
def test_gates_are_offline(gate_config: ProjectConfig, gate_repo: Path, tmp_path: Path) -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    config = dataclasses.replace(gate_config, gates={**gate_config.gates,
                                                     "net": gate_config.gates["net"][:-1] + (str(port),)})
    result = run(config, gate_repo, "net", tmp_path)
    server.close()
    assert result.exit_code == 7  # the host's loopback is not reachable from the gate sandbox


def test_missing_tool_is_blocked(gate_config: ProjectConfig, gate_repo: Path, tmp_path: Path, bwrap_ok: bool) -> None:
    result = run(gate_config, gate_repo, "missing-tool", tmp_path)
    assert result.status == "blocked" and not result.passed
    assert ("not installed" in result.summary) or not bwrap_ok


@pytest.mark.usefixtures("needs_bwrap")
def test_sandboxed_gates_produce_task_evidence(gate_config: ProjectConfig, gate_repo: Path, state_dir: Path) -> None:
    runner = MockClaudeCodeRunner(gate_config.providers["claude"])  # type: ignore[arg-type]
    coord = FeatureCoordinator(gate_config, gate_repo, StateStore(state_dir), tracker=MockIssueTracker(),
                               runners={"frontend": runner})
    spec = FeatureSpec("g", "G", (TaskSpec("ui", "frontend", "UI", required_gates=("ok",)),
                                  TaskSpec("bad", "frontend", "Bad", required_gates=("chatty",))))
    coord.activate(coord.plan(spec).id)
    assert coord.run_task("g.ui").state is TaskState.VERIFIED
    evidence = coord.store.load_task("g.ui").evidence
    assert evidence.gates[0].status == "passed" and Path(evidence.gates[0].log).is_dir()
    assert coord.run_task("g.bad").state is TaskState.FAILED
