"""W4.7 (M5): the operator's SIGNED MERGE approval — `integration approve --job J --stage merge --record FILE`
(integration_merge_record.py, integration_approval.py, integration_merge_status.py), the merge evidence and
`merge_result` schema (integration_merge_schema.py, integration_schema.py, integration.py) and `integration status` for
the merge stage.

Disposable checkout, bare `origin`, state directory and a throw-away operator SSH key per test; signatures are made and
checked by the host's real `ssh-keygen -Y sign/verify`. Jobs are driven through M1 → M4 by
fc_integration_merge_rig.ready_job (fake gate runner, fake reviewer process) with the scripted GitHub behind the real
client. No network, no real GitHub, no real merge, no model."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines
# pylint: disable=too-many-arguments,too-many-locals

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.errors import StateDirError
from feature_coordinator.integration import IntegrationError, IntegrationJob, IntegrationJobs, JobState, abort_job, \
    effective_approval, evidence_digest, job_summary, parse_approval_record
from feature_coordinator.integration_approval import approve, effective_approval_problems
from feature_coordinator.integration_commands import cmd_status
from feature_coordinator.integration_merge_record import binding_of, known_pr_number
from feature_coordinator.integration_merge_schema import merge_in_flight_problem, merge_result_change_problem, \
    open_merge_attempt
from feature_coordinator.integration_merge_status import merge_approval_blockers
from feature_coordinator.integration_schema import EVIDENCE_PREFIXES
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.model import utc_now
from feature_coordinator.paths import PathPolicy
from feature_coordinator.store import StateStore

from fc_integration_merge_rig import FakeMergeGitHub, approve_merge, github_for, merge, merge_record_for, ready_job
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, KNOWN, OPERATOR, REPOSITORY, OperatorKey, Rig, change_on, github_origin, live_client, origin,
    precise_stamp, pr_json, record_for, request_for, stamp,
)
from test_fc_integration_review import launchers, review, rrig, validated_job, with_review  # pylint: disable=unused-import
from test_fc_integration_validate import (  # pylint: disable=unused-import
    X_PY, FakeGates, inspected, key, rig, seeded, tracker, with_config,
)

NOW = datetime.now(timezone.utc)
JOB = "int-20261116-0001"


def a_second_ago() -> str:
    """`YYYY-MM-DDTHH:MM:SS` one minute ago: records then differ only in their fraction of a second."""
    return (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")


def kinds(rig: Rig) -> list[str]:
    return [event["kind"] for event in rig.store.events()]


@pytest.fixture
def ready(rrig: Rig, launchers: Any, github_origin: Path) -> IntegrationJob:
    """A job at AWAITING_MERGE_APPROVAL (M1 → M4 done), NO merge approval yet; the scripted GitHub is the client."""
    job, _fake = ready_job(rrig, launchers, github_origin, sign=False)
    return job


@pytest.fixture
def signed(rrig: Rig, launchers: Any, github_origin: Path) -> IntegrationJob:
    """The same job with a recorded, valid merge approval (apr-0002; apr-0001 is the inspect approval)."""
    job, _fake = ready_job(rrig, launchers, github_origin)
    return job


def merge_entry(rig: Rig, job_id: str) -> dict[str, Any] | None:
    return effective_approval(rig.jobs.load(job_id), "merge")


def signed_record(rig: Rig, job: IntegrationJob, change: Callable[[dict[str, Any]], None] | None = None,
                  **overrides: Any) -> Path:
    data = merge_record_for(job, **overrides)
    if change is not None:
        change(data)
    return rig.write_record(data)


def refused_merge(rig: Rig, job_id: str, match: str, **kwargs: Any) -> dict[str, Any]:
    before = rig.jobs.load(job_id).to_dict()
    result = approve_merge(rig, job_id, **kwargs)
    assert result["outcome"] == "refused" and not result["changed"], result
    assert any(match in reason for reason in result["reasons"]), result["reasons"]
    assert rig.jobs.load(job_id).to_dict() == before          # nothing but the audit event changes
    return result


def use_time_problems(rig: Rig, job_id: str, *, now: datetime | None = None) -> list[str]:
    """effective_approval_problems of the stored job for the merge stage (the check made whenever it is USED)."""
    return effective_approval_problems(rig.jobs.load(job_id), config=rig.config, verifier=rig.verifier,
                                       now=now or datetime.now(timezone.utc), stage="merge")[1]


# -- a valid merge approval ------------------------------------------------------------------------------------------

def test_a_signed_bound_merge_record_is_verified_and_recorded_with_its_binding(rrig: Rig, ready: IntegrationJob):
    job = ready
    path = rrig.write_record(merge_record_for(job, note="go"))
    preview = approve_merge(rrig, job.job_id, path=path, apply=False)
    assert preview["outcome"] == "would-record" and merge_entry(rrig, job.job_id) is None
    done = approve_merge(rrig, job.job_id, path=path)
    assert done["outcome"] == "recorded" and done["changed"] and done["effective"]
    stored = rrig.jobs.load(job.job_id)
    entry = merge_entry(rrig, job.job_id)
    assert entry is not None and entry["id"] == "apr-0002" and entry["stage"] == "merge"
    assert entry["operator"] == OPERATOR and entry["paths"] == [] and entry["accept_unverified_provenance"] is False
    assert entry["source_sha"] == job.source["sha"] and entry["base_sha"] == job.base["expected_sha"]
    assert entry["pr"] is None and entry["note"] == "go"
    candidate = job.candidate
    validation = [e for e in stored.evidence if e["kind"] == "validation"][-1]
    review_decision = [e for e in stored.evidence if e["kind"] == "review"][-1]
    assert entry["merge"] == {
        "repository": REPOSITORY, "candidate": {"head_sha": candidate["head_sha"], "tree_sha": candidate["tree_sha"]},
        "validation": {"evidence": validation["id"], "sha256": validation["sha256"],
                       "plan_sha256": validation["plan"]["sha256"]},
        "review": {"evidence": review_decision["id"], "sha256": review_decision["sha256"], "outcome": "PASS"},
        "authorizes": {"operation": "merge", "method": "merge", "create_pr": True}}
    assert entry["merge"] == binding_of(parse_approval_record(entry["record"]))
    assert entry["record"] == path.read_text(encoding="utf-8")             # the exact signed text, re-verifiable later
    assert entry["signature"] == Path(f"{path}.sig").read_text(encoding="ascii")
    assert entry["signer"]["principal"] == OPERATOR and entry["signer"]["key"].startswith("SHA256:")
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL                   # an approval never moves the job
    assert kinds(rrig)[-1] == "integration-approval"
    assert "record" not in done["approval"] and "signature" not in done["approval"]
    assert use_time_problems(rrig, job.job_id) == []
    # the inspect approval is untouched and still the effective INSPECT approval
    assert job_summary(stored)["effective_approval"] == "apr-0001" and effective_approval(stored)["stage"] == "inspect"
    assert "merge" not in effective_approval(stored)


def test_a_duplicate_merge_approval_is_idempotent(rrig: Rig, ready: IntegrationJob):
    path = rrig.write_record(merge_record_for(ready))
    assert approve_merge(rrig, ready.job_id, path=path)["outcome"] == "recorded"
    stored = rrig.jobs.load(ready.job_id)
    again = approve_merge(rrig, ready.job_id, path=path)
    assert again["outcome"] == "existing" and not again["changed"] and again["effective"]
    assert rrig.jobs.load(ready.job_id).revision == stored.revision
    assert [a["id"] for a in rrig.jobs.load(ready.job_id).approvals] == ["apr-0001", "apr-0002"]


# -- the signature identifies the operator ---------------------------------------------------------------------------

def test_an_unsigned_or_tampered_merge_record_is_refused(rrig: Rig, ready: IntegrationJob):
    refused_merge(rrig, ready.job_id, "cannot read", path=rrig.write_record(merge_record_for(ready), sign=False))
    path = rrig.write_record(merge_record_for(ready, note="merge it"))
    path.write_text(path.read_text(encoding="utf-8").replace('"merge it"', '"merge it!"'), encoding="utf-8")
    refused_merge(rrig, ready.job_id, "does not verify", path=path)           # a changed byte


def test_a_key_that_is_not_the_operators_or_another_namespace_is_refused(rrig: Rig, ready: IntegrationJob,
                                                                         key: OperatorKey):
    stranger = key.new_key("intruder", allow=False)
    refused_merge(rrig, ready.job_id, "does not verify", path=rrig.write_record(merge_record_for(ready), key=stranger))
    # a git commit signature (namespace "git") made with the operator's own key is not an approval
    refused_merge(rrig, ready.job_id, "does not verify", path=rrig.write_record(merge_record_for(ready),
                                                                                namespace="git"))
    # and an INSPECT-namespace-less random namespace is no better
    refused_merge(rrig, ready.job_id, "does not verify", path=rrig.write_record(merge_record_for(ready),
                                                                                namespace="mx-admin-cloud-other"))


def test_a_valid_signature_of_someone_who_is_not_an_operator_is_refused(rrig: Rig, ready: IntegrationJob,
                                                                        key: OperatorKey):
    other = key.new_key("mallory")  # in the allowed-signers file, but not in github.integration.operators
    refused_merge(rrig, ready.job_id, "not in github.integration.operators",
                  path=rrig.write_record(merge_record_for(ready, operator="mallory"), key=other))
    # the operator's name on a record signed with mallory's key does not verify
    refused_merge(rrig, ready.job_id, "does not verify", path=rrig.write_record(merge_record_for(ready), key=other))


def test_a_revoked_signing_key_is_refused_at_approve_time_and_at_use_time(rrig: Rig, ready: IntegrationJob,
                                                                          key: OperatorKey):
    path = rrig.write_record(merge_record_for(ready))
    trusted = key.allowed.read_text(encoding="utf-8")
    key.revoke_all()
    refused_merge(rrig, ready.job_id, "does not verify", path=path)           # revoked BEFORE it is recorded
    key.allowed.write_text(trusted, encoding="utf-8")
    assert approve_merge(rrig, ready.job_id, path=path)["outcome"] == "recorded"
    assert use_time_problems(rrig, ready.job_id) == []
    key.revoke_all()                                                            # revoked AFTER it was recorded
    problems = use_time_problems(rrig, ready.job_id)
    assert any("does not verify" in problem for problem in problems), problems
    assert merge_entry(rrig, ready.job_id)["id"] == "apr-0002"                 # still recorded: nothing is rewritten


def test_an_operator_removed_from_the_configuration_no_longer_authorizes(rrig: Rig, signed: IntegrationJob):
    assert use_time_problems(rrig, signed.job_id) == []
    github = rrig.config.github
    rrig.config = dataclasses.replace(rrig.config, github=dataclasses.replace(
        github, integration=dataclasses.replace(github.integration, operators=("someone-else",))))
    problems = use_time_problems(rrig, signed.job_id)
    assert any("not in github.integration.operators" in problem for problem in problems), problems


# -- time ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("times", "match"), [
    ({"issued_at": stamp(NOW - timedelta(hours=3)), "expires_at": stamp(NOW - timedelta(minutes=1))}, "expired at"),
    ({"issued_at": stamp(NOW + timedelta(hours=1)), "expires_at": stamp(NOW + timedelta(hours=2))},
     "issued in the future"),
    ({"issued_at": stamp(NOW - timedelta(minutes=1)), "expires_at": stamp(NOW + timedelta(days=8))},
     "lifetime exceeds 7 days"),
    ({"issued_at": stamp(NOW), "expires_at": stamp(NOW - timedelta(seconds=1))}, "later than issued_at"),
    ({"expires_at": "tomorrow"}, "UTC timestamp"),
    ({"expires_at": "2026-02-30T10:00:00Z"}, "not a valid date"),
], ids=["expired", "issued-in-future", "overlong", "expires-before-issued", "not-a-time", "impossible-date"])
def test_an_expired_early_or_overlong_merge_approval_is_refused(rrig: Rig, ready: IntegrationJob,
                                                                times: dict[str, str], match: str):
    refused_merge(rrig, ready.job_id, match, **times)
    assert kinds(rrig)[-1] == "integration-approval-refused"


def test_an_approval_that_expires_later_authorizes_nothing_at_use_time(rrig: Rig, signed: IntegrationJob):
    assert use_time_problems(rrig, signed.job_id) == []
    problems = use_time_problems(rrig, signed.job_id, now=datetime.now(timezone.utc) + timedelta(hours=3))
    assert any("expired at" in problem for problem in problems), problems


# -- binding to the job, the candidate and the evidence -------------------------------------------------------------

NO_CREATE = {"operation": "merge", "method": "merge", "create_pr": False}
BINDINGS: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("source-sha", lambda r: r["source"].update(sha="f" * 40), "pinned source SHA"),
    ("base-sha", lambda r: r["base"].update(sha="e" * 40), "pinned base"),
    ("base-ref", lambda r: r["base"].update(ref="main"), "pinned base"),
    ("source-ref", lambda r: r["source"].update(ref="integration/other"), "its repository/source is"),
    ("source-repo", lambda r: r["source"].update(repo="someone/fork"), "its repository/source is"),
    ("repository", lambda r: r.update(repository="someone/fork"), "its repository/source is"),
    ("issue", lambda r: r.update(issue=99), "Issue #99"),
    ("pr", lambda r: r.update(pr=5, authorizes=NO_CREATE), "PR 5"),
    ("other-job", lambda r: r.update(job="int-20990101-0001"), "authorizes job int-20990101-0001"),
    ("candidate-head", lambda r: r["candidate"].update(head_sha="a" * 40), "its candidate"),
    ("candidate-tree", lambda r: r["candidate"].update(tree_sha="b" * 40), "its candidate"),
    ("validation-id", lambda r: r["validation"].update(evidence="val-0099"), "that this job does not carry"),
    ("validation-digest", lambda r: r["validation"].update(sha256="0" * 64), "that this job does not carry"),
    ("validation-plan", lambda r: r["validation"].update(plan_sha256="1" * 64), "not a passed decision for the signed"),
    ("review-id", lambda r: r["review"].update(evidence="rev-0099"), "that this job does not carry"),
    ("review-digest", lambda r: r["review"].update(sha256="2" * 64), "that this job does not carry"),
    ("review-outcome", lambda r: r["review"].update(outcome="PASS_WITH_NOTES"), "not the signed PASS_WITH_NOTES"),
    ("unknown-operator", lambda r: r.update(operator="mallory"), "not in github.integration.operators"),
]


@pytest.mark.parametrize(("change", "match"), [(c, m) for _name, c, m in BINDINGS], ids=[n for n, _c, _m in BINDINGS])
def test_a_merge_record_that_does_not_bind_exactly_this_job_is_refused(rrig: Rig, ready: IntegrationJob,
                                                                       change: Callable[[dict[str, Any]], None],
                                                                       match: str):
    refused_merge(rrig, ready.job_id, match, path=signed_record(rrig, ready, change))


def test_an_approval_for_superseded_validation_evidence_is_refused(rrig: Rig, launchers: Any, github_origin: Path):
    job = inspected(rrig, {"backend/src/x.py": X_PY})
    assert rrig.validate(job.job_id, gates=FakeGates({"docs-check": "blocked"})).outcome == "blocked"
    assert rrig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert review(rrig, job.job_id, launchers("pass")).outcome == "reviewed"
    github_for(rrig, github_origin)
    stored = rrig.jobs.load(job.job_id)
    validations = [e for e in stored.evidence if e["kind"] == "validation"]
    assert [v["result"] for v in validations] == ["blocked", "passed"]
    old = validations[0]
    path = signed_record(rrig, stored, validation={"evidence": old["id"], "sha256": old["sha256"],
                                                   "plan_sha256": old["plan"]["sha256"]})
    result = refused_merge(rrig, job.job_id, "is not a passed decision", path=path)
    assert "superseded evidence" in result["reasons"][0]
    assert approve_merge(rrig, job.job_id)["outcome"] == "recorded"            # the current evidence is accepted


# -- stage -----------------------------------------------------------------------------------------------------------

def test_an_inspect_record_is_not_a_merge_approval(rrig: Rig, ready: IntegrationJob):
    path = rrig.write_record(record_for(rrig.jobs.load(ready.job_id)))         # a valid, signed version 1 record
    refused_merge(rrig, ready.job_id, "authorizes the inspect stage, not merge", path=path)
    # and an inspect approval is no longer takeable by a job past its inspection
    result = rrig.approve(ready.job_id)
    assert result["outcome"] == "refused"
    assert "only a job in AWAITING_APPROVAL or INSPECTING takes an inspect approval" in result["reasons"][0]


def test_a_merge_record_is_not_an_inspect_approval(rrig: Rig, repo: Path, github_origin: Path):
    sha = change_on(repo, "integration/fresh", {"backend/src/y.py": "Y = 1\n"})
    job = rrig.accepted(request_for(repo, sha, source_ref="integration/fresh"))
    data = valid_record(job=job.job_id, source={"repo": REPOSITORY, "ref": "integration/fresh", "sha": sha},
                        base={"ref": "dev", "sha": job.base["expected_sha"]}, issue=job.issue)
    data["issued_at"], data["expires_at"] = precise_stamp(NOW - timedelta(minutes=1)), stamp(NOW + timedelta(hours=2))
    result = rrig.approve(job.job_id, path=rrig.write_record(data))
    assert result["outcome"] == "refused" and "authorizes the merge stage, not inspect" in result["reasons"][0]
    assert rrig.jobs.load(job.job_id).approvals == []


def test_a_merge_approval_is_only_taken_in_awaiting_merge_approval(rrig: Rig):
    job = validated_job(rrig)                                                   # VALIDATING
    data = valid_record(job=job.job_id, source={"repo": REPOSITORY, "ref": BRANCH, "sha": job.source["sha"]},
                        base={"ref": "dev", "sha": job.base["expected_sha"]}, issue=job.issue)
    data["issued_at"], data["expires_at"] = precise_stamp(NOW - timedelta(minutes=1)), stamp(NOW + timedelta(hours=2))
    path = rrig.write_record(data)
    refused_merge(rrig, job.job_id, "only a job in AWAITING_MERGE_APPROVAL takes a merge approval", path=path)
    abort_job(rrig.jobs, job.job_id, "not wanted", actor="operator", apply=True)
    refused_merge(rrig, job.job_id, "terminal jobs are never reopened", path=path)


def test_a_merge_approval_needs_current_evidence_under_the_current_configuration(rrig: Rig, ready: IntegrationJob):
    with_review(rrig, independence="require")                                   # M4's review is no longer eligible
    result = refused_merge(rrig, ready.job_id, "no eligible review applies")
    assert "independence" in result["reasons"][0]
    with_review(rrig, independence="prefer")
    roles = dict(rrig.config.github.intake.role_gates)
    with_config(rrig, role_gates={**roles, "backend": (*roles["backend"], "frontend-build")})   # another gate plan
    refused_merge(rrig, ready.job_id, "M3's validation is not current")
    with_config(rrig, role_gates=roles)
    assert approve_merge(rrig, ready.job_id)["outcome"] == "recorded"
    assert not merge_approval_blockers(rrig.jobs.load(ready.job_id), rrig.config)


# -- known PR identity -----------------------------------------------------------------------------------------------

def test_the_signed_pr_must_be_the_pr_known_for_the_job(rrig: Rig, ready: IntegrationJob):
    assert known_pr_number(ready) is None
    refused_merge(rrig, ready.job_id, "PR 7", path=signed_record(rrig, ready, pr=7, authorizes=NO_CREATE))
    assert approve_merge(rrig, ready.job_id)["outcome"] == "recorded"


def job_with_known_pr(seeded: Path, state_dir: Path, key: OperatorKey, github_origin: Path, launchers: Any, *,
                      given: bool) -> tuple[Rig, IntegrationJob]:
    """A reviewed job whose PR #123 is known: given at intake (`given`), or found by the complete lookup of the passed
    inspection and reused (the job's own `pr` stays null — intake inputs are immutable)."""
    sha = change_on(seeded, BRANCH, {"backend/src/x.py": X_PY})
    fake = FakeMergeGitHub(github_origin, [pr_json(123, head_sha=sha)])
    rig = Rig(seeded, state_dir, key, client=live_client(fake))
    rig.config = dataclasses.replace(rig.config, runtime=dataclasses.replace(rig.config.runtime,
                                                                               terminate_grace_seconds=2))
    job = rig.approved(request_for(seeded, sha, pr_number=123) if given else request_for(seeded, sha))
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert review(rig, job.job_id, launchers("pass")).outcome == "reviewed"
    rig.client = live_client(fake, integration=rig.config.github.integration)
    return rig, rig.jobs.load(job.job_id)


@pytest.mark.parametrize("given", [True, False], ids=["pr-given-at-intake", "pr-found-by-the-inspection"])
def test_the_pr_known_for_the_job_is_the_one_the_approval_must_name(seeded: Path, state_dir: Path, key: OperatorKey,
                                                                    github_origin: Path, launchers: Any, given: bool):
    rig, job = job_with_known_pr(seeded, state_dir, key, github_origin, launchers, given=given)
    assert known_pr_number(job) == 123
    assert (job.pr or {}).get("number") == (123 if given else None)
    refused_merge(rig, job.job_id, "PR None", path=signed_record(rig, job, pr=None))
    refused_merge(rig, job.job_id, "PR 124", path=signed_record(rig, job, pr=124, authorizes=NO_CREATE))
    # create_pr with a known PR is malformed (nothing is created for a PR that is known)
    refused_merge(rig, job.job_id, "create_pr needs pr null",
                  path=signed_record(rig, job, authorizes={"operation": "merge", "method": "merge", "create_pr": True}))
    recorded = approve_merge(rig, job.job_id, authorizes=NO_CREATE)
    assert recorded["outcome"] == "recorded" and merge_entry(rig, job.job_id)["pr"] == 123


# -- superseded, replayed and duplicate approvals --------------------------------------------------------------------

def test_an_older_record_never_replaces_a_newer_decision_and_a_replay_never_revives_it(rrig: Rig,
                                                                                        ready: IntegrationJob):
    second = a_second_ago()
    paths = {f: rrig.write_record(merge_record_for(ready, issued_at=second + f, note=f"issued {f}"))
             for f in (".200000Z", ".300000Z", ".500000Z")}
    tie = rrig.write_record(merge_record_for(ready, issued_at=second + ".500000Z", note="another record, same time"))
    assert approve_merge(rrig, ready.job_id, path=paths[".200000Z"])["outcome"] == "recorded"
    assert approve_merge(rrig, ready.job_id, path=paths[".500000Z"])["outcome"] == "recorded"
    assert merge_entry(rrig, ready.job_id)["note"] == "issued .500000Z"
    refused_merge(rrig, ready.job_id, "before the recorded approval", path=paths[".300000Z"])  # older: never recorded
    refused_merge(rrig, ready.job_id, "same instant", path=tie)                                 # ambiguous order
    replay = approve_merge(rrig, ready.job_id, path=paths[".200000Z"])
    assert replay["outcome"] == "existing" and not replay["changed"] and not replay["effective"]
    assert "supersedes" in replay["reasons"][0] and "authorizes nothing" in replay["reasons"][0]
    assert merge_entry(rrig, ready.job_id)["note"] == "issued .500000Z"                         # still the newest
    again = approve_merge(rrig, ready.job_id, path=paths[".500000Z"])
    assert again["outcome"] == "existing" and again["effective"]


def test_a_merge_approval_signed_for_another_job_is_refused(rrig: Rig, launchers: Any, github_origin: Path,
                                                            ready: IntegrationJob):
    # a SECOND job with the same base and its own review; the first job's approval is signed text for job one
    sha = change_on(rrig.repo, "integration/second", {"backend/src/second.py": '"""S."""\nS = 2\n'})
    second = rrig.approved(request_for(rrig.repo, sha, source_ref="integration/second"))
    assert rrig.inspect(second.job_id).outcome == "passed"
    assert rrig.validate(second.job_id, gates=FakeGates()).outcome == "validated"
    assert review(rrig, second.job_id, launchers("pass")).outcome == "reviewed"
    github_for(rrig, github_origin)
    stolen = rrig.write_record(merge_record_for(ready))
    result = refused_merge(rrig, second.job_id, f"authorizes job {ready.job_id}", path=stolen)
    assert merge_entry(rrig, second.job_id) is None and result["approval"] is None
    assert approve_merge(rrig, second.job_id)["outcome"] == "recorded"          # its own record is fine
    assert merge_entry(rrig, ready.job_id) is None                              # and nothing leaked to job one


def test_concurrent_merge_approvals_leave_the_newest_issued_record_effective(rrig: Rig, ready: IntegrationJob):
    second = a_second_ago()
    fractions = [".100000Z", ".300000Z", ".500000Z", ".700000Z", ".900000Z"]
    records = {f: rrig.write_record(merge_record_for(ready, issued_at=second + f, note=f"issued {f}"))
               for f in fractions}

    def attempt(fraction: str) -> dict[str, Any]:
        jobs = IntegrationJobs(StateStore(rrig.state))                         # its own store: its own lock object
        return approve(jobs, rrig.config, ready.job_id, stage="merge", record_path=records[fraction],
                       signature_path=None, verifier=rrig.verifier, policy=PathPolicy(rrig.repo, rrig.config),
                       apply=True, ready=lambda job: merge_approval_blockers(job, rrig.config))

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(fractions)) as pool:
        answers = list(pool.map(attempt, list(reversed(fractions))))
    assert {a["outcome"] for a in answers} <= {"recorded", "refused"}, answers
    for answer in answers:
        if answer["outcome"] == "refused":
            assert "before the recorded approval" in answer["reasons"][0] or "older record" in answer["reasons"][0] \
                or "newer decision" in answer["reasons"][0], answer
    stored = rrig.jobs.load(ready.job_id)
    merge_approvals = [a for a in stored.approvals if a["stage"] == "merge"]
    issued = [a["issued_at"] for a in merge_approvals]
    assert issued == sorted(issued) and len(set(issued)) == len(issued)
    assert effective_approval(stored, "merge")["note"] == "issued .900000Z"
    assert effective_approval(stored)["stage"] == "inspect"                      # the inspect approval is unaffected


