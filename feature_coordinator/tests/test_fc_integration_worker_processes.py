"""W4.7 (M6): REAL stage processes — killed with SIGKILL at persisted checkpoints (fc_integration_worker_rig.
ForkedStages).

In these tests the stage's work does not run in the test process: it runs in a separate process (a fork that keeps
the rig's test doubles) that takes the job's real `flock`, writes the real records and starts a real reviewer process,
exactly as the stage command does. At a chosen M3–M5 fault point it stops and the test kills it with SIGKILL — no
exception unwinds, no `finally` runs, and only the kernel releases what it held. Checked: an operator's command is
refused while the stage process owns the job and admitted the moment it is dead; the Worker resumes a killed
validation without repeating a recorded gate; a reviewer process that outlives its killed review stage keeps the
execution slot (no other stage, no claim) and is collected, never duplicated; a killed recovery wrote nothing to
GitHub and is repeated to completion."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,unused-import
# pylint: disable=too-many-locals

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.claims import ClaimManager
from feature_coordinator.errors import CheckoutBusyError, CoordinatorError
from feature_coordinator.gates import GateContext, GateResult
from feature_coordinator.integration import JobState
from feature_coordinator.integration_recovery import interruption
from feature_coordinator.integration_recovery_commands import _resume
from feature_coordinator.integration_slot import execution_owner
from feature_coordinator.integration_worker import job_lock_busy
from feature_coordinator.issue_sync import SimulatedCrash

from fc_integration_merge_rig import merge, ready_job
from fc_integration_rig import OperatorKey, Rig, github_origin, origin
from fc_integration_worker_rig import Clock, ForkedStages, poll, run_until_quiet, schedule, scheduling, settle, wait_for
from test_fc_integration_review import X_PY, launchers, rrig, validated_job
from test_fc_integration_validate import FakeGates, inspected, key, rig, seeded, tracker
from test_fc_integration_worker import approved_job, view_of


def zombie_children() -> list[int]:
    """Ended, unreaped child processes of this test process (other tests of the suite check that there are none)."""
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            fields = Path(f"/proc/{entry}/stat").read_text(encoding="ascii", errors="replace").rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if fields[0] == "Z" and int(fields[1]) == os.getpid():
            found.append(int(entry))
    return found


@pytest.fixture
def forked(rrig: Rig, launchers: Any):
    made: list[ForkedStages] = []

    def make(**options: Any) -> ForkedStages:
        options.setdefault("gates", FakeGates(inspect_git=False))
        options.setdefault("launcher", launchers("pass"))
        made.append(ForkedStages(rrig, **options))
        return made[-1]
    yield make
    for item in made:
        item.cleanup()
    left = set(zombie_children())
    assert not [pid for item in made for pid in item.ever if pid in left]   # the rig reaps every process it forked


def quiet(rig: Rig, runner: ForkedStages, clock: Clock, job: str, timeout: float = 120.0) -> None:
    """Let the Worker run real stage processes until the job needs nobody but an operator (or is terminal)."""
    deadline = time.monotonic() + timeout
    while True:
        poll(rig, runner, clock)
        row = [r for r in __import__("feature_coordinator").integration_worker.schedule_view(
            rig.store, rig.config, clock()) if r["job"] == job]
        if not row or row[0]["category"] in ("operator", "held", "ended"):
            return
        assert time.monotonic() < deadline, (row, runner.errors())
        time.sleep(0.05)


def gate_entries(rig: Rig, job: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in rig.jobs.load(job).evidence:
        if entry["kind"] == "gate":
            counts[entry["gate"]] = counts.get(entry["gate"], 0) + 1
    return counts


def test_a_real_stage_process_owns_its_job_until_the_kernel_says_it_is_dead(rrig: Rig, forked: Any, tmp_path: Path):
    scheduling(rrig, stages=("validate", "review"))
    job = inspected(rrig, {"backend/src/x.py": X_PY}).job_id
    first = rrig.validate(job, apply=False).gates[0]["gate"]
    block, reached = tmp_path / "block-the-gate", tmp_path / "in-the-gate"
    block.write_text("x", encoding="utf-8")

    def gate(name: str, context: GateContext) -> GateResult:        # runs IN the stage process
        if block.exists():
            reached.write_text(name, encoding="utf-8")
            time.sleep(600)                                         # a gate that hangs: the process is killed in it
        return GateResult(name, 0, ("fake", name), status="passed", duration_seconds=0.1, log="", summary="ok")
    runner, clock = forked(gates=FakeGates({first: gate}, inspect_git=False)), Clock()
    assert poll(rrig, runner, clock).started == [job]
    wait_for(reached.exists, 60)
    # the stage process holds the job's lock: an operator's command is refused, the Worker starts nothing else
    assert job_lock_busy(rrig.store, job)
    with pytest.raises(CoordinatorError, match="is running; nothing was done"):
        rrig.validate(job, gates=FakeGates())
    assert poll(rrig, runner, clock).busy and execution_owner(rrig.store)[0] == "running"
    stored = rrig.jobs.load(job)
    assert stored.state is JobState.VALIDATING and interruption(stored)["operation"] == "validation"
    before = stored.evidence
    runner.sigkill(job)                                             # no handler, no finally: only the kernel
    assert not job_lock_busy(rrig.store, job) and execution_owner(rrig.store)[0] == ""
    settle(rrig, runner, clock, job)
    assert schedule(rrig, job).last["cause"] == "exited" and rrig.jobs.load(job).evidence == before
    block.unlink()
    clock.advance(3600)
    quiet(rrig, runner, clock, job)
    done = rrig.jobs.load(job)
    assert done.state is JobState.AWAITING_MERGE_APPROVAL and done.evidence[:len(before)] == before, runner.errors()
    assert not runner.errors()


@pytest.mark.parametrize("checkpoint", ["after-candidate", "after-attempt", "after-gate:{gate}", "before-summary"])
def test_a_validation_killed_with_sigkill_at_a_persisted_checkpoint_is_resumed(rrig: Rig, forked: Any,
                                                                                checkpoint: str):
    scheduling(rrig, stages=("validate", "review"))
    job = inspected(rrig, {"backend/src/x.py": X_PY}).job_id
    gate = rrig.validate(job, apply=False).gates[0]["gate"]
    checkpoint = checkpoint.format(gate=gate)
    runner, clock = forked(), Clock()
    runner.checkpoint["validate"] = checkpoint
    assert poll(rrig, runner, clock).started == [job]
    assert runner.at_checkpoint(job) == checkpoint
    assert job_lock_busy(rrig.store, job)                           # still inside the command, lock held
    crashed = rrig.jobs.load(job)
    recorded = gate_entries(rrig, job)
    runner.sigkill(job)
    del runner.checkpoint["validate"]
    settle(rrig, runner, clock, job)
    clock.advance(3600)
    quiet(rrig, runner, clock, job)
    done = rrig.jobs.load(job)
    assert done.state is JobState.AWAITING_MERGE_APPROVAL, (done.state, runner.errors())
    assert done.evidence[:len(crashed.evidence)] == crashed.evidence          # append-only across a real kill
    assert done.candidate == (crashed.candidate or done.candidate)            # the same candidate
    assert (done.source, done.base) == (crashed.source, crashed.base)
    after = gate_entries(rrig, job)
    assert all(after[name] == 1 for name in recorded) and set(after.values()) == {1}   # no recorded gate ran again
    assert not runner.errors() and not runner.github_writes()


def test_a_reviewer_that_outlives_its_killed_review_stage_keeps_the_slot_and_is_collected_once(
        rrig: Rig, forked: Any, launchers: Any):
    from test_fc_integration_review import inspected_on  # pylint: disable=import-outside-toplevel
    scheduling(rrig, stages=("validate", "review"))
    job = validated_job(rrig).job_id
    other = inspected_on(rrig, "integration/other", {"cli/src/kiddo_cli/y.py": X_PY}).job_id   # ready for `validate`
    runner, clock = forked(launcher=launchers("slow:4")), Clock(time.time())   # (deadlines are real time here)
    runner.checkpoint["review"] = "after-start"                     # the reviewer process runs; the stage stops here
    assert poll(rrig, runner, clock).started == [job]
    runner.at_checkpoint(job)
    runner.sigkill(job)                                             # the review command is dead; its reviewer is not
    del runner.checkpoint["review"]
    wait_for(lambda: not runner.alive(), 30)                        # the stage's own tagged process is gone
    # BEFORE the Worker has looked again: every reader of the slot already sees the reviewer (it is found by the
    # tags of the job's own review attempts), so nothing slips in between the kill and the next scheduling step
    kind, why = execution_owner(rrig.store)
    assert kind == "running" and "reviewer process it started still runs" in why
    assert schedule(rrig, job).running["reviewer"] is None          # (not yet noted by the step)
    with pytest.raises(CheckoutBusyError, match="execution slot is taken"):
        ClaimManager(rrig.store).claim("f.task", role="backend", branch="feature/x", head_sha="0" * 40)
    with pytest.raises(CoordinatorError, match="still runs"):       # … and its launch cannot be discarded
        _resume(rrig.store, job, "impatient", "spyderbg", apply=True)
    record = rrig.state / "integration-schedule" / f"{job}.json"
    good = record.read_bytes()
    record.write_text("{ unreadable", encoding="utf-8")             # … not through an unreadable record either
    with pytest.raises(CoordinatorError, match="are alive"):
        _resume(rrig.store, job, "impatient", "spyderbg", apply=True)
    assert execution_owner(rrig.store)[0] == "uncertain"
    record.write_bytes(good)
    report = poll(rrig, runner, clock)
    assert report.busy and "reviewer process" in report.busy and not report.started
    kind, why = execution_owner(rrig.store)
    assert kind == "running" and "reviewer process it started still runs" in why
    assert schedule(rrig, job).running["reviewer"].startswith("review-")
    assert view_of(rrig, job, clock)["running"]["state"] == "reviewer"
    assert runner.started(other) == []                              # no other stage beside the reviewer …
    with pytest.raises(CheckoutBusyError, match="execution slot is taken"):   # … and no agent claim either
        ClaimManager(rrig.store).claim("f.task", role="backend", branch="feature/x", head_sha="0" * 40)
    settle(rrig, runner, clock, job, timeout=90)                    # the reviewer ends: only now is the stage settled
    assert job not in execution_owner(rrig.store)[1]                # (the slot may already be the other job's)
    assert runner.started(other) in ([], ["validate"])              # … which could start only after the reviewer ended
    clock.advance(3600)
    quiet(rrig, runner, clock, job)
    done = rrig.jobs.load(job)
    assert done.state is JobState.AWAITING_MERGE_APPROVAL, (done.state, runner.errors())
    attempts = [e for e in done.evidence if e["kind"] == "review-attempt"]
    runs = [e for e in done.evidence if e["kind"] == "review-run"]
    assert len(attempts) == 1 and len(runs) == 1 and runs[0]["status"] == "ok"   # ONE reviewer, collected
    assert not runner.errors()


def test_a_recovery_killed_before_it_recorded_completion_wrote_nothing_and_is_repeated(
        rrig: Rig, forked: Any, launchers: Any, github_origin: Path):
    scheduling(rrig)
    job, fake = ready_job(rrig, launchers, github_origin)
    job_id = job.job_id
    with pytest.raises(SimulatedCrash):
        merge(rrig, job_id, faults=("after-merge-request",))        # the operator's merge: GitHub merged, run died
    sent = list(fake.writes())
    runner, clock = forked(fake=fake), Clock()
    runner.checkpoint["recover"] = "before-completed"               # reconciled and verified, COMPLETED not recorded
    assert poll(rrig, runner, clock).started == [job_id]
    assert runner.at_checkpoint(job_id) == "before-completed"
    assert rrig.state_of(job_id) is JobState.MERGED                 # the reconciliation is on disk
    runner.sigkill(job_id)
    del runner.checkpoint["recover"]
    settle(rrig, runner, clock, job_id)
    clock.advance(3600)
    quiet(rrig, runner, clock, job_id)
    done = rrig.jobs.load(job_id)
    assert done.state is JobState.COMPLETED and done.merge_result["verified"] is True, runner.errors()
    assert runner.github_writes() == [] and fake.writes() == sent   # neither recovery process sent a write
    assert not runner.errors()


def test_a_reviewer_left_behind_is_stopped_at_its_attempts_deadline_and_the_review_is_repeated_by_m4s_rules(
        rrig: Rig, forked: Any, launchers: Any):
    """The review command that would enforce the attempt's deadline is dead — the Worker enforces it: a hung reviewer
    does not hold the slot for ever."""
    scheduling(rrig, stages=("validate", "review"))
    job = validated_job(rrig).job_id
    runner, clock = forked(launcher=launchers("hang")), Clock(time.time())
    runner.checkpoint["review"] = "after-start"
    assert poll(rrig, runner, clock).started == [job]
    runner.at_checkpoint(job)
    runner.sigkill(job)
    del runner.checkpoint["review"]
    wait_for(lambda: not runner.alive(), 30)
    assert poll(rrig, runner, clock).busy and execution_owner(rrig.store)[0] == "running"   # within its deadline
    clock.advance(10 * 86400)                                       # … long past the attempt's deadline
    report = poll(rrig, runner, clock)
    assert any("stopped the reviewer process" in line and "deadline" in line for line in report.events)
    settle(rrig, runner, clock, job)
    assert execution_owner(rrig.store) == ("", "")
    stopped = [e for e in rrig.store.events() if e["kind"] == "integration-stage-reviewer-stopped"]
    assert stopped and stopped[0]["survivors"] == []
    runner.launcher = launchers("pass")                             # the next attempt's reviewer answers
    clock.advance(3600)                                             # past the delay after that run
    quiet(rrig, runner, clock, job)
    done = rrig.jobs.load(job)
    assert done.state is JobState.AWAITING_MERGE_APPROVAL, (done.state, runner.errors())
    runs = [e for e in done.evidence if e["kind"] == "review-run"]
    assert [r["status"] for r in runs] == ["cancelled", "ok"]       # the stopped attempt is recorded (given back)
    assert not runner.errors()


def test_a_service_stop_also_stops_a_reviewer_its_dead_stage_left_behind(rrig: Rig, forked: Any, launchers: Any):
    from feature_coordinator.integration_worker import stop_stages  # pylint: disable=import-outside-toplevel
    from fc_integration_worker_rig import coord_of  # pylint: disable=import-outside-toplevel
    scheduling(rrig, stages=("validate", "review"))
    job = validated_job(rrig).job_id
    runner, clock = forked(launcher=launchers("hang")), Clock(time.time())
    runner.checkpoint["review"] = "after-start"
    assert poll(rrig, runner, clock).started == [job]
    runner.at_checkpoint(job)
    runner.sigkill(job)
    wait_for(lambda: not runner.alive(), 30)
    assert execution_owner(rrig.store)[0] == "running"              # only the reviewer is left
    logs: list[str] = []
    stop_stages(coord_of(rrig), logs.append)
    assert execution_owner(rrig.store) == ("", "") and any("stopped `review`" in line for line in logs)
    rec = schedule(rrig, job)
    assert rec.running is None and rec.last["cause"] == "service-stop" and rec.stalls == 0
