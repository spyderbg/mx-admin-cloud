"""W4: the autonomous Worker service — in-process tests with mock agents and a simulated clock.

Every test uses a disposable Git repository and state directory. Agents are the coordinator's
mock runners, wrapped by `Scripted` to inject failures per invocation and to record when each
ran (and how many ran at once). Launches run in threads on their own coordinator, like
`feature.py launch` processes. Time is simulated: the service's clock only advances when it
idles. No model, no GitHub. Real-process scenarios (restart, orphan, crash loop, stop, second
service, crash during bookkeeping) are in test_fc_service_process.py."""

# pylint: disable=missing-function-docstring,redefined-outer-name,too-many-lines

from __future__ import annotations

import dataclasses
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from feature_coordinator.autonomy import (
    CRITICAL,
    EXIT_DO_NOT_RESTART,
    SUBSCRIPTION,
    TRANSIENT,
    UNKNOWN,
    VALIDATION,
    Outcome,
    Scheduling,
    apply_outcome,
    backoff_seconds,
    classify,
    health_report,
    load_scheduling,
    scrub,
    write_control,
)
from feature_coordinator.checkpoints import load_record
from feature_coordinator.claims import ClaimManager, drop_exclusive, hold_exclusive
from feature_coordinator.cli import EXAMPLE_CONFIG, main as cli_main
from feature_coordinator.config import WorkerConfig, config_from_mapping, default_config
from feature_coordinator.demo import DEMO_SCRIPTS, RecordingGates, build_coordinator, demo_spec
from feature_coordinator.gates import SandboxedGateRunner
from feature_coordinator.errors import CheckoutBusyError, ConfigError
from feature_coordinator.model import HistoryEntry, Task, TaskState, TaskStep
from feature_coordinator.runners import FileEdit, RunResult
from feature_coordinator.service import WorkerService
from feature_coordinator.worker import FeatureWorker
from feature_coordinator.workflow import FeatureSpec, TaskSpec

from fc_helpers import git
from test_fc_worker_loop import ThreadHandle

T0 = 1_900_000_000.0
EDITS = {"backend": {"w4.api": [FileEdit("backend/src/app.py", "ROUTES = ['/w4']\n")],
                     "w4.api#models": [FileEdit("backend/src/models.py", "M = 1\n")],
                     "w4.api#service": [FileEdit("backend/src/service.py", "S = 1\n")],
                     "w4.api#endpoints": [FileEdit("backend/src/app.py", "ROUTES = ['/w4']\n")]},
         "cli": {"w4.cmd": [FileEdit("cli/src/commands.py", "COMMANDS = ['w4']\n")]},
         "frontend": {"w4.view": [FileEdit("frontend/src/view.ts", "export const views = ['w4']\n")]},
         "coordination": {"w4.docs": [FileEdit("docs_agents/coordination/w4.md", "# w4\n")]}}
STEPS = (TaskStep("models", "Implement the models"), TaskStep("service", "Implement the service"),
         TaskStep("endpoints", "Implement the endpoints"))


class Clock:
    """Simulated time: it only moves when the service idles (or a test moves it)."""

    def __init__(self) -> None:
        self.now = T0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Scripted:  # pylint: disable=too-few-public-methods
    """A role's runner: per script key, a queue of behaviours (None = the mock's real edits, a
    RunResult = that result, a callable = called with the request). Records every invocation."""

    def __init__(self, runner: Any, clock: Clock, log: list, active: list, plan: dict | None = None) -> None:
        self.runner, self.clock, self.log, self.active = runner, clock, log, active
        self.plan = {k: list(v) for k, v in (plan or {}).items()}
        self.name, self.kind = runner.name, runner.kind

    def run(self, request):
        self.active[0] += 1
        self.active[1] = max(self.active[1], self.active[0])
        self.log.append((request.script_key, self.clock.now))
        try:
            queue = self.plan.get(request.script_key) or []
            behaviour = queue.pop(0) if queue else None
            if callable(behaviour):
                return behaviour(request)
            return behaviour if isinstance(behaviour, RunResult) else self.runner.run(request)
        finally:
            self.active[0] -= 1


def fail(text: str) -> RunResult:
    return RunResult("claude", "failed", error=f"runner exit 1: {text}")


def autonomous(**overrides: Any):
    config = default_config()
    return dataclasses.replace(config, worker=dataclasses.replace(config.worker, autonomous_enabled=True, **overrides))