# -- malformed records -----------------------------------------------------------------------------------------------

def valid_record(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": "w47-integration-approval", "version": 2, "job": JOB, "stage": "merge", "operator": OPERATOR,
        "issued_at": "2026-11-16T09:00:00Z", "expires_at": "2026-11-16T21:00:00Z", "repository": REPOSITORY,
        "source": {"repo": REPOSITORY, "ref": BRANCH, "sha": "a" * 40}, "base": {"ref": "dev", "sha": "b" * 40},
        "issue": 65, "pr": None, "candidate": {"head_sha": "c" * 40, "tree_sha": "d" * 40},
        "validation": {"evidence": "val-0008", "sha256": "e" * 64, "plan_sha256": "f" * 64},
        "review": {"evidence": "rev-0011", "sha256": "1" * 64, "outcome": "PASS"},
        "authorizes": {"operation": "merge", "method": "merge", "create_pr": True}, "note": "optional"}
    data.update(changes)
    return data


def test_a_well_formed_merge_record_parses_and_a_version_1_record_still_does():
    record = parse_approval_record(json.dumps(valid_record()))
    assert record["stage"] == "merge" and record["version"] == 2 and set(binding_of(record)) == {
        "repository", "candidate", "validation", "review", "authorizes"}
    v1 = {"kind": "w47-integration-approval", "version": 1, "job": JOB, "stage": "inspect", "operator": OPERATOR,
          "issued_at": "2026-11-16T09:00:00Z", "expires_at": "2026-11-16T21:00:00Z",
          "source": {"repo": REPOSITORY, "ref": BRANCH, "sha": "a" * 40}, "base": {"ref": "dev", "sha": "b" * 40},
          "issue": 65, "pr": None, "paths": ["README.md"], "accept_unverified_provenance": False}
    assert parse_approval_record(json.dumps(v1))["stage"] == "inspect"           # unchanged, byte for byte
    note_less = valid_record()
    del note_less["note"]
    assert parse_approval_record(json.dumps(note_less))["stage"] == "merge"      # `note` is optional


