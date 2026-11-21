"""W4.7 (M6): the Worker schedules integration jobs (integration_worker.py, integration_schedule.py).

What these tests pin down: scheduling is off by default and can never name the merge; the Worker drives an approved
job through the EXISTING stages and stops at the operator's merge decision; one stage at a time; Feature Jobs first;
a job that needs an operator is not started; a stage that changes nothing is delayed, then held; a stage process is
watched by its tag, stopped at its deadline, and survives or dies with the service without ever being started twice;
unreadable records stop scheduling (fail closed); an operator's command owns its job while it runs.

Stages run through the M2–M5 rig (fc_integration_worker_rig.Stages): the real inspector, validator, reviewer and
merger over disposable repositories, with fake gates, a fake reviewer process and a scripted GitHub. No network, no
real GitHub, no model, no real merge."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines
# pylint: disable=unused-import

from __future__ import annotations

import dataclasses
import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator import integration_recovery, integration_recovery_commands, integration_schedule, \
    integration_slot, integration_worker, review_process
from feature_coordinator.claims import ClaimManager
from feature_coordinator.cli import main as cli_main
from feature_coordinator.demo import build_coordinator
from feature_coordinator.errors import CheckoutBusyError, ConfigError, CoordinatorError, StateDirError, \
    WritersActiveError
from feature_coordinator.integration import JobState
from feature_coordinator.integration_config import DEFAULT_WORKER_STAGES, IntegrationWorkerConfig, \
    integration_from_mapping, worker_from_mapping
from feature_coordinator.integration_recovery_commands import _resume
from feature_coordinator.integration_schedule import ACTOR, OPERATOR as BY_OPERATOR, RECORD_KIND, STAGES, WORKER, \
    Schedule, assigned, next_step, stage_argv
from feature_coordinator.integration_slot import confirm_launch, execution_owner, list_schedules, read_schedule
from feature_coordinator.integration_worker import IntegrationStep, IntegrationStepReport, integration_state, \
    integration_steps, schedule_view, stop_stages
from feature_coordinator.workflow import FeatureSpec, TaskSpec
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.review_worker import github_steps

from fc_integration_merge_rig import approve_merge, github_for
from fc_integration_rig import (
    BRANCH, REPOSITORY, OperatorKey, Rig, change_on, github_origin, head, origin, request_for, snapshot,
)
from fc_integration_worker_rig import (
    ALL_STAGES, Clock, Stages, coord_of, events_of, hold_job_lock, poll, run_until_quiet, schedule, scheduling, step,
    wait_for,
)
from test_fc_integration_review import X_PY, launchers, rrig, validated_job
from test_fc_integration_validate import FakeGates, inspected, key, rig, seeded, tracker


@pytest.fixture
def stages(rrig: Rig, launchers: Any):
    made: list[Stages] = []

    def make(**options: Any) -> Stages:
        options.setdefault("gates", FakeGates())
        options.setdefault("launcher", launchers("pass"))
        made.append(Stages(rrig, **options))
        return made[-1]
    yield make
    for item in made:
        item.cleanup()


def approved_job(rig: Rig, branch: str = BRANCH, edits: dict[str, str | None] | None = None) -> str:
    """A job in AWAITING_APPROVAL with a recorded, valid inspect approval: everything after it is automatable."""
    sha = change_on(rig.repo, branch, edits or {"backend/src/x.py": X_PY})
    return rig.approved(request_for(rig.repo, sha, source_ref=branch)).job_id


def running_entry(**changes: Any) -> dict[str, Any]:
    """A well-formed `running` entry of a schedule record (for int-20261121-0001)."""
    return {"stage": "validate", "launch": 1, "tag": "istage-0123456789abcdef", "pid": None, "process": None,
            "launcher": {"pid": 1, "start_ticks": 1, "boot_id": "no-such-boot"},
            "started_at": "2026-11-21T00:00:00Z", "started_epoch": 1.0, "deadline": "2026-11-21T01:00:00Z",
            "deadline_epoch": 2.0, "instance": "", "log": "integration-int-20261121-0001.validate.1-abcdef.log",
            "basis": {"state": "INSPECTING", "revision": 3}, "watchdog": None, "reviewer": None, **changes}


def closed_entry(**changes: Any) -> dict[str, Any]:
    """A well-formed closed launch (`last` / `history`) of a schedule record."""
    return {"stage": "validate", "launch": 1, "started_at": "2026-11-21T00:00:00Z", "ended_at": "2026-11-21T00:10:00Z",
            "cause": "exited", "exit_code": 0, "outcome": "validated", "detail": "", "progressed": True,
            "state_before": "INSPECTING", "state_after": "VALIDATING", **changes}


def view_of(rig: Rig, job: str, clock: Clock | None = None) -> dict[str, Any]:
    rows = [row for row in schedule_view(rig.store, rig.config, (clock or Clock())()) if row["job"] == job]
    assert len(rows) == 1, rows
    return rows[0]


# -- off by default; the merge cannot be scheduled -----------------------------------------------------------------

def test_scheduling_is_off_by_default_and_then_nothing_is_read_or_started(rrig: Rig, stages: Any):
    job = approved_job(rrig)
    cfg = rrig.config.github.integration.worker
    assert cfg.enabled is False and cfg.stages == DEFAULT_WORKER_STAGES == ("validate", "review")
    runner = stages()
    assert not integration_steps(coord_of(rrig), runner)                  # the service never constructs the step
    assert [name for name, _cfg, _make in github_steps(rrig.config, coord_of(rrig), {}, None)] == \
        ["publish", "review", "intake"]                                    # the existing steps, unchanged
    report = IntegrationStep(coord_of(rrig), start=runner).poll()
    assert not report.lines() and report.service_state() is None and not runner.calls
    assert not (rrig.state / RECORD_KIND).exists() and not events_of(rrig, "integration-stage-starting")
    stop_stages(coord_of(rrig), lambda _line: None)                        # nothing to stop, nothing raised
    row = view_of(rrig, job)                                               # the view still says what an operator does
    assert row["who"] == BY_OPERATOR and "scheduling is off" in row["reason"] and "integration inspect" in row["action"]


@pytest.mark.parametrize("stage", ["merge", "approve", "intake", "abort", "review-exec", "status", ""])
def test_the_merge_and_every_operator_command_cannot_be_configured_or_built_as_a_stage(stage: str):
    with pytest.raises(ConfigError, match="stages"):
        worker_from_mapping({"enabled": True, "stages": ["validate", stage]})
    with pytest.raises(CoordinatorError, match="not a stage the Worker schedules"):
        stage_argv(stage, "int-20261121-0001")
    record = Schedule(id="int-20261121-0001").to_dict()
    with pytest.raises(StateDirError):                                     # a tampered record cannot name it either
        Schedule.from_dict({**record, "launches": {stage: 1}})
    good = running_entry()
    assert Schedule.from_dict({**record, "running": good}).running["stage"] == "validate"
    for bad in ({"stage": stage}, {"tag": "review-0123456789abcdef"}, {"tag": ""}, {"log": "../../etc/passwd"},
                {"log": "/tmp/x.log"}, {"log": f"integration-int-20261121-0001.{stage or 'merge'}.1-abcdef.log"}):
        with pytest.raises(StateDirError):                                 # nor a process tag or a file of another kind
            Schedule.from_dict({**record, "running": {**good, **bad}})


def test_the_stage_commands_are_exactly_the_existing_commands():
    assert STAGES == ("inspect", "validate", "review", "recover")
    for stage in STAGES:
        assert stage_argv(stage, "int-20261121-0001") == ["integration", stage, "--job", "int-20261121-0001",
                                                          "--apply", "--actor", ACTOR, "--json"]
    # the scheduling and recovery code holds no GitHub client, binds no trust anchors and has no path to the merge run
    for module in (integration_worker, integration_schedule, integration_recovery, integration_recovery_commands):
        source = inspect.getsource(module)
        for forbidden in ("_bind_integration", "IntegrationAnchors", "merge_integration_pull_request",
                          "create_integration_pull_request", "cmd_merge", "--retry\""):
            assert forbidden not in source, (module.__name__, forbidden)
    assert ".run(" not in inspect.getsource(integration_recovery_commands)   # recover calls settle(), never run()


def test_the_worker_configuration_is_strict():
    assert worker_from_mapping(None) == IntegrationWorkerConfig()
    cfg = worker_from_mapping({"enabled": True, "stages": ["inspect", "validate", "review", "recover"],
                               "stage_timeouts": {"validate": 60}})
    assert cfg.stages == ALL_STAGES and cfg.timeout("validate") == 60 and cfg.timeout("review") == 7200
    for bad in ({"enabled": "yes"}, {"stages": []}, {"stages": "validate"}, {"stages": ["validate", "validate"]},
                {"surprise": 1}, {"poll_interval_seconds": 0}, {"max_stalls": 0}, {"max_launches": True},
                {"retry_seconds": 600, "max_retry_seconds": 60}, {"stage_timeouts": {"merge": 60}},
                {"stage_timeouts": {"validate": 0}}, {"stage_timeouts": [1]}, ["validate"]):
        with pytest.raises(ConfigError):
            worker_from_mapping(bad)
    with pytest.raises(ConfigError, match="worker.enabled needs"):         # never on while integration jobs are off
        integration_from_mapping({"enabled": False, "worker": {"enabled": True}})
    loaded = integration_from_mapping({"enabled": True, "known_authors": ["a@example.invalid"],
                                       "worker": {"enabled": True}})
    assert loaded.worker.enabled and loaded.worker.stages == ("validate", "review")


# -- the whole automated part, and where it stops ----------------------------------------------------------------

def test_the_worker_drives_an_approved_job_to_the_merge_approval_and_never_merges(rrig: Rig, stages: Any,
                                                                                   github_origin: Path):
    fake = github_for(rrig, github_origin)             # the scripted GitHub serves every read; it records every write
    scheduling(rrig)
    job = approved_job(rrig)
    before = snapshot(rrig.repo, github_origin)
    runner, clock = stages(fake=fake), Clock()
    reports = run_until_quiet(rrig, runner, clock)
    assert runner.stages == ["inspect", "validate", "review"]             # each existing stage once, in order
    stored = rrig.jobs.load(job)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL
    assert [h["to"] for h in stored.history] == ["INTAKE", "AWAITING_APPROVAL", "INSPECTING", "CANDIDATE_BUILT",
                                                 "VALIDATING", "IN_REVIEW", "AWAITING_MERGE_APPROVAL"]
    assert {h["actor"] for h in stored.history[2:]} == {ACTOR}            # the audit trail names the Worker
    assert not fake.writes() and snapshot(rrig.repo, github_origin) == before
    # it stops there: the merge is the operator's — WAITING, not attention, and no further stage however often it polls
    row = view_of(rrig, job, clock)
    assert (row["who"], row["phase"], row["urgent"], row["stage"]) == (BY_OPERATOR, "merge-approval", False, "")
    assert "the Worker never merges" in row["action"]
    state = reports[-1].service_state()
    assert state is not None and state[0] == "WAITING" and "merge" in state[1]
    # … also once a VALID SIGNED merge approval is on record: the Worker still starts nothing and writes nothing
    assert approve_merge(rrig, job)["outcome"] == "recorded"
    clock.advance(10 * 86400)
    for _ in range(5):
        assert not poll(rrig, runner, clock).started
    assert runner.stages == ["inspect", "validate", "review"] and not fake.writes()
    assert rrig.state_of(job) is JobState.AWAITING_MERGE_APPROVAL
    assert view_of(rrig, job, clock)["reason"].startswith("a signed merge approval is recorded")
    launched = schedule(rrig, job)
    assert launched.launches == {"inspect": 1, "validate": 1, "review": 1} and launched.running is None \
        and launched.stalls == 0 and launched.hold is None
    assert [e["stage"] for e in events_of(rrig, "integration-stage-ended", job)] == ["inspect", "validate", "review"]
    assert all(e["progressed"] for e in events_of(rrig, "integration-stage-ended", job))


def test_by_default_the_worker_validates_and_reviews_but_does_not_inspect(rrig: Rig, stages: Any):
    scheduling(rrig, stages=DEFAULT_WORKER_STAGES)
    job = approved_job(rrig)
    runner, clock = stages(), Clock()
    assert not run_until_quiet(rrig, runner, clock)[-1].started and not runner.calls
    row = view_of(rrig, job, clock)
    assert row["who"] == BY_OPERATOR and "`inspect` is not in github.integration.worker.stages" in row["reason"]
    assert rrig.inspect(job).outcome == "passed"                          # the operator inspects …
    run_until_quiet(rrig, runner, clock)                                  # … and the Worker takes it from there
    assert runner.stages == ["validate", "review"] and rrig.state_of(job) is JobState.AWAITING_MERGE_APPROVAL


def test_a_job_without_a_signed_approval_is_never_started(rrig: Rig, stages: Any):
    scheduling(rrig)
    sha = change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY})
    job = rrig.accepted(request_for(rrig.repo, sha)).job_id               # AWAITING_APPROVAL, nothing signed
    runner, clock = stages(), Clock()
    report = run_until_quiet(rrig, runner, clock)[-1]
    assert not runner.calls and rrig.state_of(job) is JobState.AWAITING_APPROVAL
    row = view_of(rrig, job, clock)
    assert row["who"] == BY_OPERATOR and not row["urgent"] and "signed inspect approval" in row["reason"]
    assert report.service_state()[0] == "WAITING"


# -- one execution slot ------------------------------------------------------------------------------------------

def test_one_stage_process_at_a_time_across_jobs_oldest_first(rrig: Rig, stages: Any):
    scheduling(rrig)
    first, second = approved_job(rrig), approved_job(rrig, "integration/other", {"cli/src/kiddo_cli/y.py": X_PY})
    runner, clock = stages(hold=("inspect",)), Clock()
    report = poll(rrig, runner, clock)
    assert report.started == [first] and report.service_state()[0] == "BUSY" and len(runner.alive()) == 1
    for _ in range(3):                                                    # while it runs nothing else starts
        again = poll(rrig, runner, clock)
        assert not again.started and again.busy and runner.started() == ["inspect"] and len(runner.alive()) == 1
    assert view_of(rrig, first, clock)["category"] == "running" and view_of(rrig, second, clock)["category"] == "ready"
    runner.release(first)
    settled = poll(rrig, runner, clock)                                   # settles the first and starts the next stage
    assert any(f"{first}: `inspect` ended" in line for line in settled.events) and len(settled.started) == 1
    assert len(runner.alive()) <= 1
    runner.hold.clear()
    for job in list(runner.pending):
        runner.release(job)
    run_until_quiet(rrig, runner, clock)
    assert rrig.state_of(first) is rrig.state_of(second) is JobState.AWAITING_MERGE_APPROVAL
    counts = [sum(1 for e in rrig.store.events()[:i + 1] if e["kind"] == "integration-stage-starting")
              - sum(1 for e in rrig.store.events()[:i + 1] if e["kind"] == "integration-stage-ended")
              for i in range(len(rrig.store.events()))]
    assert max(counts) == 1 and min(counts) == 0                          # never two stages open at once


def test_a_stage_waits_while_an_agent_holds_a_claim(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    with rrig.store.transaction():
        rrig.store.save_claim("f.task", {"task_id": "f.task", "role": "backend", "identity": None})
    runner, clock = stages(), Clock()
    report = poll(rrig, runner, clock)
    assert not report.started and not runner.calls and not report.lines()   # waiting is not logged every step
    assert view_of(rrig, job, clock)["category"] == "ready"
    state = report.service_state()
    assert state == ("WAITING", f"integration job {job} (AWAITING_APPROVAL): `inspect` starts when the checkout is "
                                "idle")
    assert integration_worker.feature_work_pending(rrig.store, rrig.config, clock()) == \
        "an agent holds a claim on the checkout"
    with rrig.store.transaction():
        rrig.store.delete_claim("f.task")
    assert poll(rrig, runner, clock).started == [job]


# -- jobs that need an operator ------------------------------------------------------------------------------------

def test_a_job_with_attention_is_the_operators_and_is_not_started(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(gates=FakeGates({"backend-tests": "blocked"})), Clock()
    reports = run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(job)
    assert stored.state is JobState.VALIDATING and stored.attention                 # BLOCKED: an environment problem
    assert runner.stages == ["inspect", "validate"]
    for _ in range(4):
        clock.advance(86400)
        assert not poll(rrig, runner, clock).started
    assert runner.stages == ["inspect", "validate"]                                # never repeated by itself
    row = view_of(rrig, job, clock)
    assert row["who"] == BY_OPERATOR and row["urgent"] and "integration validate" in row["action"]
    kind, text = reports[-1].service_state()
    assert kind == "NEEDS_ATTENTION" and job in text
    # the operator fixes the environment and validates: the job moves on, and the Worker continues with the review
    assert rrig.validate(job, gates=FakeGates()).outcome == "validated"
    run_until_quiet(rrig, runner, clock)
    assert runner.stages == ["inspect", "validate", "review"]


def test_a_failed_gate_or_requested_changes_is_never_retried_or_repaired_by_the_worker(rrig: Rig, stages: Any,
                                                                                         launchers: Any):
    scheduling(rrig)
    failed = approved_job(rrig)
    runner, clock = stages(gates=FakeGates({"backend-tests": "failed"})), Clock()
    run_until_quiet(rrig, runner, clock)
    assert rrig.state_of(failed) is JobState.NEEDS_REPAIR and runner.stages == ["inspect", "validate"]
    clock.advance(86400)
    assert not poll(rrig, runner, clock).started
    row = view_of(rrig, failed, clock)
    assert (row["who"], row["phase"], row["urgent"]) == (BY_OPERATOR, "repair", True) and "NEW `integration intake`" \
        in row["action"]
    assert len(rrig.jobs.list()) == 1                                              # no job was created for it


# -- bounded automation: stalls, delays, holds ---------------------------------------------------------------------

def test_a_stage_that_changes_nothing_is_delayed_then_held_and_resumed_by_the_operator(rrig: Rig, stages: Any):
    scheduling(rrig, retry_seconds=60, max_retry_seconds=600, max_stalls=3)
    job = approved_job(rrig)
    runner, clock = stages(), Clock()
    runner.scripted["inspect"] = ("deferred", "origin could not be read: timeout")   # the command changed nothing
    record_before = (rrig.state / "integration" / f"{job}.json").read_bytes()
    assert poll(rrig, runner, clock).started == [job]
    first = poll(rrig, runner, clock)                                      # settled: a stall, a delay, no new start
    assert any("nothing changed" in line and "`deferred`" in line for line in first.events) and not first.started
    rec = schedule(rrig, job)
    assert rec.stalls == 1 and rec.next_attempt_at == clock() + 60 and rec.hold is None
    assert view_of(rrig, job, clock)["category"] == "delayed"
    clock.advance(59)
    assert not poll(rrig, runner, clock).started                           # not yet
    clock.advance(1)
    assert poll(rrig, runner, clock).started == [job]
    poll(rrig, runner, clock)
    assert schedule(rrig, job).next_attempt_at == clock() + 120            # the delay doubles
    clock.advance(120)
    assert poll(rrig, runner, clock).started == [job]
    third = poll(rrig, runner, clock)
    rec = schedule(rrig, job)
    assert rec.stalls == 3 and rec.hold["kind"] == "stalls" and "`deferred`: origin could not be read" in \
        rec.hold["reason"] and any("HELD for the operator" in line for line in third.events)
    clock.advance(10 * 86400)
    for _ in range(3):
        assert not poll(rrig, runner, clock).started                       # held: never again by itself
    assert runner.stages == ["inspect"] * 3
    assert (rrig.state / "integration" / f"{job}.json").read_bytes() == record_before   # the job was never touched
    kind, text = poll(rrig, runner, clock).service_state()
    assert kind == "NEEDS_ATTENTION" and "HELD" in text and "--resume" in text
    assert [e["progressed"] for e in events_of(rrig, "integration-stage-ended", job)] == [False] * 3
    # the operator's recorded decision clears it
    assert _resume(rrig.store, job, "origin is reachable again", "spyderbg", apply=False) == "would-resume"
    assert schedule(rrig, job).hold is not None
    assert _resume(rrig.store, job, "origin is reachable again", "spyderbg", apply=True) == "resumed"
    resumed = events_of(rrig, "integration-schedule-resumed", job)
    assert resumed[0]["by"] == "spyderbg" and resumed[0]["reason"] == "origin is reachable again"
    del runner.scripted["inspect"]
    run_until_quiet(rrig, runner, clock)
    assert rrig.state_of(job) is JobState.AWAITING_MERGE_APPROVAL
    assert _resume(rrig.store, job, "again", "spyderbg", apply=True) in ("resumed", "nothing-held")


def test_a_hold_lapses_when_someone_changes_the_job(rrig: Rig, stages: Any):
    scheduling(rrig, max_stalls=1)
    job = approved_job(rrig)
    runner, clock = stages(), Clock()
    runner.scripted["inspect"] = ("deferred", "GitHub is unreachable")
    run_until_quiet(rrig, runner, clock)
    assert schedule(rrig, job).hold is not None and view_of(rrig, job, clock)["category"] == "held"
    assert rrig.inspect(job).outcome == "passed"                           # the operator ran the stage: the job moved
    assert view_of(rrig, job, clock)["category"] == "ready"                # the view already says so
    del runner.scripted["inspect"]
    run_until_quiet(rrig, runner, clock)
    rec = schedule(rrig, job)
    assert rec.hold is None and rec.stalls == 0 and runner.stages == ["inspect", "validate", "review"]


def test_the_launch_budget_bounds_a_stage_that_keeps_changing_the_record(rrig: Rig, stages: Any):
    scheduling(rrig, max_launches=2, retry_seconds=1, max_retry_seconds=1)
    job = approved_job(rrig)
    runner, clock = stages(), Clock()

    def touch(job_id: str) -> tuple[str, str]:                             # "progress" that never leaves the state
        with rrig.store.transaction():
            rrig.jobs.save(rrig.jobs.load(job_id))
        return "deferred", "still not there"
    runner.scripted["inspect"] = touch
    for _ in range(12):
        poll(rrig, runner, clock)
        clock.advance(5)
    rec = schedule(rrig, job)
    assert runner.stages == ["inspect", "inspect"] and rec.launches == {"inspect": 2}
    assert rec.hold["kind"] == "launches" and "max_launches 2" in rec.hold["reason"]
    assert len(events_of(rrig, "integration-stage-held", job)) == 1
    touch(job)                                                             # a changed job does NOT lift this hold
    clock.advance(5)
    assert not poll(rrig, runner, clock).started and schedule(rrig, job).hold["kind"] == "launches"
    assert _resume(rrig.store, job, "looked at it", "spyderbg", apply=True) == "resumed"
    assert not schedule(rrig, job).launches and poll(rrig, runner, clock).started == [job]


# -- the stage process: deadline, crashes, restarts -----------------------------------------------------------------

def test_a_stage_past_its_deadline_is_stopped_after_the_decision_is_recorded(rrig: Rig, stages: Any):
    scheduling(rrig, stage_timeouts={"inspect": 100})
    job = approved_job(rrig)
    runner, clock = stages(hold=("inspect",)), Clock()
    assert poll(rrig, runner, clock).started == [job]
    (pid,) = runner.alive()
    clock.advance(100 + integration_worker.STAGE_SLACK_SECONDS - 1)
    assert poll(rrig, runner, clock).busy and runner.alive() == [pid]      # within its deadline: left alone
    clock.advance(2)
    report = poll(rrig, runner, clock)
    assert not runner.alive() and any("passed its deadline" in line for line in report.events)
    kinds = [e["kind"] for e in rrig.store.events() if e["kind"].startswith("integration-stage-")]
    assert kinds == ["integration-stage-starting", "integration-stage-started", "integration-stage-stopping",
                     "integration-stage-stopped", "integration-stage-ended"]   # decided BEFORE the signal
    assert events_of(rrig, "integration-stage-stopped", job)[0]["survivors"] == []
    rec = schedule(rrig, job)
    assert rec.running is None and rec.last["cause"] == "deadline" and rec.stalls == 1
    assert rrig.state_of(job) is JobState.AWAITING_APPROVAL                # the job is exactly where it was
    runner.hold.clear()
    runner.pending.clear()
    clock.advance(3600)
    run_until_quiet(rrig, runner, clock)                                   # … and the next launch does the work
    assert rrig.state_of(job) is JobState.AWAITING_MERGE_APPROVAL


def test_a_crash_between_counting_a_launch_and_starting_it_never_starts_two_processes(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(), Clock()
    with pytest.raises(SimulatedCrash):
        poll(rrig, runner, clock, faults=(f"crash-after-stage-counted:{job}",))
    rec = schedule(rrig, job)
    assert not runner.calls and rec.launches == {"inspect": 1} and rec.running["pid"] is None   # counted, not started
    assert view_of(rrig, job, clock)["category"] == "unsettled"
    report = poll(rrig, runner, clock)                                     # restarted: nothing carries the tag
    assert not report.started and not runner.calls
    rec = schedule(rrig, job)
    assert rec.running is None and rec.last["cause"] == "exited" and rec.last["exit_code"] is None and rec.stalls == 1
    clock.advance(60)
    run_until_quiet(rrig, runner, clock)
    assert runner.stages == ["inspect", "validate", "review"] and schedule(rrig, job).launches["inspect"] == 2


def test_a_stage_that_survives_a_service_crash_is_watched_by_the_next_instance_not_started_again(rrig: Rig,
                                                                                                  stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(hold=("inspect",)), Clock()
    with pytest.raises(SimulatedCrash):                                    # the service dies right after the start:
        poll(rrig, runner, clock, faults=(f"crash-after-stage-started:{job}",))   # its PID was never recorded
    assert schedule(rrig, job).running["pid"] is None and len(runner.alive()) == 1
    integration_worker._CHILDREN.clear()                                   # the next instance has no Popen handle
    for _ in range(3):
        report = poll(rrig, runner, clock, instance="second")
        assert report.busy and not report.started and runner.stages == ["inspect"]   # found by its tag: no second one
    assert view_of(rrig, job, clock)["running"]["state"] == "running"
    runner.release(job)
    done = poll(rrig, runner, clock, instance="second")
    assert any("`inspect` ended (exited, `passed`)" in line for line in done.events)
    assert rrig.state_of(job) is not JobState.AWAITING_APPROVAL and schedule(rrig, job).launches["inspect"] == 1


def test_a_stage_process_that_cannot_start_is_a_stall_not_a_launch_and_is_held_in_the_end(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(), Clock()
    runner.fail_start = OSError("fork failed")
    report = poll(rrig, runner, clock)
    assert not report.started and any("could not be started: fork failed" in d for d in report.deferred)
    rec = schedule(rrig, job)
    assert rec.launches == {"inspect": 0} and rec.running is None                # the launch budget is not spent …
    assert rec.stalls == 1 and rec.next_attempt_at == clock() + 60              # … but a failed start is a stall
    for expected in (2, 3):                                                      # it keeps failing: delayed, then held
        clock.advance(600)
        assert not poll(rrig, runner, clock).started and schedule(rrig, job).stalls == expected
    rec = schedule(rrig, job)
    assert rec.launches == {"inspect": 0} and rec.hold["kind"] == "stalls" \
        and "could not be started: fork failed" in rec.hold["reason"]
    clock.advance(10 * 86400)
    assert not poll(rrig, runner, clock).started and not runner.calls           # held: no endless retry
    assert poll(rrig, runner, clock).service_state()[0] == "NEEDS_ATTENTION"
    runner.fail_start = None
    assert _resume(rrig.store, job, "the log directory is writable again", "spyderbg", apply=True) == "resumed"
    assert poll(rrig, runner, clock).started == [job]


def test_a_graceful_service_stop_stops_the_stage_and_counts_nobodys_stall(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(hold=("inspect",)), Clock()
    assert poll(rrig, runner, clock).started == [job] and len(runner.alive()) == 1
    logs: list[str] = []
    stop_stages(coord_of(rrig), logs.append)
    assert not runner.alive() and any("stopped `inspect`" in line for line in logs)
    rec = schedule(rrig, job)
    assert rec.running is None and rec.last["cause"] == "service-stop" and rec.stalls == 0 \
        and rec.next_attempt_at is None and rec.hold is None
    stop_stages(coord_of(rrig), logs.append)                               # nothing left: nothing happens
    runner.hold.clear()
    runner.pending.clear()
    assert poll(rrig, runner, clock).started == [job]                      # the next instance starts it again at once


# -- fail closed on unreadable records -------------------------------------------------------------------------------

def test_an_unreadable_job_record_stops_every_start(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    path = rrig.state / "integration" / f"{job}.json"
    good = path.read_bytes()
    path.write_text("{not json", encoding="utf-8")
    runner, clock = stages(), Clock()
    report = poll(rrig, runner, clock)
    assert not runner.calls and any("job records cannot be read" in a for a in report.attention)
    kind, text = report.service_state()
    assert kind == "NEEDS_ATTENTION" and "cannot be read" in text
    path.write_bytes(good)
    assert poll(rrig, runner, clock).started == [job]


def test_an_unreadable_schedule_record_stops_every_start_until_the_operator_resets_it(rrig: Rig, stages: Any):
    scheduling(rrig)
    job, other = approved_job(rrig), approved_job(rrig, "integration/other", {"cli/src/kiddo_cli/y.py": X_PY})
    runner, clock = stages(hold=("inspect",)), Clock()
    assert poll(rrig, runner, clock).started == [job]
    runner.kill(job)
    path = rrig.state / RECORD_KIND / f"{job}.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["running"]["stage"] = "merge"                                 # a record that names what is not a stage
    path.write_text(json.dumps(tampered), encoding="utf-8")
    runner.hold.clear()
    for _ in range(3):
        report = poll(rrig, runner, clock)
        assert not report.started and any("kept as evidence" in a for a in report.attention)
    assert runner.stages == ["inspect"] and "merge" not in runner.stages   # nothing starts — for the other job either
    assert view_of(rrig, job, clock)["category"] == "unreadable" and view_of(rrig, other, clock)["category"] == "ready"
    assert _resume(rrig.store, job, "inspected the record", "spyderbg", apply=True) == "reset"
    kept = [p.name for p in (rrig.state / RECORD_KIND).iterdir() if "unreadable" in p.name]
    assert len(kept) == 1 and events_of(rrig, "integration-schedule-reset", job)[0]["kept"] == kept[0]
    run_until_quiet(rrig, runner, clock)
    assert rrig.state_of(job) is rrig.state_of(other) is JobState.AWAITING_MERGE_APPROVAL


# -- exclusive ownership of a job --------------------------------------------------------------------------------------

def test_an_operators_command_owns_its_job_and_the_worker_counts_no_launch(rrig: Rig, stages: Any):
    scheduling(rrig)
    job, other = approved_job(rrig), approved_job(rrig, "integration/other", {"cli/src/kiddo_cli/y.py": X_PY})
    holder = hold_job_lock(rrig, job)                                      # a REAL other process holds the job's lock
    try:
        runner, clock = stages(hold=("inspect",)), Clock()
        report = poll(rrig, runner, clock)
        assert report.started == [other]                                   # the busy job is skipped, not blocked on
        assert not schedule(rrig, job).launches and runner.started(job) == []
        runner.release(other)
        runner.hold.clear()
        run_until_quiet(rrig, runner, clock)
        assert rrig.state_of(other) is JobState.AWAITING_MERGE_APPROVAL
        assert rrig.state_of(job) is JobState.AWAITING_APPROVAL and runner.started(job) == []
        with pytest.raises(CoordinatorError, match="is running; nothing was done"):
            rrig.inspect(job)                                              # the stage command itself refuses too
    finally:
        holder.kill()
        holder.wait(timeout=30)
    run_until_quiet(rrig, runner, clock)                                   # the lock died with its process: not stale
    assert rrig.state_of(job) is JobState.AWAITING_MERGE_APPROVAL


def test_two_scheduling_steps_at_once_start_one_stage(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(hold=("inspect",)), Clock()
    held = integration_worker.FileLock(rrig.store.locks.dir / "integration-worker.lock").acquire()
    try:
        report = poll(rrig, runner, clock)                                 # another step is in its critical section
        assert not report.started and not runner.calls and "integration-worker.lock is held" in report.deferred[0]
    finally:
        held.release()
    assert poll(rrig, runner, clock).started == [job] and len(runner.alive()) == 1


# -- the execution slot is durable and shared (integration_slot.py) --------------------------------------------------

def test_resume_cannot_discard_a_launch_that_is_between_its_record_and_its_process(rrig: Rig, stages: Any,
                                                                                   monkeypatch: pytest.MonkeyPatch):
    """`schedule --resume` while the step is between recording a launch and starting its process: the step holds its
    lock across that handoff, so the resume is refused — the launch stays recorded and its process tracked."""
    monkeypatch.setattr(integration_recovery_commands, "STEP_WAIT_SECONDS", 0.2)
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(hold=("inspect",)), Clock()
    seen: list[str] = []
    real = runner.__call__

    def racing(tail: list[str], log_name: str, env: dict[str, str]) -> Any:
        assert schedule(rrig, job).running["pid"] is None             # recorded, not started: the window
        with pytest.raises(CoordinatorError, match="scheduling step of the Worker is running"):
            _resume(rrig.store, job, "impatient", "spyderbg", apply=True)
        seen.append("refused")
        return real(tail, log_name, env)
    report = poll(rrig, racing, clock)
    assert seen == ["refused"] and report.started == [job]
    rec = schedule(rrig, job)
    assert rec.running is not None and rec.running["pid"] and rec.launches == {"inspect": 1}   # still tracked
    assert execution_owner(rrig.store)[0] == "running" and len(runner.alive()) == 1
    with pytest.raises(CoordinatorError, match="still runs"):         # and never while its process lives
        _resume(rrig.store, job, "impatient", "spyderbg", apply=True)


def test_a_claim_and_a_feature_integration_are_refused_while_a_stage_holds_the_slot(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    claims = ClaimManager(rrig.store)
    runner, clock = stages(hold=("inspect",)), Clock()
    assert execution_owner(rrig.store) == ("", "")                    # nothing was ever scheduled: free, one stat
    assert poll(rrig, runner, clock).started == [job]
    kind, why = execution_owner(rrig.store)
    assert kind == "running" and f"integration job {job}: `inspect` runs" in why
    with pytest.raises(CheckoutBusyError, match="execution slot is taken: integration job"):
        claims.claim("f.task", role="backend", branch="feature/x", head_sha="0" * 40)   # a manual launch too
    assert not rrig.store.list_claims()
    coord = build_coordinator(rrig.repo, rrig.state, config=rrig.config)
    with pytest.raises(WritersActiveError, match="execution slot is taken"):
        coord.integrate(coord.plan(FeatureSpec("slot", "Slot", (TaskSpec("api", "backend", "API"),))).id)
    runner.release(job)
    poll(rrig, runner, clock)                                          # settled: the slot is free again
    assert execution_owner(rrig.store) == ("", "")
    with pytest.raises(KeyError):                                      # the claim now fails for its own reason
        claims.claim("f.task", role="backend", branch="feature/x", head_sha="0" * 40)


def test_a_stage_is_not_admitted_beside_a_claim_or_a_barrier_that_appears_at_the_last_moment(
        rrig: Rig, stages: Any, monkeypatch: pytest.MonkeyPatch):
    """The idle check and the launch are not one step — the admission inside the launch's critical section is."""
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(), Clock()
    monkeypatch.setattr(integration_worker, "feature_work_pending", lambda *_a, **_k: "")   # the early check saw idle
    with rrig.store.transaction():
        rrig.store.save_claim("f.task", {"task_id": "f.task", "role": "backend", "identity": None})
    assert not poll(rrig, runner, clock).started and not runner.calls and not schedule(rrig, job).launches
    with rrig.store.transaction():
        rrig.store.delete_claim("f.task")
    barrier = rrig.store.locks.project(shared=False).acquire()         # a feature integration holds the project lock
    try:
        assert not poll(rrig, runner, clock).started and not runner.calls and not schedule(rrig, job).launches
    finally:
        barrier.release()
    assert poll(rrig, runner, clock).started == [job]


