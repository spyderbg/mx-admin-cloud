"""W4.7 (M1): the integration job record, its lifecycle state machine and its persistence (integration.py).

Temporary state directories only (the conftest fixtures): no Git repository, no network, no GitHub."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from feature_coordinator import integration as integ
from feature_coordinator.errors import IllegalTransitionError, StateConflictError, StateDirError
from feature_coordinator.integration import (
    ABORTABLE_STATES,
    JOB_TRANSITIONS,
    TERMINAL_STATES,
    IntakeRequest,
    IntegrationError,
    IntegrationJob,
    IntegrationJobs,
    JobState,
    abort_job,
    check_transition,
    job_summary,
    transition_job,
)
from feature_coordinator.store import StateStore

from fc_helpers import run_py

S = JobState
SRC = "a" * 40
BASE = "b" * 40
OTHER = "c" * 40

# The design's table (§4), verbatim, plus ABORTED from every non-terminal state before MERGED (§4, below the table).
# NEEDS_REPAIR → INTAKE is a NEW job (§6.4), not an edge (see integration.py and integration-jobs.md).
DESIGN_EDGES = {
    S.INTAKE: {S.AWAITING_APPROVAL, S.REFUSED},
    S.AWAITING_APPROVAL: {S.INSPECTING, S.REJECTED, S.EXPIRED, S.STALE},
    S.INSPECTING: {S.CANDIDATE_BUILT, S.REFUSED, S.NEEDS_REPAIR, S.STALE},
    S.CANDIDATE_BUILT: {S.VALIDATING, S.REFUSED, S.STALE},
    S.VALIDATING: {S.IN_REVIEW, S.NEEDS_REPAIR, S.FAILED, S.STALE},
    S.IN_REVIEW: {S.AWAITING_MERGE_APPROVAL, S.NEEDS_REPAIR, S.FAILED, S.STALE},
    S.AWAITING_MERGE_APPROVAL: {S.MERGED, S.REJECTED, S.STALE},
    S.MERGED: {S.COMPLETED, S.FAILED},
    S.NEEDS_REPAIR: {S.STALE},
}
PERMITTED = sorted(((a, b) for a in JobState for b in JOB_TRANSITIONS[a]), key=lambda e: (e[0].value, e[1].value))
FORBIDDEN = sorted(((a, b) for a in JobState for b in JobState if a is not b and b not in JOB_TRANSITIONS[a]),
                   key=lambda e: (e[0].value, e[1].value))


def request(**changes) -> IntakeRequest:
    fields = {"source_repo": "spyderbg/mx-admin-cloud", "source_ref": "integration/improvements",
              "source_sha": SRC, "base_ref": "dev", "base_sha": BASE, "issue": 63, "pr_number": None}
    fields.update(changes)
    return IntakeRequest(**fields)


def new_job(job_id: str = "int-20261111-0001", **changes) -> IntegrationJob:
    return IntegrationJob.new(job_id, request(**changes), actor="operator")


NOW = "2026-11-11T10:00:00.000000Z"


def provenance() -> dict:
    return {"authors": ["A <a@example.invalid>"], "committers": [], "commits": 1, "head_commit": SRC,
            "history": {"boundary_parents": 1, "base_commits_read": 1},
            "origin": {"repo": "spyderbg/mx-admin-cloud", "ref": "integration/improvements",
                       "resolved_via": "git ls-remote origin", "at": NOW}}


def attention(reason: str = "bad input") -> dict:
    return {"reason": reason, "reasons": [reason], "observed": {}, "since": NOW}


def job_in(state: JobState) -> IntegrationJob:
    """A job walked along permitted edges to *state* (with the facts the guards ask for)."""
    paths = {S.INTAKE: [], S.AWAITING_APPROVAL: [S.AWAITING_APPROVAL],
             S.INSPECTING: [S.AWAITING_APPROVAL, S.INSPECTING],
             S.CANDIDATE_BUILT: [S.AWAITING_APPROVAL, S.INSPECTING, S.CANDIDATE_BUILT],
             S.VALIDATING: [S.AWAITING_APPROVAL, S.INSPECTING, S.CANDIDATE_BUILT, S.VALIDATING],
             S.IN_REVIEW: [S.AWAITING_APPROVAL, S.INSPECTING, S.CANDIDATE_BUILT, S.VALIDATING, S.IN_REVIEW],
             S.NEEDS_REPAIR: [S.AWAITING_APPROVAL, S.INSPECTING, S.NEEDS_REPAIR]}
    paths[S.AWAITING_MERGE_APPROVAL] = paths[S.IN_REVIEW] + [S.AWAITING_MERGE_APPROVAL]
    paths[S.MERGED] = paths[S.AWAITING_MERGE_APPROVAL] + [S.MERGED]
    paths[S.COMPLETED] = paths[S.MERGED] + [S.COMPLETED]
    for terminal, via in ((S.REFUSED, [S.REFUSED]), (S.REJECTED, [S.AWAITING_APPROVAL, S.REJECTED]),
                          (S.EXPIRED, [S.AWAITING_APPROVAL, S.EXPIRED]), (S.STALE, [S.AWAITING_APPROVAL, S.STALE]),
                          (S.ABORTED, [S.ABORTED]), (S.FAILED, paths[S.MERGED] + [S.FAILED])):
        paths[terminal] = via
    job = new_job()
    job.provenance = provenance()
    job.merge_result = {"commit_sha": OTHER, "parents": [BASE, SRC], "tree_sha": OTHER, "verified": True}
    for step in paths[state]:
        assert transition_job(job, step, f"to {step.value}", actor="test")
    assert job.state is state
    return job


@pytest.fixture
def jobs(store: StateStore) -> IntegrationJobs:
    return IntegrationJobs(store)


def saved(jobs: IntegrationJobs, job: IntegrationJob) -> IntegrationJob:
    with jobs.store.transaction():
        jobs.save(job)
    return jobs.load(job.job_id)


# -- the state machine -----------------------------------------------------------------------------

def test_transition_table_is_the_design_table_plus_operator_abort():
    for state in JobState:
        expected = DESIGN_EDGES.get(state, set()) | ({S.ABORTED} if state in ABORTABLE_STATES else set())
        assert set(JOB_TRANSITIONS[state]) == expected, state
    assert TERMINAL_STATES == {S.COMPLETED, S.REFUSED, S.REJECTED, S.EXPIRED, S.FAILED, S.ABORTED, S.STALE}
    assert ABORTABLE_STATES == set(JobState) - TERMINAL_STATES - {S.MERGED}


def test_intake_has_no_stale_transition_and_merged_cannot_be_aborted():
    assert S.STALE not in JOB_TRANSITIONS[S.INTAKE]  # nothing is pinned yet: a mismatch is REFUSED
    assert S.STALE not in JOB_TRANSITIONS[S.MERGED] and S.ABORTED not in JOB_TRANSITIONS[S.MERGED]
    assert S.INTAKE not in JOB_TRANSITIONS[S.NEEDS_REPAIR]  # a repair is a NEW job, never a reopened one


@pytest.mark.parametrize(("current", "target"), PERMITTED, ids=lambda s: s.value)
def test_every_permitted_transition_is_applied_and_recorded(current: JobState, target: JobState):
    job = job_in(current)
    length = len(job.history)
    assert transition_job(job, target, "permitted", actor="test") is True
    assert job.state is target
    assert len(job.history) == length + 1
    assert job.history[-1] == {"from": current.value, "to": target.value, "at": job.history[-1]["at"],
                               "reason": "permitted", "actor": "test"}


@pytest.mark.parametrize(("current", "target"), FORBIDDEN, ids=lambda s: s.value)
def test_every_forbidden_transition_is_refused_without_change(current: JobState, target: JobState):
    job = job_in(current)
    before = job.to_dict()
    with pytest.raises(IllegalTransitionError):
        transition_job(job, target, "forbidden", actor="test")
    assert job.to_dict() == before


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda s: s.value), ids=lambda s: s.value)
def test_terminal_states_are_never_reopened(terminal: JobState):
    job = job_in(terminal)
    for target in set(JobState) - {terminal}:
        with pytest.raises(IllegalTransitionError, match="terminal|not allowed"):
            transition_job(job, target, "reopen", actor="test")
    assert transition_job(job, terminal, "again", actor="test") is False  # same state: a no-op
    assert job.state is terminal


def test_guards_awaiting_approval_merged_and_completed():
    job = new_job()
    with pytest.raises(IllegalTransitionError, match="provenance"):
        transition_job(job, S.AWAITING_APPROVAL, "x", actor="t")
    job = job_in(S.AWAITING_MERGE_APPROVAL)
    job.merge_result = None
    with pytest.raises(IllegalTransitionError, match="merge commit"):
        transition_job(job, S.MERGED, "x", actor="t")
    job = job_in(S.MERGED)
    job.merge_result = {"commit_sha": OTHER, "verified": False}
    with pytest.raises(IllegalTransitionError, match="verified"):
        transition_job(job, S.COMPLETED, "x", actor="t")


def test_check_transition_message_names_allowed_states():
    with pytest.raises(IllegalTransitionError, match="AWAITING_APPROVAL → MERGED is not allowed"):
        check_transition(S.AWAITING_APPROVAL, S.MERGED)


# -- the request: strict validation --------------------------------------------------------------------

@pytest.mark.parametrize("sha", ["a" * 39, "a" * 41, "A" * 40, "g" * 40, "abc123", "", "HEAD", "dev", " " + "a" * 39,
                                 "a" * 40 + "\n", None, 1234])
def test_invalid_source_and_base_shas_are_refused(sha):
    with pytest.raises(IntegrationError, match="40-character"):
        request(source_sha=sha)
    with pytest.raises(IntegrationError, match="40-character"):
        request(base_sha=sha)


@pytest.mark.parametrize("ref", ["", "-x", "refs/heads/x", "a..b", "x.lock", ".hidden", "a//b", "a/", "HEAD",
                                 "with space", "x~1", "x^", "a:b", "@{u}", "x/.y"])
def test_invalid_refs_are_refused(ref):
    with pytest.raises(IntegrationError):
        request(source_ref=ref)


@pytest.mark.parametrize("repo", ["", "noslash", "a/b/c", "/x", "owner/", "owner/name.git", "o wner/x"])
def test_invalid_repositories_are_refused(repo):
    with pytest.raises(IntegrationError, match="owner/name"):
        request(source_repo=repo)


@pytest.mark.parametrize(("name", "value"), [("issue", 0), ("issue", -1), ("issue", True), ("issue", "64"),
                                             ("pr_number", 0), ("pr_number", "62")])
def test_invalid_numbers_are_refused(name, value):
    with pytest.raises(IntegrationError, match="positive integer"):
        request(**{name: value})


@pytest.mark.parametrize("missing", ["source_repo", "source_ref", "source_sha", "base_ref", "base_sha", "issue"])
def test_missing_required_fields_are_refused(missing):
    data = {"source_repo": "o/r", "source_ref": "x", "source_sha": SRC, "base_ref": "dev", "base_sha": BASE,
            "issue": 1}
    del data[missing]
    with pytest.raises(IntegrationError, match=f"missing required intake field.*{missing}"):
        IntakeRequest.from_mapping(data)
    with pytest.raises(IntegrationError, match="unknown intake field"):
        IntakeRequest.from_mapping({**data, missing: "x" if missing != "issue" else 1, "extra": 1})


def test_request_key_is_stable_and_distinguishes_every_input():
    assert request().key == request().key == request(source_repo="SpyderBG/MX-admin-cloud").key
    keys = {request().key, request(source_ref="other").key, request(source_sha=OTHER).key,
            request(base_sha=OTHER).key, request(issue=64).key, request(pr_number=62).key}
    assert len(keys) == 6


# -- the record -------------------------------------------------------------------------------------

def test_valid_job_creation_captures_immutable_inputs():
    job = new_job(pr_number=62)
    assert job.state is S.INTAKE and job.revision == 0
    assert job.source == {"repo": "spyderbg/mx-admin-cloud", "ref": "integration/improvements", "sha": SRC}
    assert job.base == {"ref": "dev", "expected_sha": BASE}
    assert job.pr == {"number": 62} and job.issue == 63
    assert job.history == [{"from": None, "to": "INTAKE", "at": job.created_at, "reason": "intake request recorded",
                            "actor": "operator"}]
    assert job.request() == request(pr_number=62)
    stored = {**job.to_dict(), "revision": 1}  # as the store writes it
    assert IntegrationJob.from_dict(stored).to_dict() == stored


@pytest.mark.parametrize("job_id", ["int-2026111-0001", "INT-20261111-0001", "int-20261111-1", "../x", "x"])
def test_invalid_job_ids_are_refused(job_id):
    with pytest.raises(IntegrationError, match="job ID"):
        new_job(job_id)


def test_status_serialization_is_json_and_complete():
    job = job_in(S.AWAITING_APPROVAL)
    data = json.loads(json.dumps(job_summary(job)))
    assert data["state"] == "AWAITING_APPROVAL" and data["terminal"] is False
    assert data["allowed_next"] == ["ABORTED", "EXPIRED", "INSPECTING", "REJECTED", "STALE"]
    assert set(data) >= {"job_id", "issue", "source", "base", "pr", "provenance", "candidate", "merge_result", "paths",
                         "evidence", "approvals", "history", "created_at", "updated_at", "schema_version"}
    assert job_summary(job_in(S.ABORTED))["allowed_next"] == []


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version=2),
    lambda d: d.update(extra=1),
    lambda d: d.pop("history"),
    lambda d: d.update(state="DONE"),
    lambda d: d["source"].update(sha="abc"),
    lambda d: d["source"].update(extra="x"),
    lambda d: d.update(request_key="0" * 64),
    lambda d: d.update(history=[]),
    lambda d: d["history"].append({"from": "AWAITING_APPROVAL", "to": "MERGED", "at": "x", "reason": "r",
                                   "actor": "a"}),
    lambda d: d.update(state="ABORTED"),  # history does not end there
])
def test_malformed_records_are_refused_not_repaired(mutate):
    data = {**job_in(S.AWAITING_APPROVAL).to_dict(), "revision": 1}  # valid as stored: only *mutate* breaks it
    IntegrationJob.from_dict(json.loads(json.dumps(data)))
    mutate(data)
    with pytest.raises(StateDirError):
        IntegrationJob.from_dict(data)


# -- persistence ----------------------------------------------------------------------------------------

def test_saved_job_round_trips_and_ids_are_sequential(jobs: IntegrationJobs, store: StateStore):
    with store.transaction():
        first = jobs.next_id()
        jobs.save(IntegrationJob.new(first, request(), actor="operator"))
        second = jobs.next_id()
    assert first.endswith("-0001") and second.endswith("-0002") and first[:12] == second[:12]
    loaded = jobs.load(first)
    assert loaded.revision == 1 and loaded.request() == request()
    assert (store.root / "integration" / f"{first}.json").is_file()
    assert [job.job_id for job in jobs.list()] == [first]


def test_mutations_need_the_coordinator_transaction(jobs: IntegrationJobs):
    with pytest.raises(StateDirError, match="transaction"):
        jobs.save(new_job())


def test_a_new_job_never_replaces_an_existing_record(jobs: IntegrationJobs):
    saved(jobs, new_job())
    with pytest.raises(StateConflictError, match="already exists"):
        saved(jobs, new_job(source_ref="other"))
    assert jobs.load("int-20261111-0001").source["ref"] == "integration/improvements"


def test_immutable_fields_and_append_only_history_are_enforced_on_save(jobs: IntegrationJobs):
    job = saved(jobs, new_job())
    for change in (lambda j: j.source.update(sha=OTHER), lambda j: j.base.update(expected_sha=OTHER),
                   lambda j: setattr(j, "issue", 99), lambda j: setattr(j, "supersedes", "int-20261111-0009"),
                   lambda j: setattr(j, "pr", {"number": 5})):
        copy = jobs.load(job.job_id)
        change(copy)
        with pytest.raises(IntegrationError, match="immutable"):
            saved(jobs, copy)
    copy = jobs.load(job.job_id)
    copy.provenance = provenance()
    transition_job(copy, S.AWAITING_APPROVAL, "ok", actor="t")
    saved(jobs, copy)
    rewritten = jobs.load(job.job_id)
    rewritten.history[0]["reason"] = "rewritten"
    with pytest.raises(IntegrationError, match="append-only"):
        saved(jobs, rewritten)
    assert jobs.load(job.job_id).history[0]["reason"] == "intake request recorded"


def test_a_terminal_record_is_final(jobs: IntegrationJobs):
    saved(jobs, new_job())
    jobs.transition("int-20261111-0001", S.ABORTED, "stop", actor="op")
    job = jobs.load("int-20261111-0001")
    job.attention = attention("late edit")
    with pytest.raises(IntegrationError, match="terminal"):
        saved(jobs, job)


def test_stale_in_memory_copy_is_refused(jobs: IntegrationJobs):
    saved(jobs, new_job())
    first, second = jobs.load("int-20261111-0001"), jobs.load("int-20261111-0001")
    first.deferred = {"reason": "a", "at": NOW}
    saved(jobs, first)
    second.deferred = {"reason": "b", "at": NOW}
    with pytest.raises(StateConflictError, match="concurrently"):
        saved(jobs, second)
    assert jobs.load("int-20261111-0001").deferred["reason"] == "a"


def test_transition_helper_is_idempotent_and_respects_expectations(jobs: IntegrationJobs, store: StateStore):
    saved(jobs, new_job())
    job, changed = jobs.transition("int-20261111-0001", S.REFUSED, "bad input", actor="op", expect=(S.INTAKE,),
                                   attention=attention())
    assert changed and job.state is S.REFUSED and job.attention == attention()
    again, changed = jobs.transition("int-20261111-0001", S.REFUSED, "bad input", actor="op", expect=(S.INTAKE,))
    assert not changed and again.revision == job.revision  # repeated: no write, no second history entry
    other, changed = jobs.transition("int-20261111-0001", S.AWAITING_APPROVAL, "late", actor="op", expect=(S.INTAKE,))
    assert not changed and other.state is S.REFUSED  # decided on INTAKE, but it is REFUSED now: nothing happens
    with pytest.raises(IllegalTransitionError):
        jobs.transition("int-20261111-0001", S.ABORTED, "x", actor="op")
    events = [e for e in store.events() if e["kind"] == "integration-transition"]
    assert [(e["from"], e["to"]) for e in events] == [("INTAKE", "REFUSED")]
    saved(jobs, new_job("int-20261111-0002", source_ref="other"))
    with pytest.raises(IntegrationError, match="cannot be set"):
        jobs.transition("int-20261111-0002", S.REFUSED, "x", actor="op", candidate={"head_sha": SRC})
    assert jobs.load("int-20261111-0002").state is S.INTAKE


def test_persistence_across_process_instances(state_dir: Path):
    code = """
        import sys
        from feature_coordinator.store import StateStore
        from feature_coordinator.integration import IntakeRequest, IntegrationJob, IntegrationJobs, JobState
        store = StateStore(__import__('pathlib').Path(sys.argv[1]))
        jobs = IntegrationJobs(store)
        if sys.argv[2] == "create":
            req = IntakeRequest("spyderbg/mx-admin-cloud", "x", "a" * 40, "dev", "b" * 40, 1)
            with store.transaction():
                jobs.save(IntegrationJob.new(jobs.next_id(), req, actor="p1"))
        elif sys.argv[2] == "abort":
            jobs.transition(sys.argv[3], JobState.ABORTED, "from another process", actor="p2")
        print(";".join(f"{j.job_id}={j.state.value}@{j.revision}" for j in jobs.list()))
    """
    created = run_py(code, str(state_dir), "create")
    assert created.returncode == 0, created.stderr
    job_id = created.stdout.strip().split("=")[0]
    aborted = run_py(code, str(state_dir), "abort", job_id)
    assert aborted.returncode == 0, aborted.stderr
    assert aborted.stdout.strip() == f"{job_id}=ABORTED@2"
    job = IntegrationJobs(StateStore(state_dir)).load(job_id)  # this process: a third instance
    assert [h["actor"] for h in job.history] == ["p1", "p2"]


def test_interrupted_write_leaves_the_previous_record(jobs: IntegrationJobs, monkeypatch: pytest.MonkeyPatch):
    saved(jobs, new_job())
    path = jobs.store.root / "integration" / "int-20261111-0001.json"
    before = path.read_bytes()

    def crash(*_args, **_kwargs):
        raise OSError("disk died during rename")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError, match="disk died"):
        jobs.transition("int-20261111-0001", S.ABORTED, "stop", actor="op")
    monkeypatch.undo()
    assert path.read_bytes() == before  # the old record, never a torn one
    assert not list(path.parent.glob(".*.tmp"))  # the temporary file was removed
    assert jobs.load("int-20261111-0001").state is S.INTAKE
    jobs.transition("int-20261111-0001", S.ABORTED, "stop", actor="op")  # the retry succeeds once
    assert jobs.load("int-20261111-0001").state is S.ABORTED


def test_leftover_temp_files_are_ignored_and_corrupt_records_fail_closed(jobs: IntegrationJobs):
    saved(jobs, new_job())
    folder = jobs.store.root / "integration"
    (folder / ".int-20261111-0002.json.x1y2.tmp").write_text('{"torn": ', encoding="utf-8")  # a crashed writer
    assert [job.job_id for job in jobs.list()] == ["int-20261111-0001"]
    (folder / "int-20261111-0003.json").write_text('{"job_id": "int-20261111-0003", "sta', encoding="utf-8")
    with pytest.raises(StateDirError, match="corrupt"):
        jobs.list()
    with pytest.raises(StateDirError, match="corrupt"):
        jobs.load("int-20261111-0003")
    with pytest.raises(IntegrationError, match="unknown integration job"):
        jobs.load("int-20261111-0004")


# -- malformed persisted records (Codex review of M1, finding 4) ------------------------------------------------

def stored(jobs: IntegrationJobs, state: JobState = S.AWAITING_APPROVAL) -> tuple[Path, dict]:
    """A valid record of a job in *state* on disk, and its raw JSON."""
    job = saved(jobs, job_in(state))
    path = jobs.store.root / "integration" / f"{job.job_id}.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def history_of(data: dict) -> list:
    return data["history"]


MALFORMED = {
    "revision-zero": lambda d: d.update(revision=0),
    "revision-negative": lambda d: d.update(revision=-3),
    "revision-text": lambda d: d.update(revision="1"),
    "revision-bool": lambda d: d.update(revision=True),
    "revision-float": lambda d: d.update(revision=1.0),
    "revision-null": lambda d: d.update(revision=None),
    "schema-bool": lambda d: d.update(schema_version=True),
    "state-list": lambda d: d.update(state=["AWAITING_APPROVAL"]),
    "issue-text": lambda d: d.update(issue="63"),
    "job-id-number": lambda d: d.update(job_id=1),
    "source-list": lambda d: d.update(source=["spyderbg/mx-admin-cloud"]),
    "base-sha-uppercase": lambda d: d["base"].update(expected_sha="B" * 40),
    "pr-number-text": lambda d: d.update(pr={"number": "7"}),
    "pr-partially-verified": lambda d: d.update(pr={"number": 7, "head_sha": SRC}),
    "pr-list": lambda d: d.update(pr=[7]),
    "request-key-number": lambda d: d.update(request_key=5),
    "supersedes-itself": lambda d: d.update(supersedes=d["job_id"]),
    "provenance-text": lambda d: d.update(provenance="trusted"),
    "provenance-missing-authors": lambda d: d["provenance"].pop("authors"),
    "provenance-authors-text": lambda d: d["provenance"].update(authors="A <a@example.invalid>"),
    "provenance-other-head": lambda d: d["provenance"].update(head_commit=OTHER),
    "provenance-zero-commits": lambda d: d["provenance"].update(commits=0),
    "provenance-history-text": lambda d: d["provenance"].update(history="verified"),
    "provenance-origin-bad-time": lambda d: d["provenance"]["origin"].update(at="yesterday"),
    "provenance-null-after-approval": lambda d: d.update(provenance=None),
    "attention-text": lambda d: d.update(attention="look"),
    "attention-partial": lambda d: d.update(attention={"reason": "look"}),
    "deferred-outside-intake": lambda d: d.update(deferred={"reason": "late", "at": NOW}),
    "deferred-bad-time": lambda d: d.update(deferred={"reason": "late", "at": "t"}),
    "paths-of-text": lambda d: d.update(paths=["backend/x.py"]),
    "candidate-list": lambda d: d.update(candidate=[]),
    "created-at-bad": lambda d: d.update(created_at="yesterday"),
    "updated-at-number": lambda d: d.update(updated_at=5),
    "history-not-a-list": lambda d: d.update(history={"0": history_of(d)[0]}),
    "history-entry-text": lambda d: d.update(history=["INTAKE", "AWAITING_APPROVAL"]),
    "history-bad-time": lambda d: history_of(d)[0].update(at="t"),
    "history-actor-number": lambda d: history_of(d)[0].update(actor=7),
    "history-reason-too-long": lambda d: history_of(d)[1].update(reason="x" * 600),
    "history-unknown-state": lambda d: history_of(d)[1].update(to="DONE"),
    "history-reordered": lambda d: d.update(history=list(reversed(history_of(d)))),
    "history-duplicated": lambda d: d.update(history=history_of(d) + [history_of(d)[-1]]),
    "history-first-entry-dropped": lambda d: d.update(history=history_of(d)[1:]),
    "history-skips-a-state": lambda d: history_of(d)[1].update(**{"from": "INSPECTING"}),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_persisted_records_fail_closed_on_load_and_list(jobs: IntegrationJobs, name: str):
    path, data = stored(jobs)
    MALFORMED[name](data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError):
        jobs.load(data["job_id"] if isinstance(data["job_id"], str) else "int-20261111-0001")
    with pytest.raises(StateDirError):
        jobs.list()


@pytest.mark.parametrize("content", ["[]", "[1, 2]", '"text"', "42", "true", "null"])
def test_a_record_that_is_not_a_json_object_fails_closed(jobs: IntegrationJobs, content: str):
    folder = jobs.store.root / "integration"
    folder.mkdir(parents=True)
    (folder / "int-20261111-0001.json").write_text(content, encoding="utf-8")
    with pytest.raises(StateDirError, match="JSON object"):
        jobs.load("int-20261111-0001")
    with pytest.raises(StateDirError, match="JSON object"):
        jobs.list()


def test_corrupted_history_of_a_terminal_or_merged_job_fails_closed(jobs: IntegrationJobs):
    path, data = stored(jobs, S.ABORTED)
    data["history"].append({"from": "ABORTED", "to": "INTAKE", "at": NOW, "reason": "reopen", "actor": "x"})
    data["state"] = "INTAKE"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match="terminal"):
        jobs.load(data["job_id"])
    path.unlink()
    path, data = stored(jobs, S.MERGED)
    data["merge_result"] = None
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match="merge commit"):
        jobs.load(data["job_id"])


def test_a_record_under_another_jobs_file_name_or_a_stray_file_fails_closed(jobs: IntegrationJobs):
    path, _data = stored(jobs)
    folder = path.parent
    (folder / "int-20261111-0002.json").write_bytes(path.read_bytes())
    with pytest.raises(StateDirError, match="holds job int-20261111-0001"):
        jobs.load("int-20261111-0002")
    with pytest.raises(StateDirError):
        jobs.list()
    (folder / "int-20261111-0002.json").unlink()
    for stray in ("notes.json", "Bad Name.json"):
        (folder / stray).write_text("{}", encoding="utf-8")
        with pytest.raises(StateDirError, match="not an integration job record"):
            jobs.list()
        (folder / stray).unlink()
    assert [job.job_id for job in jobs.list()] == ["int-20261111-0001"]


@pytest.mark.parametrize(("change", "match"), [
    (lambda j: setattr(j, "deferred", {"reason": "late", "at": NOW}), "only recorded while the job is in INTAKE"),
    (lambda j: setattr(j, "attention", "look"), "attention must have exactly"),
    (lambda j: setattr(j, "provenance", {"authors": []}), "provenance must have exactly"),
    (lambda j: setattr(j, "candidate", []), "candidate must be null or an object"),
    (lambda j: setattr(j, "paths", [None]), "paths must be a list of objects"),
], ids=["deferred-outside-intake", "attention-text", "provenance-partial", "candidate-list", "paths-none"])
def test_save_applies_the_same_invariants_and_writes_nothing_invalid(jobs: IntegrationJobs, change, match: str):
    path, _data = stored(jobs)
    before = path.read_bytes()
    job = jobs.load("int-20261111-0001")
    change(job)
    with pytest.raises(IntegrationError, match=match):
        saved(jobs, job)
    assert path.read_bytes() == before


@pytest.mark.parametrize("content", ["[]", '"text"', "null", '{"job_id": "int-20261111-0001"}', '{"torn": ',
                                     "BAD-REVISION"])
def test_cli_reports_corrupt_state_as_a_json_error_never_a_success(state_dir: Path, content: str,
                                                                   capsys: pytest.CaptureFixture[str]):
    from feature_coordinator.cli import main as cli_main  # pylint: disable=import-outside-toplevel
    jobs = IntegrationJobs(StateStore(state_dir))
    if content == "BAD-REVISION":
        path, data = stored(jobs)
        path.write_text(json.dumps({**data, "revision": "2"}), encoding="utf-8")
    else:
        folder = state_dir / "integration"
        folder.mkdir(parents=True)
        (folder / "int-20261111-0001.json").write_text(content, encoding="utf-8")
    before = sorted((p.name, p.read_bytes()) for p in state_dir.rglob("*") if p.is_file())
    for argv in (["status"], ["status", "--job", "int-20261111-0001"],
                 ["abort", "--job", "int-20261111-0001", "--reason", "corrupt?"]):
        assert cli_main(["--state-dir", str(state_dir), "integration", *argv, "--json"]) == 1
        captured = capsys.readouterr()
        answer = json.loads(captured.out)
        assert answer["ok"] is False and answer["error"]["type"] == "StateDirError", answer
        assert "Traceback" not in captured.err and captured.err.startswith("error: ")
        assert cli_main(["--state-dir", str(state_dir), "integration", *argv]) == 1  # plain: `error: …`, exit 1
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err.startswith("error: ")
    assert sorted((p.name, p.read_bytes()) for p in state_dir.rglob("*") if p.is_file()) == before


def test_abort_behaviour(jobs: IntegrationJobs, store: StateStore):
    saved(jobs, new_job())
    preview = abort_job(jobs, "int-20261111-0001", "operator stop", actor="op", apply=False)
    assert preview["outcome"] == "would-abort" and jobs.load("int-20261111-0001").state is S.INTAKE
    done = abort_job(jobs, "int-20261111-0001", "  operator\nstop  ", actor="op", apply=True)
    assert done["outcome"] == "aborted" and done["job"]["state"] == "ABORTED"
    assert done["job"]["history"][-1]["reason"] == "operator stop"
    again = abort_job(jobs, "int-20261111-0001", "operator stop", actor="op", apply=True)
    assert again["outcome"] == "already-aborted" and not again["changed"]
    assert len(jobs.load("int-20261111-0001").history) == 2
    with pytest.raises(IntegrationError, match="non-empty"):
        abort_job(jobs, "int-20261111-0001", "   ", actor="op", apply=True)
    refused = job_in(S.REFUSED)
    refused.job_id = "int-20261111-0002"
    saved(jobs, refused)
    with pytest.raises(IllegalTransitionError, match="terminal"):
        abort_job(jobs, "int-20261111-0002", "x", actor="op", apply=True)
    assert len([e for e in store.events() if e["kind"] == "integration-transition"]) == 1


@pytest.mark.parametrize("state", sorted(ABORTABLE_STATES, key=lambda s: s.value), ids=lambda s: s.value)
def test_abort_from_every_abortable_state(jobs: IntegrationJobs, state: JobState):
    job = job_in(state)
    saved(jobs, job)
    assert abort_job(jobs, job.job_id, "stop", actor="op", apply=True)["outcome"] == "aborted"


@pytest.mark.parametrize("state", [S.MERGED, S.COMPLETED], ids=lambda s: s.value)
def test_abort_after_the_merge_is_refused(jobs: IntegrationJobs, state: JobState):
    saved(jobs, job_in(state))
    with pytest.raises(IllegalTransitionError):
        abort_job(jobs, "int-20261111-0001", "too late", actor="op", apply=True)
    assert jobs.load("int-20261111-0001").state is state


def test_concurrent_aborts_from_many_processes_apply_exactly_once(jobs: IntegrationJobs, state_dir: Path):
    saved(jobs, new_job())
    code = """
        import sys
        from pathlib import Path
        from feature_coordinator.store import StateStore
        from feature_coordinator.integration import IntegrationJobs, abort_job
        result = abort_job(IntegrationJobs(StateStore(Path(sys.argv[1]))), "int-20261111-0001", "race",
                           actor="p" + sys.argv[2], apply=True)
        print(result["outcome"])
    """
    import concurrent.futures  # pylint: disable=import-outside-toplevel
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda n: run_py(code, str(state_dir), str(n)), range(6)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    outcomes = sorted(r.stdout.strip() for r in results)
    assert outcomes.count("aborted") == 1 and outcomes.count("already-aborted") == 5
    job = jobs.load("int-20261111-0001")
    assert job.state is S.ABORTED and len(job.history) == 2 and job.revision == 2


def test_module_has_no_git_or_network_dependency():
    source = Path(integ.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in source and "gitops" not in source and "github_api" not in source