class Rig:  # pylint: disable=too-many-instance-attributes
    """A coordinator, scripted agents and a service factory over one disposable repository."""

    def __init__(self, repo: Path, state: Path, *, config=None, failing_gates=(), plan=None) -> None:
        self.config = config or autonomous()
        self.clock, self.log, self.active, self.out = Clock(), [], [0, 0], []
        self.gates = RecordingGates(failing=list(failing_gates))
        self.coord = build_coordinator(repo, state, config=self.config, scripts=EDITS, gates=self.gates)
        for role in ("backend", "cli", "frontend", "coordination"):
            self.coord.runners[role] = Scripted(self.coord.runners[role], self.clock, self.log, self.active,
                                                (plan or {}).get(role))

    def activate(self, *tasks: TaskSpec, slug: str = "w4"):
        return self.coord.activate(self.coord.plan(FeatureSpec(slug, "W4", tasks)).id)

    def spawn(self, task_id: str, launch: int, env: dict | None = None) -> ThreadHandle:
        del launch, env  # a thread: no process to tag
        own = build_coordinator(self.coord.root, self.coord.store.root, config=self.config, gates=self.gates)
        own.runners = self.coord.runners
        return ThreadHandle(lambda: own.run_task(task_id))

    def service(self, *, until=None, max_cycles: int = 60, **kwargs: Any) -> WorkerService:
        return WorkerService(self.config, self.coord.root, self.coord.store.root, coord=self.coord, spawn=self.spawn,
                             clock=self.clock, idle_sleep=self.clock.sleep, poll_sleep=lambda _s: time.sleep(0.002),
                             echo=self.out.append, max_cycles=max_cycles, stop_when=until, **kwargs)

    def task(self, task_id: str) -> Task:
        return self.coord.store.load_task(task_id)

    def sched(self, task_id: str) -> Scheduling:
        return load_scheduling(self.coord.store, self.task(task_id))

    def events(self, kind: str | None = None) -> list[dict]:
        return [e for e in self.coord.store.events() if kind is None or e["kind"] == kind]


def last_state(rig: Rig) -> tuple[str, str]:
    """The service's last state before it stopped (the record itself says OFFLINE afterwards)."""
    event = [e for e in rig.events("worker-state") if e["state"] != "OFFLINE"][-1]
    return event["state"], event["reason"]


def long_waits(**overrides: Any):
    """Idle polls longer than any wait, so a simulated wait of hours takes a few cycles."""
    return autonomous(idle_poll_seconds=86400, **overrides)


def state_is(*states: str):
    return lambda service: bool(service.record) and service.record.state in states and service.cycles > 1


@pytest.fixture
def rig(repo: Path, state_dir: Path) -> Rig:
    return Rig(repo, state_dir)


API, CMD, VIEW = TaskSpec("api", "backend", "API"), TaskSpec("cmd", "cli", "Command", depends_on=("api",)), \
    TaskSpec("view", "frontend", "View", depends_on=("api",))


# -- policy units ----------------------------------------------------------------------------------------

def task_with(state: TaskState, reason: str = "", steps=()) -> Task:
    task = Task(id="f.a", feature_id="f", role="backend", title="A", steps=tuple(steps), state=state)
    task.history.append(HistoryEntry("2026-01-01T00:00:00Z", "in-progress", state.value, reason))
    return task


@pytest.mark.parametrize("state,reason,events,log,category", [
    (TaskState.VERIFIED, "", [], "", "success"),
    (TaskState.FAILED, "gates failed: ['backend-tests']", [], "", VALIDATION),
    (TaskState.FAILED, "gates could not run: ['backend-tests'] (backend-tests blocked: bwrap cannot create namespaces "
                       "here: Permission denied)", [], "", CRITICAL),
    (TaskState.FAILED, "runner failed: runner exit 1: read ECONNRESET", [], "", TRANSIENT),
    (TaskState.FAILED, "runner failed: runner exit 1: Claude AI usage limit reached|1900003600", [], "", SUBSCRIPTION),
    (TaskState.FAILED, "runner failed: You've hit your usage limit. Try again later.", [], "", SUBSCRIPTION),
    (TaskState.FAILED, "runner timed out after 3600s", [], "", TRANSIENT),
    (TaskState.FAILED, "model substitution: requested 'opus', Claude Code reported 'claude-sonnet-5'", [], "",
     CRITICAL),
    (TaskState.FAILED, "runner rejected: changed paths the role may not write", [], "", CRITICAL),
    (TaskState.FAILED, "runner failed: something nobody anticipated", [], "", UNKNOWN),
    (TaskState.NEEDS_HUMAN, "recovery conflict: checkpoint is not on the branch", [], "", CRITICAL),
    (TaskState.READY, "", [{"kind": "runner-unavailable", "error": "subscription authentication: API key"}], "",
     CRITICAL),
    (TaskState.READY, "", [{"kind": "runner-unavailable", "error": "'claude' is not installed"}], "", TRANSIENT),
    (TaskState.READY, "", [{"kind": "runner-unavailable", "error": "usage limit reached"}], "", SUBSCRIPTION),
    (TaskState.READY, "", [{"kind": "runner-unavailable", "error": "provider down (weird)"}], "", UNKNOWN),
    (TaskState.READY, "", [{"kind": "run-cancelled", "error": "stop"}], "", "cancelled"),
    (TaskState.READY, "", [{"kind": "commit-deferred", "error": "unstable"}], "", TRANSIENT),
    (TaskState.READY, "", [], "error: sequential mode (worker): another agent's claim exists", "external"),
    (TaskState.READY, "", [], "", UNKNOWN),
    (TaskState.IN_PROGRESS, "", [], "", "interrupted"),
])
def test_outcome_classification(state, reason, events, log, category) -> None:
    after = task_with(state, reason)
    outcome = classify(after, after, events, exit_code=1, log_text=log)
    assert outcome.category == category, outcome
    if "1900003600" in reason:
        assert outcome.retry_at == 1_900_003_600.0  # the CLI's machine-readable reset time