MALFORMED: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("extra-key", lambda r: r.update(extra=1), "unknown field"),
    ("missing-review", lambda r: r.pop("review"), "missing field"),
    ("missing-authorizes", lambda r: r.pop("authorizes"), "missing field"),
    ("paths-are-a-v1-key", lambda r: r.update(paths=[]), "unknown field"),
    ("bad-version", lambda r: r.update(version=3), "unknown field"),
    ("string-version", lambda r: r.update(version="2"), "unknown field"),
    ("v2-with-stage-inspect", lambda r: r.update(stage="inspect"), "unknown field"),   # only a v2 merge record is v2
    ("bad-outcome", lambda r: r["review"].update(outcome="REQUEST_CHANGES"), "review.outcome must be one of"),
    ("error-outcome", lambda r: r["review"].update(outcome="ERROR"), "review.outcome must be one of"),
    ("squash", lambda r: r["authorizes"].update(method="squash"), "authorizes must be"),
    ("rebase", lambda r: r["authorizes"].update(method="rebase"), "authorizes must be"),
    ("operation-push", lambda r: r["authorizes"].update(operation="push"), "authorizes must be"),
    ("auto-merge-key", lambda r: r["authorizes"].update(auto_merge=True), "authorizes must be"),
    ("create-pr-text", lambda r: r["authorizes"].update(create_pr="yes"), "authorizes must be"),
    ("create-pr-with-a-known-pr", lambda r: r.update(pr=5), "create_pr needs pr null"),
    ("candidate-missing-tree", lambda r: r["candidate"].pop("tree_sha"), "candidate must have exactly"),
    ("candidate-short-sha", lambda r: r["candidate"].update(head_sha="abc"), "40-character"),
    ("validation-id", lambda r: r["validation"].update(evidence="val-1"), "id of a validation evidence entry"),
    ("review-id", lambda r: r["review"].update(evidence="ins-0001"), "id of a review evidence entry"),
    ("review-digest", lambda r: r["review"].update(sha256="abc"), "must be the SHA-256"),
    ("plan-digest", lambda r: r["validation"].update(plan_sha256="abc"), "plan_sha256 must be a SHA-256"),
    ("operator", lambda r: r.update(operator="bad name!"), "operator must be a GitHub login"),
    ("repository", lambda r: r.update(repository="owner"), "must be 'owner/name'"),
    ("issue", lambda r: r.update(issue=0), "positive integer"),
    ("source-key", lambda r: r["source"].update(extra="x"), "source must have exactly"),
    ("expires-first", lambda r: r.update(expires_at="2026-11-16T08:00:00Z"), "later than issued_at"),
    ("note-newline", lambda r: r.update(note="two\nlines"), "one line"),
]


@pytest.mark.parametrize(("change", "match"), [(c, m) for _n, c, m in MALFORMED], ids=[n for n, _c, _m in MALFORMED])
def test_malformed_merge_records_are_refused(change: Callable[[dict[str, Any]], None], match: str):
    data = valid_record()
    change(data)
    with pytest.raises(IntegrationError, match=match):
        parse_approval_record(json.dumps(data))