def test_a_terminal_outcome_of_a_scheduled_stage_stays_in_attention_until_acknowledged(rrig: Rig, stages: Any,
                                                                                       github_origin: Path):
    scheduling(rrig, stages=("validate", "review"))
    job = validated_job(rrig).job_id
    change_on(rrig.repo, BRANCH, {"backend/src/more.py": X_PY})        # the source branch moves
    runner, clock = stages(), Clock()
    reports = run_until_quiet(rrig, runner, clock)
    assert rrig.state_of(job) is JobState.STALE                        # found by the scheduled review: terminal
    assert any("NEEDS ATTENTION: the job ended STALE" in line for r in reports for line in r.events)
    row = view_of(rrig, job, clock)                                    # … and it does not vanish from the view
    assert row["category"] == "ended" and row["urgent"] and "a scheduled `review` ended it STALE" in row["reason"]
    kind, text = poll(rrig, runner, clock).service_state()
    assert kind == "NEEDS_ATTENTION" and job in text and "acknowledge" in text
    assert not poll(rrig, runner, clock).started                       # nothing is continued or recreated
    assert _resume(rrig.store, job, "seen: a new job follows", "spyderbg", apply=True) == "resumed"
    assert schedule(rrig, job).acknowledged["state"] == "STALE"
    assert events_of(rrig, "integration-schedule-resumed", job)[0]["acknowledged"] == "STALE"
    assert not [r for r in schedule_view(rrig.store, rrig.config, clock()) if r["job"] == job]
    assert poll(rrig, runner, clock).service_state() is None
    assert _resume(rrig.store, job, "again", "spyderbg", apply=True) == "nothing-held"