def test_a_gate_that_could_not_run_is_an_environment_failure_with_its_cause(tmp_path: Path, repo: Path,
                                                                             state_dir: Path) -> None:
    """W4.2.1, from a real run: bubblewrap could not create namespaces (Ubuntu's AppArmor restriction
    of unprivileged user namespaces), so the gate never judged the code. It must be recorded with its
    cause and fail closed as CRITICAL — not as a validation failure, which would point the operator at
    correct code and, with auto-fix enabled, spend another AI session redoing it."""
    fake = tmp_path / "bin" / "bwrap"  # what bwrap reports under that restriction
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS,
                              gates=SandboxedGateRunner(default_config(), state_dir / "logs", bwrap=str(fake)))
    coord.activate(coord.plan(demo_spec()).id)
    before = coord.store.load_task("items-export.view")
    assert coord.run_task("items-export.view").state is TaskState.FAILED
    after = coord.store.load_task("items-export.view")
    reason = after.history[-1].reason
    assert reason.startswith("gates could not run: ['frontend-type-check', 'frontend-tests']"), reason
    assert "blocked: bwrap cannot create namespaces here: bwrap: setting up uid map: Permission denied" in reason
    outcome = classify(before, after, [], exit_code=0)
    assert outcome.category == CRITICAL, outcome
    rec = Scheduling(id=after.id, feature_id=after.feature_id)
    actions = apply_outcome(rec, task_with(TaskState.FAILED, reason, STEPS), outcome,
                            WorkerConfig(validation_auto_fix_enabled=True), T0, 1)
    assert rec.attention and rec.attention["category"] == CRITICAL and not actions["redo_step"]  # no AI redo


def test_backoff_and_category_budgets() -> None:
    config = WorkerConfig(transient_max_attempts=3, transient_initial_delay_seconds=60, transient_max_delay_seconds=200)
    assert [backoff_seconds(config, n) for n in (1, 2, 3, 4)] == [60, 120, 200, 200]
    rec, task = Scheduling(id="f.a", feature_id="f"), task_with(TaskState.FAILED, "x")
    for n, delay in ((1, 60), (2, 120), (3, 200)):
        actions = apply_outcome(rec, task, Outcome(TRANSIENT, "ECONNRESET"), config, T0, n)
        assert rec.next_attempt_at == T0 + delay and actions["ready"] and not rec.attention
    apply_outcome(rec, task, Outcome(TRANSIENT, "ECONNRESET"), config, T0, 4)
    assert rec.attention["category"] == TRANSIENT and rec.next_attempt_at is None  # budget enforced
    rec = Scheduling(id="f.a", feature_id="f")
    apply_outcome(rec, task, Outcome(SUBSCRIPTION, "limit", retry_at=T0 + 3600), config, T0, 1)
    assert rec.next_attempt_at == T0 + 3600 and rec.waiting["category"] == SUBSCRIPTION
    apply_outcome(rec, task, Outcome(SUBSCRIPTION, "limit", retry_at=T0 + 40 * 86400), config, T0, 2)
    assert rec.next_attempt_at == T0 + config.subscription_recheck_interval_seconds  # implausible: not trusted
    rec = Scheduling(id="f.a", feature_id="f")
    actions = apply_outcome(rec, task_with(TaskState.FAILED, "gates", STEPS), Outcome(VALIDATION, "gates failed"),
                            config, T0, 1)
    assert rec.attention and not actions["redo_step"]  # auto-fix is OFF by default
    for category in (CRITICAL, UNKNOWN):
        rec = Scheduling(id="f.a", feature_id="f")
        apply_outcome(rec, task, Outcome(category, "x"), config, T0, 1)
        assert rec.attention and rec.next_attempt_at is None  # fail closed


