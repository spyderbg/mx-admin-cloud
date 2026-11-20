"""W4.7 (M5), Codex F1 and F2: the WRITE BOUNDARY of an integration job's two GitHub writes
(integration_authority.py, github_api.GitHubClient.create_/merge_integration_pull_request).

F1 — no caller can obtain a working write by saying things about itself, or by handing the client an authorization
of its own (there is no parameter for one): the real `GitHubClient` over the scripted transport sends nothing unless
the PERSISTED job in the state directory bound to the client (a signed merge approval, the candidate and evidence it
binds, a recorded write-ahead intent naming exactly that operation) authorizes it, and the authorization is consumed by
the dispatch that is recorded before the request leaves.
F2 — the policy is read AGAIN at the write boundary: a configuration, key or operator that changes after the
workflow's own checks stops the write.

Disposable checkout/origin, a throw-away operator key and `FakeMergeGitHub` (real merges on the bare origin only). No
network, no real GitHub, no model."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines

from __future__ import annotations

from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any, Callable

import pytest

from feature_coordinator import integration_authority
from feature_coordinator.errors import IntegrationWriteRefused
from feature_coordinator.integration import JobState, abort_job
from feature_coordinator.integration_authority import IntegrationAnchors
from feature_coordinator.integration_approval import SshSignatureVerifier
from feature_coordinator.integration_merge import IntegrationMerger
from feature_coordinator.issue_sync import SimulatedCrash

from fc_integration_merge_rig import (
    FakeMergeGitHub, anchors_for, approve_merge, bind, merge, merge_record_for, ready_job,
)
from fc_integration_rig import (  # pylint: disable=unused-import
    REPOSITORY, OperatorKey, Rig, change_on, github_origin, live_client, origin, request_for,
)
from test_fc_integration_merge import Clock, existing_pr
from test_fc_integration_review import kinds, launchers, of_kind, review, rrig  # pylint: disable=unused-import
from test_fc_integration_validate import (  # pylint: disable=unused-import
    FakeGates, key, rig, seeded, tracker, with_integration,
)

LATER = timedelta(hours=3)      # the merge approval lasts two hours


def last_intent(rig: Rig, job_id: str, action: str) -> dict[str, Any]:
    return [a for a in of_kind(rig, job_id, "merge-attempt") if a["action"] == action][-1]


def create_intent(rig: Rig, launchers: Any, origin_path: Path, **kwargs: Any) -> tuple[Any, FakeMergeGitHub, dict]:
    """A job whose PR creation is recorded as an INTENT (the process 'crashed' before the write boundary)."""
    job, fake = ready_job(rig, launchers, origin_path, **kwargs)
    with pytest.raises(SimulatedCrash):
        merge(rig, job.job_id, faults=("after-pr-intent",))
    return job, fake, last_intent(rig, job.job_id, "create-pr")


def merge_intent(rig: Rig, launchers: Any, origin_path: Path, **kwargs: Any) -> tuple[Any, FakeMergeGitHub, dict]:
    """A job whose MERGE is recorded as an intent (PR 101 exists, bound; the crash is before the write boundary)."""
    job, fake = ready_job(rig, launchers, origin_path, **kwargs)
    existing_pr(fake, job)
    with pytest.raises(SimulatedCrash):
        merge(rig, job.job_id, faults=("before-merge-request",))
    return job, fake, last_intent(rig, job.job_id, "merge")


def write_merge(rig: Rig, job_id: str, attempt: str, *, number: int = 101, sha: str | None = None,
                client: Any = None) -> Any:
    """What ANY caller holding the coordinator's client (its anchors bound) can do: call its public write method."""
    client = client or bound(rig)
    sha = sha or rig.jobs.load(job_id).source["sha"]
    return client.merge_integration_pull_request(number, sha, job_id=job_id, attempt=attempt)


def write_create(rig: Rig, job_id: str, attempt: str, *, head: str | None = None, base: str | None = None,
                 client: Any = None) -> Any:
    client = client or bound(rig)
    job = rig.jobs.load(job_id)
    return client.create_integration_pull_request(head or job.source["ref"], base or job.base["ref"], "t", "b",
                                                  job_id=job_id, attempt=attempt)