@pytest.mark.parametrize(("text", "match"), [
    ("not json", "not valid JSON"),
    ('{"kind": "w47-integration-approval", "version": 2, "version": 2}', "more than once"),
    ("[1, 2]", "must be a JSON object"),
    ('{"version": 2, "kind": NaN}', "not JSON"),
    ('{"version": 2}', "missing field"),
], ids=["not-json", "duplicate-key", "array", "nan", "version-only"])
def test_malformed_merge_record_text_is_refused(text: str, match: str):
    with pytest.raises(IntegrationError, match=match):
        parse_approval_record(text)


def test_a_malformed_merge_record_is_refused_through_approve_and_changes_nothing(rrig: Rig, ready: IntegrationJob):
    data = merge_record_for(ready)
    data["authorizes"]["method"] = "squash"
    refused_merge(rrig, ready.job_id, "authorizes must be", path=rrig.write_record(data))
    refused_merge(rrig, ready.job_id, "more than once", path=rrig.write_record({}, text='{"version": 2, "version": 2}'))
    assert kinds(rrig)[-1] == "integration-approval-refused"


# -- existing inspect approvals and records keep working -------------------------------------------------------------

def _file(rig: Rig, job_id: str) -> Path:
    return rig.state / "integration" / f"{job_id}.json"


def test_inspect_approvals_and_m4_records_are_unchanged_by_the_merge_stage(rrig: Rig, ready: IntegrationJob):
    stored = rrig.jobs.load(ready.job_id)                                       # written by the M1 → M4 code paths
    [inspect_entry] = stored.approvals
    assert inspect_entry["id"] == "apr-0001" and inspect_entry["stage"] == "inspect" and "merge" not in inspect_entry
    assert parse_approval_record(inspect_entry["record"])["version"] == 1
    summary = job_summary(stored)
    assert summary["merge_pr"] is None and summary["merge_attempt"] is None and summary["merge_open"] is False
    assert summary["merge_result"] is None and summary["reviewed"] is True
    before = copy.deepcopy(inspect_entry)
    assert approve_merge(rrig, ready.job_id)["outcome"] == "recorded"
    reloaded = rrig.jobs.load(ready.job_id)
    assert reloaded.approvals[0] == before and effective_approval(reloaded) == before


def test_the_stored_approval_entries_of_both_stages_are_tamper_evident(rrig: Rig, signed: IntegrationJob):
    path, pristine = _file(rrig, signed.job_id), _file(rrig, signed.job_id).read_text(encoding="utf-8")
    for mutate, match in (
            (lambda d: d["approvals"][0].update(merge={"x": 1}), "must have exactly"),           # inspect entry + merge
            (lambda d: d["approvals"][1].pop("merge"), "must have exactly"),                     # merge entry - merge
            (lambda d: d["approvals"][1]["merge"]["candidate"].update(head_sha="a" * 40), "differ from the signed"),
            (lambda d: d["approvals"][1]["merge"]["authorizes"].update(create_pr=False), "differ from the signed"),
            (lambda d: d["approvals"][1].update(stage="inspect"), "must have exactly"),
            (lambda d: d["approvals"][1].update(record=d["approvals"][1]["record"].replace("merge it", "x")),
             "does not match its SHA-256"),
            (lambda d: d["approvals"][1]["signer"].update(principal="mallory"), "signer must be"),
            (lambda d: d["approvals"][1].update(paths=["README.md"]), "differ from the signed"),
            (lambda d: d["approvals"][1].update(accept_unverified_provenance=True), "differ from the signed"),
            (lambda d: d["approvals"][1].update(source_sha="f" * 40), "differ from the signed")):
        data = json.loads(pristine)
        mutate(data)
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(StateDirError, match=match):
            rrig.jobs.load(signed.job_id)
    path.write_text(pristine, encoding="utf-8")
    stored = rrig.jobs.load(signed.job_id)
    stored.approvals.pop()
    with rrig.store.transaction(), pytest.raises(IntegrationError, match="approvals is append-only"):
        rrig.jobs.save(stored)


def test_a_stored_merge_approval_that_no_longer_binds_the_jobs_evidence_is_refused_on_load(rrig: Rig,
                                                                                          signed: IntegrationJob):
    path, pristine = _file(rrig, signed.job_id), _file(rrig, signed.job_id).read_text(encoding="utf-8")
    data = json.loads(pristine)
    data["candidate"]["head_sha"] = "a" * 40                                      # a different candidate than signed
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError):
        rrig.jobs.load(signed.job_id)
    path.write_text(pristine, encoding="utf-8")
    assert rrig.jobs.load(signed.job_id).state is JobState.AWAITING_MERGE_APPROVAL


# -- the merge evidence schema ---------------------------------------------------------------------------------------

class Doc:
    """A stored job record as JSON, to be tampered with and loaded again (the pristine text is restored)."""

    def __init__(self, rig: Rig, job_id: str) -> None:
        self.rig, self.job_id, self.path = rig, job_id, _file(rig, job_id)
        self.pristine = json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any], *, digest: bool) -> None:
        if digest:
            for entry in data["evidence"]:
                entry["sha256"] = evidence_digest(entry)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def check(self, build: Callable[[dict[str, Any]], Any], match: str | None = None, *,
              digest: bool = True) -> IntegrationJob | None:
        """Load the tampered record: it must be refused with *match*, or (match None) load."""
        data = copy.deepcopy(self.pristine)
        build(data)
        self._write(data, digest=digest)
        try:
            if match is None:
                return self.rig.jobs.load(self.job_id)
            with pytest.raises(StateDirError, match=match):
                self.rig.jobs.load(self.job_id)
            return None
        finally:
            self.path.write_text(json.dumps(self.pristine), encoding="utf-8")

    def write(self, build: Callable[[dict[str, Any]], Any]) -> IntegrationJob:
        """Keep the change (a valid record) and load it."""
        data = copy.deepcopy(self.pristine)
        build(data)
        self._write(data, digest=True)
        self.pristine = data
        return self.rig.jobs.load(self.job_id)


def add(data: dict[str, Any], kind: str, **fields: Any) -> dict[str, Any]:
    entry = {"kind": kind, "id": f"{EVIDENCE_PREFIXES[kind]}-{len(data['evidence']) + 1:04d}", "at": utc_now(),
             **fields}
    entry["sha256"] = evidence_digest(entry)
    data["evidence"].append(entry)
    return entry


def _approval(data: dict[str, Any]) -> dict[str, Any]:
    return [a for a in data["approvals"] if a["stage"] == "merge"][-1]


def _bound(data: dict[str, Any]) -> dict[str, Any]:
    candidate = data["candidate"]
    return {"candidate": candidate["evidence"], "head_sha": candidate["head_sha"], "tree_sha": candidate["tree_sha"]}


def attempt(data: dict[str, Any], action: str, pr: int | None, /, **over: Any) -> dict[str, Any]:
    app = _approval(data)
    request = {"head": data["source"]["ref"], "base": data["base"]["ref"], "draft": False} if action == "create-pr" \
        else {"merge_method": "merge", "sha": data["source"]["sha"]}
    fields = {**_bound(data), "source_sha": data["source"]["sha"], "base_sha": data["base"]["expected_sha"],
              "approval": app["id"], "approval_sha256": app["record_sha256"],
              "validation": app["merge"]["validation"]["evidence"], "review": app["merge"]["review"]["evidence"],
              "action": action, "pr": pr, "request": request, "actor": "operator", **over}
    return add(data, "merge-attempt", **fields)


def dispatch(data: dict[str, Any], of: dict[str, Any]) -> dict[str, Any]:
    """The write boundary's dispatch entry of attempt *of* (the request is being sent now)."""
    return add(data, "merge-dispatch", **_bound(data), attempt=of["id"], action=of["action"],
               approval=of["approval"], approval_sha256=of["approval_sha256"], policy_sha256="c" * 64)


def _dispatched(data: dict[str, Any], of: dict[str, Any]) -> bool:
    return any(e["kind"] == "merge-dispatch" and e["attempt"] == of["id"] for e in data["evidence"])


def bound_pr(data: dict[str, Any], number: int, origin: str = "discovered", via: str | None = None,
             **over: Any) -> dict[str, Any]:
    app = _approval(data)
    if origin == "created" and via is not None:
        made = next(e for e in data["evidence"] if e["id"] == via)
        if not _dispatched(data, made):
            dispatch(data, made)
    fields = {**_bound(data), "approval": app["id"], "approval_sha256": app["record_sha256"], "number": number,
              "origin": origin, "attempt": via,
              "head": {"repo": REPOSITORY, "ref": data["source"]["ref"], "sha": data["source"]["sha"]},
              "base": {"repo": REPOSITORY, "ref": data["base"]["ref"]}, "draft": False,
              "url": f"https://github.com/{REPOSITORY}/pull/{number}", "pr_id": 700000 + number, **over}
    return add(data, "merge-pr", **fields)


def outcome(data: dict[str, Any], of: dict[str, Any], status: str, basis: str | None = None, *,
            dispatched: bool = True, **over: Any) -> dict[str, Any]:
    """The outcome of attempt *of*; its basis defaults to GitHub's answer (`response` / `rejected`), which needs the
    attempt to have been dispatched (done here unless the basis is `not-dispatched` or *dispatched* is false)."""
    basis = basis or ("response" if status == "merged" else "rejected")
    known = any(e["id"] == of["id"] for e in data["evidence"])
    if dispatched and known and basis != "not-dispatched" and not _dispatched(data, of):
        dispatch(data, of)
    fields = {**_bound(data), "attempt": of["id"], "status": status, "basis": basis, "pr": of["pr"],
              "merge_commit_sha": "c" * 40 if status == "merged" else "", "detail": "scripted", **over}
    return add(data, "merge-outcome", **fields)


def create_and_bind(data: dict[str, Any], number: int = 101) -> dict[str, Any]:
    """create-pr attempt → merge-pr (created): the PR is bound; returns the merge-pr entry."""
    made = attempt(data, "create-pr", None)
    return bound_pr(data, number, "created", made["id"])


