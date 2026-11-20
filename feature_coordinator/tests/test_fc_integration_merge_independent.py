"""W4.7 (M5): regression tests for the findings of the independent adversarial review of M5 (M1–M4 and L1–L4 of that
review; every one was reproduced by the reviewer against the code before it was fixed).

  M1  a PR created before a crash is bound after the operator signed a NEWER merge approval (it wedged the job);
  M2  a merge request that may still be in flight ends the run before any staleness check (a late landing moved `dev`
      and made the job terminal STALE over a merged PR); STALE is confirmed by reading the PR again;
  M3  the PR is read again right before the merge intent: a retargeted PR is never merged into another branch;
  M4  a job with a candidate that reached MERGED must carry the complete merge_result (approval tests);
  L1  a 100-character post-merge check name; L2 origin lagging behind GitHub is pending, not FAILED;
  L3  a revoked key also revokes the inspect approval that authorized the candidate's paths; L4 recorded attention
      keeps `status` from saying MERGE-READY.

Same rig as the other M5 tests (disposable repositories, a scripted GitHub that really merges on the disposable origin).
No network, no real GitHub, no model."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument,protected-access

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator import integration_postmerge
from feature_coordinator.github_api import TransportFailure
from feature_coordinator.integration import JobState
from feature_coordinator.integration_merge import IntegrationMerger
from feature_coordinator.integration_intake import ReadOnlyGit
from feature_coordinator.integration_merge_status import merge_status
from feature_coordinator.issue_sync import SimulatedCrash

from fc_integration_merge_rig import approve_merge, merge, merge_record_for, merger, ready_job
from fc_integration_rig import OPERATOR, Rig, github_origin, origin, precise_stamp, stamp  # pylint: disable=unused-import
from test_fc_integration_review import launchers, rrig  # pylint: disable=unused-import
from test_fc_integration_validate import key, rig, seeded, tracker  # pylint: disable=unused-import


def later(hours: float) -> Any:
    return lambda: datetime.now(timezone.utc) + timedelta(hours=hours)


def of_kind(rig: Rig, job_id: str, kind: str) -> list[dict[str, Any]]:
    return [entry for entry in rig.jobs.load(job_id).evidence if entry["kind"] == kind]


def with_checks(rig: Rig, **changes: Any) -> Rig:
    integration = dataclasses.replace(rig.config.github.integration, **changes)
    rig.config = dataclasses.replace(rig.config, github=dataclasses.replace(rig.config.github, integration=integration))
    return rig


# -- M1 ------------------------------------------------------------------------------------------------------------

def test_m1_a_pr_created_before_a_crash_is_bound_after_a_newer_approval_replaced_the_signer_of_the_attempt(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("after-pr-created",))
    assert len(fake.created) == 1 and not of_kind(rrig, job.job_id, "merge-pr")
    first = [a for a in rrig.jobs.load(job.job_id).approvals if a["stage"] == "merge"][-1]["id"]
    recorded = approve_merge(rrig, job.job_id, issued_at=precise_stamp(datetime.now(timezone.utc)))
    assert recorded["outcome"] == "recorded" and recorded["approval"]["id"] != first
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    bound = of_kind(rrig, job.job_id, "merge-pr")[0]
    attempt = of_kind(rrig, job.job_id, "merge-attempt")[0]
    assert bound["origin"] == "created" and bound["attempt"] == attempt["id"]
    assert attempt["approval"] == first and bound["approval"] == recorded["approval"]["id"]
    assert len(fake.created) == 1 and len(fake.merge_requests) == 1       # never a second PR, one merge request


# -- M2 ------------------------------------------------------------------------------------------------------------

def test_m2_a_young_dispatched_merge_attempt_ends_the_run_before_any_staleness_check(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.merge_failure = TransportFailure("PUT merge: no response within 30s", maybe_applied=True)   # lost, not applied
    assert merge(rrig, job.job_id).outcome == "merge-uncertain" and len(fake.merge_requests) == 1
    checks: list[str] = []
    original = IntegrationMerger._verify
    monkeypatch.setattr(IntegrationMerger, "_verify",
                        lambda self, *args, **kwargs: checks.append("verify") or original(self, *args, **kwargs))
    report = merge(rrig, job.job_id)
    assert report.outcome == "merge-uncertain" and not checks and len(fake.merge_requests) == 1
    assert rrig.jobs.load(job.job_id).state is JobState.AWAITING_MERGE_APPROVAL
    fake.do_merge(fake.prs[0])                      # the request that was in flight lands: dev moves (by our account)
    report = merge(rrig, job.job_id)                # → reconciled, verified; never STALE
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.merge_requests) == 1 and rrig.jobs.load(job.job_id).state is JobState.COMPLETED


def test_m2_a_merge_that_lands_after_the_last_read_is_never_a_terminal_stale_over_a_merged_pr(
        rrig: Rig, launchers: Any, github_origin: Path):
    """A job with a merge attempt (here: closed, nothing was ever sent), the PR read as unmerged — then the PR gets
    merged and the pinned-value check finds the base moved. The PR is read AGAIN before STALE is recorded: it is merged,
    so the run defers instead of recording a terminal STALE over an unrecorded merge, and the next run reconciles it
    — as an EXTERNAL merge (this job sent nothing): STALE with the merge's details."""
    job, fake = ready_job(rrig, launchers, github_origin)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("before-merge-request",))
    run = merger(rrig)
    close = run._close_undispatched

    def landing(*args: Any, **kwargs: Any) -> None:
        close(*args, **kwargs)
        fake.do_merge(fake.prs[0], actor="somebody-else")

    run._close_undispatched = landing                                     # type: ignore[method-assign]
    report = run.run(job.job_id, apply=True)
    assert report.outcome == "merge-uncertain", (report.outcome, report.reasons)
    assert rrig.jobs.load(job.job_id).state is JobState.AWAITING_MERGE_APPROVAL
    assert [(o["status"], o["basis"]) for o in of_kind(rrig, job.job_id, "merge-outcome")] == \
        [("not-merged", "not-dispatched")]
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale", (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and stored.attention["observed"]["attribution"] == "external"
    assert stored.attention["observed"]["merged_by"] == "somebody-else" and not fake.merge_requests