def test_worker_configuration_is_strict_and_safe_by_default(tmp_path: Path) -> None:
    worker = default_config().worker
    assert not worker.autonomous_enabled and worker.max_parallel == 1 and not worker.validation_auto_fix_enabled
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert config_from_mapping(data) == default_config()  # the documented example equals the built-in
    legacy = dict(data)
    del legacy["worker"]
    assert config_from_mapping(legacy).worker == WorkerConfig()  # W2/W3 configurations load unchanged
    for path, value, match in ((("worker", "surprise"), 1, "unknown worker key"),
                               (("worker", "recovery", "stop_on_conflict"), False, "fixed"),
                               (("worker", "max_parallel"), 0, "at least 1"),
                               (("worker", "retry", "enabled"), "yes", "true or false"),
                               (("worker", "retry", "transient", "max_attempts"), -1, "must not be negative")):
        bad = json.loads(json.dumps(data))
        node = bad
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
        with pytest.raises(ConfigError, match=match):
            config_from_mapping(bad)
    del tmp_path


def test_exclusive_flag_makes_claims_sequential(rig: Rig) -> None:
    feature = rig.activate(TaskSpec("api", "backend", "API"), TaskSpec("cmd", "cli", "Command"))
    head = git(rig.coord.root, "rev-parse", feature.branch).strip()
    claims = ClaimManager(rig.coord.store)
    first = claims.claim("w4.api", role="backend", branch=feature.branch, head_sha=head)
    second = claims.claim("w4.cmd", role="cli", branch=feature.branch, head_sha=head)  # W2: different roles in parallel
    claims.release(second, yield_task=True)
    hold_exclusive(rig.coord.store, "test worker")
    try:
        with pytest.raises(CheckoutBusyError, match="sequential mode"):
            claims.claim("w4.cmd", role="cli", branch=feature.branch, head_sha=head)
    finally:
        drop_exclusive(rig.coord.store)
    claims.release(first, yield_task=True)


# -- A, B: one agent at a time, dependencies first ------------------------------------------------------------

def test_a_b_one_agent_at_a_time_in_dependency_order(rig: Rig) -> None:
    rig.activate(API, CMD, VIEW, TaskSpec("docs", "coordination", "Docs"))
    service = rig.service(until=state_is("AVAILABLE"))
    assert service.run() == 0
    assert rig.active[1] == 1  # never two agents at once
    assert [k for k, _t in rig.log] == ["w4.api", "w4.cmd", "w4.docs", "w4.view"] or rig.log[0][0] == "w4.api"
    events = rig.events()
    verified = next(i for i, e in enumerate(events) if e["kind"] == "task-finished" and e["task"] == "w4.api")
    claims = {e["task"]: i for i, e in enumerate(events) if e["kind"] == "claim"}
    assert verified < claims["w4.cmd"] and verified < claims["w4.view"]  # B: Backend verified first
    releases = [i for i, e in enumerate(events) if e["kind"] == "release"]
    starts = sorted(claims.values())
    assert all(r < s for r, s in zip(releases, starts[1:]))  # each claim only after the previous release
    assert all(rig.task(t).state is TaskState.VERIFIED for t in ("w4.api", "w4.cmd", "w4.view", "w4.docs"))
    assert service.record.state == "OFFLINE"  # recorded at shutdown
    assert any(e["state"] == "AVAILABLE" and "ready for integration" in e["reason"] for e in rig.events("worker-state"))


def test_a_manual_launch_cannot_run_beside_the_service(rig: Rig) -> None:
    feature = rig.activate(TaskSpec("api", "backend", "API"), TaskSpec("cmd", "cli", "Command"))
    refused: list[str] = []

    def manual_attempt(request):
        head = git(rig.coord.root, "rev-parse", feature.branch).strip()
        try:
            ClaimManager(rig.coord.store).claim("w4.cmd", role="cli", branch=feature.branch, head_sha=head)
        except CheckoutBusyError as exc:
            refused.append(str(exc))
        return rig.coord.runners["backend"].runner.run(request)
    rig.coord.runners["backend"].plan["w4.api"] = [manual_attempt]
    assert rig.service(until=state_is("AVAILABLE")).run() == 0
    assert refused and "sequential mode" in refused[0] and rig.active[1] == 1


# -- D: transient failures ----------------------------------------------------------------------------------------

def test_d_transient_failure_is_retried_with_bounded_backoff(repo: Path, state_dir: Path) -> None:
    rig = Rig(repo, state_dir, plan={"backend": {"w4.api": [fail("read ECONNRESET"), fail("socket hang up")]}})
    rig.activate(API)
    assert rig.service(until=state_is("AVAILABLE")).run() == 0
    times = [t for k, t in rig.log if k == "w4.api"]
    assert len(times) == 3 and times[1] - times[0] >= 60 and times[2] - times[1] >= 120  # backoff 60 s, 120 s
    rec = rig.sched("w4.api")
    assert rec.attempts == {TRANSIENT: 2} and rec.launches == 3 and rig.task("w4.api").state is TaskState.VERIFIED
    assert [h["category"] for h in rec.history] == [TRANSIENT, TRANSIENT, "success"]
    assert len(rig.events("worker-retry-scheduled")) == 2