def test_valid_merge_evidence_loads_and_every_entry_is_digest_bound(rrig: Rig, signed: IntegrationJob):
    doc = Doc(rrig, signed.job_id)

    def build(data: dict[str, Any]) -> None:
        create_and_bind(data)
        merge_attempt = attempt(data, "merge", 101)
        outcome(data, merge_attempt, "not-merged")
        again = attempt(data, "merge", 101)
        outcome(data, again, "merged")
    stored = doc.check(build)
    assert stored is not None and [e["kind"] for e in stored.evidence][-6:] == [
        "merge-attempt", "merge-dispatch", "merge-outcome", "merge-attempt", "merge-dispatch", "merge-outcome"]
    # a changed payload with the digest left alone is refused; so is a new key
    doc.check(lambda d: create_and_bind(d) and d["evidence"][-1].update(url="https://example.invalid/x"),
              "SHA-256 does not match", digest=False)
    doc.check(lambda d: create_and_bind(d) and d["evidence"][-1].update(extra=1), "must have exactly")
    # append-only through the store
    doc.write(create_and_bind)
    kept = rrig.jobs.load(signed.job_id)
    kept.evidence[-1] = {**kept.evidence[-1], "url": "https://example.invalid/rewritten"}
    with rrig.store.transaction(), pytest.raises(IntegrationError, match="append-only|SHA-256"):
        rrig.jobs.save(kept)


def test_merge_attempts_must_be_exactly_the_authorized_operation(rrig: Rig, signed: IntegrationJob):
    doc = Doc(rrig, signed.job_id)

    def merge_after_bind(**over: Any) -> Callable[[dict[str, Any]], Any]:
        def build(data: dict[str, Any]) -> Any:
            create_and_bind(data)
            return attempt(data, "merge", over.pop("pr", 101), **over)
        return build

    doc.check(merge_after_bind())                                                  # positive control
    doc.check(lambda d: attempt(d, "merge", 101), "names the bound PR")            # no PR bound yet
    doc.check(merge_after_bind(pr=102), "names the bound PR")                       # another PR
    doc.check(merge_after_bind(request={"merge_method": "squash", "sha": signed.source["sha"]}), "exactly the request")
    doc.check(merge_after_bind(request={"merge_method": "rebase", "sha": signed.source["sha"]}), "exactly the request")
    doc.check(merge_after_bind(request={"merge_method": "merge", "sha": "a" * 40}), "exactly the request")
    doc.check(merge_after_bind(request={"merge_method": "merge", "sha": signed.source["sha"], "auto_merge": True}),
              "exactly the request")
    doc.check(merge_after_bind(approval="apr-0001"), "must name a merge approval of this job")   # the inspect approval
    doc.check(merge_after_bind(approval_sha256="0" * 64), "must name a merge approval of this job")
    doc.check(merge_after_bind(validation="val-0099"), "other SHAs or evidence")
    doc.check(merge_after_bind(review="rev-0099"), "other SHAs or evidence")
    doc.check(merge_after_bind(source_sha="a" * 40), "other SHAs or evidence")
    doc.check(merge_after_bind(base_sha="a" * 40), "other SHAs or evidence")
    doc.check(merge_after_bind(action="push"), "action must be one of")
    doc.check(merge_after_bind(actor=""), "actor must be a name")
    doc.check(merge_after_bind(extra=1), "must have exactly")
    doc.check(merge_after_bind(head_sha="a" * 40), "must name the built candidate")


def test_a_pr_creation_needs_the_explicit_authorization_and_the_exact_request(rrig: Rig, signed: IntegrationJob):
    doc = Doc(rrig, signed.job_id)
    doc.check(lambda d: attempt(d, "create-pr", None))                              # positive control (create_pr: true)
    doc.check(lambda d: attempt(d, "create-pr", 7), "needs the approval's explicit create_pr authorization")
    doc.check(lambda d: attempt(d, "create-pr", None, request={"head": d["source"]["ref"], "base": "main",
                                                               "draft": False}), "exactly the request")
    doc.check(lambda d: attempt(d, "create-pr", None, request={"head": d["source"]["ref"], "base": d["base"]["ref"],
                                                               "draft": True}), "exactly the request")

    def second_creation(data: dict[str, Any]) -> None:
        create_and_bind(data)
        attempt(data, "create-pr", None)                                            # a PR is bound already
    doc.check(second_creation, "no PR bound yet")