def bound(rig: Rig) -> Any:
    """The rig's client with the rig's trust anchors bound (as `integration merge` binds its own)."""
    bind(rig)
    return rig.client


def unbound(rig: Rig, fake: FakeMergeGitHub, **settings: Any) -> Any:
    """Another client over the same scripted GitHub, with NO anchors (any client but the merge command's)."""
    return live_client(fake, integration=rig.config.github.integration, **settings)


def boundary_clock(monkeypatch: pytest.MonkeyPatch, ahead: timedelta) -> None:
    """Move the write boundary's clock (in-process, for the test only: the boundary reads the real clock)."""
    real = integration_authority.now_utc
    monkeypatch.setattr(integration_authority, "now_utc", lambda: real() + ahead)


def dispatched(rig: Rig, job_id: str, attempt: str | None = None) -> list[dict[str, Any]]:
    return [e for e in of_kind(rig, job_id, "merge-dispatch") if attempt is None or e["attempt"] == attempt]


# -- F1: no write without the persisted authorization -----------------------------------------------------------------

@pytest.mark.parametrize("operation", ["create-pr", "merge"])
def test_f1_a_caller_with_a_client_and_a_job_id_but_no_signed_approval_writes_nothing(
        rrig: Rig, launchers: Any, github_origin: Path, operation: str):
    """The original reproduction: `authorize_integration_write(job-shaped facts)` then PR creation and merge, with no
    signed merge approval and no intent. The grant is gone; the boundary asks the persisted job."""
    job, fake = ready_job(rrig, launchers, github_origin, sign=False)
    assert not [a for a in rrig.jobs.load(job.job_id).approvals if a["stage"] == "merge"]
    before = rrig.jobs.load(job.job_id).to_dict()
    with pytest.raises(IntegrationWriteRefused, match="no effective merge approval|has no merge approval"):
        if operation == "create-pr":
            write_create(rrig, job.job_id, "mat-0001")
        else:
            write_merge(rrig, job.job_id, "mat-0001")
    assert fake.writes() == [] and rrig.jobs.load(job.job_id).to_dict() == before
    assert not hasattr(rrig.client, "authorize_integration_write")


@pytest.mark.parametrize("operation", ["create-pr", "merge"])
def test_f1_a_client_without_anchors_sends_nothing_even_with_everything_signed(
        rrig: Rig, launchers: Any, github_origin: Path, operation: str):
    job, fake, intent = (create_intent if operation == "create-pr" else merge_intent)(rrig, launchers, github_origin)
    other = unbound(rrig, fake)
    with pytest.raises(IntegrationWriteRefused, match="no integration anchors"):
        if operation == "create-pr":
            other.create_integration_pull_request(job.source["ref"], job.base["ref"], "t", "b",
                                                  job_id=job.job_id, attempt=intent["id"])
        else:
            other.merge_integration_pull_request(101, job.source["sha"], job_id=job.job_id, attempt=intent["id"])
    assert fake.writes() == [] and not dispatched(rrig, job.job_id, intent["id"])