def test_d_transient_retries_are_bounded(repo: Path, state_dir: Path) -> None:
    rig = Rig(repo, state_dir, plan={"backend": {"w4.api": [fail("ECONNRESET")] * 10}})
    rig.activate(API)
    rig.service(until=state_is("NEEDS_ATTENTION")).run()
    rig.clock.now += 10 * 86400
    rig.service(max_cycles=5).run()  # much later, a restarted service: still nothing
    assert len(rig.log) == 4  # the first launch + 3 retries (max_attempts), never more
    assert rig.sched("w4.api").attention["category"] == TRANSIENT


# -- E: subscription exhausted ------------------------------------------------------------------------------------

def test_e_exhausted_subscription_waits_and_resumes_from_the_checkpoint(repo: Path, state_dir: Path) -> None:
    reset = T0 + 7200
    rig = Rig(repo, state_dir, config=long_waits(),
              plan={"backend": {"w4.api#service": [fail(f"Claude AI usage limit reached|{int(reset)}")]}})
    rig.activate(TaskSpec("api", "backend", "API", steps=STEPS))
    service = rig.service(until=state_is("WAITING"))
    service.run()
    rec = rig.sched("w4.api")
    assert rec.waiting["category"] == SUBSCRIPTION and rec.next_attempt_at == reset
    assert rig.task("w4.api").state is TaskState.READY and last_state(rig)[1].startswith("next attempt")
    assert [c.status for c in load_record(rig.coord.store, "w4.api").checkpoints] == ["committed"]  # #1 survives
    rig.service(until=state_is("AVAILABLE")).run()
    keys = [k for k, _t in rig.log]
    assert keys == ["w4.api#models", "w4.api#service", "w4.api#service", "w4.api#endpoints"]  # models not repeated
    assert [t for k, t in rig.log][2] >= reset  # not before the CLI's reported reset time
    started = rig.events("worker-agent-started")  # never another provider or model
    assert {(e["agent"], e["model"]) for e in started} == {("claude", rig.config.model_for("backend"))}
    assert rig.task("w4.api").state is TaskState.VERIFIED


def test_e_subscription_rechecks_are_conservative_and_bounded(repo: Path, state_dir: Path) -> None:
    config = long_waits(subscription_recheck_interval_seconds=900, subscription_max_rechecks=2)
    rig = Rig(repo, state_dir, config=config, plan={"backend": {"w4.api": [fail("usage limit reached")] * 9}})
    rig.activate(API)
    rig.service(until=state_is("NEEDS_ATTENTION"), max_cycles=40).run()
    times = [t for _k, t in rig.log]
    assert len(times) == 3 and times[1] - times[0] >= 900 and times[2] - times[1] >= 900  # no reset time → 900 s
    assert rig.sched("w4.api").attention["category"] == SUBSCRIPTION


# -- F: validation --------------------------------------------------------------------------------------------------

def test_f_validation_failure_needs_an_operator_by_default(repo: Path, state_dir: Path) -> None:
    rig = Rig(repo, state_dir, failing_gates=["backend-tests"])
    rig.activate(TaskSpec("api", "backend", "API", required_gates=("backend-tests",)))
    rig.service(until=state_is("NEEDS_ATTENTION")).run()
    rig.clock.now += 86400
    rig.service(max_cycles=5).run()
    assert len(rig.log) == 1 and rig.task("w4.api").state is TaskState.FAILED  # never VERIFIED, never retried
    assert rig.sched("w4.api").attention["category"] == VALIDATION


def test_f_validation_auto_fix_is_bounded(repo: Path, state_dir: Path) -> None:
    config = autonomous(validation_auto_fix_enabled=True, validation_max_attempts=2)
    rig = Rig(repo, state_dir, config=config, failing_gates=["backend-tests"])
    rig.activate(TaskSpec("api", "backend", "API", steps=STEPS, required_gates=("backend-tests",)))
    rig.service(until=state_is("NEEDS_ATTENTION"), max_cycles=40).run()
    keys = [k for k, _t in rig.log]
    assert keys.count("w4.api#endpoints") == 3 and keys.count("w4.api#models") == 1  # the last step redone twice
    assert rig.sched("w4.api").attempts[VALIDATION] == 3 and rig.task("w4.api").state is TaskState.FAILED


# -- G: unexpected branch movement -------------------------------------------------------------------------------------