def test_schedule_records_are_strict(rrig: Rig, stages: Any):
    job = "int-20261121-0001"
    good = Schedule(id=job).to_dict()
    assert Schedule.from_dict(good).id == job
    nan, inf = float("nan"), float("inf")
    bad = [
        None, [], "x", 1,
        {**good, "schema_version": True}, {**good, "schema_version": 1.0}, {**good, "schema_version": 2},
        {**good, "job_id": "../x"}, {**good, "job_id": "int-1"}, {**good, "job_id": 7},
        {**good, "stalls": True}, {**good, "stalls": -1}, {**good, "revision": 1.5},
        {**good, "launches": {"validate": True}}, {**good, "launches": {"merge": 1}}, {**good, "launches": []},
        {**good, "next_attempt_at": nan}, {**good, "next_attempt_at": inf}, {**good, "next_attempt_at": "soon"},
        {**good, "history": {}}, {**good, "history": ["x"]}, {**good, "history": [{}] * 21}, {**good, "last": []},
        {**good, "extra": 1}, {k: v for k, v in good.items() if k != "hold"},
        {**good, "hold": {"kind": "forever", "stage": "validate", "reason": "x", "since": "x",
                          "basis": {"state": "X", "revision": 1}}},
        {**good, "acknowledged": {"by": "x"}},
        {**good, "running": running_entry(deadline_epoch=nan)},        # a deadline that would never pass
        {**good, "running": running_entry(deadline_epoch=inf)}, {**good, "running": running_entry(started_epoch=-1)},
        {**good, "running": running_entry(launch=0)}, {**good, "running": running_entry(launch=2)},   # not its log
        {**good, "running": running_entry(pid=True)}, {**good, "running": running_entry(reviewer="istage-00")},
        {**good, "running": running_entry(basis={})}, {**good, "running": running_entry(process={"pid": 1})},
        {**good, "running": running_entry(launcher=None)}, {**good, "running": running_entry(watchdog={"kind": "x"})},
        {**good, "last": {"state_after": "FAILED"}}, {**good, "last": closed_entry(stage="merge")},
        {**good, "last": closed_entry(exit_code=nan)}, {**good, "last": closed_entry(progressed="yes")},
        {**good, "last": closed_entry(cause="because")}, {**good, "history": [closed_entry(), {"stage": "validate"}]},
        {**good, "history": [{**closed_entry(), "extra": nan}]},
    ]
    assert Schedule.from_dict({**good, "last": closed_entry(), "history": [closed_entry()] * 20}).last["launch"] == 1
    for record in bad:
        with pytest.raises(StateDirError):
            Schedule.from_dict(record)
    # a file is "absent" only when it does not exist: empty, null, not JSON and another job's record are unreadable
    folder = rrig.state / RECORD_KIND
    folder.mkdir(parents=True)
    path = folder / f"{job}.json"
    assert read_schedule(rrig.store, job) is None and execution_owner(rrig.store) == ("", "")
    for text in ("", "null", "{", "[]", json.dumps({**good, "job_id": "int-20261121-0002"})):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(StateDirError):
            read_schedule(rrig.store, job)
        assert list_schedules(rrig.store)[0][1] is None                # listed as unreadable, never skipped
        kind, why = execution_owner(rrig.store)
        assert kind == "uncertain" and "cannot be read" in why         # unknown is not free
        with pytest.raises(CheckoutBusyError):
            ClaimManager(rrig.store).claim("f.task", role="backend", branch="feature/x", head_sha="0" * 40)
    path.write_text(json.dumps(good), encoding="utf-8")
    assert read_schedule(rrig.store, job).id == job and execution_owner(rrig.store) == ("", "")
    assert integration_slot.REVIEW_TAG_ENV == review_process.REVIEW_TAG_ENV


