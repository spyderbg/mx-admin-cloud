"""W4.7 (M6): the Worker SERVICE and integration jobs — one execution slot, shared with Feature Jobs (service.py
`_poll_github`, integration_worker.py).

A real WorkerService over a disposable checkout with scripted agents (the test_fc_autonomy rig) and, in the same state
directory, an integration job whose next stage is the Worker's. The stage process is a real tagged process
(fc_integration_worker_rig.Stages); agents are the scripted runners. Checked: a feature task that is ready starts
before any integration stage; while a stage runs the service is BUSY and starts no agent; pause starts nothing; a
service stop stops the stage and the next instance starts it again; with scheduling off the service does none of it."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,unused-import

from __future__ import annotations

import dataclasses
import time
from typing import Any

import pytest

from feature_coordinator import integration_worker
from feature_coordinator.autonomy import write_control
from feature_coordinator.errors import CoordinatorError
from feature_coordinator.integration import JobState
from feature_coordinator.integration_config import IntegrationWorkerConfig
from feature_coordinator.integration_recovery_commands import _resume
from feature_coordinator.model import TaskState
from feature_coordinator.service import WorkerService

from fc_integration_rig import OperatorKey, Rig, github_origin, origin
from fc_integration_worker_rig import Clock, Stages, events_of, poll, schedule, scheduling
from test_fc_autonomy import API, Rig as AgentRig
from test_fc_integration_review import launchers, rrig
from test_fc_integration_validate import FakeGates, key, rig, seeded, tracker
from test_fc_integration_worker import approved_job, stages


class Spawn:  # pylint: disable=too-few-public-methods
    """The service's spawner: tasks run as the agent rig's scripted agents; `.start` starts integration stages."""

    def __init__(self, agents: AgentRig, start: Any) -> None:
        self.agents, self.start = agents, start

    def __call__(self, task_id: str, launch: int, env: Any = None) -> Any:
        return self.agents.spawn(task_id, launch, env)


def agents_for(rig: Rig) -> AgentRig:
    """The agent rig over the integration rig's checkout and state directory, with the autonomous service enabled and
    short polls (the service's simulated clock moves only while it idles)."""
    worker = dataclasses.replace(rig.config.worker, autonomous_enabled=True, idle_poll_seconds=2)
    rig.config = dataclasses.replace(rig.config, worker=worker)
    return AgentRig(rig.repo, rig.state, config=rig.config)


def service(agents: AgentRig, runner: Any, *, until: Any, max_cycles: int = 400) -> WorkerService:
    return WorkerService(agents.config, agents.coord.root, agents.coord.store.root, coord=agents.coord,
                         spawn=Spawn(agents, runner), clock=agents.clock,
                         idle_sleep=lambda s: (agents.clock.sleep(s), time.sleep(0.002)),
                         poll_sleep=lambda _s: time.sleep(0.002), echo=agents.out.append, max_cycles=max_cycles,
                         stop_when=until)


def states(agents: AgentRig) -> list[tuple[str, str]]:
    return [(e["state"], e["reason"]) for e in agents.events("worker-state")]


def test_a_ready_feature_task_starts_before_any_integration_stage(rrig: Rig, stages: Any):
    scheduling(rrig, stages=("inspect",), poll_interval_seconds=1)
    job = approved_job(rrig)
    agents = agents_for(rrig)
    agents.activate(API)
    runner = stages()
    seen: list[str] = []

    def until(svc: WorkerService) -> bool:
        if runner.calls and not seen:
            seen.append(agents.task("w4.api").state.value)                 # the task's state when the stage started
        return rrig.state_of(job) is JobState.INSPECTING and bool(seen)
    assert service(agents, runner, until=until).run() == 0
    assert seen == [TaskState.VERIFIED.value]                              # Feature Jobs first
    assert [key for key, _at in agents.log] == ["w4.api"] and runner.stages == ["inspect"]
    started = events_of(rrig, "integration-stage-starting", job)[0]["at"]
    verified = agents.events("worker-task-verified")[0]["at"]
    assert verified <= started


def test_while_a_stage_runs_the_service_is_busy_and_starts_no_agent(rrig: Rig, stages: Any):
    scheduling(rrig, stages=("inspect",), poll_interval_seconds=1)
    job = approved_job(rrig)
    agents = agents_for(rrig)
    runner = stages(hold=("inspect",))
    phase: dict[str, Any] = {"activated": None, "released": None, "busy": []}

    def until(svc: WorkerService) -> bool:
        assert svc.record is not None
        if runner.calls and phase["activated"] is None:
            agents.activate(API)                                           # a task becomes ready while the stage runs
            phase["activated"] = svc.cycles
        elif phase["activated"] is not None and phase["released"] is None:
            phase["busy"].append((svc.record.state, len(agents.log), len(runner.alive())))
            if svc.cycles - phase["activated"] >= 12:
                runner.release(job)                                        # the stage finishes its work
                phase["released"] = svc.cycles
        return phase["released"] is not None and agents.task("w4.api").state is TaskState.VERIFIED
    assert service(agents, runner, until=until).run() == 0
    assert len(phase["busy"]) >= 10 and all(entry == ("BUSY", 0, 1) for entry in phase["busy"]), phase["busy"][:3]
    busy = [reason for state, reason in states(agents) if state == "BUSY"]
    assert busy and f"integration job {job}: `inspect` runs" in busy[0] and "no agent is started meanwhile" in busy[0]
    ended = events_of(rrig, "integration-stage-ended", job)[0]
    launched = [e for e in agents.events() if e["kind"] == "worker-task-verified"][0]
    assert ended["at"] <= launched["at"] and ended["progressed"] and rrig.state_of(job) is JobState.INSPECTING
    assert [key for key, _at in agents.log] == ["w4.api"] and runner.stages == ["inspect"]


def test_while_a_stage_runs_no_other_service_step_runs(rrig: Rig, stages: Any, monkeypatch: pytest.MonkeyPatch):
    """The one slot covers the service's other steps too: a publication pass (it runs a feature's integration gates),
    intake (a planner) and the Draft PR review wait until the stage has ended."""
    from feature_coordinator import review_worker  # pylint: disable=import-outside-toplevel
    scheduling(rrig, stages=("inspect",), poll_interval_seconds=1)
    job = approved_job(rrig)
    agents = agents_for(rrig)
    runner = stages(hold=("inspect",))
    polls: list[tuple[str, bool]] = []

    class Other:  # pylint: disable=too-few-public-methods
        """A stand-in for the publication / review / intake step: enabled, polled every second, it only counts."""
        enabled, poll_interval_seconds = True, 1

        def __init__(self, name: str) -> None:
            self.name = name

        def poll(self, *, apply: bool = True) -> Any:
            polls.append((self.name, bool(runner.alive())))
            return type("Report", (), {"lines": lambda self: [], "deferred": []})()
    monkeypatch.setattr(review_worker, "github_steps", lambda *_a, **_k: [
        (name, Other, lambda name=name: Other(name)) for name in ("publish", "review", "intake")])
    phase: dict[str, Any] = {"since": None}

    def until(svc: WorkerService) -> bool:
        if runner.pending and phase["since"] is None:
            phase["since"] = svc.cycles
        elif runner.pending and svc.cycles - phase["since"] >= 12:
            runner.release(job)
        return not runner.pending and rrig.state_of(job) is JobState.INSPECTING \
            and len([p for p in polls if not p[1]]) >= 9
    assert service(agents, runner, until=until).run() == 0
    during = [name for name, running in polls if running]
    assert during == []                                                    # nothing else ran beside the stage
    after = [name for name, running in polls if not running]
    assert {"publish", "review", "intake"} <= set(after)                   # … and they all run again afterwards


def test_a_paused_service_starts_no_stage(rrig: Rig, stages: Any):
    scheduling(rrig, poll_interval_seconds=1)
    job = approved_job(rrig)
    agents = agents_for(rrig)
    write_control(agents.coord.store, pause=True)
    runner = stages()
    assert service(agents, runner, until=lambda svc: svc.cycles >= 15).run() == 0
    assert not runner.calls and rrig.state_of(job) is JobState.AWAITING_APPROVAL
    assert ("PAUSED", "paused by the operator: no new work is started") in states(agents)
    write_control(agents.coord.store, pause=False)
    assert service(agents, runner, until=lambda svc: bool(runner.calls)).run() == 0
    assert runner.stages[0] == "inspect"


def test_a_service_stop_stops_the_stage_and_the_next_instance_starts_it_again(rrig: Rig, stages: Any):
    scheduling(rrig, stages=("inspect",), poll_interval_seconds=1)
    job = approved_job(rrig)
    agents = agents_for(rrig)
    runner = stages(hold=("inspect",))
    assert service(agents, runner, until=lambda svc: bool(runner.calls) and svc.record.state == "BUSY").run() == 0
    assert not runner.alive()                                              # stopped with the service
    rec = schedule(rrig, job)
    assert rec.running is None and rec.last["cause"] == "service-stop" and rec.stalls == 0 and rec.hold is None
    assert rrig.state_of(job) is JobState.AWAITING_APPROVAL
    assert any("integration: stopped `inspect`" in line for line in agents.out)
    runner.hold.clear()
    runner.pending.clear()
    assert service(agents, runner, until=lambda svc: rrig.state_of(job) is JobState.INSPECTING).run() == 0
    assert runner.stages == ["inspect", "inspect"] and schedule(rrig, job).launches == {"inspect": 2}


def test_with_scheduling_off_the_service_ignores_integration_jobs(rrig: Rig, stages: Any):
    job = approved_job(rrig)                                               # github.integration.worker is absent
    agents = agents_for(rrig)
    agents.activate(API)
    runner = stages()
    def done(svc: WorkerService) -> bool:
        return agents.task("w4.api").state is TaskState.VERIFIED and svc.cycles > 20
    assert service(agents, runner, until=done).run() == 0
    assert not runner.calls and rrig.state_of(job) is JobState.AWAITING_APPROVAL
    assert not (rrig.state / "integration-schedule").exists()
    assert not [e for e in agents.events() if e["kind"].startswith("integration-stage")]
    assert not [line for line in agents.out if "integration job" in line or "integration:" in line]
    assert [key for key, _at in agents.log] == ["w4.api"]                  # Feature Jobs exactly as before


# -- the slot is read from the records, not remembered (Codex review of M6) ------------------------------------------

class Counting:  # pylint: disable=too-few-public-methods
    """A stand-in for the publication / review / intake step: enabled, polled every second, it only counts."""
    enabled, poll_interval_seconds = True, 1

    def __init__(self, name: str, polls: list[tuple[str, bool]], runner: Any) -> None:
        self.name, self.polls, self.runner = name, polls, runner

    def poll(self, *, apply: bool = True) -> Any:
        self.polls.append((self.name, bool(self.runner.alive())))
        return type("Report", (), {"lines": lambda self: [], "deferred": []})()


def counted_steps(monkeypatch: pytest.MonkeyPatch, runner: Any) -> list[tuple[str, bool]]:
    from feature_coordinator import review_worker  # pylint: disable=import-outside-toplevel
    polls: list[tuple[str, bool]] = []
    monkeypatch.setattr(review_worker, "github_steps", lambda *_a, **_k: [
        (name, Counting, lambda name=name: Counting(name, polls, runner)) for name in ("publish", "review", "intake")])
    return polls


@pytest.mark.parametrize("scheduling_after_restart", [True, False])
def test_a_restarted_service_finds_a_surviving_stage_before_it_does_anything_else(
        rrig: Rig, stages: Any, monkeypatch: pytest.MonkeyPatch, scheduling_after_restart: bool):
    """A service dies without stopping its stage (outside systemd the stage survives). The next instance — also one
    whose configuration no longer schedules integration jobs — must see the survivor in its FIRST cycle: no
    publication pass, no intake, no reviewer and no agent beside it; it watches it and settles it."""
    scheduling(rrig, stages=("inspect",), poll_interval_seconds=1)
    job = approved_job(rrig)
    runner = stages(hold=("inspect",))
    assert poll(rrig, runner, Clock()).started == [job] and len(runner.alive()) == 1   # … and that service is gone
    integration_worker._CHILDREN.clear()
    if not scheduling_after_restart:                                   # the operator switched scheduling off
        github = rrig.config.github
        rrig.config = dataclasses.replace(rrig.config, github=dataclasses.replace(
            github, integration=dataclasses.replace(github.integration, worker=IntegrationWorkerConfig())))
    agents = agents_for(rrig)
    agents.activate(API)                                               # and a feature task is ready
    polls = counted_steps(monkeypatch, runner)
    phase: dict[str, Any] = {"cycles": 0}

    def until(svc: WorkerService) -> bool:
        phase["cycles"] += 1
        if runner.pending and phase["cycles"] == 12:
            assert svc.record.state == "BUSY" and not agents.log and not polls   # 12 cycles: nothing beside it
            runner.release(job)
        return not runner.pending and agents.task("w4.api").state is TaskState.VERIFIED and len(polls) >= 3 \
            and schedule(rrig, job).running is None                    # (settled at the step's own poll interval)
    assert service(agents, runner, until=until).run() == 0
    assert [name for name, running in polls if running] == []          # no other step ever ran beside the stage
    rec = schedule(rrig, job)
    assert rec.running is None and rec.last["cause"] == "exited" and rec.last["progressed"]   # watched and settled
    assert rrig.state_of(job) is JobState.INSPECTING and runner.stages == ["inspect"]         # … and never restarted
    busy = [reason for state, reason in states(agents) if state == "BUSY"]
    assert busy and f"integration job {job}: `inspect` runs" in busy[0]
    assert [key for key, _at in agents.log] == ["w4.api"]              # the agent ran only afterwards


def test_a_stage_record_that_becomes_unreadable_while_it_runs_stops_agents_too(rrig: Rig, stages: Any):
    """Unknown ownership is not free: the service starts no agent while it cannot tell whether the stage runs."""
    scheduling(rrig, stages=("inspect",), poll_interval_seconds=1)
    job = approved_job(rrig)
    agents = agents_for(rrig)
    runner = stages(hold=("inspect",))
    path = rrig.state / "integration-schedule" / f"{job}.json"
    phase: dict[str, Any] = {"corrupted": None, "seen": []}

    def until(svc: WorkerService) -> bool:
        assert svc.record is not None
        if runner.calls and phase["corrupted"] is None:
            path.write_text("{ this is not a schedule record", encoding="utf-8")   # while the stage process runs
            agents.activate(API)                                                   # … and a task becomes ready
            phase["corrupted"] = svc.cycles
        elif phase["corrupted"] is not None:
            phase["seen"].append((svc.record.state, len(agents.log)))
        return phase["corrupted"] is not None and svc.cycles - phase["corrupted"] >= 15
    assert service(agents, runner, until=until).run() == 0
    assert phase["seen"] and all(entry == ("NEEDS_ATTENTION", 0) for entry in phase["seen"]), phase["seen"][:3]
    reason = [r for state, r in states(agents) if state == "NEEDS_ATTENTION"][-1]
    assert "cannot be read" in reason and "whether an integration stage runs is unknown" in reason
    assert agents.task("w4.api").state is TaskState.READY and not agents.log
    # the operator cannot set the record aside while a stage process lives; once it is dead, the reset frees the slot
    with pytest.raises(CoordinatorError, match="stage processes .* are alive"):
        _resume(rrig.store, job, "looked at it", "spyderbg", apply=True)
    runner.kill(job)
    assert _resume(rrig.store, job, "looked at it", "spyderbg", apply=True) == "reset"
    done = lambda svc: agents.task("w4.api").state is TaskState.VERIFIED   # noqa: E731  # pylint: disable=unnecessary-lambda-assignment
    assert service(agents, runner, until=done).run() == 0
