"""W1 Priority 3: hardened Claude Code (opt-in native hardening, so NO Bash tool) can still carry a
representative task to a verified result autonomously, because validation runs Coordinator-side.

The real `NativeCliRunner` drives the fake-agent CLI (no model, no network) through the real
`FeatureCoordinator` and the real autonomous `WorkerService`: the agent only edits files (it has no
Bash tool), the Coordinator runs the role's gate, the gate fails once, the existing validation
auto-fix retry re-runs the step, the gate then passes, and the task reaches VERIFIED with no operator
intervention. Reuses the autonomy harness (`test_fc_autonomy.Rig`) and the installed fake agent.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import glob
import json
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.model import TaskState
from feature_coordinator.native_backend import NativeCliRunner
from feature_coordinator.native_hardening import NativeHardening
from feature_coordinator.workflow import TaskSpec, TaskStep

import test_fc_autonomy as autonomy
from fc_helpers import config_with_agents, install_fake_agent

STEP_INSTRUCTION = (  # the fake agent (follow_prompt) obeys exactly this — a file edit, no command
    "Create the file backend/src/app.py with exactly the content below and nothing else in it.\n"
    "```python\nROUTES = ['/health']\n```\n"
    "Do not create, change or delete any other file. Do not run any command.")


class FailingOnce:  # pylint: disable=too-few-public-methods
    """A gate runner that fails *name* the first time it is asked and passes afterwards — a validation
    failure the agent is expected to recover from on the next attempt."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []
        self.failed = False

    def __call__(self, name: str, context: object) -> int:
        del context
        self.calls.append(name)
        if name == self.name and not self.failed:
            self.failed = True
            return 1
        return 0


@pytest.fixture
def hardened_native_config(tmp_path: Path) -> tuple[ProjectConfig, Path]:
    agent = install_fake_agent(tmp_path / "agents")
    Path(f"{agent}.json").write_text(json.dumps({"follow_prompt": True}), encoding="utf-8")
    cfg = autonomy.autonomous(validation_auto_fix_enabled=True, validation_max_attempts=3)
    cfg = config_with_agents(cfg, agent)
    cfg = dataclasses.replace(cfg, runtime=dataclasses.replace(
        cfg.runtime, native_hardening=NativeHardening(enabled=True)))
    return cfg, agent


def latest_native_record(state_root: Path) -> dict:
    records = sorted(glob.glob(str(state_root / "runs" / "*" / "native.json")))
    assert records, "the native backend recorded no run"
    return json.loads(Path(records[-1]).read_text(encoding="utf-8"))


def test_hardened_claude_reaches_verified_through_validation_retry(hardened_native_config, repo, state_dir,
                                                                   fake_home) -> None:
    del fake_home  # the native runner reads a fake HOME, never real credentials
    cfg, _agent = hardened_native_config
    rig = autonomy.Rig(repo, state_dir, config=cfg)
    # the real hardened native runner replaces the mock backend runner; the service shares it via spawn
    rig.coord.runners["backend"] = NativeCliRunner(cfg.provider_for("backend"), cfg)  # type: ignore[arg-type]
    rig.gates = FailingOnce("backend-tests")
    rig.activate(TaskSpec("api", "backend", "Add the health route",
                          steps=(TaskStep("endpoints", STEP_INSTRUCTION),), required_gates=("backend-tests",)))

    exit_code = rig.service(until=autonomy.state_is("AVAILABLE"), max_cycles=40).run()

    task = rig.task("w4.api")
    assert exit_code == 0 and task.state is TaskState.VERIFIED, (task.state, rig.out[-5:])
    # the Coordinator ran the gate (the agent has no Bash): it failed once, then passed on the retry
    assert rig.gates.failed and rig.gates.calls == ["backend-tests", "backend-tests"]
    # the correction came from the existing validation retry, not an operator
    assert rig.sched("w4.api").attempts.get(autonomy.VALIDATION) == 1
    assert not [e for e in rig.events() if e.get("kind") == "worker-state" and e.get("state") == "NEEDS_ATTENTION"]
    # the agent produced the code change with file tools only and it was committed on the branch
    assert (repo / "backend/src/app.py").read_text(encoding="utf-8") == "ROUTES = ['/health']\n"
    # the launched Claude Code genuinely had no Bash tool (hardening in effect)
    record = latest_native_record(rig.coord.store.root)
    argv = record["argv"]
    assert "Bash" not in argv[argv.index("--tools") + 1].split(",")
    assert record["hardening"] == {"enabled": True, "agent_shell": "none (no Bash tool)",
                                   "cli_process": "unconfined (host network, own login)"}


def test_a_single_hardened_run_completes_and_commits_without_bash(hardened_native_config, repo, state_dir,
                                                                  fake_home) -> None:
    """The simplest slice: one hardened run obtains and commits a change, gate passing, no Bash, no operator."""
    del fake_home
    cfg, _agent = hardened_native_config
    rig = autonomy.Rig(repo, state_dir, config=cfg)
    rig.coord.runners["backend"] = NativeCliRunner(cfg.provider_for("backend"), cfg)  # type: ignore[arg-type]
    rig.gates = FailingOnce("never-fails")  # the required gate passes on the first call
    rig.activate(TaskSpec("api", "backend", "Add the health route",
                          steps=(TaskStep("endpoints", STEP_INSTRUCTION),), required_gates=("backend-tests",)))
    assert rig.service(until=autonomy.state_is("AVAILABLE"), max_cycles=20).run() == 0
    assert rig.task("w4.api").state is TaskState.VERIFIED
    assert rig.gates.calls == ["backend-tests"]  # one Coordinator-side validation, agent needed no Bash
    assert (repo / "backend/src/app.py").exists()