def test_a_stage_with_an_unusable_deadline_is_overdue_not_immortal(rrig: Rig, stages: Any):
    """Defence in depth behind the strict record: the watcher's comparison cannot be won by a bad number."""
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(hold=("inspect",)), Clock()
    assert poll(rrig, runner, clock).started == [job]
    rec = schedule(rrig, job)
    rec.running["deadline_epoch"] = float("nan")                       # (cannot be loaded from disk: forced here)
    report = IntegrationStepReport()
    busy = step(rrig, runner, clock)._watch(rec, report)
    assert not busy and not runner.alive() and any("passed its deadline" in line for line in report.events)


def test_an_unstarted_launch_holds_the_slot_until_it_is_settled_not_until_a_clock_or_its_launcher_gives_up(
        rrig: Rig, stages: Any):
    """Between the launch record and the stage process the slot is RESERVED. No elapsed time releases it (a launcher
    suspended for ten minutes still owns it) and neither does the launcher's death (the stage process it started may
    still be about to confirm): only a settlement in a critical section does."""
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(hold=("inspect",)), Clock()
    real = runner.__call__
    seen: list[str] = []

    def suspended(tail: list[str], log_name: str, env: dict[str, str]) -> Any:
        clock.advance(600)                                             # the launcher was suspended for ten minutes
        kind, why = execution_owner(rrig.store)
        seen.append(f"{kind}: {why}")
        with pytest.raises(CheckoutBusyError, match="is being started"):
            ClaimManager(rrig.store).claim("f.task", role="backend", branch="feature/x", head_sha="0" * 40)
        return real(tail, log_name, env)
    assert poll(rrig, suspended, clock).started == [job]
    assert seen and seen[0].startswith("running: ") and "is being started" in seen[0]
    runner.kill(job)
    other = approved_job(rrig, "integration/other", {"cli/src/kiddo_cli/y.py": X_PY})
    reservation(rrig, other)                                           # a launch whose launcher is dead, no process
    kind, why = execution_owner(rrig.store)
    assert kind == "running" and f"integration job {other}" in why and "which is gone" in why
    with pytest.raises(CheckoutBusyError, match="is being started"):   # still reserved: nobody settled it yet
        ClaimManager(rrig.store).claim("f.task", role="backend", branch="feature/x", head_sha="0" * 40)
    report = step(rrig, None, clock).poll()                            # a scheduling step (it can start nothing) …
    assert any(f"{other}: `validate` ended" in line for line in report.events)   # … settles the dead launch
    assert schedule(rrig, other).running is None and schedule(rrig, other).last["cause"] == "exited"
    assert execution_owner(rrig.store) == ("", "")                     # only now is the slot released


