"""W4.7 (M5): the reconcile-first refinements and the signing template of `integration status`
(integration_merge_recovery.py `_reconcile_first`, integration_merge_status.merge_status).

Same rig as the other M5 tests: disposable checkout and bare origin, a throw-away operator key, a scripted GitHub behind
the real client that really merges on the disposable origin. No network, no real GitHub, no model."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.github_api import TransportFailure
from feature_coordinator.integration import JobState, abort_job
from feature_coordinator.integration_merge_record import merge_record_template
from feature_coordinator.integration_merge_status import merge_status
from feature_coordinator.issue_sync import SimulatedCrash

from fc_integration_merge_rig import approve_merge, merge, merge_record_for, ready_job
from fc_integration_rig import Rig, github_origin, origin  # pylint: disable=unused-import
from test_fc_integration_review import launchers, rrig  # pylint: disable=unused-import
from test_fc_integration_validate import key, rig, seeded, tracker  # pylint: disable=unused-import


def later(hours: float) -> Any:
    return lambda: datetime.now(timezone.utc) + timedelta(hours=hours)


def of_kind(rig: Rig, job_id: str, kind: str) -> list[dict[str, Any]]:
    return [entry for entry in rig.jobs.load(job_id).evidence if entry["kind"] == kind]


def test_an_undispatched_merge_attempt_is_closed_as_a_fact_even_without_a_valid_approval(rrig: Rig, launchers: Any,
                                                                                         github_origin: Path):
    """Reconcile-first records what is established (no request was ever dispatched for the intent): it needs no
    approval — so an operator can abort a job whose merge approval expired meanwhile."""
    job, fake = ready_job(rrig, launchers, github_origin)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("before-merge-request",))
    assert [a["action"] for a in of_kind(rrig, job.job_id, "merge-attempt")] == ["create-pr", "merge"]
    assert abort_job(rrig.jobs, job.job_id, "not wanted", actor="operator", apply=False)["outcome"] == "would-abort"
    assert not of_kind(rrig, job.job_id, "merge-outcome")[1:]
    # three hours later: the approval (two hours) has expired
    report = merge(rrig, job.job_id, clock=later(3))
    assert report.outcome == "no-approval" and not fake.merge_requests
    outcomes = of_kind(rrig, job.job_id, "merge-outcome")
    assert [(o["status"], o["basis"]) for o in outcomes][-1] == ("not-merged", "not-dispatched")
    assert rrig.jobs.load(job.job_id).state is JobState.AWAITING_MERGE_APPROVAL
    result = abort_job(rrig.jobs, job.job_id, "approval expired", actor="operator", apply=True)
    assert result["outcome"] == "aborted"
    assert not [call for call in fake.writes() if call[0] == "PUT"]


def test_a_dispatched_merge_attempt_with_an_unknown_outcome_is_never_closed_by_the_reconciliation(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.merge_failure = TransportFailure("PUT merge: no response within 30s", maybe_applied=True)
    assert merge(rrig, job.job_id).outcome == "merge-uncertain"
    before = list(of_kind(rrig, job.job_id, "merge-outcome"))
    report = merge(rrig, job.job_id, apply=False)           # a dry run writes nothing, whatever the age
    assert of_kind(rrig, job.job_id, "merge-outcome") == before and len(fake.merge_requests) == 1
    assert report.outcome == "merge-uncertain", report.outcome
    for ahead in (0, 3, 72):                                # however old: unresolved, not closed, never repeated
        report = merge(rrig, job.job_id, clock=later(ahead))
        assert report.outcome == ("merge-uncertain" if ahead == 0 else "merge-unresolved"), report.outcome
        assert of_kind(rrig, job.job_id, "merge-outcome") == before and len(fake.merge_requests) == 1


def test_a_merged_pr_without_a_merge_commit_yet_is_deferred_not_stale(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("after-merge-request",))
    assert len(fake.merge_requests) == 1
    sha = fake.prs[0]["merge_commit_sha"]
    fake.prs[0]["merge_commit_sha"] = None                   # GitHub has not (yet) reported the merge commit
    report = merge(rrig, job.job_id)
    assert report.outcome == "deferred", (report.outcome, report.reasons)
    assert rrig.jobs.load(job.job_id).state is JobState.AWAITING_MERGE_APPROVAL
    assert len(fake.merge_requests) == 1
    fake.prs[0]["merge_commit_sha"] = sha                    # reported now: reconciled, verified, completed
    assert merge(rrig, job.job_id).outcome == "completed"
    assert len(fake.merge_requests) == 1


def test_status_offers_the_record_to_sign_and_stops_once_a_valid_approval_exists(rrig: Rig, launchers: Any,
                                                                                  github_origin: Path):
    job, _fake = ready_job(rrig, launchers, github_origin, sign=False)
    when = datetime.now(timezone.utc)
    view = merge_status(job, rrig.config, rrig.verifier, when)
    template = view["record_template"]
    assert template == merge_record_template(job) and view["approval"] is None and not view["ready"]
    assert "no signed merge approval" in view["blockers"]
    expected = merge_record_for(job)
    placeholders = {"operator", "issued_at", "expires_at"}
    assert {k: v for k, v in template.items() if k not in placeholders} == \
        {k: v for k, v in expected.items() if k not in placeholders | {"note"}}
    assert all(str(template[k]).startswith("<") for k in placeholders)
    # filling the placeholders gives a record the approval accepts
    filled = {**template, **{k: expected[k] for k in placeholders}}
    path = rrig.write_record(filled)
    assert approve_merge(rrig, job.job_id, path=path)["outcome"] == "recorded"
    signed = rrig.jobs.load(job.job_id)
    view = merge_status(signed, rrig.config, rrig.verifier, when)
    assert view["record_template"] is None and view["approval"]["valid"] and view["ready"]
    assert view["approval"]["expires_at"] and view["candidate"]["head_sha"] == signed.candidate["head_sha"]


def test_an_expired_approval_is_shown_as_not_valid_and_offers_a_new_template(rrig: Rig, launchers: Any,
                                                                             github_origin: Path):
    job, _fake = ready_job(rrig, launchers, github_origin)
    view = merge_status(job, rrig.config, rrig.verifier, datetime.now(timezone.utc) + timedelta(hours=3))
    assert view["approval"]["valid"] is False and not view["ready"]
    assert any("expired" in problem for problem in view["approval"]["problems"])
    assert view["record_template"] is not None