def test_f1_the_recorded_intent_authorizes_one_merge_and_a_consumed_attempt_is_refused(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake, intent = merge_intent(rrig, launchers, github_origin)
    response = write_merge(rrig, job.job_id, intent["id"])                    # the exact signed, recorded operation
    assert response.merged and len(fake.merge_requests) == 1
    dispatched = [e for e in of_kind(rrig, job.job_id, "merge-dispatch") if e["attempt"] == intent["id"]]
    assert len(dispatched) == 1 and dispatched[0]["approval"] == intent["approval"]
    assert dispatched[0]["action"] == "merge" and dispatched[0]["head_sha"] == job.candidate["head_sha"]
    with pytest.raises(IntegrationWriteRefused, match="already dispatched"):    # consumed, across authority instances
        write_merge(rrig, job.job_id, intent["id"])
    assert len(fake.merge_requests) == 1


def test_f1_the_recorded_intent_authorizes_one_pr_creation_and_a_consumed_attempt_is_refused(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake, intent = create_intent(rrig, launchers, github_origin)
    created = write_create(rrig, job.job_id, intent["id"])
    assert created.number == 101 and len(fake.created) == 1 and created.draft is False
    assert fake.created[0]["draft"] is False and fake.created[0]["head"] == job.source["ref"]
    with pytest.raises(IntegrationWriteRefused, match="already dispatched"):
        write_create(rrig, job.job_id, intent["id"])
    assert len(fake.created) == 1


@pytest.mark.parametrize("what", ["pr", "sha", "repository", "operation-create"])
def test_f1_a_merge_authorization_cannot_be_redirected(rrig: Rig, launchers: Any, github_origin: Path, what: str):
    job, fake, intent = merge_intent(rrig, launchers, github_origin)
    elsewhere = unbound(rrig, fake, repository="somebody/else")
    elsewhere._bind_integration(anchors_for(rrig))
    attempts: dict[str, Callable[[], Any]] = {
        "pr": partial(write_merge, rrig, job.job_id, intent["id"], number=102),
        "sha": partial(write_merge, rrig, job.job_id, intent["id"], sha="f" * 40),
        "repository": partial(write_merge, rrig, job.job_id, intent["id"], client=elsewhere),
        "operation-create": partial(write_create, rrig, job.job_id, intent["id"]),   # no authorization to create a PR
    }
    with pytest.raises(IntegrationWriteRefused):
        attempts[what]()
    assert fake.writes() == [] and not dispatched(rrig, job.job_id, intent["id"])
    assert write_merge(rrig, job.job_id, intent["id"]).merged               # the intent itself is still good: once


@pytest.mark.parametrize("what", ["head", "base", "operation-merge"])
def test_f1_a_creation_authorization_cannot_be_redirected(rrig: Rig, launchers: Any, github_origin: Path, what: str):
    job, fake, intent = create_intent(rrig, launchers, github_origin)
    attempts: dict[str, Callable[[], Any]] = {
        "head": partial(write_create, rrig, job.job_id, intent["id"], head="feature/other"),
        "base": partial(write_create, rrig, job.job_id, intent["id"], base="main"),
        "operation-merge": partial(write_merge, rrig, job.job_id, intent["id"], number=1),
    }
    with pytest.raises(IntegrationWriteRefused):
        attempts[what]()
    assert fake.writes() == [] and not dispatched(rrig, job.job_id, intent["id"])
    assert write_create(rrig, job.job_id, intent["id"]).number == 101


def test_f1_an_approval_or_attempt_of_another_job_authorizes_nothing(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake, intent = merge_intent(rrig, launchers, github_origin)
    for other in ("int-20990101-0001", "int-20260101-0042"):                   # no such job
        with pytest.raises(IntegrationWriteRefused):
            write_merge(rrig, other, intent["id"], sha=job.source["sha"])
    # a SECOND real job, with its own signed merge approval and no intent: job one's attempt id is nothing to it, and
    # the second job's approval does not make job one's request more authorized
    sha = change_on(rrig.repo, "integration/second", {"backend/src/second.py": '"""S."""\nS = 2\n'})
    second = rrig.approved(request_for(rrig.repo, sha, source_ref="integration/second"))
    assert rrig.inspect(second.job_id).outcome == "passed"
    assert rrig.validate(second.job_id, gates=FakeGates()).outcome == "validated"
    assert review(rrig, second.job_id, launchers("pass")).outcome == "reviewed"
    assert approve_merge(rrig, second.job_id)["outcome"] == "recorded"
    for attempt in (intent["id"], "mat-0001"):
        with pytest.raises(IntegrationWriteRefused, match="intent"):
            write_merge(rrig, second.job_id, attempt, sha=sha)
    assert fake.writes() == [] and not dispatched(rrig, job.job_id)
    assert not of_kind(rrig, second.job_id, "merge-dispatch")


def test_f1_a_valid_approval_without_a_matching_intent_authorizes_nothing(rrig: Rig, launchers: Any,
                                                                         github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)                       # signed, no attempt recorded at all
    existing_pr(fake, job)
    for attempt in ("mat-0001", "mat-9999", "nonsense"):
        with pytest.raises(IntegrationWriteRefused, match="intent"):
            write_merge(rrig, job.job_id, attempt)
        with pytest.raises(IntegrationWriteRefused, match="intent"):
            write_create(rrig, job.job_id, attempt)
    assert fake.writes() == []
    # an intent of the OTHER operation, or another attempt id, is no authorization either
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("before-merge-request",))
    intent = last_intent(rrig, job.job_id, "merge")
    with pytest.raises(IntegrationWriteRefused, match="intent"):
        write_merge(rrig, job.job_id, "mat-0001")
    with pytest.raises(IntegrationWriteRefused, match="intent"):
        write_create(rrig, job.job_id, intent["id"])
    assert fake.writes() == []


REVOCATIONS: list[tuple[str, Callable[[Rig, Any, pytest.MonkeyPatch], Any], str]] = [
    ("key-revoked", lambda rig, job, mp: rig.key.revoke_all(), "signature|key|allowed"),
    ("operator-removed", lambda rig, job, mp: with_integration(rig, operators=()), "operator"),
    ("another-operator-only", lambda rig, job, mp: with_integration(rig, operators=("someone-else",)),
     "not in github.integration.operators"),
    ("integration-disabled", lambda rig, job, mp: with_integration(rig, enabled=False), "enabled is false"),
    ("approval-expired", lambda rig, job, mp: boundary_clock(mp, LATER), "expired"),
    ("aborted", lambda rig, job, mp: abort_job(rig.jobs, job.job_id, "no", actor="operator", apply=True), "ABORTED"),
]


@pytest.mark.parametrize(("name", "revoke", "match"), REVOCATIONS, ids=[n for n, _r, _m in REVOCATIONS])
@pytest.mark.parametrize("operation", ["create-pr", "merge"])
def test_f1_revoking_the_authorization_before_the_write_stops_it(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, operation: str, name: str,
        revoke: Callable[[Rig, Any, pytest.MonkeyPatch], Any], match: str):
    job, fake, intent = (create_intent if operation == "create-pr" else merge_intent)(rrig, launchers, github_origin)
    revoke(rrig, job, monkeypatch)
    with pytest.raises(IntegrationWriteRefused, match=match):
        if operation == "create-pr":
            write_create(rrig, job.job_id, intent["id"])
        else:
            write_merge(rrig, job.job_id, intent["id"])
    assert fake.writes() == [] and not dispatched(rrig, job.job_id, intent["id"])


def test_f1_the_anchors_are_bound_once_and_never_replaced(rrig: Rig, launchers: Any, github_origin: Path,
                                                         tmp_path: Path):
    """A holder of the coordinator's client cannot point it at another state directory or another configuration."""
    job, fake, intent = merge_intent(rrig, launchers, github_origin)
    client = bound(rrig)
    elsewhere = IntegrationAnchors(tmp_path / "forged-state", rrig.repo, configuration=lambda: rrig.config)
    with pytest.raises(IntegrationWriteRefused, match="bound once and never replaced"):
        client._bind_integration(elsewhere)
    assert client._integration_anchors is not elsewhere and fake.writes() == []
    assert write_merge(rrig, job.job_id, intent["id"]).merged                # still the real state directory's intent


def test_f1_a_failing_boundary_fails_closed(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake, intent = merge_intent(rrig, launchers, github_origin)

    def broken() -> Any:
        raise RuntimeError("the policy store exploded")
    client = unbound(rrig, fake)
    client._bind_integration(IntegrationAnchors(rrig.state, rrig.repo, configuration=broken))
    with pytest.raises(IntegrationWriteRefused, match="could not be established"):
        write_merge(rrig, job.job_id, intent["id"], client=client)
    assert fake.writes() == [] and not dispatched(rrig, job.job_id)


def test_f1_the_legitimate_approved_workflow_still_creates_and_merges_exactly_once(rrig: Rig, launchers: Any,
                                                                                  github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert [m for m, _p in fake.writes()] == ["POST", "PUT"] and len(fake.created) == len(fake.merge_requests) == 1
    stored = rrig.jobs.load(job.job_id)
    assert kinds(rrig, job.job_id)[-6:] == ["merge-attempt", "merge-dispatch", "merge-pr", "merge-attempt",
                                            "merge-dispatch", "merge-outcome"]
    attempts = {a["id"]: a for a in of_kind(rrig, job.job_id, "merge-attempt")}
    dispatches = of_kind(rrig, job.job_id, "merge-dispatch")
    assert [attempts[d["attempt"]]["action"] for d in dispatches] == ["create-pr", "merge"]
    assert {d["approval"] for d in dispatches} == {attempts[d["attempt"]]["approval"] for d in dispatches}
    assert stored.state is JobState.COMPLETED


# -- F2: the policy is read again at the write boundary ----------------------------------------------------------------

def tamper_before_write(monkeypatch: pytest.MonkeyPatch, action: str, mutate: Callable[[], None],
                        ) -> tuple[list[int], Callable[[], None]]:
    """`mutate()` right after the workflow recorded its INTENT for *action* and right before the request is made — after
    every check the workflow itself makes. Returns (calls made, a function that removes the hook again — never
    `monkeypatch.undo()`: it would also undo the `github_origin` fixture)."""
    done: list[int] = []
    real = IntegrationMerger._record_attempt

    def recording(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        entry = real(self, *args, **kwargs)
        if entry["action"] == action and not done:
            mutate()
            done.append(1)
        return entry
    monkeypatch.setattr(IntegrationMerger, "_record_attempt", recording)
    return done, lambda: monkeypatch.setattr(IntegrationMerger, "_record_attempt", real)


def prepared(rig: Rig, launchers: Any, origin_path: Path, action: str, *, two_keys: bool = False,
             **kwargs: Any) -> tuple[Any, FakeMergeGitHub]:
    """A signed job (with the merge approval signed by a SECOND key when *two_keys*: the inspect approval and the merge
    approval then rest on different keys); a PR exists unless the action is `create-pr`."""
    if two_keys:
        job, fake = ready_job(rig, launchers, origin_path, sign=False)
        second = rig.key.new_key("spyderbg")
        path = rig.write_record(merge_record_for(job), key=second)
        assert approve_merge(rig, job.job_id, path=path)["outcome"] == "recorded"
        job = rig.jobs.load(job.job_id)
    else:
        job, fake = ready_job(rig, launchers, origin_path, **kwargs)
    if action == "merge":
        existing_pr(fake, job)
    return job, fake


def sample_policy_changes(rig: Rig) -> dict[str, Callable[[], None]]:
    def drop_first_key() -> None:
        lines = rig.key.allowed.read_text().splitlines()
        assert len(lines) == 2
        rig.key.allowed.write_text(lines[-1] + "\n")

    def drop_second_key() -> None:
        lines = rig.key.allowed.read_text().splitlines()
        assert len(lines) == 2
        rig.key.allowed.write_text(lines[0] + "\n")

    def hide_signers() -> None:
        rig.key.allowed.rename(rig.key.allowed.with_name("allowed_signers.gone"))
    return {
        "operators-empty": lambda: with_integration(rig, operators=()),
        "merge-operator-removed": lambda: with_integration(rig, operators=("someone-else",)),
        "inspect-key-revoked": drop_first_key,
        "merge-key-revoked": drop_second_key,
        "all-keys-revoked": rig.key.revoke_all,
        "integration-disabled": lambda: with_integration(rig, enabled=False),
        "allowed-signers-unavailable": hide_signers,
    }


@pytest.mark.parametrize("action", ["create-pr", "merge"])
@pytest.mark.parametrize("change", ["operators-empty", "merge-operator-removed", "inspect-key-revoked",
                                    "merge-key-revoked", "all-keys-revoked", "integration-disabled",
                                    "allowed-signers-unavailable"])
def test_f2_a_policy_change_after_the_workflows_own_checks_stops_the_write(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, action: str, change: str):
    job, fake = prepared(rrig, launchers, github_origin, action, two_keys=True)
    done, _restore = tamper_before_write(monkeypatch, action, sample_policy_changes(rrig)[change])
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "no-approval", (report.outcome, report.reasons)
    assert "nothing was written to GitHub" in " ".join(report.messages)
    assert fake.writes() == [] and not fake.merge_requests and not fake.created           # zero GitHub writes
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL
    assert not [e for e in stored.evidence if e["kind"] == "merge-dispatch"]
    intent = last_intent(rrig, job.job_id, action)
    assert not [e for e in stored.evidence if e["kind"] == "merge-outcome" and e["attempt"] == intent["id"]]


@pytest.mark.parametrize("action", ["create-pr", "merge"])
def test_f2_a_changed_policy_that_is_restored_lets_the_same_job_proceed_without_a_duplicate(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, action: str):
    """Previously working path and recovery: the refusal left an undispatched intent; once the policy is as it was, the
    next run closes it as never dispatched and performs each write exactly once."""
    job, fake = prepared(rrig, launchers, github_origin, action)
    saved = rrig.config
    done, restore = tamper_before_write(monkeypatch, action, lambda: with_integration(rrig, operators=()))
    assert merge(rrig, job.job_id).outcome == "no-approval" and done and fake.writes() == []
    restore()
    rrig.config = saved
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.merge_requests) == 1 and len(fake.created) == (1 if action == "create-pr" else 0)
    closed = [(o["status"], o["basis"]) for o in of_kind(rrig, job.job_id, "merge-outcome")]
    assert closed[0] == ("not-created" if action == "create-pr" else "not-merged", "not-dispatched")


@pytest.mark.parametrize("action", ["create-pr", "merge"])
def test_f2_a_configuration_that_cannot_be_read_authorizes_nothing_and_there_is_no_cached_fallback(
        rrig: Rig, launchers: Any, github_origin: Path, action: str):
    job, fake = prepared(rrig, launchers, github_origin, action)
    rrig.config_unreadable = "project.yaml: [Errno 13] Permission denied"
    report = merge(rrig, job.job_id)
    assert report.outcome == "no-approval" and fake.writes() == [], (report.outcome, report.reasons)
    assert "could not be established" in " ".join(report.reasons) and "cannot read configuration" in report.reasons[0]
    rrig.config_unreadable = None
    assert merge(rrig, job.job_id).outcome == "completed"                     # readable again: the job proceeds


@pytest.mark.parametrize("action", ["create-pr", "merge"])
@pytest.mark.parametrize("when", ["between-the-two-signatures", "after-both-signatures"])
def test_f2_an_allowed_signers_file_that_changes_during_the_boundary_check_stops_the_write(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, action: str, when: str):
    job, fake = prepared(rrig, launchers, github_origin, action)
    armed: list[int] = []
    calls: list[int] = []
    real = SshSignatureVerifier.verify

    def verifying(self: SshSignatureVerifier, record: bytes, signature: str, principal: str) -> str:
        found = real(self, record, signature, principal)
        if armed:
            calls.append(1)
            if len(calls) == (1 if when == "between-the-two-signatures" else 2):
                with rrig.key.allowed.open("a", encoding="utf-8") as out:     # a key revoked/added: the digest differs
                    out.write("# edited during the boundary check\n")
        return found
    monkeypatch.setattr(SshSignatureVerifier, "verify", verifying)
    tamper_before_write(monkeypatch, action, lambda: armed.append(1))
    report = merge(rrig, job.job_id)
    assert report.outcome == "no-approval" and fake.writes() == [], (report.outcome, report.reasons)
    assert "allowed-signers file changed" in " ".join(report.reasons)
    assert calls and not dispatched(rrig, job.job_id)


@pytest.mark.parametrize("action", ["create-pr", "merge"])
def test_f2_a_merge_approval_that_expires_before_the_write_stops_it(rrig: Rig, launchers: Any, github_origin: Path,
                                                                   monkeypatch: pytest.MonkeyPatch, action: str):
    job, fake = prepared(rrig, launchers, github_origin, action)
    tamper_before_write(monkeypatch, action, lambda: boundary_clock(monkeypatch, LATER))
    report = merge(rrig, job.job_id)
    assert report.outcome == "no-approval" and "expired" in " ".join(report.reasons), (report.outcome, report.reasons)
    assert fake.writes() == [] and not [e for e in rrig.jobs.load(job.job_id).evidence if e["kind"] == "merge-dispatch"]


@pytest.mark.parametrize("action", ["create-pr", "merge"])
def test_f2_with_the_policy_unchanged_the_write_is_made_once(rrig: Rig, launchers: Any, github_origin: Path,
                                                            monkeypatch: pytest.MonkeyPatch, action: str):
    job, fake = prepared(rrig, launchers, github_origin, action, two_keys=True)
    done, _restore = tamper_before_write(monkeypatch, action, lambda: None)   # the same hook, nothing changes
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.merge_requests) == 1 and len(fake.created) == (1 if action == "create-pr" else 0)


def test_f2_the_inspect_approvals_expiry_does_not_matter_but_its_revoked_key_does(rrig: Rig, launchers: Any,
                                                                                 github_origin: Path):
    """The documented exception: an inspect approval that EXPIRED after the candidate was built still counts (the
    inspection it authorized is finished data); a REVOKED inspect key does not."""
    job, fake = prepared(rrig, launchers, github_origin, "merge", two_keys=True)
    expired = merge(rrig, job.job_id, apply=False, clock=Clock(timedelta(hours=1)))   # inside the merge approval's life
    assert expired.outcome == "would-merge", (expired.outcome, expired.reasons)
    lines = rrig.key.allowed.read_text().splitlines()
    rrig.key.allowed.write_text(lines[-1] + "\n")                                    # the inspect key is revoked
    refused = merge(rrig, job.job_id)
    assert refused.outcome == "no-approval" and "inspect approval" in " ".join(refused.reasons)
    assert fake.writes() == []


def test_f2_the_cli_rereads_the_project_configuration_file_at_the_write_boundary(
        rrig: Rig, launchers: Any, github_origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]):
    """The original reproduction, through the CLI: the operators list in the project configuration FILE is emptied while
    the merge runs (after the workflow's own checks). The CLI binds the FILE as the client's configuration anchor; the
    write boundary reads it again."""
    import json  # pylint: disable=import-outside-toplevel
    import yaml  # pylint: disable=import-outside-toplevel
    from feature_coordinator import github_commands  # pylint: disable=import-outside-toplevel
    from feature_coordinator.cli import main as cli_main  # pylint: disable=import-outside-toplevel
    from feature_coordinator.config import load_config  # pylint: disable=import-outside-toplevel
    from test_fc_integration_validate import _cli_config  # pylint: disable=import-outside-toplevel
    config_path = _cli_config(tmp_path, rrig.key)
    rrig.config = load_config(config_path)
    job, fake = ready_job(rrig, launchers, github_origin)
    monkeypatch.setattr(github_commands, "_tracker", lambda *args: rrig.client)
    changed: list[int] = []

    def empty_the_operators() -> None:
        data = yaml.safe_load(config_path.read_text())
        data["github"]["integration"]["operators"] = []
        config_path.write_text(yaml.safe_dump(data, sort_keys=False))
        changed.append(1)
    tamper_before_write(monkeypatch, "create-pr", empty_the_operators)
    args = ["--repo", str(rrig.repo), "--state-dir", str(rrig.state), "--project-config", str(config_path),
            "--tracker", "github", "integration", "merge", "--job", job.job_id, "--apply", "--json"]
    assert cli_main(args) == 1
    output = json.loads(capsys.readouterr().out)
    assert changed and output["outcome"] == "no-approval" and fake.writes() == [], output
    assert "operator" in " ".join(output["reasons"])