# -- the hand-off from the launcher to its stage process (final Codex re-review) ----------------------------------

def reservation(rig: Rig, job: str, **changes: Any) -> Schedule:
    """A recorded launch of `validate` for *job* whose process is not recorded and whose launcher is dead."""
    stage = changes.get("stage", "validate")
    rec = Schedule(id=job, launches={stage: 1})
    rec.running = running_entry(**{"log": f"integration-{job}.{stage}.1-abcdef.log", **changes})
    with rig.store.transaction():
        integration_slot.save_schedule(rig.store, rec)
    return rec


def test_a_dead_launchers_unstarted_launch_is_not_free_for_a_restarted_service_either(rrig: Rig, stages: Any):
    """A launcher dies after starting a stage process that has not shown itself yet. Nobody — no claim, and no
    restarted service about to run its other steps — may find the slot free: the launch holds it until it is settled.
    Then exactly one of two things is true: the stage process confirmed first (it runs, the slot is its own), or the
    settlement came first (the launch is closed, and the stage process, arriving late, does nothing)."""
    scheduling(rrig)
    job, tag = approved_job(rrig), "istage-0123456789abcdef"
    reservation(rrig, job, stage="inspect")
    assert execution_owner(rrig.store)[0] == "running"                 # what the service reads before any other step
    runner, clock = stages(), Clock()
    report = poll(rrig, runner, clock)                                 # the scheduling step settles the dead launch
    assert schedule(rrig, job).running is None and any("`inspect` ended" in line for line in report.events)
    assert "no longer recorded" in confirm_launch(rrig.store, job, tag, None)   # the late stage process does nothing
    assert rrig.state_of(job) is JobState.AWAITING_APPROVAL or runner.results   # (only a NEW launch may have worked)