def test_g_unexpected_branch_movement_needs_attention(rig: Rig) -> None:
    feature = rig.activate(TaskSpec("api", "backend", "API", steps=STEPS))
    base = feature.base_sha

    def rewind(request):
        git(rig.coord.root, "update-ref", f"refs/heads/{feature.branch}", base)  # someone rewinds the branch
        return rig.coord.runners["backend"].runner.run(request)
    rig.coord.runners["backend"].plan["w4.api#service"] = [rewind]
    rig.service(until=state_is("NEEDS_ATTENTION")).run()
    rec = rig.sched("w4.api")
    assert rec.attention["category"] == CRITICAL and git(rig.coord.root, "rev-parse", feature.branch).strip() == base
    rig.clock.now += 86400
    rig.service(max_cycles=4).run()
    assert [k for k, _t in rig.log] == ["w4.api#models", "w4.api#service"]  # nothing after the conflict


# -- J: pause and resume ----------------------------------------------------------------------------------------------

def test_j_pause_stops_new_work_and_resume_continues(rig: Rig) -> None:
    rig.activate(TaskSpec("api", "backend", "API"), TaskSpec("cmd", "cli", "Command"))

    def pause_during(request):
        write_control(rig.coord.store, pause=True)
        return rig.coord.runners["backend"].runner.run(request)
    rig.coord.runners["backend"].plan["w4.api"] = [pause_during]
    paused_cycles = []

    def until(service):
        if service.record.state == "PAUSED":
            paused_cycles.append(service.cycles)
            if len(paused_cycles) == 5:
                assert [k for k, _t in rig.log] == ["w4.api"]  # the active agent finished; nothing new started
                write_control(rig.coord.store, pause=False)
        return service.record.state == "AVAILABLE" and service.cycles > 1
    rig.service(until=until).run()
    assert len(paused_cycles) == 5 and [k for k, _t in rig.log] == ["w4.api", "w4.cmd"]
    assert rig.task("w4.cmd").state is TaskState.VERIFIED


# -- L: retry state survives a restart (in-process; the process variant is in test_fc_service_process.py) --------------

def test_l_retry_schedule_survives_a_restart(repo: Path, state_dir: Path) -> None:
    rig = Rig(repo, state_dir, plan={"backend": {"w4.api": [fail("ECONNRESET")]}})
    rig.activate(API)
    rig.service(until=state_is("WAITING")).run()
    before = rig.sched("w4.api")
    rig.clock.now = before.next_attempt_at - 10
    rig.service(max_cycles=1).run()  # a NEW service instance, just before the retry is due
    after = rig.sched("w4.api")
    assert (after.attempts, after.next_attempt_at, after.launches) == (before.attempts, before.next_attempt_at, 1)
    assert len(rig.log) == 1  # not launched early
    rig.service(until=state_is("AVAILABLE")).run()
    assert len(rig.log) == 2 and rig.sched("w4.api").launches == 2


# -- M: unknown errors -------------------------------------------------------------------------------------------------

def test_m_unknown_error_is_not_retried(repo: Path, state_dir: Path) -> None:
    rig = Rig(repo, state_dir, plan={"backend": {"w4.api": [fail("the agent printed something nobody expected")]}})
    rig.activate(API)
    rig.service(until=state_is("NEEDS_ATTENTION")).run()
    rig.clock.now += 30 * 86400
    rig.service(max_cycles=5).run()
    assert len(rig.log) == 1 and rig.sched("w4.api").attention["category"] == UNKNOWN


# -- N: explicit parallel mode -----------------------------------------------------------------------------------------

def test_n_explicit_parallel_mode_still_works(repo: Path, state_dir: Path) -> None:
    config = dataclasses.replace(default_config(), worker=dataclasses.replace(default_config().worker, max_parallel=3))
    rig = Rig(repo, state_dir, config=config)
    barrier = threading.Barrier(2)

    def meet(request):
        barrier.wait(timeout=20)  # only passes when CLI and Frontend run at the same time
        return rig.coord.runners[request.role].runner.run(request)
    rig.coord.runners["cli"].plan["w4.cmd"] = [meet]
    rig.coord.runners["frontend"].plan["w4.view"] = [meet]
    feature = rig.activate(API, CMD, VIEW)
    report = FeatureWorker(rig.coord, rig.spawn, max_parallel=config.worker.max_parallel, poll_interval=0.02).run(
        feature.id)
    assert report.all_verified and report.max_concurrency == 2 and rig.active[1] == 2
    refused = autonomous(max_parallel=3)
    assert WorkerService(refused, repo, state_dir, coord=rig.coord, spawn=rig.spawn, echo=lambda _l: None).run() \
        == EXIT_DO_NOT_RESTART  # the W4 service itself stays sequential


# -- O: idle -----------------------------------------------------------------------------------------------------------

