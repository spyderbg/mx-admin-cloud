"""W4.7 (M6): crash recovery of integration jobs — failure injection through the Worker and `integration recover`
(integration_recovery.py, IntegrationMerger.settle, integration_worker.py).

Each test interrupts one operation at one of the M3–M5 fault points (a stage process killed there), then lets the
Worker — or the operator's `recover` — carry on, and checks the rules of integration-jobs.md §64: the evidence is
append-only across the crash; the pinned SHAs and the candidate never change; a gate result or a reviewer run that was
recorded is not repeated; an uncertain GitHub write is NEVER sent again; `recover` cannot write to GitHub at all; a
stale approval, a changed configuration, a moved ref or a changed PR stops the job instead of being worked around; a
repair is a new job with nothing carried over.

Disposable repositories, fake gates, a fake reviewer process, a scripted GitHub behind the real client (it performs
real merges on the disposable bare origin). No network, no real GitHub, no model, no real merge."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines
# pylint: disable=unused-import,too-many-locals

from __future__ import annotations

import dataclasses
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.errors import IntegrationWriteRefused, RemoteUnavailableError
from feature_coordinator.github_api import ApiResponse, TransportFailure
from feature_coordinator.integration import IntegrationError, JobState, abort_job, approval_binding_problems
from feature_coordinator.integration_recovery import diagnose, diagnosis_lines, interruption, repair_routing
from feature_coordinator.integration_schedule import ACTOR, OPERATOR as BY_OPERATOR, WORKER, assigned, next_step
from feature_coordinator.issue_sync import SimulatedCrash

from fc_helpers import git
from fc_integration_merge_rig import FakeMergeGitHub, approve_merge, bare, github_for, merge, ready_job
from fc_integration_rig import (
    BRANCH, OPERATOR, REPOSITORY, OperatorKey, Rig, change_on, github_origin, head, origin, pr_json, record_for,
    request_for, snapshot,
)
from fc_integration_worker_rig import (
    ALL_STAGES, Clock, Stages, coord_of, events_of, poll, run_until_quiet, schedule, scheduling, settle,
)
from test_fc_integration_merge_recovery import advance_dev, with_post_checks
from test_fc_integration_review import X_PY, launchers, review, rrig, validated_job, with_review
from test_fc_integration_validate import FakeGates, inspected, key, rig, seeded, tracker, with_config
from test_fc_integration_worker import approved_job, stages, view_of


def evidence(rig: Rig, job: str) -> list[dict[str, Any]]:
    return rig.jobs.load(job).evidence


def pins(rig: Rig, job: str) -> tuple[Any, ...]:
    stored = rig.jobs.load(job)
    return (stored.source, stored.base, (stored.candidate or {}).get("head_sha"),
            (stored.candidate or {}).get("tree_sha"))


def puts(fake: FakeMergeGitHub) -> list[tuple[str, str]]:
    return [(m, p) for m, p in fake.writes() if m == "PUT"]


def posts(fake: FakeMergeGitHub) -> list[tuple[str, str]]:
    return [(m, p) for m, p in fake.writes() if m == "POST"]


def step_of(rig: Rig, job: str) -> Any:
    return assigned(next_step(rig.jobs.load(job), rig.config), rig.config.github.integration.worker)


# -- candidate preparation and validation gates ------------------------------------------------------------------

@pytest.mark.parametrize("fault,interrupted,state", [
    ("after-build", None, JobState.INSPECTING),                 # built, nothing recorded yet
    ("after-candidate", "validation", JobState.CANDIDATE_BUILT),
    ("after-validating", None, JobState.VALIDATING),            # VALIDATING, no attempt yet
    ("after-attempt", "validation", JobState.VALIDATING),
    ("after-gate:{gate}", "validation", JobState.VALIDATING),
    ("before-summary", "validation", JobState.VALIDATING),
])
def test_a_validation_killed_at_any_point_is_resumed_by_the_worker_without_repeating_or_rewriting(
        rrig: Rig, stages: Any, fault: str, interrupted: str | None, state: JobState):
    scheduling(rrig, stages=("validate", "review"))
    job = inspected(rrig, {"backend/src/x.py": X_PY}).job_id
    gate = rrig.validate(job, apply=False).gates[0]["gate"]
    fault = fault.format(gate=gate)
    gates = FakeGates()
    runner, clock = stages(gates=gates), Clock()
    runner.faults["validate"] = (fault,)
    assert poll(rrig, runner, clock).started == [job]                      # … and the stage process dies at *fault*
    crashed, crashed_pins, ran = evidence(rrig, job), pins(rrig, job), list(gates.calls)
    assert rrig.state_of(job) is state and ("validate", job, "validated") not in runner.results
    found = interruption(rrig.jobs.load(job))
    assert (found or {}).get("operation") == interrupted
    assert (found or {"github_write": "none"})["github_write"] == "none"
    assert step_of(rrig, job).stage == "validate" and step_of(rrig, job).who == WORKER   # the next step: resume it
    poll(rrig, runner, clock)                                              # settled from the job record
    last = schedule(rrig, job).last
    assert last["cause"] == "exited" and last["exit_code"] == 137 and last["outcome"] == ""
    clock.advance(3600)
    run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(job)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and runner.stages == ["validate", "validate", "review"]
    assert stored.evidence[:len(crashed)] == crashed                       # append-only: nothing rewritten or dropped
    if crashed_pins[2]:
        assert pins(rrig, job) == crashed_pins                             # the same candidate commit and tree
    assert pins(rrig, job)[:2] == crashed_pins[:2]
    for name in set(ran):                                                  # a recorded gate result is never re-run
        recorded = [e for e in crashed if e["kind"] == "gate" and e["gate"] == name]
        assert gates.calls.count(name) == (1 if recorded else 2), (name, gates.calls)
    assert len([e for e in stored.evidence if e["kind"] == "candidate"]) == 1


# -- the independent AI review ------------------------------------------------------------------------------------

@pytest.mark.parametrize("fault,reviewers", [
    ("after-attempt", 1),     # attempt recorded, no process: given back, then ONE reviewer
    ("after-request", 1),
    ("after-start", 1),       # the reviewer runs on, alone: collected, never a second one for the attempt
    ("while-waiting", 1),
    ("before-collect", 1),
    ("after-run", 1),         # its answer is recorded: decided without another run
    ("before-decision", 1),
])
def test_a_review_killed_at_any_point_is_recovered_by_the_worker_with_one_reviewer(
        rrig: Rig, stages: Any, launchers: Any, fault: str, reviewers: int):
    scheduling(rrig, stages=("validate", "review"))
    job = validated_job(rrig).job_id
    launcher = launchers("pass")
    runner, clock = stages(launcher=launcher), Clock(time.time())   # real time: a reviewer's deadline is real
    runner.faults["review"] = (fault,)
    assert poll(rrig, runner, clock).started == [job]
    crashed, crashed_pins = evidence(rrig, job), pins(rrig, job)
    assert rrig.state_of(job) in (JobState.VALIDATING, JobState.IN_REVIEW)
    assert not [e for e in crashed if e["kind"] == "review"]               # no decision yet
    assert step_of(rrig, job).stage == "review"
    settle(rrig, runner, clock, job)         # (a reviewer the killed stage left running keeps the slot until it ends)
    clock.advance(3600)
    run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(job)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL, (stored.state, runner.results)
    assert stored.evidence[:len(crashed)] == crashed and pins(rrig, job) == crashed_pins
    assert len(launcher.calls) == reviewers                                # reviewer processes ever started
    decisions = [e for e in stored.evidence if e["kind"] == "review"]
    assert len(decisions) == 1 and decisions[0]["outcome"] == "PASS" and decisions[0]["head_sha"] == crashed_pins[2]
    assert interruption(stored) is None


def test_a_review_error_round_is_never_retried_by_the_worker(rrig: Rig, stages: Any, launchers: Any):
    scheduling(with_review(rrig, max_attempts=1), stages=("validate", "review"))
    job = validated_job(rrig).job_id
    launcher = launchers("invalid")
    runner, clock = stages(launcher=launcher), Clock()
    run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(job)
    assert stored.state is JobState.IN_REVIEW and stored.attention          # ERROR: the round is over
    for _ in range(4):
        clock.advance(86400)
        assert not poll(rrig, runner, clock).started
    assert len(launcher.calls) == 1 and runner.stages == ["review"]        # no second reviewer run, ever
    found = step_of(rrig, job)
    assert found.who == BY_OPERATOR and found.urgent and "--retry --reason" in found.action


# -- merge dispatch, PR creation, post-merge verification -----------------------------------------------------------

def test_recover_cannot_write_to_github_and_refuses_a_client_that_could(rrig: Rig, launchers: Any, stages: Any,
                                                                        github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    runner = stages(fake=fake)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("before-merge-request",))          # the operator's run binds ITS client
    assert rrig.client.integration_bound
    bound = Stages(rrig, fake=fake)
    bound.reader = lambda: rrig.client                                     # type: ignore[method-assign]
    with pytest.raises(IntegrationError, match="never runs with a GitHub client that can send"):
        bound.recoverer().settle(job.job_id, apply=True)
    reader = runner.reader()
    assert not reader.integration_bound
    attempt = [e for e in evidence(rrig, job.job_id) if e["kind"] == "merge-attempt"][-1]
    before = list(fake.writes())
    with pytest.raises(IntegrationWriteRefused):                           # the recover client cannot send the merge
        reader.merge_integration_pull_request(attempt["pr"], job.source["sha"], job_id=job.job_id,
                                              attempt=attempt["id"])
    with pytest.raises(IntegrationWriteRefused):
        reader.create_integration_pull_request(BRANCH, "dev", "t", "b", job_id=job.job_id, attempt=attempt["id"])
    assert fake.writes() == before


def test_an_intent_that_was_never_dispatched_is_closed_and_the_merge_stays_the_operators(
        rrig: Rig, launchers: Any, stages: Any, github_origin: Path):
    scheduling(rrig)
    job, fake = ready_job(rrig, launchers, github_origin)
    job_id = job.job_id
    runner, clock = stages(fake=fake), Clock()
    with pytest.raises(SimulatedCrash):
        merge(rrig, job_id, faults=("before-merge-request",))              # intent on disk, nothing dispatched
    found = interruption(rrig.jobs.load(job_id))
    assert found["operation"] == "merge" and found["github_write"] == "not-sent"
    assert step_of(rrig, job_id).stage == "recover"
    crashed, sent = evidence(rrig, job_id), list(fake.writes())
    assert not puts(fake)
    run_until_quiet(rrig, runner, clock)
    assert runner.stages == ["recover"] and runner.results == [("recover", job_id, "settled")]
    stored = rrig.jobs.load(job_id)
    outcome = stored.evidence[-1]
    assert stored.evidence[:len(crashed)] == crashed and stored.state is JobState.AWAITING_MERGE_APPROVAL
    assert (outcome["kind"], outcome["status"], outcome["basis"]) == ("merge-outcome", "not-merged", "not-dispatched")
    assert fake.writes() == sent and not puts(fake)                        # recover sent nothing
    assert interruption(stored) is None and step_of(rrig, job_id).who == BY_OPERATOR   # the merge: the operator's
    clock.advance(10 * 86400)
    assert not poll(rrig, runner, clock).started and not puts(fake)        # the Worker does not merge it later either
    report = merge(rrig, job_id)                                           # the operator merges: ONE request
    assert report.outcome == "completed" and len(puts(fake)) == 1


def test_a_merge_that_github_performed_before_the_crash_is_reconciled_never_repeated(
        rrig: Rig, launchers: Any, stages: Any, github_origin: Path):
    scheduling(rrig)
    job, fake = ready_job(rrig, launchers, github_origin)
    job_id = job.job_id
    runner, clock = stages(fake=fake), Clock()
    with pytest.raises(SimulatedCrash):
        merge(rrig, job_id, faults=("after-merge-request",))               # GitHub merged; MERGED was not recorded
    assert rrig.state_of(job_id) is JobState.AWAITING_MERGE_APPROVAL and len(puts(fake)) == 1
    found = interruption(rrig.jobs.load(job_id), datetime.now(timezone.utc))
    assert found["operation"] == "merge" and found["github_write"] == "unknown" and found["age_seconds"] >= 0
    crashed = evidence(rrig, job_id)
    run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(job_id)
    assert stored.state is JobState.COMPLETED and stored.merge_result["verified"] is True
    assert runner.results == [("recover", job_id, "completed")] and len(puts(fake)) == 1   # still exactly one
    assert stored.evidence[:len(crashed)] == crashed
    assert [h["actor"] for h in stored.history[-2:]] == [ACTOR, ACTOR]     # MERGED and COMPLETED by the recovery
    assert stored.merge_result["parents"] == [job.base["expected_sha"], job.source["sha"]]
    assert stored.merge_result["tree_sha"] == job.candidate["tree_sha"]


def test_a_merge_request_with_a_lost_answer_is_never_sent_again(rrig: Rig, launchers: Any, stages: Any,
                                                                github_origin: Path):
    scheduling(rrig, retry_seconds=10, max_retry_seconds=10, max_stalls=50, max_launches=50)
    job, fake = ready_job(rrig, launchers, github_origin)
    job_id = job.job_id
    runner, clock = stages(fake=fake), Clock()
    fake.merge_failure = TransportFailure("PUT merge: no response within 30s", maybe_applied=True)
    report = merge(rrig, job_id)                                           # dispatched; no answer; GitHub shows nothing
    assert report.outcome == "merge-uncertain" and len(puts(fake)) == 1
    assert interruption(rrig.jobs.load(job_id))["github_write"] == "unknown"
    for _ in range(6):                                                     # young: the Worker only reads
        poll(rrig, runner, clock)
        clock.advance(11)
    assert set(runner.results) == {("recover", job_id, "merge-uncertain")} and len(puts(fake)) == 1
    def later() -> datetime:                                               # past the observation window
        return datetime.now(timezone.utc) + timedelta(seconds=600)
    report = runner.recoverer(clock=later).settle(job_id, apply=True)
    assert report.outcome == "merge-unresolved" and rrig.jobs.load(job_id).attention
    found = step_of(rrig, job_id)                                          # now the operator's: nothing starts
    assert found.who == BY_OPERATOR and found.urgent and "never sent again" in found.reason
    launches = len(runner.calls)
    for _ in range(5):
        clock.advance(86400)
        assert not poll(rrig, runner, clock).started
    assert len(runner.calls) == launches and len(puts(fake)) == 1          # one PUT, for ever
    assert rrig.state_of(job_id) is JobState.AWAITING_MERGE_APPROVAL
    # GitHub turns out to have merged it after all: recover adopts exactly that merge and verifies it
    fake.do_merge(next(p for p in fake.prs if not p["merged"]))
    report = runner.recoverer().settle(job_id, apply=True)
    assert report.outcome == "completed" and len(puts(fake)) == 1 and rrig.state_of(job_id) is JobState.COMPLETED


def test_a_merged_job_is_verified_to_completion_and_a_pending_check_is_waited_for(
        rrig: Rig, launchers: Any, stages: Any, github_origin: Path):
    scheduling(with_post_checks(rrig, "ci"), retry_seconds=10, max_retry_seconds=10, max_stalls=50)
    job, fake = ready_job(rrig, launchers, github_origin)
    job_id = job.job_id
    runner, clock = stages(fake=fake), Clock()
    with pytest.raises(SimulatedCrash):
        merge(rrig, job_id, faults=("after-merged-record",))               # MERGED recorded, never verified
    assert rrig.state_of(job_id) is JobState.MERGED
    found = interruption(rrig.jobs.load(job_id))
    assert found["operation"] == "post-merge-verification" and step_of(rrig, job_id).stage == "recover"
    commit = rrig.jobs.load(job_id).merge_result["commit_sha"]
    for _ in range(4):                                                     # the check has not reported: pending
        poll(rrig, runner, clock)
        clock.advance(11)
    assert rrig.state_of(job_id) is JobState.MERGED and ("recover", job_id, "pending-verification") in runner.results
    fake.checks[commit] = [{"id": 1, "name": "ci", "status": "completed", "conclusion": "success"}]
    clock.advance(11)
    run_until_quiet(rrig, runner, clock)
    assert rrig.state_of(job_id) is JobState.COMPLETED and len(puts(fake)) == 1
    assert runner.results[-1] == ("recover", job_id, "completed")


def test_a_wrong_merge_found_by_recovery_is_failed_and_nothing_is_reverted(rrig: Rig, launchers: Any, stages: Any,
                                                                           github_origin: Path):
    scheduling(rrig)
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.mode = "squash"                                                   # GitHub did something else than approved
    runner, clock = stages(fake=fake), Clock()
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("after-merge-request",))
    dev = bare(github_origin, "rev-parse", "refs/heads/dev").strip()
    run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.FAILED and stored.attention and not stored.merge_result["verified"]
    assert bare(github_origin, "rev-parse", "refs/heads/dev").strip() == dev    # nothing reverted or pushed
    assert len(fake.writes()) == 2 and step_of(rrig, job.job_id).who == "nobody"


def test_someone_elses_merge_is_stale_never_adopted(rrig: Rig, launchers: Any, stages: Any, github_origin: Path):
    scheduling(rrig)
    job, fake = ready_job(rrig, launchers, github_origin)
    runner, clock = stages(fake=fake), Clock()
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("before-merge-request",))          # an intent, never dispatched …
    fake.do_merge(fake.prs[0], actor="someone-else")                       # … and then a person merged the PR
    run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and stored.attention["observed"]["attribution"] == "external"
    assert not puts(fake) and stored.merge_result is None


def test_pr_creation_is_never_repeated_by_recovery(rrig: Rig, launchers: Any, stages: Any, github_origin: Path):
    scheduling(rrig)
    job, fake = ready_job(rrig, launchers, github_origin)
    job_id = job.job_id
    runner, clock = stages(fake=fake), Clock()
    with pytest.raises(SimulatedCrash):
        merge(rrig, job_id, faults=("after-pr-intent",))                   # creation intent, nothing dispatched
    assert interruption(rrig.jobs.load(job_id)) == {**interruption(rrig.jobs.load(job_id)),
                                                    "operation": "pr-creation", "github_write": "not-sent"}
    run_until_quiet(rrig, runner, clock)
    assert runner.results == [("recover", job_id, "settled")] and not posts(fake)
    assert evidence(rrig, job_id)[-1]["status"] == "not-created"
    with pytest.raises(SimulatedCrash):
        merge(rrig, job_id, faults=("after-pr-created",))                  # created on GitHub; its number not recorded
    assert len(posts(fake)) == 1 and interruption(rrig.jobs.load(job_id))["github_write"] == "unknown"
    found = step_of(rrig, job_id)
    assert found.who == BY_OPERATOR and found.urgent and "never creates a second one" in found.action
    clock.advance(86400)
    assert not poll(rrig, runner, clock).started                           # the Worker does not touch it
    report = runner.recoverer().settle(job_id, apply=True)                 # the operator's recover creates no PR
    assert report.outcome == "pr-uncertain" and len(posts(fake)) == 1 and not report.changed
    report = merge(rrig, job_id)                                           # the operator's merge finds and binds it
    assert report.outcome == "completed" and len(posts(fake)) == 1 and len(puts(fake)) == 1


def test_recover_has_nothing_to_settle_before_a_merge_run_and_changes_nothing(rrig: Rig, launchers: Any, stages: Any,
                                                                              github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    runner = stages(fake=fake)
    before = (rrig.state / "integration" / f"{job.job_id}.json").read_bytes()
    for apply in (False, True):
        report = runner.recoverer().settle(job.job_id, apply=apply)
        assert report.outcome == "nothing-to-settle" and not report.changed
    assert (rrig.state / "integration" / f"{job.job_id}.json").read_bytes() == before and not fake.writes()
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("after-merge-request",))
    before = (rrig.state / "integration" / f"{job.job_id}.json").read_bytes()
    dry = runner.recoverer().settle(job.job_id, apply=False)               # a dry run reads and records nothing
    assert dry.outcome == "would-reconcile"
    assert (rrig.state / "integration" / f"{job.job_id}.json").read_bytes() == before


# -- moved refs, a changed PR, stale approvals, changed configuration, GitHub timeouts ------------------------------

@pytest.mark.parametrize("moved", ["source", "base"])
def test_a_moved_ref_makes_the_job_stale_and_the_worker_stops(rrig: Rig, stages: Any, github_origin: Path, moved: str):
    scheduling(rrig, stages=("validate", "review"))
    job = validated_job(rrig).job_id
    crashed_pins = pins(rrig, job)
    if moved == "source":
        change_on(rrig.repo, BRANCH, {"backend/src/more.py": X_PY})        # one more commit on the source branch
    else:
        advance_dev(github_origin)
    runner, clock = stages(), Clock()
    run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(job)
    assert stored.state is JobState.STALE and stored.attention and runner.stages == ["review"]
    assert pins(rrig, job) == crashed_pins and len(rrig.jobs.list()) == 1  # never re-based, never recreated
    clock.advance(86400)
    assert not poll(rrig, runner, clock).started


def test_a_changed_pull_request_makes_the_job_stale(rrig: Rig, stages: Any, launchers: Any, github_origin: Path):
    scheduling(rrig)
    sha = change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY})
    fake = FakeMergeGitHub(github_origin, [pr_json(7, head_sha=sha)])
    from fc_integration_rig import live_client  # pylint: disable=import-outside-toplevel
    rrig.client = live_client(fake, integration=rrig.config.github.integration)
    job = rrig.approved(request_for(rrig.repo, sha, pr_number=7)).job_id
    runner, clock = stages(fake=fake, hold=("review",)), Clock()
    for _ in range(6):
        poll(rrig, runner, clock)
    assert runner.stages == ["inspect", "validate", "review"] and rrig.state_of(job) is JobState.VALIDATING
    fake.prs[0]["base"]["ref"] = "main"                                    # someone retargets the pinned PR meanwhile
    runner.release(job)
    run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(job)
    assert stored.state is JobState.STALE and "now targets" in stored.attention["reason"] and not fake.writes()
    assert not [e for e in stored.evidence if e["kind"] == "review"]       # no decision for a job that went stale


def test_a_revoked_approval_stops_the_worker_with_the_reason(rrig: Rig, stages: Any, key: OperatorKey):
    scheduling(rrig, stages=("validate", "review"), retry_seconds=1, max_retry_seconds=1, max_stalls=2)
    job = validated_job(rrig).job_id
    key.revoke_all()                                                       # the operator's key is withdrawn
    runner, clock = stages(), Clock()
    for _ in range(8):
        poll(rrig, runner, clock)
        clock.advance(5)
    rec = schedule(rrig, job)
    assert rrig.state_of(job) is JobState.VALIDATING and runner.stages == ["review", "review"]
    assert rec.hold and "`no-approval`" in rec.hold["reason"]
    assert not [e for e in evidence(rrig, job) if e["kind"].startswith("review")]   # no reviewer ran, nothing recorded


def test_a_changed_gate_plan_is_validated_again_before_any_review(rrig: Rig, stages: Any):
    scheduling(rrig, stages=("validate", "review"))
    job = validated_job(rrig).job_id
    assert step_of(rrig, job).stage == "review"
    gates = dict(rrig.config.github.intake.role_gates)
    with_config(rrig, role_gates={**gates, "backend": (*gates["backend"], "frontend-build")})   # the policy changed
    scheduling(rrig, stages=("validate", "review"))
    assert step_of(rrig, job).stage == "validate"                          # the old validation is not current
    runner, clock = stages(), Clock()
    run_until_quiet(rrig, runner, clock)
    assert runner.stages == ["validate", "review"] and rrig.state_of(job) is JobState.AWAITING_MERGE_APPROVAL
    validations = [e for e in evidence(rrig, job) if e["kind"] == "validation"]
    assert len(validations) == 2 and evidence(rrig, job)[-1]["validation"] == validations[-1]["id"]


def test_a_github_timeout_defers_the_stage_and_it_is_repeated_later(rrig: Rig, stages: Any, monkeypatch: Any):
    scheduling(rrig, stages=("validate", "review"), retry_seconds=30)
    job = inspected(rrig, {"backend/src/x.py": X_PY}).job_id
    real, failures = rrig.client.get_issue, [2]

    def flaky(number: int) -> Any:
        if failures[0] > 0:
            failures[0] -= 1
            raise RemoteUnavailableError("GitHub: timeout")
        return real(number)
    monkeypatch.setattr(rrig.client, "get_issue", flaky)
    runner, clock = stages(), Clock()
    before = evidence(rrig, job)
    run_until_quiet(rrig, runner, clock)
    assert rrig.state_of(job) is JobState.INSPECTING and evidence(rrig, job) == before   # deferred: nothing recorded
    assert schedule(rrig, job).stalls == 1 and view_of(rrig, job, clock)["category"] == "delayed"
    for _ in range(3):
        clock.advance(3600)
        run_until_quiet(rrig, runner, clock)
    assert rrig.state_of(job) is JobState.AWAITING_MERGE_APPROVAL and schedule(rrig, job).stalls == 0


# -- repair routing; a replacement job carries nothing over -----------------------------------------------------------

def test_a_failed_gate_routes_the_repair_to_the_owning_roles_and_the_operator(rrig: Rig):
    job = inspected(rrig, {"backend/src/x.py": X_PY, "cli/src/kiddo_cli/y.py": X_PY}).job_id
    first = rrig.validate(job, apply=False).gates[0]["gate"]
    assert rrig.validate(job, gates=FakeGates({first: "failed"})).outcome == "failed"
    stored = rrig.jobs.load(job)
    assert stored.state is JobState.NEEDS_REPAIR
    routing = repair_routing(stored, rrig.config)
    assert routing["cause"] == "validation" and any(f"gate {first} FAILED" in text for text in routing["findings"])
    assert set(routing["roles"]) <= {"backend", "cli"} and routing["roles"]
    assert all(path.startswith(("backend/", "cli/")) for info in routing["roles"].values() for path in info["paths"])
    assert any("Issue #67" in text for text in routing["next"]) and any("NEW `integration intake`" in text
                                                                       for text in routing["next"])
    text = "\n".join(diagnosis_lines(diagnose(stored, rrig.config)))
    assert "repair routing (validation)" in text and "URGENT" in text


def test_requested_changes_route_by_the_findings_files(rrig: Rig, launchers: Any):
    job = validated_job(rrig).job_id
    assert review(rrig, job, launchers("changes")).outcome == "changes-requested"
    stored = rrig.jobs.load(job)
    routing = repair_routing(stored, rrig.config)
    assert routing["cause"] == "review" and routing["findings"] and stored.state is JobState.NEEDS_REPAIR
    assert set(routing["roles"]) | ({"operator"} if routing["operator"]["count"] else set())
    assert repair_routing(rrig.jobs.load(validated_job_other(rrig)), rrig.config) is None   # only for NEEDS_REPAIR


def validated_job_other(rig: Rig) -> str:
    sha = change_on(rig.repo, "integration/other", {"cli/src/kiddo_cli/y.py": X_PY})
    return rig.approved(request_for(rig.repo, sha, source_ref="integration/other")).job_id


def test_a_repaired_source_is_a_new_job_and_no_approval_or_evidence_moves_to_it(rrig: Rig, stages: Any):
    scheduling(rrig)
    old = approved_job(rrig)
    runner, clock = stages(gates=FakeGates({"backend-tests": "failed"})), Clock()
    run_until_quiet(rrig, runner, clock)
    stored = rrig.jobs.load(old)
    assert stored.state is JobState.NEEDS_REPAIR
    repaired = change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY + "FIXED = True\n"})   # a repair commit
    # the Worker never recreates the job, and while the old one is open the branch cannot be taken twice
    clock.advance(86400)
    assert not poll(rrig, runner, clock).started and len(rrig.jobs.list()) == 1
    with pytest.raises(IntegrationError, match="abort"):
        rrig.intake(request_for(rrig.repo, repaired))
    abort_job(rrig.jobs, old, "repaired in a new commit", actor="spyderbg", apply=True)
    new = rrig.accepted(request_for(rrig.repo, repaired))
    assert new.job_id != old and new.source["sha"] == repaired != stored.source["sha"]
    assert new.approvals == [] and new.evidence == [] and new.candidate is None      # nothing was carried over
    old_record = rrig.write_record(record_for(stored))                              # the OLD job's signed approval …
    result = rrig.approve(new.job_id, path=old_record)
    assert result["outcome"] != "recorded" and rrig.jobs.load(new.job_id).approvals == []   # … authorizes nothing here
    assert approval_binding_problems(record_for(stored), new)
    assert step_of(rrig, new.job_id).who == BY_OPERATOR and not runner.started(new.job_id)  # waits for its OWN approval
    final_old = rrig.jobs.load(old)
    assert final_old.state is JobState.ABORTED and final_old.evidence == stored.evidence   # history kept, untouched
    # with its own approval the new job gets its own inspection, candidate, validation and review
    assert rrig.approve(new.job_id)["outcome"] == "recorded"
    runner.gates = FakeGates()
    run_until_quiet(rrig, runner, clock)
    done = rrig.jobs.load(new.job_id)
    assert done.state is JobState.AWAITING_MERGE_APPROVAL
    assert done.candidate["head_sha"] != stored.candidate["head_sha"]
    assert all(e["source_sha"] == repaired for e in done.evidence if "source_sha" in e)
    assert not {e["sha256"] for e in done.evidence if "sha256" in e} & {e["sha256"] for e in stored.evidence
                                                                       if "sha256" in e}