def test_a_stage_process_confirms_its_launch_before_it_does_anything(rrig: Rig):
    job, tag = approved_job(rrig), "istage-0123456789abcdef"
    me = integration_worker.ProcessIdentity.current()
    assert "no longer recorded" in confirm_launch(rrig.store, job, tag, me)          # nothing was ever recorded
    reservation(rrig, job)
    assert "no longer recorded" in confirm_launch(rrig.store, job, "istage-" + "f" * 16, me)   # another launch's tag
    # the worst moment: its launcher died, nothing showed the stage yet, and a claim got in — the stage does not run
    with rrig.store.transaction():
        rrig.store.save_claim("f.task", {"task_id": "f.task", "role": "backend", "identity": None})
    assert "an agent holds a claim" in confirm_launch(rrig.store, job, tag, me)
    assert schedule(rrig, job).running["pid"] is None
    with rrig.store.transaction():
        rrig.store.delete_claim("f.task")
    barrier = rrig.store.locks.project(shared=False).acquire()
    try:
        assert "quiescent barrier" in confirm_launch(rrig.store, job, tag, me)
    finally:
        barrier.release()
    # free: it records itself — from now on every reader sees it, and a claim is refused
    assert confirm_launch(rrig.store, job, tag, me) == ""
    rec = schedule(rrig, job)
    assert rec.running["pid"] == me.pid and rec.running["process"] == me.to_dict()
    with rrig.store.transaction():
        rrig.store.save_claim("f.task", {"task_id": "f.task", "role": "backend", "identity": None})
    assert confirm_launch(rrig.store, job, tag, me) == ""              # already registered: confirmed, no re-admission
    path = rrig.state / RECORD_KIND / f"{job}.json"
    path.write_text("{", encoding="utf-8")
    assert "cannot be read" in confirm_launch(rrig.store, job, tag, me)