def test_o_idle_service_starts_nothing_unapproved(rig: Rig) -> None:
    planned = rig.coord.plan(FeatureSpec("w4", "W4", (API,)))  # planned, never activated: not approved for work
    service = rig.service(max_cycles=6)
    assert service.run() == 0
    assert not rig.log and rig.coord.store.load_feature(planned.id).state.value == "planned"
    assert rig.clock.sleeps == [rig.config.worker.idle_poll_seconds] * 6  # one bounded sleep per cycle, no busy loop
    assert "no active feature" in last_state(rig)[1]


def test_disabled_autonomous_mode_refuses_to_start(rig: Rig) -> None:
    rig.config = default_config()  # autonomous.enabled is false by default
    assert rig.service(max_cycles=1).run() == EXIT_DO_NOT_RESTART
    assert not rig.log


# -- one task's broken state never blocks the others; a failing cycle is not a crash -----------------------------------

def test_unreadable_task_state_holds_only_that_task(rig: Rig) -> None:
    rig.activate(TaskSpec("api", "backend", "API", steps=STEPS), TaskSpec("cmd", "cli", "Command"),
                 TaskSpec("view", "frontend", "View"))
    root = rig.coord.store.root
    (root / "scheduling").mkdir(exist_ok=True)
    (root / "scheduling" / "w4.cmd.json").write_text("{torn", encoding="utf-8")
    (root / "checkpoints").mkdir(exist_ok=True)
    (root / "checkpoints" / "w4.api.json").write_text("[not a record", encoding="utf-8")
    rig.service(until=state_is("NEEDS_ATTENTION")).run()
    assert rig.task("w4.view").state is TaskState.VERIFIED  # the unrelated task ran
    assert [k for k, _t in rig.log] == ["w4.view"]
    attention = rig.sched("w4.api").attention
    assert attention["category"] == CRITICAL and "unreadable" in attention["reason"]  # W3 reports it as a conflict
    assert (root / "scheduling" / "w4.cmd.json").read_text(encoding="utf-8") == "{torn"  # kept as evidence
    report = health_report(rig.coord.store, rig.config, rig.clock.now)
    assert {t["task"] for t in report["attention"]} == {"w4.api", "w4.cmd"}