def test_an_approval_that_does_not_authorize_creating_a_pr_allows_no_create_attempt(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, _fake = ready_job(rrig, launchers, github_origin, authorizes=NO_CREATE)
    doc = Doc(rrig, job.job_id)
    doc.check(lambda d: attempt(d, "create-pr", None), "needs the approval's explicit create_pr authorization")
    doc.check(lambda d: bound_pr(d, 101, "discovered"))                             # a discovered PR is still fine


def test_the_bound_pr_is_recorded_once_and_must_match_the_pinned_head_and_the_signature(rrig: Rig,
                                                                                         signed: IntegrationJob):
    doc = Doc(rrig, signed.job_id)
    doc.check(lambda d: bound_pr(d, 101, "discovered"))                             # positive control
    doc.check(lambda d: (bound_pr(d, 101), bound_pr(d, 102)), "bound once and never changed")
    doc.check(lambda d: bound_pr(d, 101, "created"), "a created PR names its DISPATCHED create-pr attempt")
    doc.check(lambda d: bound_pr(d, 101, "discovered", via=attempt(d, "create-pr", None)["id"]),
              "a created PR names its DISPATCHED create-pr attempt")
    doc.check(lambda d: bound_pr(d, 101, "recorded"), "names no PR, so the PR is discovered or created")
    doc.check(lambda d: bound_pr(d, 101, "inspected"), "names no PR, so the PR is discovered or created")
    doc.check(lambda d: bound_pr(d, 101, head={"repo": REPOSITORY, "ref": d["source"]["ref"], "sha": "a" * 40}),
              "from the pinned source branch at the pinned SHA")
    doc.check(lambda d: bound_pr(d, 101, base={"repo": REPOSITORY, "ref": "main"}), "into the pinned base")
    doc.check(lambda d: bound_pr(d, 101, head={"repo": "someone/fork", "ref": d["source"]["ref"],
                                               "sha": d["source"]["sha"]}), "from the pinned source branch")
    doc.check(lambda d: bound_pr(d, 101, draft=True), "a draft PR is never bound")
    doc.check(lambda d: bound_pr(d, 0), "positive integers")
    doc.check(lambda d: bound_pr(d, 101, approval="apr-0001"), "must name a merge approval of this job")
    doc.check(lambda d: bound_pr(d, 101, extra=1), "must have exactly")


def test_a_pr_bound_for_a_known_pr_must_be_that_pr(seeded: Path, state_dir: Path, key: OperatorKey,
                                                   github_origin: Path, launchers: Any):
    rig, job = job_with_known_pr(seeded, state_dir, key, github_origin, launchers, given=True)
    assert approve_merge(rig, job.job_id, authorizes=NO_CREATE)["outcome"] == "recorded"
    doc = Doc(rig, job.job_id)
    doc.check(lambda d: bound_pr(d, 123, "recorded"))                               # the signed PR
    doc.check(lambda d: bound_pr(d, 124, "recorded"), "no other PR is bound")
    doc.check(lambda d: bound_pr(d, 123, "discovered"), "no other PR is bound")
    doc.check(lambda d: bound_pr(d, 123, "created", via=attempt(d, "create-pr", None)["id"]),
              "needs the approval's explicit create_pr authorization")


def test_a_merge_outcome_ends_only_the_newest_attempt_once_and_needs_a_basis_that_is_not_elapsed_time(
        rrig: Rig, signed: IntegrationJob):
    doc = Doc(rrig, signed.job_id)

    def bound(data: dict[str, Any]) -> dict[str, Any]:
        create_and_bind(data)
        return attempt(data, "merge", 101)

    doc.check(lambda d: outcome(d, bound(d), "merged"))                              # positive controls
    doc.check(lambda d: outcome(d, bound(d), "not-merged"))
    doc.check(lambda d: outcome(d, attempt(d, "create-pr", None), "not-created"))
    doc.check(lambda d: outcome(d, bound(d), "merged", merge_commit_sha=""), "actual merge commit")
    doc.check(lambda d: outcome(d, bound(d), "not-merged", merge_commit_sha="c" * 40), "actual merge commit")
    doc.check(lambda d: outcome(d, bound(d), "merged", merge_commit_sha="abc"), "actual merge commit")
    doc.check(lambda d: outcome(d, attempt(d, "create-pr", None), "merged"), "does not end a create-pr attempt")
    doc.check(lambda d: outcome(d, bound(d), "not-created"), "does not end a merge attempt")
    doc.check(lambda d: outcome(d, bound(d), "closed"), "status must be one of")
    doc.check(lambda d: outcome(d, bound(d), "merged", detail="x" * 301), "status must be one of")
    doc.check(lambda d: outcome(d, bound(d), "merged", pr=102), "pr must be the attempt's")
    doc.check(lambda d: outcome(d, bound(d), "merged", attempt="mat-0001"), "newest merge attempt")
    doc.check(lambda d: outcome(d, {"id": "mat-0099", "pr": None}, "merged"), "newest merge attempt")
    # Codex F3/F4: an outcome has a BASIS — GitHub's answer or the absence of any dispatch, never elapsed time — and an
    # outcome of this job's own request needs that request to have been dispatched
    doc.check(lambda d: outcome(d, bound(d), "not-merged", "not-dispatched"))                 # positive control
    doc.check(lambda d: outcome(d, bound(d), "not-merged", "not-sent"))
    doc.check(lambda d: outcome(d, attempt(d, "create-pr", None), "not-created", "superseded"))
    doc.check(lambda d: outcome(d, bound(d), "merged", "actor"))
    doc.check(lambda d: outcome(d, bound(d), "not-merged", "timeout"), "basis of a not-merged outcome")
    doc.check(lambda d: outcome(d, bound(d), "not-merged", "elapsed"), "elapsed time is never a basis")
    doc.check(lambda d: outcome(d, bound(d), "merged", "rejected"), "basis of a merged outcome")
    doc.check(lambda d: outcome(d, bound(d), "merged", "not-dispatched"), "basis of a merged outcome")
    doc.check(lambda d: outcome(d, bound(d), "merged", "response", dispatched=False), "needs a dispatch entry")
    doc.check(lambda d: outcome(d, bound(d), "not-merged", "rejected", dispatched=False), "needs a dispatch entry")

    def dispatched_but_not_dispatched(data: dict[str, Any]) -> None:
        made = bound(data)
        dispatch(data, made)
        outcome(data, made, "not-merged", "not-dispatched")                          # a request WAS dispatched
    doc.check(dispatched_but_not_dispatched, "is impossible with a dispatch entry")

    def stale_attempt(data: dict[str, Any]) -> None:
        first = bound(data)
        outcome(data, first, "not-merged")
        later = attempt(data, "merge", 101)
        outcome(data, first, "not-merged")                                           # not the newest attempt any more
        assert later
    doc.check(stale_attempt, "newest merge attempt")

    def twice(data: dict[str, Any]) -> None:
        first = bound(data)
        outcome(data, first, "not-merged")
        outcome(data, first, "not-merged")
    doc.check(twice, "the attempt already ended")

    def merged_then_not_merged(data: dict[str, Any]) -> None:
        first = bound(data)
        outcome(data, first, "merged")
        outcome(data, first, "not-merged")
    doc.check(merged_then_not_merged, "the attempt already ended")

    def merged_after_not_merged(data: dict[str, Any]) -> None:
        first = bound(data)
        outcome(data, first, "not-merged")
        outcome(data, first, "merged")        # Codex F3: never "in flight after all" — an outcome is final
    doc.check(merged_after_not_merged, "the attempt already ended")


def test_a_dispatch_entry_is_the_one_shot_of_the_newest_open_attempt(rrig: Rig, signed: IntegrationJob):
    doc = Doc(rrig, signed.job_id)

    def merge_attempt(data: dict[str, Any]) -> dict[str, Any]:
        create_and_bind(data)
        return attempt(data, "merge", 101)

    doc.check(lambda d: dispatch(d, merge_attempt(d)))                                 # positive control

    def twice(data: dict[str, Any]) -> None:
        made = merge_attempt(data)
        dispatch(data, made)
        dispatch(data, made)
    doc.check(twice, "dispatched once, and never after it ended")
    doc.check(lambda d: dispatch(d, {**merge_attempt(d), "id": "mat-0001"}), "newest merge attempt")
    doc.check(lambda d: dispatch(d, merge_attempt(d)) and d["evidence"][-1].update(extra=1), "must have exactly")
    doc.check(lambda d: add(d, "merge-dispatch", **_bound(d), attempt=merge_attempt(d)["id"], action="create-pr",
                            approval=_approval(d)["id"], approval_sha256=_approval(d)["record_sha256"],
                            policy_sha256="c" * 64),
              "repeat its attempt's action and approval")
    for bad in ("", "C" * 64, "c" * 63, None):     # the fingerprint of the policy snapshot the write was decided under
        doc.check(lambda d, bad=bad: dispatch(d, merge_attempt(d)) and d["evidence"][-1].update(policy_sha256=bad),
                  "policy_sha256 must be the SHA-256")

    def after_outcome(data: dict[str, Any]) -> None:
        made = merge_attempt(data)
        outcome(data, made, "not-merged", "not-dispatched")
        dispatch(data, made)                                                           # after the attempt ended
    doc.check(after_outcome, "dispatched once, and never after it ended")


def test_attempts_follow_ended_attempts_and_nothing_follows_a_merged_outcome(rrig: Rig, signed: IntegrationJob):
    doc = Doc(rrig, signed.job_id)

    def two_open(data: dict[str, Any]) -> None:
        create_and_bind(data)
        attempt(data, "merge", 101)
        attempt(data, "merge", 101)
    doc.check(two_open, "an attempt starts only after the previous one ended")

    def after_merged(data: dict[str, Any]) -> None:
        create_and_bind(data)
        outcome(data, attempt(data, "merge", 101), "merged")
        attempt(data, "merge", 101)
    doc.check(after_merged, "no attempt follows a merged outcome")

    def create_then_merge(data: dict[str, Any]) -> None:
        made = attempt(data, "create-pr", None)
        outcome(data, made, "not-created")
        create_and_bind(data)                                          # a closed attempt: a new one is fine
        attempt(data, "merge", 101)
    doc.check(create_then_merge)

    def too_many(data: dict[str, Any]) -> None:
        create_and_bind(data)
        for _ in range(19):                                                           # 1 create + 19 merges = 20
            outcome(data, attempt(data, "merge", 101), "not-merged")
        attempt(data, "merge", 101)                                                   # the 21st
    doc.check(too_many, "at most 20 merge attempts")


def test_merge_evidence_exists_only_after_awaiting_merge_approval(rrig: Rig):
    job = validated_job(rrig)                                                        # VALIDATING: never reached it
    doc = Doc(rrig, job.job_id)

    def forged(data: dict[str, Any]) -> None:
        candidate = data["candidate"]
        add(data, "merge-pr", candidate=candidate["evidence"], head_sha=candidate["head_sha"],
            tree_sha=candidate["tree_sha"], approval="apr-0001", approval_sha256="0" * 64, number=1,
            origin="discovered", attempt=None, head={}, base={}, draft=False, url="", pr_id=1)
    doc.check(forged, "reached AWAITING_MERGE_APPROVAL")


def test_a_dispatched_merge_attempt_blocks_abort_while_young_and_an_undispatched_one_does_not(
        rrig: Rig, signed: IntegrationJob):
    doc = Doc(rrig, signed.job_id)
    stored = doc.write(lambda d: attempt(d, "create-pr", None))                       # a PR creation in flight
    assert open_merge_attempt(stored)["action"] == "create-pr" and merge_in_flight_problem(stored) == ""
    summary = job_summary(stored)
    assert summary["merge_open"] is True and summary["merge_attempt"]["action"] == "create-pr"
    assert summary["merge_pr"] is None

    def merge_intent(data: dict[str, Any]) -> None:
        last = data["evidence"][-1]
        bound_pr(data, 101, "created", last["id"])
        attempt(data, "merge", 101)
    stored = doc.write(merge_intent)                                                  # the intent: never dispatched
    opened = open_merge_attempt(stored)
    assert opened["action"] == "merge" and opened["pr"] == 101
    assert merge_in_flight_problem(stored) == ""                                      # nothing was sent: not in flight
    assert abort_job(rrig.jobs, signed.job_id, "stop", actor="operator", apply=False)["outcome"] == "would-abort"
    stored = doc.write(lambda d: dispatch(d, d["evidence"][-1]))                      # the request is being dispatched
    problem = merge_in_flight_problem(stored)
    assert opened["id"] in problem and "in flight" in problem and "integration merge" in problem
    summary = job_summary(stored)
    assert summary["merge_open"] is True and summary["merge_pr"]["number"] == 101
    assert summary["merge_attempt"]["id"] == opened["id"]
    before = rrig.jobs.load(signed.job_id).to_dict()
    for apply in (False, True):                                                       # dry run and apply both refuse
        with pytest.raises(IntegrationError, match="merge request in flight"):
            abort_job(rrig.jobs, signed.job_id, "stop", actor="operator", apply=apply)
    assert rrig.jobs.load(signed.job_id).to_dict() == before
    # Codex F3: past the settle window the operator may abort (an unresolved request is theirs to judge) — the abort
    # records that the request's outcome was never established
    dispatched_at = datetime.fromisoformat(of_kind_first(stored, "merge-dispatch")["at"].replace("Z", "+00:00"))
    assert merge_in_flight_problem(stored, dispatched_at + timedelta(seconds=119)) != ""
    assert merge_in_flight_problem(stored, dispatched_at + timedelta(seconds=121)) == ""
    stored = doc.write(lambda d: outcome(d, next(e for e in d["evidence"] if e["id"] == opened["id"]), "not-merged"))
    assert open_merge_attempt(stored) is None and merge_in_flight_problem(stored) == ""
    assert job_summary(stored)["merge_open"] is False
    result = abort_job(rrig.jobs, signed.job_id, "stop", actor="operator", apply=True)
    assert result["outcome"] == "aborted" and rrig.state_of(signed.job_id) is JobState.ABORTED


def test_the_abort_window_of_a_dispatched_merge_starts_at_the_dispatch_not_at_the_intent(rrig: Rig,
                                                                                         signed: IntegrationJob):
    """Review M3: the intent may be minutes older than the request (a retry wait, a slow boundary check)."""
    doc = Doc(rrig, signed.job_id)

    def build(data: dict[str, Any]) -> None:
        create_and_bind(data)
        made = attempt(data, "merge", 101, at="2026-11-17T08:00:00.000000Z")         # the intent: long ago
        dispatch(data, made)                                                          # the dispatch: now
    stored = doc.write(build)
    dispatched_at = datetime.fromisoformat(of_kind_first(stored, "merge-dispatch")["at"].replace("Z", "+00:00"))
    assert merge_in_flight_problem(stored, dispatched_at + timedelta(seconds=60)) != ""
    assert merge_in_flight_problem(stored, dispatched_at + timedelta(seconds=121)) == ""


def of_kind_first(job: IntegrationJob, kind: str) -> dict[str, Any]:
    return [e for e in job.evidence if e["kind"] == kind][-1]


def test_an_open_pr_creation_alone_can_be_aborted(rrig: Rig, signed: IntegrationJob):
    Doc(rrig, signed.job_id).write(lambda d: attempt(d, "create-pr", None))
    assert abort_job(rrig.jobs, signed.job_id, "stop", actor="operator", apply=True)["outcome"] == "aborted"


# -- merge_result ----------------------------------------------------------------------------------------------------

@pytest.fixture
def completed(rrig: Rig, launchers: Any, github_origin: Path) -> IntegrationJob:
    job, _fake = ready_job(rrig, launchers, github_origin)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    return rrig.jobs.load(job.job_id)


@pytest.fixture
def merged(rrig: Rig, launchers: Any, github_origin: Path) -> IntegrationJob:
    """MERGED, pending verification: the process crashes right after GitHub's merge was recorded."""
    job, _fake = ready_job(rrig, launchers, github_origin)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("after-merged-record",))
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.MERGED
    return stored


def test_a_completed_job_carries_the_complete_verified_merge_result(rrig: Rig, completed: IntegrationJob):
    result = completed.merge_result
    assert completed.state is JobState.COMPLETED and result["verified"] is True
    assert result["parents"] == [completed.base["expected_sha"], completed.source["sha"]]
    assert result["tree_sha"] == completed.candidate["tree_sha"] and result["verification"]["state"] == "verified"
    assert {c["status"] for c in result["verification"]["checks"]} <= {"pass", "not-configured"}
    summary = job_summary(completed)
    assert summary["merge_pr"]["number"] == result["pr"] and summary["merge_open"] is False
    assert summary["merge_attempt"]["action"] == "merge" and summary["merge_pr"]["origin"] == "created"