@pytest.mark.parametrize("stage", ["inspect", "validate", "review", "recover"])
def test_the_real_stage_commands_do_nothing_when_their_launch_is_not_confirmed(
        rrig: Rig, stage: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Through the real CLI, as the Worker starts it (the stage tag in the environment): a launch that is not the
    recorded one, or one a claim overtook, does nothing at all. An operator's own command carries no tag."""
    job, tag = approved_job(rrig), "istage-0123456789abcdef"
    before = (rrig.state / "integration" / f"{job}.json").read_bytes()
    argv = ["--repo", str(rrig.repo), "--state-dir", str(rrig.state), "--tracker", "local", *stage_argv(stage, job)]
    monkeypatch.setenv(integration_slot.STAGE_TAG_ENV, tag)
    assert cli_main(argv) == 1                                         # no such launch is recorded
    out, err = capsys.readouterr()
    assert "this scheduled stage does not run: its launch is no longer recorded" in out + err
    reservation(rrig, job, stage=stage)
    with rrig.store.transaction():
        rrig.store.save_claim("f.task", {"task_id": "f.task", "role": "backend", "identity": None})
    assert cli_main(argv) == 1                                         # a claim overtook it
    out, err = capsys.readouterr()
    assert "the checkout is no longer free for it: an agent holds a claim" in out + err
    assert (rrig.state / "integration" / f"{job}.json").read_bytes() == before
    assert schedule(rrig, job).running["pid"] is None and not events_of(rrig, "integration-transition", job)[2:]


def test_a_claim_that_overtakes_a_dead_launchers_stage_wins_and_the_stage_does_nothing(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    runner, clock = stages(), Clock()
    real = runner.__call__

    def overtaken(tail: list[str], log_name: str, env: dict[str, str]) -> Any:
        with rrig.store.transaction():                                 # (stands for: launcher dead, claim admitted)
            rrig.store.save_claim("f.task", {"task_id": "f.task", "role": "backend", "identity": None})
        return real(tail, log_name, env)
    assert poll(rrig, overtaken, clock).started == [job]
    assert runner.refused and "an agent holds a claim" in runner.refused[0][2]
    assert runner.results == [("inspect", job, "error")]               # refused before any work
    assert rrig.state_of(job) is JobState.AWAITING_APPROVAL            # the stage did nothing
    with rrig.store.transaction():
        rrig.store.delete_claim("f.task")
    poll(rrig, runner, clock)
    rec = schedule(rrig, job)
    assert rec.running is None and rec.stalls == 1 and "does not run" in rec.last["detail"]


def test_reviewer_tags_of_a_file_that_is_not_the_jobs_record_are_never_trusted_or_signalled(rrig: Rig, stages: Any):
    import subprocess  # pylint: disable=import-outside-toplevel
    import sys  # pylint: disable=import-outside-toplevel
    job, other = approved_job(rrig), approved_job(rrig, "integration/other", {"cli/src/kiddo_cli/y.py": X_PY})
    tag = "review-0123456789abcdef"
    foreign = json.loads((rrig.state / "integration" / f"{other}.json").read_text(encoding="utf-8"))
    foreign["evidence"] = [{"kind": "review-attempt", "id": "rva-0001", "tag": tag}]   # (another job's reviewer)
    (rrig.state / "integration" / f"{job}.json").write_text(json.dumps(foreign), encoding="utf-8")
    env = {**os.environ, integration_slot.REVIEW_TAG_ENV: tag}
    reviewer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], env=env)  # pylint: disable=consider-using-with
    try:
        wait_for(lambda: integration_slot.tagged_processes(integration_slot.REVIEW_TAG_ENV, tag))
        assert integration_slot.review_tags(rrig.store, job) is None    # the file names another job: unknown
        rec = reservation(rrig, job, stage="review")
        assert integration_slot.reviewer_processes(rrig.store, rec) == ([], True)
        assert "cannot be read" in integration_slot.owner_of(rrig.store, rec)          # unknown is not free
        with pytest.raises(CoordinatorError):                           # the strict load refuses before any signal
            integration_worker.stop_stale_reviewers(rrig.store, rrig.jobs, job, 9e10, 1.0)
        logs: list[str] = []
        scheduling(rrig)
        stop_stages(coord_of(rrig), logs.append)
        assert reviewer.poll() is None                                  # the other job's reviewer was not touched
    finally:
        reviewer.kill()
        reviewer.wait(timeout=30)


