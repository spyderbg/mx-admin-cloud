"""W4.7 (M5), Codex F3 and F4: a write whose outcome is unknown is never repeated because time passed (F3), and a merge
this job did not make is never adopted as its own (F4).

The facts the merge keeps apart (integration_merge_recovery.py): an INTENT (recorded before the write), a DISPATCH
(recorded by the write boundary immediately before the request leaves: its absence proves nothing was sent), GitHub's
ANSWER (an error answer = confirmed failure; the merge = confirmed success), and an unknown outcome (dispatched, no
answer: never closed by a timer). Scripted GitHub behind the real client, real merges on the disposable origin only."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from feature_coordinator.errors import GitHubResponseError
from feature_coordinator.github_api import ApiResponse, TransportFailure
from feature_coordinator.integration import IntegrationError, JobState, abort_job
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.locks import FileLock

from fc_integration_merge_rig import bare, merge, ready_job
from fc_integration_rig import Rig, github_origin, live_client, origin  # pylint: disable=unused-import
from test_fc_integration_merge import Clock, existing_pr
from test_fc_integration_review import history, kinds, launchers, of_kind, rrig  # pylint: disable=unused-import
from test_fc_integration_validate import key, rig, seeded, tracker  # pylint: disable=unused-import

LATER = timedelta(seconds=121)          # one second past the settle window
EXTERNAL_ACTOR = "unrelated-external-operator"


def outcomes(rig: Rig, job_id: str) -> list[tuple[str, str]]:
    return [(o["status"], o["basis"]) for o in of_kind(rig, job_id, "merge-outcome")]


def merged_prs(fake: Any) -> list[dict[str, Any]]:
    return [p for p in fake.prs if p["merged"]]


# -- F3: PR creation --------------------------------------------------------------------------------------------------

def test_f3_an_uncertain_pr_creation_is_not_repeated_after_121_seconds_even_if_the_pr_was_closed(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch):
    """The original reproduction: the create response is lost, the listing shows no PR (it was created and is
    closed), and 121 seconds later the old code created a second PR."""
    job, fake = ready_job(rrig, launchers, github_origin)
    actual = fake.request

    def hide_original(method: str, path: str, body: Any = None) -> ApiResponse:      # a stale listing
        route = urlparse(path).path
        listing = (method == "GET" and "/pulls" in route and not route.rsplit("/", 1)[-1].isdigit()) \
            or (method == "POST" and route == "graphql")
        if listing:
            everything = fake.prs
            fake.prs = [p for p in everything if p["number"] != 101]
            try:
                return actual(method, path, body)
            finally:
                fake.prs = everything
        return actual(method, path, body)
    monkeypatch.setattr(fake, "request", hide_original)
    fake.lose_create_response = True
    first = merge(rrig, job.job_id)
    assert first.outcome == "pr-uncertain" and len(fake.created) == 1 and len(fake.prs) == 1
    fake.prs[0]["state"] = "closed"
    for clock in (None, Clock(LATER), Clock(timedelta(hours=1))):
        again = merge(rrig, job.job_id, clock=clock)
        assert again.outcome in ("pr-uncertain", "pr-unresolved"), (again.outcome, again.reasons)
        assert len(fake.created) == 1 and len(fake.prs) == 1                        # never a second PR
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and not of_kind(rrig, job.job_id, "merge-outcome")


def test_f3_a_creation_the_transport_never_sent_is_closed_and_may_be_tried_again(rrig: Rig, launchers: Any,
                                                                                github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.create_failure = TransportFailure("POST pulls: connection refused", maybe_applied=False)
    first = merge(rrig, job.job_id)
    assert first.outcome == "github-refused", (first.outcome, first.reasons)
    assert outcomes(rrig, job.job_id) == [("not-created", "not-sent")]
    assert merge(rrig, job.job_id).outcome == "completed"
    assert len(fake.prs) == 1 and len(fake.created) == 2
    assert outcomes(rrig, job.job_id)[0] == ("not-created", "not-sent")


def test_f3_concurrent_recovery_is_refused_by_the_job_lock_and_changes_nothing(rrig: Rig, launchers: Any,
                                                                               github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_failure = TransportFailure("PUT merge: no response within 30s", maybe_applied=True)
    assert merge(rrig, job.job_id).outcome == "merge-uncertain"
    before = (rrig.state / "integration" / f"{job.job_id}.json").read_bytes()
    lock = FileLock(rrig.store.locks.dir / f"integration-{job.job_id}.lock")
    lock.acquire(timeout=0)                                                        # another recovery is running
    try:
        with pytest.raises(IntegrationError, match="another integration operation"):
            merge(rrig, job.job_id, clock=Clock(LATER))
    finally:
        lock.release()
    assert (rrig.state / "integration" / f"{job.job_id}.json").read_bytes() == before and len(fake.merge_requests) == 1


# -- F3: the merge request --------------------------------------------------------------------------------------------

def test_f3_an_uncertain_merge_request_is_not_repeated_after_121_seconds(rrig: Rig, launchers: Any,
                                                                         github_origin: Path):
    """The original reproduction: the response is lost; 121 seconds later the old code sent the PUT again."""
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.merge_failure = TransportFailure("request accepted but response lost", maybe_applied=True)
    first = merge(rrig, job.job_id)
    assert first.outcome == "merge-uncertain" and len(fake.merge_requests) == 1
    for clock in (Clock(LATER), Clock(timedelta(hours=1)), Clock(timedelta(hours=1))):
        again = merge(rrig, job.job_id, clock=clock)
        assert again.outcome == "merge-unresolved", (again.outcome, again.reasons)
        assert len(fake.merge_requests) == 1
    assert rrig.jobs.load(job.job_id).attention and not of_kind(rrig, job.job_id, "merge-outcome")


def test_f3_a_stale_pr_state_after_an_accepted_request_is_reconciled_when_it_catches_up_never_repeated(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.lose_merge_response = True                                                # GitHub merged; the answer was lost
    fake.merge_state[101] = {"state": "open", "merged": False, "merge_commit_sha": None, "merged_by": None}  # lagging
    assert merge(rrig, job.job_id).outcome == "merge-uncertain" and len(merged_prs(fake)) == 1
    for clock in (None, Clock(LATER), Clock(timedelta(days=2))):
        assert merge(rrig, job.job_id, clock=clock).outcome in ("merge-uncertain", "merge-unresolved")
        assert len(fake.merge_requests) == 1
    del fake.merge_state[101]                                                      # GitHub catches up
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.merge_requests) == 1 and outcomes(rrig, job.job_id) == [("merged", "actor")]


def test_f3_a_confirmed_rejection_allows_a_new_request_after_fresh_authorization(rrig: Rig, launchers: Any,
                                                                                github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_failure = ApiResponse(405, {}, {"message": "Pull Request is not mergeable"})
    assert merge(rrig, job.job_id).outcome == "merge-rejected"
    assert outcomes(rrig, job.job_id) == [("not-merged", "rejected")]
    assert merge(rrig, job.job_id).outcome == "completed" and len(fake.merge_requests) == 2
    assert outcomes(rrig, job.job_id) == [("not-merged", "rejected"), ("merged", "response")]
    assert len(of_kind(rrig, job.job_id, "merge-dispatch")) == 2                 # one dispatch per request sent


def test_f3_a_merge_request_the_transport_never_sent_is_closed_and_may_be_tried_again(rrig: Rig, launchers: Any,
                                                                                     github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_failure = TransportFailure("PUT merge: connection refused", maybe_applied=False)
    assert merge(rrig, job.job_id).outcome == "merge-rejected"
    assert outcomes(rrig, job.job_id) == [("not-merged", "not-sent")]
    assert merge(rrig, job.job_id).outcome == "completed"
    assert outcomes(rrig, job.job_id) == [("not-merged", "not-sent"), ("merged", "response")]


def test_f3_repeating_the_command_after_an_uncertain_outcome_with_a_dry_run_writes_nothing(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_failure = TransportFailure("PUT merge: no response within 30s", maybe_applied=True)
    assert merge(rrig, job.job_id).outcome == "merge-uncertain"
    before = (rrig.state / "integration" / f"{job.job_id}.json").read_bytes()
    for clock in (None, Clock(LATER)):
        report = merge(rrig, job.job_id, apply=False, clock=clock)
        assert report.outcome in ("merge-uncertain", "merge-unresolved") and not report.ok
    assert (rrig.state / "integration" / f"{job.job_id}.json").read_bytes() == before and len(fake.merge_requests) == 1


# -- F4: a merge this job did not make ---------------------------------------------------------------------------

def crash_before_the_request(rrig: Rig, launchers: Any, github_origin: Path, **kwargs: Any) -> tuple[Any, Any]:
    job, fake = ready_job(rrig, launchers, github_origin, **kwargs)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("before-merge-request",))
    assert not fake.merge_requests
    return job, fake


@pytest.mark.parametrize("actor", [EXTERNAL_ACTOR, None], ids=["another-account", "the-coordinators-own-account"])
def test_f4_an_external_merge_after_an_unsent_intent_is_not_adopted(rrig: Rig, launchers: Any, github_origin: Path,
                                                                    actor: str | None):
    """The original reproduction: intent recorded, crash before the request, an unrelated operator merges the PR,
    the coordinator resumes — it must not record the merge as its own. (Even by the SAME account: no request of this
    job was ever dispatched.)"""
    job, fake = crash_before_the_request(rrig, launchers, github_origin)
    fake.do_merge(fake.prs[0], actor=actor)
    commit = fake.prs[0]["merge_commit_sha"]
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale", (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and stored.merge_result is None and not fake.merge_requests
    assert history(rrig, job.job_id)[-1] == "STALE" and "COMPLETED" not in history(rrig, job.job_id)
    observed = stored.attention["observed"]
    assert observed["attribution"] == "external" and observed["merge_commit"] == commit
    assert observed["merged_by"] == (actor or fake.viewer) and observed["pr"] == 101 and observed["merged_at"]
    assert "EXTERNAL" in stored.attention["reason"] and not [o for o in outcomes(rrig, job.job_id) if o[0] == "merged"]
    assert bare(github_origin, "rev-parse", "refs/heads/dev").strip() == commit          # never reverted or rewritten
    again = merge(rrig, job.job_id)
    assert again.outcome == "terminal" and not fake.merge_requests


def test_f4_a_dry_run_reports_the_external_merge_and_writes_nothing(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = crash_before_the_request(rrig, launchers, github_origin)
    fake.do_merge(fake.prs[0], actor=EXTERNAL_ACTOR)
    before = (rrig.state / "integration" / f"{job.job_id}.json").read_bytes()
    report = merge(rrig, job.job_id, apply=False)
    assert report.outcome == "would-stale" and not report.ok
    assert (rrig.state / "integration" / f"{job.job_id}.json").read_bytes() == before and not fake.merge_requests


@pytest.mark.parametrize("mode", ["merge", "wrong-parent", "wrong-tree"])
def test_f4_an_external_merge_is_stale_whatever_its_parents_or_tree(rrig: Rig, launchers: Any, github_origin: Path,
                                                                   mode: str):
    """The exact candidate (or something else) merged by another actor: still not this job's merge — STALE, never
    FAILED (nothing of this job's is wrong) and never COMPLETED."""
    job, fake = crash_before_the_request(rrig, launchers, github_origin)
    fake.mode = mode
    fake.do_merge(fake.prs[0], actor=EXTERNAL_ACTOR)
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale" and rrig.state_of(job.job_id) is JobState.STALE
    assert not fake.merge_requests and rrig.jobs.load(job.job_id).merge_result is None


def test_f4_an_external_merge_during_an_unknown_outcome_is_not_adopted_either(rrig: Rig, launchers: Any,
                                                                             github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_failure = TransportFailure("PUT merge: no response within 30s", maybe_applied=True)   # not applied
    assert merge(rrig, job.job_id).outcome == "merge-uncertain"
    fake.do_merge(fake.prs[0], actor=EXTERNAL_ACTOR)                              # somebody else merges it meanwhile
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale" and len(fake.merge_requests) == 1
    assert rrig.jobs.load(job.job_id).attention["observed"]["merged_by"] == EXTERNAL_ACTOR


def test_f4_a_merge_request_refused_because_somebody_else_just_merged_is_not_adopted(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.on_merge = lambda: fake.do_merge(fake.prs[0], actor=EXTERNAL_ACTOR)      # lands just before OUR request
    report = merge(rrig, job.job_id)                                              # → HTTP 405: already merged
    assert report.outcome == "stale", (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and stored.merge_result is None and len(fake.merge_requests) == 1
    assert [o for o in outcomes(rrig, job.job_id) if o[0] == "merged"] == []


@pytest.mark.parametrize("how", ["crash-after-the-request", "lost-response"])
def test_f4_a_genuine_merge_followed_by_a_crash_or_a_lost_response_is_recovered_as_this_jobs(
        rrig: Rig, launchers: Any, github_origin: Path, how: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    if how == "lost-response":
        fake.lose_merge_response = True                  # GitHub merged; the answer never arrived: read, attributed
        first = merge(rrig, job.job_id)
        assert first.outcome == "completed", (first.outcome, first.reasons)
    else:
        with pytest.raises(SimulatedCrash):
            merge(rrig, job.job_id, faults=("after-merge-request",))
        assert merge(rrig, job.job_id).outcome == "completed"
    assert len(fake.merge_requests) == 1 and len(merged_prs(fake)) == 1
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.COMPLETED and stored.merge_result["merged_by"] == fake.viewer
    assert outcomes(rrig, job.job_id) == [("merged", "actor")]                    # attributed: dispatched + our account
    assert merge(rrig, job.job_id).outcome == "completed" and len(fake.merge_requests) == 1   # repeated recovery: no-op


def make_unattributable(fake: Any, failure: str) -> None:
    """GitHub's merge is genuine but the facts do not say whose it was."""
    if failure == "viewer-unreadable":
        fake.unreadable = {"user"}                                                # GET /user fails
        return
    real = fake.do_merge

    def anonymous(pr: dict[str, Any], actor: str | None = None) -> str:
        commit = real(pr, actor)
        pr["merged_by"] = None                                                    # GitHub does not name the merger
        return commit
    fake.do_merge = anonymous


@pytest.mark.parametrize("failure", ["viewer-unreadable", "no-merger-reported"])
def test_f4_when_attribution_is_unavailable_the_job_is_neither_completed_nor_stale_and_nothing_is_sent(
        rrig: Rig, launchers: Any, github_origin: Path, failure: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    make_unattributable(fake, failure)
    fake.lose_merge_response = True                                               # merged by the coordinator's account
    for _ in range(3):                                                            # repeated recovery: the same verdict
        report = merge(rrig, job.job_id)
        assert report.outcome == "merge-ambiguous", (report.outcome, report.reasons)
        assert "cannot be attributed" in " ".join(report.reasons)
        stored = rrig.jobs.load(job.job_id)
        assert stored.state is JobState.AWAITING_MERGE_APPROVAL and stored.merge_result is None and stored.attention
        assert len(fake.merge_requests) == 1 and not outcomes(rrig, job.job_id)
    fake.unreadable = set()
    if failure == "no-merger-reported":                                           # GitHub names the merger at last
        fake.prs[0]["merged_by"] = {"login": fake.viewer}
    assert merge(rrig, job.job_id).outcome == "completed" and len(fake.merge_requests) == 1


def test_f4_an_unattributable_merge_can_be_aborted_by_the_operator_once_the_window_has_passed(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    make_unattributable(fake, "viewer-unreadable")
    fake.lose_merge_response = True
    assert merge(rrig, job.job_id).outcome == "merge-ambiguous"
    with pytest.raises(IntegrationError, match="in flight"):                      # young: the request may still land
        abort_job(rrig.jobs, job.job_id, "decided by me", actor="operator", apply=True)
    result = abort_job(rrig.jobs, job.job_id, "decided by me", actor="operator", apply=True,
                       now=datetime.now(timezone.utc) + LATER)
    assert result["outcome"] == "aborted" and "never established" in rrig.jobs.load(job.job_id).history[-1]["reason"]
    assert kinds(rrig, job.job_id)[-1] == "merge-dispatch"


# -- independent review of the F1-F6 fixes ---------------------------------------------------------------------------

def test_review_m1_a_5xx_whose_body_is_not_json_is_no_rejection_of_the_merge(rrig: Rig, launchers: Any,
                                                                            github_origin: Path):
    """The transport raises GitHubResponseError(status=502) for an HTML error page before the request core can class
    it as an uncertain write. The PUT had landed: it must not be recorded as 'no effect' (the merge adopted as
    external)."""
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.on_merge = lambda: fake.do_merge(fake.prs[0])                     # the PUT lands (merged by our account) …
    fake.merge_failure = GitHubResponseError("PUT .../merge: HTTP 502 body is not JSON", status=502)   # … no answer
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert outcomes(rrig, job.job_id) == [("merged", "actor")] and len(fake.merge_requests) == 1


def test_review_m1_a_5xx_whose_body_is_not_json_does_not_close_a_pr_creation_either(rrig: Rig, launchers: Any,
                                                                                   github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.create_failure = GitHubResponseError("POST pulls: HTTP 502 body is not JSON", status=502)
    assert merge(rrig, job.job_id).outcome == "pr-uncertain"
    for clock in (None, Clock(LATER)):
        assert merge(rrig, job.job_id, clock=clock).outcome in ("pr-uncertain", "pr-unresolved")
    assert len(fake.created) == 1 and not of_kind(rrig, job.job_id, "merge-outcome")


@pytest.mark.parametrize("status", [400, 403, 404, 405, 409, 422, 429])
def test_review_m1_only_an_http_4xx_answer_is_a_definite_rejection(rrig: Rig, launchers: Any, github_origin: Path,
                                                                  status: int):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_failure = GitHubResponseError(f"PUT .../merge: HTTP {status}", status=status)
    assert merge(rrig, job.job_id).outcome == "merge-rejected"
    assert outcomes(rrig, job.job_id) == [("not-merged", "rejected")]


def test_review_m3_a_rate_limited_merge_is_not_resent_inside_the_client(rrig: Rig, launchers: Any,
                                                                       github_origin: Path):
    """The client's own backoff would resend the PUT after the dispatch, with no second authorization (an abort, a
    revoked key or an expired approval in between would not be seen)."""
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    rrig.client = live_client(fake, integration=rrig.config.github.integration, max_retries=3)
    fake.merge_failure = ApiResponse(429, {"retry-after": "5"}, {"message": "rate limited"})
    report = merge(rrig, job.job_id)
    assert report.outcome == "merge-rejected" and len(fake.merge_requests) == 1, (report.outcome, report.reasons)
    assert outcomes(rrig, job.job_id) == [("not-merged", "rejected")]
    assert merge(rrig, job.job_id).outcome == "completed"                  # a NEW request, authorized afresh
    assert len(fake.merge_requests) == 2 and len(of_kind(rrig, job.job_id, "merge-dispatch")) == 2


def merged_awaiting_verification(rrig: Rig, launchers: Any, github_origin: Path) -> tuple[Any, Any]:
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("after-merged-record",))
    assert rrig.state_of(job.job_id) is JobState.MERGED
    return job, fake


@pytest.mark.parametrize("lag", [{"merge_commit_sha": None},
                                 {"merged": False, "state": "open", "merge_commit_sha": None}],
                         ids=["no-merge-commit-yet", "pr-not-merged-yet"])
def test_review_l1_a_lagging_pr_read_after_the_merge_is_pending_not_failed(rrig: Rig, launchers: Any,
                                                                          github_origin: Path, lag: dict[str, Any]):
    job, fake = merged_awaiting_verification(rrig, launchers, github_origin)
    fake.merge_state[101] = lag
    report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification", (report.outcome, report.reasons)
    assert rrig.state_of(job.job_id) is JobState.MERGED
    del fake.merge_state[101]
    assert merge(rrig, job.job_id).outcome == "completed"


def test_review_l1_a_branch_read_that_has_not_caught_up_is_pending(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = merged_awaiting_verification(rrig, launchers, github_origin)
    fake.branch_override["dev"] = job.base["expected_sha"]                 # GitHub still shows the tip before the merge
    report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification", (report.outcome, report.reasons)
    assert any("has not caught up" in reason or "not caught up" in reason for reason in report.reasons)
    fake.branch_override.clear()
    assert merge(rrig, job.job_id).outcome == "completed"


def test_review_l3_a_merge_run_without_an_authoritative_configuration_source_refuses_every_write(
        rrig: Rig, launchers: Any, github_origin: Path):
    from feature_coordinator.integration_merge import IntegrationMerger  # pylint: disable=import-outside-toplevel
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    runner = IntegrationMerger(rrig.store, rrig.config, rrig.git, verifier=rrig.verifier,
                               tracker=lambda: rrig.client, sleep=lambda _s: None)       # a client with no anchors
    before = rrig.jobs.load(job.job_id).to_dict()
    with pytest.raises(IntegrationError, match="no integration anchors"):
        runner.run(job.job_id, apply=True)
    assert fake.writes() == [] and rrig.jobs.load(job.job_id).to_dict() == before
    assert merge(rrig, job.job_id).outcome == "completed"                  # the same job proceeds once they are bound