COMPLETED_TAMPERING: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("not-verified", lambda r: r.update(verified=False), "exactly for a COMPLETED job"),
    ("state-pending", lambda r: r["verification"].update(state="pending"), "exactly for a COMPLETED job"),
    ("parents-swapped", lambda r: r.update(parents=list(reversed(r["parents"]))), "must have the parents base_sha"),
    ("squash-parent", lambda r: r.update(parents=r["parents"][:1]), "must have the parents base_sha"),
    ("other-tree", lambda r: r.update(tree_sha="a" * 40), "approved candidate tree"),
    ("failed-check", lambda r: r["verification"]["checks"][0].update(status="fail"), "must pass"),
    ("pending-check", lambda r: r["verification"]["checks"][0].update(status="pending"), "must pass"),
    ("unavailable-check", lambda r: r["verification"]["checks"][0].update(status="unavailable"), "must pass"),
    ("unknown-check-status", lambda r: r["verification"]["checks"][0].update(status="great"),
     "merge_result.verification must be"),
    ("extra-key", lambda r: r.update(extra=1), "merge_result may only have"),
    ("missing-attempt", lambda r: r.pop("attempt"), "merge_result lacks"),
    ("other-commit", lambda r: r.update(commit_sha="a" * 40), "must be the merged outcome"),
    ("other-pr", lambda r: r.update(pr=r["pr"] + 1), "must be the merged outcome"),
    ("other-attempt", lambda r: r.update(attempt="mat-0001"), "must be the merged outcome"),
    ("three-parents", lambda r: r.update(parents=[*r["parents"], "a" * 40]), "parents must be"),
    ("tree-without-parents", lambda r: r.update(parents=[]), "filled in together"),
    ("merged-by-too-long", lambda r: r.update(merged_by="x" * 101), "merged_by a login"),
]


@pytest.mark.parametrize(("change", "match"), [(c, m) for _n, c, m in COMPLETED_TAMPERING],
                         ids=[n for n, _c, _m in COMPLETED_TAMPERING])
def test_the_merge_result_of_a_completed_job_is_checked_on_every_load(rrig: Rig, completed: IntegrationJob,
                                                                      change: Callable[[dict[str, Any]], None],
                                                                      match: str):
    Doc(rrig, completed.job_id).check(lambda d: change(d["merge_result"]), match)


def test_the_minimal_legacy_merge_result_shape_is_refused_for_a_job_with_a_candidate(rrig: Rig,
                                                                                    completed: IntegrationJob):
    doc = Doc(rrig, completed.job_id)
    result = completed.merge_result
    minimal = {"commit_sha": result["commit_sha"], "parents": result["parents"], "tree_sha": result["tree_sha"],
               "verified": True}
    # M1's minimal shape (no `verification`) is only valid for a record WITHOUT a candidate (a state-machine unit
    # record, test_fc_integration.py); a job that has a candidate and reached MERGED must carry the complete shape
    # (independent review of M5, finding M4: a hand-edited COMPLETED record could otherwise skip the verification)
    doc.check(lambda d: d.update(merge_result=minimal), "merge_result lacks")
    doc.check(lambda d: d.update(merge_result={"commit_sha": "x"}), "full commit SHA")
    doc.check(lambda d: d.update(merge_result={"commit_sha": result["commit_sha"], "verified": "yes"}),
              "true or false")


def test_a_merged_job_cannot_claim_verification_and_the_parents_and_tree_come_together(rrig: Rig,
                                                                                       merged: IntegrationJob):
    doc = Doc(rrig, merged.job_id)
    result = merged.merge_result
    assert result["verified"] is False and result["parents"] == [] and result["tree_sha"] == ""
    assert result["verification"]["state"] == "pending" and result["verification"]["checks"] == []
    doc.check(lambda d: d["merge_result"].update(verified=True), "exactly for a COMPLETED job")
    doc.check(lambda d: d["merge_result"]["verification"].update(state="verified"), "exactly for a COMPLETED job")
    doc.check(lambda d: d["merge_result"].update(parents=[d["base"]["expected_sha"]]), "filled in together")
    doc.check(lambda d: d["merge_result"].update(tree_sha=d["candidate"]["tree_sha"]), "filled in together")
    legacy = {"commit_sha": result["commit_sha"], "verified": False}
    doc.check(lambda d: d.update(merge_result=legacy), "merge_result lacks")      # not for a job with a candidate
    doc.check(lambda d: d.update(merge_result=None), "carry the merge commit")


def test_the_identity_of_the_merge_never_changes_and_parents_and_tree_are_filled_in_once(rrig: Rig,
                                                                                         merged: IntegrationJob):
    def save(change: Callable[[dict[str, Any]], None]) -> None:
        job = rrig.jobs.load(merged.job_id)
        change(job.merge_result)
        with rrig.store.transaction():
            rrig.jobs.save(job)

    for key_, value in (("commit_sha", "a" * 40), ("pr", merged.merge_result["pr"] + 1), ("attempt", "mat-0001"),
                        ("merged_by", "someone-else"), ("merged_at", "2026-01-01T00:00:00Z")):
        before = rrig.jobs.load(merged.job_id).to_dict()
        with pytest.raises(IntegrationError, match="cannot change"):
            save(lambda r, k=key_, v=value: r.update({k: v}))
        assert rrig.jobs.load(merged.job_id).to_dict() == before
    job = rrig.jobs.load(merged.job_id)
    job.merge_result = None
    with rrig.store.transaction(), pytest.raises(IntegrationError, match="cannot be removed"):
        rrig.jobs.save(job)
    parents = [merged.base["expected_sha"], merged.source["sha"]]
    checks = [{"name": "checks", "status": "pending", "detail": "still running"}]

    def fill(result: dict[str, Any]) -> None:                          # the first verification fills them in
        result.update(parents=parents, tree_sha=merged.candidate["tree_sha"])
        result["verification"].update(checks=checks)
    save(fill)
    stored = rrig.jobs.load(merged.job_id)
    assert stored.merge_result["parents"] == parents and stored.merge_result["verification"]["checks"] == checks
    save(lambda r: r["verification"].update(checks=[{"name": "checks", "status": "pass", "detail": "ok"}]))  # allowed
    with pytest.raises(IntegrationError, match="cannot change once the verification filled it in"):
        save(lambda r: r.update(parents=list(reversed(parents))))
    with pytest.raises(IntegrationError, match="cannot change once the verification filled it in"):
        save(lambda r: r.update(tree_sha="a" * 40))


def test_merge_result_change_problem_is_a_pure_rule():
    base = {"commit_sha": "a" * 40, "pr": 5, "attempt": "mat-0002", "merged_at": "", "merged_by": "x", "parents": [],
            "tree_sha": "", "verified": False}
    assert merge_result_change_problem(None, None) == "" and merge_result_change_problem(None, base) == ""
    assert "cannot be removed" in merge_result_change_problem(base, None)
    filling = {**base, "parents": ["b" * 40], "tree_sha": "c" * 40, "verified": True}
    assert merge_result_change_problem(base, filling) == ""
    assert "commit_sha" in merge_result_change_problem(base, {**base, "commit_sha": "b" * 40})
    filled = {**base, "parents": ["b" * 40, "c" * 40], "tree_sha": "d" * 40}
    assert "parents" in merge_result_change_problem(filled, {**filled, "parents": ["c" * 40, "b" * 40]})
    assert "tree_sha" in merge_result_change_problem(filled, {**filled, "tree_sha": "e" * 40})
    assert merge_result_change_problem(filled, {**filled, "verified": True}) == ""


# -- integration status for the merge stage --------------------------------------------------------------------------

def _status(rig: Rig, job_id: str, capsys: pytest.CaptureFixture[str],
            config: Any = None) -> tuple[str, dict[str, Any]]:
    """`integration status --job` (text, then --json) through cmd_status under *config*; it writes nothing."""
    config = config or rig.config
    before = _file(rig, job_id).read_bytes()
    args = argparse.Namespace(state_dir=str(rig.state), repo=str(rig.repo), job=job_id, json=False)
    assert cmd_status(args, config) == 0
    text = capsys.readouterr().out
    args.json = True
    assert cmd_status(args, config) == 0
    data = json.loads(capsys.readouterr().out)["job"]
    assert _file(rig, job_id).read_bytes() == before                                   # read-only
    return text, data


def test_status_of_a_reviewed_job_without_a_merge_approval_is_not_merge_ready(rrig: Rig, ready: IntegrationJob,
                                                                              capsys: pytest.CaptureFixture[str]):
    text, data = _status(rrig, ready.job_id, capsys)
    assert "merge approval: none" in text and "not merge-ready" in text and "MERGE-READY" not in text
    assert "blocker: no signed merge approval" in text and "action: sign a version 2 merge record" in text
    assert "blockers: merge approval and merge (M5" in text                     # the M4 status text is unchanged
    merge_view = data["merge"]
    assert merge_view["ready"] is False and merge_view["approval"] is None and merge_view["state"] == \
        "AWAITING_MERGE_APPROVAL"
    assert merge_view["candidate"] == {"head_sha": ready.candidate["head_sha"], "tree_sha": ready.candidate["tree_sha"]}
    assert merge_view["validation"] == {"eligible": True}
    assert merge_view["review"]["eligible"] is True and merge_view["review"]["outcome"] == "PASS"
    assert merge_view["pr"] == {"bound": None, "known": None, "creation_authorized": False}
    assert merge_view["blockers"] == ["no signed merge approval"] and merge_view["open_attempt"] is None
    assert data["reviewed"] is True and data["merge_result"] is None


def test_status_shows_the_signed_approval_its_expiry_and_binding_and_only_then_merge_ready(
        rrig: Rig, signed: IntegrationJob, capsys: pytest.CaptureFixture[str]):
    entry = merge_entry(rrig, signed.job_id)
    text, data = _status(rrig, signed.job_id, capsys)
    assert f"merge approval {entry['id']} by {OPERATOR} (key {entry['signer']['key']})" in text
    assert f"EXPIRES {entry['expires_at']}" in text and "VALID" in text and "NOT VALID" not in text
    candidate = signed.candidate
    assert f"binds candidate {candidate['head_sha']} (tree {candidate['tree_sha']})" in text
    assert f"validation {entry['merge']['validation']['evidence']}" in text and "(PASS)" in text
    assert "PR none known, PR creation authorized" in text
    assert "validation eligible, review eligible" in text and "MERGE-READY (re-verified at merge time)" in text
    assert "blocker:" not in text and "integration merge --job" in text
    view = data["merge"]
    assert view["ready"] is True and view["blockers"] == [] and view["approval"]["valid"] is True
    assert view["approval"]["problems"] == [] and view["approval"]["expires_at"] == entry["expires_at"]
    assert view["approval"]["merge"]["candidate"] == view["candidate"]
    assert "record" not in view["approval"] and "signature" not in view["approval"]
    assert view["pr"] == {"bound": None, "known": None, "creation_authorized": True}
    assert view["actions"] and "integration merge --job" in view["actions"][0]