def test_reviewer_cleanup_keeps_its_hands_off_while_another_command_owns_the_job(rrig: Rig):
    job = approved_job(rrig)
    holder = hold_job_lock(rrig, job)                                  # an operator's `integration review` runs
    try:
        found = integration_worker.stop_stale_reviewers(rrig.store, rrig.jobs, job, 9e10, 1.0)
        assert found["owned"] and not found["stopped"] and not found["survivors"]
    finally:
        holder.kill()
        holder.wait(timeout=30)
    assert not integration_worker.stop_stale_reviewers(rrig.store, rrig.jobs, job, 9e10, 1.0)["owned"]


def test_a_launch_is_not_closed_once_its_stage_process_has_confirmed_it(rrig: Rig, stages: Any):
    """The watcher judges a launch gone from one read; the stage process (started by a launcher that then died) may
    confirm that launch a moment later. Closing and confirming are one critical section each: whichever comes first
    wins, and the other sees it — the launch is never closed under a stage process that is about to work."""
    scheduling(rrig)
    job, tag = approved_job(rrig), "istage-0123456789abcdef"
    rec = reservation(rrig, job, stage="inspect")                      # recorded, no process, its launcher dead
    env = {**os.environ, integration_slot.STAGE_TAG_ENV: tag}
    import subprocess  # pylint: disable=import-outside-toplevel
    import sys  # pylint: disable=import-outside-toplevel
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], env=env)  # pylint: disable=consider-using-with
    try:
        wait_for(lambda: integration_slot.tagged_processes(integration_slot.STAGE_TAG_ENV, tag))
        identity = integration_worker.ProcessIdentity.of_pid(child.pid)
        stale = Schedule.from_dict(rec.to_dict())                      # what the watcher read BEFORE the stage existed
        assert confirm_launch(rrig.store, job, tag, identity) == ""    # … the stage process confirms its launch …
        worker = step(rrig, stages(), Clock())
        report = IntegrationStepReport()
        worker._settle(job, stale.running, "exited", report)           # … and the watcher closes on its stale read
        now = schedule(rrig, job)
        assert now.running is not None and now.running["pid"] == child.pid and not report.events   # not closed
        assert execution_owner(rrig.store)[0] == "running"
        with pytest.raises(CoordinatorError, match="still runs"):
            _resume(rrig.store, job, "impatient", "spyderbg", apply=True)
    finally:
        child.kill()
        child.wait(timeout=30)
    # the other order: the launch is closed first — the stage process then finds nothing to confirm and does nothing
    poll(rrig, stages(), Clock())
    assert schedule(rrig, job).running is None
    assert "no longer recorded" in confirm_launch(rrig.store, job, tag, None)