# -- M3 ------------------------------------------------------------------------------------------------------------

def _tamper_after_pins(run: IntegrationMerger, change: Any) -> None:
    pins = run._final_pins

    def after(*args: Any, **kwargs: Any) -> None:
        pins(*args, **kwargs)
        change()

    run._final_pins = after                                               # type: ignore[method-assign]


def test_m3_a_pr_retargeted_after_the_lookup_is_never_merged_into_another_branch(rrig: Rig, launchers: Any,
                                                                                github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    run = merger(rrig)
    _tamper_after_pins(run, lambda: fake.prs and fake.prs[0]["base"].update(ref="main"))
    report = run.run(job.job_id, apply=True)
    assert report.outcome == "pr-blocked" and "main" in " ".join(report.reasons), (report.outcome, report.reasons)
    assert not fake.merge_requests and rrig.jobs.load(job.job_id).state is JobState.AWAITING_MERGE_APPROVAL
    assert not [a for a in of_kind(rrig, job.job_id, "merge-attempt") if a["action"] == "merge"]
    assert rrig.jobs.load(job.job_id).attention is not None


def test_m3_a_pr_head_that_moved_after_the_lookup_makes_the_job_stale(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    run = merger(rrig)
    _tamper_after_pins(run, lambda: fake.prs and fake.prs[0]["head"].update(sha="f" * 40))
    report = run.run(job.job_id, apply=True)
    assert report.outcome == "stale" and not fake.merge_requests
    assert rrig.jobs.load(job.job_id).state is JobState.STALE


# -- L1, L2 --------------------------------------------------------------------------------------------------------

class AnyChecks(dict):
    """Check runs for ANY commit (the merge commit's SHA is not known in advance)."""

    def __init__(self, runs: list[dict[str, Any]]) -> None:
        super().__init__()
        self.runs = runs

    def get(self, key: Any, default: Any = None) -> Any:
        return self.runs


def test_l1_a_post_merge_check_name_of_100_characters_completes_the_job(rrig: Rig, launchers: Any,
                                                                       github_origin: Path):
    name = "c" * 100
    with_checks(rrig, post_merge_checks=(name,))
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.checks = AnyChecks([{"id": 1, "name": name, "status": "completed", "conclusion": "success"}])
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    names = [c["name"] for c in rrig.jobs.load(job.job_id).merge_result["verification"]["checks"]]
    assert f"check {name}" in names


def test_l2_origin_lagging_behind_github_is_pending_never_failed(rrig: Rig, launchers: Any, github_origin: Path,
                                                                monkeypatch: pytest.MonkeyPatch):
    """GitHub says the base branch contains the merge commit; origin's copy (fetched) does not contain it yet: not a
    failure — the job stays MERGED (pending) until origin has caught up. A stale fetch tip, where the commit object is
    not even present, is `unavailable` and pending as well."""
    job, _fake = ready_job(rrig, launchers, github_origin)
    original, original_fetch = ReadOnlyGit.is_ancestor, integration_postmerge.isolated_fetch
    monkeypatch.setattr(ReadOnlyGit, "is_ancestor", lambda self, ancestor, descendant: False)
    report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification", (report.outcome, report.reasons)
    assert rrig.jobs.load(job.job_id).state is JobState.MERGED
    assert any(c["name"] == "commit" and c["status"] == "pending" for c in report.verification["checks"])
    monkeypatch.setattr(ReadOnlyGit, "is_ancestor", original)
    monkeypatch.setattr(integration_postmerge, "isolated_fetch", lambda *args, **kwargs: job.base["expected_sha"])
    report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification" and rrig.jobs.load(job.job_id).state is JobState.MERGED
    monkeypatch.setattr(integration_postmerge, "isolated_fetch", original_fetch)
    assert merge(rrig, job.job_id).outcome == "completed"


# -- L3, L4 --------------------------------------------------------------------------------------------------------

def test_l3_a_revoked_key_also_revokes_the_inspect_approval_that_authorized_the_candidate(rrig: Rig, launchers: Any,
                                                                                         github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin, sign=False)
    second = rrig.key.new_key(OPERATOR)                    # another key of the same operator, allowed as well
    path = rrig.write_record(merge_record_for(job), key=second)
    assert approve_merge(rrig, job.job_id, path=path)["outcome"] == "recorded"
    allowed = rrig.key.allowed
    allowed.write_text(allowed.read_text(encoding="utf-8").splitlines()[-1] + "\n", encoding="utf-8")   # first revoked
    report = merge(rrig, job.job_id)
    assert report.outcome == "no-approval" and "inspect approval" in " ".join(report.reasons), report.reasons
    assert not fake.writes() and rrig.jobs.load(job.job_id).state is JobState.AWAITING_MERGE_APPROVAL


def test_l3_an_expired_inspect_approval_still_does_not_block_the_merge(rrig: Rig, launchers: Any,
                                                                      github_origin: Path):
    """The inspect approval (two hours) has expired, the merge approval (six hours) has not: the merge goes through —
    only the inspect approval's signature and operator are re-verified, never its expiry."""
    expires = stamp(datetime.now(timezone.utc) + timedelta(hours=6))
    job, _fake = ready_job(rrig, launchers, github_origin, expires_at=expires)
    report = merge(rrig, job.job_id, clock=later(3))
    assert report.outcome == "completed", (report.outcome, report.reasons)


def test_l4_recorded_attention_keeps_status_from_saying_merge_ready(rrig: Rig, launchers: Any, github_origin: Path):
    with_checks(rrig, required_checks=("ci",))
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.checks[job.source["sha"]] = [{"id": 1, "name": "ci", "status": "completed", "conclusion": "failure"}]
    report = merge(rrig, job.job_id)
    assert report.outcome == "checks-failed" and not fake.merge_requests
    current = rrig.jobs.load(job.job_id)
    assert current.attention is not None
    view = merge_status(current, rrig.config, rrig.verifier, datetime.now(timezone.utc))
    assert not view["ready"] and any("attention" in blocker for blocker in view["blockers"]), view["blockers"]
    fake.checks[job.source["sha"]] = [{"id": 2, "name": "ci", "status": "completed", "conclusion": "success"}]
    assert merge(rrig, job.job_id).outcome == "completed"
    assert rrig.jobs.load(job.job_id).attention is None