def _not_ready(text: str, data: dict[str, Any], blocker: str) -> None:
    assert "MERGE-READY" not in text and "not merge-ready" in text
    assert data["merge"]["ready"] is False and any(blocker in b for b in data["merge"]["blockers"]), \
        data["merge"]["blockers"]
    assert "blocker: " in text and blocker in text


def test_a_job_is_never_called_merge_ready_when_its_evidence_or_approval_is_ineligible(
        rrig: Rig, signed: IntegrationJob, key: OperatorKey, capsys: pytest.CaptureFixture[str]):
    # (a) the review is no longer eligible under the current independence policy: the approval itself is still valid
    with_review(rrig, independence="require")
    text, data = _status(rrig, signed.job_id, capsys)
    _not_ready(text, data, "the review is not eligible")
    assert data["merge"]["approval"]["valid"] is True and data["merge"]["review"]["eligible"] is False
    assert "review NOT eligible" in text
    with_review(rrig, independence="prefer")
    # (b) another gate plan: M3's validation is no longer current
    roles = dict(rrig.config.github.intake.role_gates)
    with_config(rrig, role_gates={**roles, "backend": (*roles["backend"], "frontend-build")})
    text, data = _status(rrig, signed.job_id, capsys)
    _not_ready(text, data, "M3's validation is not current")
    assert data["merge"]["validation"] == {"eligible": False} and "validation NOT eligible" in text
    with_config(rrig, role_gates=roles)
    assert _status(rrig, signed.job_id, capsys)[1]["merge"]["ready"] is True
    # (c) the key is revoked: the signature no longer verifies
    key.revoke_all()
    text, data = _status(rrig, signed.job_id, capsys)
    _not_ready(text, data, "does not authorize the merge now")
    assert data["merge"]["approval"]["valid"] is False and "NOT VALID" in text
    assert any("does not verify" in p for p in data["merge"]["approval"]["problems"])
    assert any("sign a new merge approval record" in a for a in data["merge"]["actions"])


def test_an_expired_merge_approval_is_shown_as_not_valid(rrig: Rig, ready: IntegrationJob,
                                                         capsys: pytest.CaptureFixture[str]):
    issued, expires = NOW - timedelta(hours=3), NOW - timedelta(hours=1)
    record = rrig.write_record(merge_record_for(ready, issued_at=precise_stamp(issued), expires_at=stamp(expires)))
    result = approve_merge(rrig, ready.job_id, path=record, clock=lambda: NOW - timedelta(hours=2))   # valid THEN
    assert result["outcome"] == "recorded"
    text, data = _status(rrig, ready.job_id, capsys)
    _not_ready(text, data, "does not authorize the merge now")
    assert any("expired at" in p for p in data["merge"]["approval"]["problems"])
    assert data["merge"]["approval"]["expires_at"] == stamp(expires) and f"EXPIRES {stamp(expires)}" in text


def test_status_names_the_pr_an_open_attempt_and_the_merge_result(rrig: Rig, signed: IntegrationJob,
                                                                   capsys: pytest.CaptureFixture[str]):
    def open_merge(data: dict[str, Any]) -> None:
        made = attempt(data, "create-pr", None)
        bound_pr(data, 101, "created", made["id"])
        attempt(data, "merge", 101)
    doc = Doc(rrig, signed.job_id)
    doc.write(open_merge)
    # the intent only: nothing was ever sent, so it is no blocker — the next run closes it and decides afresh
    text, data = _status(rrig, signed.job_id, capsys)
    assert "#101 (bound)" in text and data["merge"]["pr"]["bound"] == 101
    assert data["merge"]["open_attempt"]["action"] == "merge" and data["merge"]["open_attempt"]["state"] == \
        "not-dispatched"
    assert any("never dispatched" in a and "--apply" in a for a in data["merge"]["actions"])
    assert data["merge_open"] is True and data["merge_pr"]["number"] == 101
    # dispatched: the request may have been sent and its outcome is unknown — a blocker
    doc.write(lambda d: dispatch(d, d["evidence"][-1]))
    text, data = _status(rrig, signed.job_id, capsys)
    _not_ready(text, data, "was dispatched and its outcome is unknown")
    assert data["merge"]["open_attempt"]["state"] == "dispatched"
    assert any("integration merge --job" in a and "--apply" in a for a in data["merge"]["actions"])


def test_status_of_a_merged_job_says_verification_is_pending_and_of_a_completed_job_what_was_verified(
        rrig: Rig, merged: IntegrationJob, capsys: pytest.CaptureFixture[str]):
    text, data = _status(rrig, merged.job_id, capsys)
    assert data["state"] == "MERGED" and data["merge"]["ready"] is False
    assert any("post-merge verification is pending" in b for b in data["merge"]["blockers"])
    assert any("integration merge --job" in a for a in data["merge"]["actions"])
    assert f"merge_result: commit {merged.merge_result['commit_sha']}" in text and "verification pending" in text
    assert "VERIFIED" not in text and "blockers: post-merge verification is pending" in text
    report = merge(rrig, merged.job_id)                                                  # repeat: verified now
    assert report.outcome == "completed", (report.outcome, report.reasons)
    text, data = _status(rrig, merged.job_id, capsys)
    result = data["merge_result"]
    assert data["state"] == "COMPLETED" and result["verified"] is True and data["merge"]["blockers"] == []
    assert data["merge"]["ready"] is False and data["merge"]["merge_result"] == result
    assert f"merge_result: commit {result['commit_sha']} (parents " in text and ", VERIFIED" in text
    assert f"tree {result['tree_sha']})" in text and f", PR #{result['pr']}, verification verified" in text
    for check in result["verification"]["checks"]:
        assert f"{check['status']:14s} {check['name']}" in text
    assert "(terminal)" in text and "blocker:" not in text


def test_status_of_a_failed_merge_requires_manual_remediation(rrig: Rig, launchers: Any, github_origin: Path,
                                                              capsys: pytest.CaptureFixture[str]):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.mode = "squash"                                                  # the merge is not the approved one
    report = merge(rrig, job.job_id)
    assert report.outcome == "failed", (report.outcome, report.reasons)
    text, data = _status(rrig, job.job_id, capsys)
    assert data["state"] == "FAILED" and data["merge_result"]["verified"] is False
    assert any("manual operator remediation is required" in b for b in data["merge"]["blockers"])
    assert data["merge"]["ready"] is False and "MERGE-READY" not in text and "FAILED" in text


# -- the CLI ---------------------------------------------------------------------------------------------------------

def _cli_config(tmp_path: Path, key: OperatorKey) -> Path:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["integration"] = {"enabled": True, "known_authors": [KNOWN], "operators": [OPERATOR],
                                     "allowed_signers": str(key.allowed), "trusted_logins": [OPERATOR]}
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_cli_approve_merge_dry_run_apply_json_and_refusal(rrig: Rig, ready: IntegrationJob, key: OperatorKey,
                                                          tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    from feature_coordinator.config import load_config  # pylint: disable=import-outside-toplevel
    config_path = _cli_config(tmp_path, key)
    rrig.config = load_config(config_path)                                       # the CLI's configuration: one plan
    if merge_approval_blockers(rrig.jobs.load(ready.job_id), rrig.config):
        pytest.skip("the example configuration derives another gate plan than the rig's; covered by cmd_* tests")
    base = ["--tracker", "local", "--repo", str(rrig.repo), "--state-dir", str(rrig.state), "--project-config",
            str(config_path), "integration"]
    path = rrig.write_record(merge_record_for(ready, note="via the CLI"))
    argv = [*base, "approve", "--job", ready.job_id, "--stage", "merge", "--record", str(path)]
    assert cli_main(argv) == 0
    out = capsys.readouterr().out
    assert "approve: would-record" in out and "dry run" in out and "stage" not in out.split("would-record")[0]
    assert merge_entry(rrig, ready.job_id) is None
    assert cli_main([*argv, "--apply", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["outcome"] == "recorded" and data["approval"]["id"] == "apr-0002"
    assert data["approval"]["stage"] == "merge" and data["approval"]["merge"]["authorizes"]["create_pr"] is True
    assert cli_main([*base, "status", "--job", ready.job_id]) == 0
    out = capsys.readouterr().out
    assert "merge approval apr-0002 by spyderbg" in out and "VALID" in out and "MERGE-READY" in out
    expired = rrig.write_record(merge_record_for(ready, issued_at=stamp(NOW - timedelta(days=2)),
                                                 expires_at=stamp(NOW - timedelta(days=1))))
    code = cli_main([*base, "approve", "--job", ready.job_id, "--stage", "merge", "--record", str(expired), "--apply",
                     "--json"])
    refusal = json.loads(capsys.readouterr().out)
    assert code == 1 and not refusal["ok"] and refusal["outcome"] == "refused" and "expired" in refusal["reasons"][0]
    code = cli_main([*base, "approve", "--job", ready.job_id, "--stage", "inspect", "--record", str(path), "--apply"])
    assert code == 1 and "takes an inspect approval" in capsys.readouterr().out     # the job is past its inspection
    with pytest.raises(SystemExit) as exc:
        cli_main([*base, "approve", "--job", ready.job_id, "--stage", "bogus", "--record", str(path)])
    assert exc.value.code == 2


def test_cli_approve_merge_prints_the_text_and_refuses_ineligible_evidence(rrig: Rig, ready: IntegrationJob,
                                                                            capsys: pytest.CaptureFixture[str]):
    from feature_coordinator.integration_commands import cmd_approve  # pylint: disable=import-outside-toplevel
    path = rrig.write_record(merge_record_for(ready))
    args = argparse.Namespace(state_dir=str(rrig.state), repo=str(rrig.repo), job=ready.job_id, stage="merge",
                              record=str(path), signature=None, apply=True, json=False)
    assert cmd_approve(args, rrig.config) == 0
    out = capsys.readouterr().out
    assert "approve: recorded" in out and "apr-0002: merge by spyderbg" in out and "PR creation authorized" in out
    line = next(row for row in out.splitlines() if row.startswith("  apr-0002:"))
    assert f"candidate {ready.candidate['head_sha']}" in line and "review rev-" in line and "path(s)" not in line
    again = argparse.Namespace(**{**vars(args), "json": True})
    assert cmd_approve(again, rrig.config) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "existing"
    with_review(rrig, independence="require")
    newer = rrig.write_record(merge_record_for(ready, issued_at=precise_stamp(datetime.now(timezone.utc))))
    again = argparse.Namespace(**{**vars(args), "record": str(newer)})
    assert cmd_approve(again, rrig.config) == 1
    assert "no eligible review applies" in capsys.readouterr().out