def test_a_failing_cycle_is_recorded_and_retried(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    from feature_coordinator import service as service_module  # pylint: disable=import-outside-toplevel
    rig.activate(API)
    real, calls = service_module.workspace_problems, []

    def flaky(root, feature):
        calls.append(1)
        if len(calls) <= 2:
            raise OSError("disk briefly unavailable")
        return real(root, feature)
    monkeypatch.setattr(service_module, "workspace_problems", flaky)
    rig.service(until=state_is("AVAILABLE")).run()
    failed = rig.events("worker-cycle-failed")
    assert len(failed) == 1 and "disk briefly unavailable" in failed[0]["error"]  # one event, not one per cycle
    assert [e["state"] for e in rig.events("worker-state")][:3] == ["RECOVERING", "NEEDS_ATTENTION", "BUSY"]
    assert rig.task("w4.api").state is TaskState.VERIFIED and rig.clock.sleeps[:2] == [30, 30]


# -- health, operator commands -----------------------------------------------------------------------------------------

def test_health_and_status_commands_are_read_only(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    rig.coord.runners["backend"].plan["w4.api"] = [fail("mystery")]
    rig.activate(API)
    rig.service(until=state_is("NEEDS_ATTENTION")).run()
    report = health_report(rig.coord.store, rig.config, time.time())
    assert report["state"] == "OFFLINE" and not report["healthy"] and report["attention"][0]["task"] == "w4.api"
    assert report["last_error"]["category"] == UNKNOWN and report["tasks"][0]["status"] == "needs-attention"
    snapshot = {p: p.read_bytes() for p in rig.coord.store.root.rglob("*.json")}
    base = ["--repo", str(rig.coord.root), "--state-dir", str(rig.coord.store.root)]
    assert cli_main([*base, "worker", "status"]) == 0
    assert cli_main([*base, "worker", "health"]) == 1 and cli_main([*base, "worker", "attention"]) == 1
    assert cli_main([*base, "worker", "waiting"]) == 0
    out = capsys.readouterr().out
    assert "needs-attention" in out and "worker reset-retries" in out
    assert {p: p.read_bytes() for p in rig.coord.store.root.rglob("*.json")} == snapshot


def test_reset_retries_is_an_explicit_operator_decision(rig: Rig) -> None:
    rig.coord.runners["backend"].plan["w4.api"] = [fail("mystery")]
    rig.activate(API)
    rig.service(until=state_is("NEEDS_ATTENTION")).run()
    base = ["--repo", str(rig.coord.root), "--state-dir", str(rig.coord.store.root)]
    assert cli_main([*base, "worker", "reset-retries", "w4.api"]) == 0 and rig.sched("w4.api").attention  # dry run
    assert cli_main([*base, "worker", "reset-retries", "w4.api", "--apply"]) == 0
    rec = rig.sched("w4.api")
    assert rec.attention is None and rec.launches == 0 and rig.task("w4.api").state is TaskState.READY
    rig.service(until=state_is("AVAILABLE")).run()
    assert rig.task("w4.api").state is TaskState.VERIFIED and rig.sched("w4.api").history[-1]["category"] == "success"


def test_secrets_never_reach_records_events_or_logs(repo: Path, state_dir: Path) -> None:
    secret = "sk-ant-api03-" + "Q" * 40
    rig = Rig(repo, state_dir, plan={"backend": {"w4.api": [fail(f"Invalid API key {secret}; "
                                                                 f"ANTHROPIC_API_KEY={secret}")]}})
    rig.activate(API)
    rig.service(until=state_is("NEEDS_ATTENTION")).run()
    rec = rig.sched("w4.api")
    assert rec.attention["category"] == CRITICAL and "[redacted]" in rec.attention["reason"]
    stored = [p.read_text(encoding="utf-8") for p in rig.coord.store.root.rglob("*") if p.is_file()]
    assert len(stored) > 5 and rig.out and not [t for t in stored + rig.out if "QQQQQQQQ" in t]
    assert "Invalid API key [redacted]" in rig.task("w4.api").history[-1].reason  # the task record too
    bearer_value = "test-" + "x" * 24  # build a synthetic token at runtime, not a hardcoded credential
    assert scrub(f"Authorization: Bearer {bearer_value}") == "Authorization: [redacted]"
    assert scrub("gates failed: ['backend-tests']; usage limit reached|1900003600") == \
        "gates failed: ['backend-tests']; usage limit reached|1900003600"  # evidence stays readable


# -- accelerated long run ----------------------------------------------------------------------------------------------

def invariants(rig: Rig) -> None:
    store = rig.coord.store
    assert len(store.list_claims()) <= 1
    assert rig.active[1] <= 1
    shas, ids = [], []
    for task_id in ("w4.api", "w4.cmd", "w4.view", "w4.docs"):
        record = load_record(store, task_id)
        if record:
            shas += [c.commit for c in record.checkpoints if c.commit]
            ids += [c.id for c in record.checkpoints]
        rec = rig.sched(task_id)
        assert not (rec.attention and rec.next_attempt_at) and rec.launches <= rig.config.worker.max_launches_per_task
    assert len(shas) == len(set(shas)) and len(ids) == len(set(ids))  # no duplicate checkpoint or commit


def test_long_run_accelerated(repo: Path, state_dir: Path) -> None:  # pylint: disable=too-many-locals
    """Several tasks, transient failures, an exhausted subscription, idle intervals, a pause, a
    restart and one task needing attention — the service returns to a consistent, observable
    state after every cycle. Simulated time; this does NOT prove real-world 24/7 reliability."""
    rig = Rig(repo, state_dir, failing_gates=["frontend-type-check"], plan={
        "backend": {"w4.api#service": [fail("ECONNRESET")]},
        "cli": {"w4.cmd": [fail("You've hit your usage limit")]}})
    rig.activate(TaskSpec("api", "backend", "API", steps=STEPS), CMD,
                 TaskSpec("view", "frontend", "View", depends_on=("api",), required_gates=("frontend-type-check",)),
                 TaskSpec("docs", "coordination", "Docs", depends_on=("cmd",)))
    seen_states: list[str] = []

    def until(service) -> bool:
        invariants(rig)
        seen_states.append(service.record.state)
        if service.cycles == 3:
            write_control(rig.coord.store, pause=True)
        if service.cycles == 6:
            write_control(rig.coord.store, pause=False)
        return service.cycles >= 12  # then "restart"
    rig.service(until=until, max_cycles=100).run()
    report = health_report(rig.coord.store, rig.config, rig.clock.now)
    assert report["state"] == "OFFLINE"  # stopped between cycles; its last state is recorded
    rig.service(until=lambda s: (invariants(rig) or True) and s.record.state in ("NEEDS_ATTENTION", "AVAILABLE")
                and s.cycles > 1, max_cycles=200).run()
    states = {t: rig.task(t).state for t in ("w4.api", "w4.cmd", "w4.view", "w4.docs")}
    assert states == {"w4.api": TaskState.VERIFIED, "w4.cmd": TaskState.VERIFIED, "w4.view": TaskState.FAILED,
                      "w4.docs": TaskState.VERIFIED}
    assert rig.sched("w4.view").attention["category"] == VALIDATION  # the one task needing a human
    assert {"PAUSED", "WAITING", "BUSY"} <= set(seen_states)
    keys = [k for k, _t in rig.log]
    assert keys.count("w4.api#models") == 1 and keys.count("w4.api#service") == 2 and keys.count("w4.cmd") == 2
    assert rig.active[1] == 1
    invariants(rig)
