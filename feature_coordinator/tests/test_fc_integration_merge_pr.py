"""W4.7 (M5): PR reuse and creation for the merge (design §4.1) — `integration merge` on disposable repositories and a
SCRIPTED GitHub behind the real client (fc_integration_merge_rig.FakeMergeGitHub: it performs REAL merges on the
disposable bare `origin`). A compatible PR is reused, a missing one is created ONLY when the signed approval says so,
every other PR situation is reported and nothing is worked around; a crash or an ambiguous answer never produces a
second PR. No network, no real GitHub, no model."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.github_api import ApiResponse
from feature_coordinator.integration import JobState
from feature_coordinator.issue_sync import SimulatedCrash

from fc_integration_merge_rig import FakeMergeGitHub, approve_merge, bare, merge, ready_job
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, REPOSITORY, OperatorKey, Rig, change_on, github_origin, live_client, origin, pr_json,
    request_for, snapshot,
)
from test_fc_integration_merge import Clock, LATER, attention, existing_pr, world
from test_fc_integration_review import (  # pylint: disable=unused-import
    history, kinds, launchers, of_kind, review, rrig, validated_job,
)
from test_fc_integration_validate import X_PY, FakeGates, key, rig, seeded, tracker  # pylint: disable=unused-import

NO_CREATE = {"operation": "merge", "method": "merge", "create_pr": False}


class LaggingGitHub(FakeMergeGitHub):
    """GitHub's eventual consistency: while `lag` is on, a PR created a moment ago is missing from every LISTING (the
    full list, the head query, the commit query) although a read of that PR by number finds it — and, like GitHub, a
    second open PR for the same head and base is refused with HTTP 422."""

    def __init__(self, origin_path: Path) -> None:
        super().__init__(origin_path)
        self.lag = False
        self.hidden: set[int] = set()

    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:
        route = path.split("?")[0]
        by_commit = route.startswith(f"repos/{REPOSITORY}/commits/") and route.endswith("/pulls")
        listing = method == "GET" and (route.endswith(f"repos/{REPOSITORY}/pulls") or by_commit)
        if not (self.lag and listing):
            return super().request(method, path, body)
        full, self.prs = self.prs, [p for p in self.prs if p["number"] not in self.hidden]
        try:
            return super().request(method, path, body)
        finally:
            self.prs = full

    def _create(self, method: str, path: str, body: Any) -> ApiResponse:
        if any(p["state"] == "open" and p["head"]["ref"] == body["head"] and p["base"]["ref"] == body["base"]
               for p in self.prs):
            self.calls.append((method, path))
            self.created.append(body)
            owner = REPOSITORY.split("/", maxsplit=1)[0]
            return ApiResponse(422, {}, {"message": f"A pull request already exists for {owner}:{body['head']}."})
        known = len(self.prs)
        try:
            return super()._create(method, path, body)
        finally:
            if self.lag and len(self.prs) > known:
                self.hidden.add(self.prs[-1]["number"])


class MovingHeadGitHub(FakeMergeGitHub):
    """The source branch moves on origin just as GitHub creates the PR (the PR's head is then another SHA)."""

    def _create(self, method: str, path: str, body: Any) -> ApiResponse:
        source = bare(self.origin, "rev-parse", f"refs/heads/{body['head']}").strip()
        tree = bare(self.origin, "rev-parse", f"{source}^{{tree}}").strip()
        moved = bare(self.origin, "commit-tree", tree, "-p", source, "-m", "moved").strip()
        bare(self.origin, "update-ref", f"refs/heads/{body['head']}", moved, source)
        return super()._create(method, path, body)


def swap_client(rig: Rig, fake: FakeMergeGitHub) -> FakeMergeGitHub:
    rig.client = live_client(fake, integration=rig.config.github.integration)
    return fake


def pr_ready(rig: Rig, launchers_: Any, origin_path: Path, **overrides: Any) -> tuple[Any, FakeMergeGitHub]:
    """A job given `--pr` at intake (PR 123, verified by the intake) driven to a signed merge approval."""
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    fake = FakeMergeGitHub(origin_path, [pr_json(123, head_sha=sha)])
    swap_client(rig, fake)
    job = rig.approved(request_for(rig.repo, sha, pr_number=123))
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert review(rig, job.job_id, launchers_("pass")).outcome == "reviewed"
    assert approve_merge(rig, job.job_id, **overrides)["outcome"] == "recorded"
    return rig.jobs.load(job.job_id), fake


def bound(rig: Rig, job_id: str) -> dict[str, Any]:
    return of_kind(rig, job_id, "merge-pr")[-1]


# -- reuse -----------------------------------------------------------------------------------------------------------

def test_a_compatible_existing_pr_is_reused_and_no_pr_is_created(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert not fake.created and [m for m, _p in fake.writes()] == ["PUT"]
    entry = bound(rrig, job.job_id)
    assert (entry["number"], entry["origin"], entry["attempt"], entry["draft"]) == (101, "discovered", None, False)
    assert entry["head"] == {"repo": REPOSITORY, "ref": BRANCH, "sha": job.source["sha"]}
    assert entry["base"] == {"repo": REPOSITORY, "ref": "dev"}
    assert kinds(rrig, job.job_id)[-4:] == ["merge-pr", "merge-attempt", "merge-dispatch", "merge-outcome"]
    assert rrig.jobs.load(job.job_id).pr is None                    # the job's input is never edited to store it


def test_the_pr_given_at_intake_is_the_recorded_pr_the_approval_is_signed_for_and_the_one_merged(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = pr_ready(rrig, launchers, github_origin)
    approval = [a for a in job.approvals if a["stage"] == "merge"][0]
    assert approval["pr"] == 123 and approval["merge"]["authorizes"]["create_pr"] is False
    assert merge(rrig, job.job_id).outcome == "completed"
    assert bound(rrig, job.job_id)["origin"] == "recorded" and not fake.created
    assert [r["number"] for r in fake.merge_requests] == [123]
    assert rrig.jobs.load(job.job_id).pr["number"] == 123


def test_a_second_compatible_pr_never_replaces_the_recorded_one(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = pr_ready(rrig, launchers, github_origin)
    existing_pr(fake, job, 124)                          # appears after the signing (the inspection refuses two)
    report = merge(rrig, job.job_id)
    assert report.outcome == "pr-blocked" and "more than one candidate PR" in report.reasons[0], report.reasons
    assert fake.writes() == [] and not fake.merge_requests
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


def test_a_recorded_pr_that_was_closed_is_reported_and_nothing_is_created(rrig: Rig, launchers: Any,
                                                                         github_origin: Path):
    job, fake = pr_ready(rrig, launchers, github_origin)
    fake.prs[0].update(state="closed")
    report = merge(rrig, job.job_id)
    assert report.outcome == "pr-blocked" and "it is closed" in report.reasons[0]
    assert fake.writes() == [] and "closed" in attention(rrig, job.job_id)


@pytest.mark.parametrize("apply,outcome", [(True, "stale"), (False, "would-stale")])
def test_a_recorded_pr_whose_head_moved_makes_the_job_stale(rrig: Rig, launchers: Any, github_origin: Path,
                                                           apply: bool, outcome: str):
    job, fake = pr_ready(rrig, launchers, github_origin)
    fake.prs[0]["head"]["sha"] = "c" * 40
    before = world(rrig, github_origin, job.job_id, fake)
    report = merge(rrig, job.job_id, apply=apply)
    assert report.outcome == outcome and fake.writes() == [], (report.outcome, report.reasons)
    if apply:
        assert rrig.state_of(job.job_id) is JobState.STALE and "head moved" in attention(rrig, job.job_id)
    else:
        assert world(rrig, github_origin, job.job_id, fake) == before


# -- creation --------------------------------------------------------------------------------------------------------

def test_a_missing_pr_is_created_with_exactly_the_authorized_request_and_bound_as_evidence(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    assert merge(rrig, job.job_id).outcome == "completed"
    approval = [a for a in rrig.jobs.load(job.job_id).approvals if a["stage"] == "merge"][-1]
    assert len(fake.created) == 1 and len(fake.prs) == 1
    request = fake.created[0]
    assert set(request) == {"title", "body", "head", "base", "draft", "maintainer_can_modify"}
    assert (request["head"], request["base"], request["draft"], request["maintainer_can_modify"]) == \
        (BRANCH, "dev", False, False)
    assert request["title"] == f"Integration {job.job_id}: {BRANCH} into dev"
    assert f"<!-- w47-integration-job: {job.job_id} approval: {approval['id']} -->" in request["body"]
    assert approval["record_sha256"] in request["body"] and job.source["sha"] in request["body"]
    stored = rrig.jobs.load(job.job_id)
    attempts = of_kind(rrig, job.job_id, "merge-attempt")
    assert [(a["action"], a["pr"]) for a in attempts] == [("create-pr", None), ("merge", 101)]
    assert attempts[0]["request"] == {"head": BRANCH, "base": "dev", "draft": False}
    assert attempts[0]["approval"] == approval["id"] and attempts[0]["approval_sha256"] == approval["record_sha256"]
    entry = bound(rrig, job.job_id)
    assert (entry["number"], entry["origin"], entry["attempt"]) == (101, "created", attempts[0]["id"])
    assert stored.pr is None and stored.request_key == job.request_key          # no immutable input was touched


def test_the_pr_is_created_only_when_the_signed_approval_authorizes_it(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin, authorizes=NO_CREATE)
    for apply in (False, True):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == "pr-missing" and "does not authorize creating one" in report.reasons[0]
    assert fake.writes() == [] and not fake.created and rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL
    assert "does not authorize" in attention(rrig, job.job_id)
    existing_pr(fake, job)                            # the operator opens it: reuse needs no creation right
    assert merge(rrig, job.job_id).outcome == "completed" and not fake.created


def test_repeated_merge_commands_never_create_a_second_pr(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.merge_failure = ApiResponse(403, {}, {"message": "Resource not accessible by integration"})
    assert merge(rrig, job.job_id).outcome == "merge-rejected"
    assert len(fake.created) == 1
    for _ in range(2):
        assert merge(rrig, job.job_id, apply=False).outcome == "would-merge"
    assert merge(rrig, job.job_id).outcome == "completed"
    assert len(fake.created) == 1 and len(fake.prs) == 1 and bound(rrig, job.job_id)["origin"] == "created"


def test_the_pr_identity_never_changes_once_bound(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.merge_failure = ApiResponse(403, {}, {"message": "no"})
    assert merge(rrig, job.job_id).outcome == "merge-rejected"
    fake.prs[0].update(state="closed")                                # the bound PR is closed …
    other = pr_json(102, head_ref=BRANCH, head_sha=job.source["sha"])
    fake.prs.append(other)                                          # … and another compatible one appears
    report = merge(rrig, job.job_id)
    assert report.outcome == "pr-blocked" and "more than one candidate PR" in report.reasons[0]
    assert len(fake.created) == 1 and len(fake.merge_requests) == 1 and not other["merged"]
    assert bound(rrig, job.job_id)["number"] == 101


# -- PRs that cannot be reused -----------------------------------------------------------------------------------------

def test_more_than_one_matching_pr_is_reported_and_none_is_reused(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job, 101)
    existing_pr(fake, job, 102)
    for apply in (False, True):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == "pr-blocked" and "more than one candidate PR for this head" in report.reasons[0]
    assert fake.writes() == [] and not [e for e in rrig.jobs.load(job.job_id).evidence if e["kind"] == "merge-pr"]


@pytest.mark.parametrize("changes,fragment", [
    ({"state": "closed"}, "it is closed"),
    ({"merged": True}, "it is merged"),
    ({"base": "main"}, "targets"),
    ({"head_repo": "someone/fork"}, "head repository is someone/fork"),
])
def test_a_matching_pr_that_cannot_be_reused_is_reported_and_no_pr_is_created(
        rrig: Rig, launchers: Any, github_origin: Path, changes: dict[str, Any], fragment: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job, **changes)
    before = world(rrig, github_origin, job.job_id, fake, events=False)
    dry = merge(rrig, job.job_id, apply=False)
    assert dry.outcome == "pr-blocked" and fragment in dry.reasons[0] and not dry.ok
    assert world(rrig, github_origin, job.job_id, fake, events=False) == before
    report = merge(rrig, job.job_id)
    assert report.outcome == "pr-blocked" and fragment in report.reasons[0], (report.outcome, report.reasons)
    assert fake.writes() == [] and not fake.created and not fake.merge_requests
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL and fragment in attention(rrig, job.job_id)


def test_a_pr_merged_outside_the_job_makes_it_stale_and_never_a_second_pr(rrig: Rig, launchers: Any,
                                                                         github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    pr = existing_pr(fake, job)
    fake.do_merge(pr)                                               # somebody merges it by hand: dev moved
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale" and "moved from the pinned" in report.reasons[0], (report.outcome, report.reasons)
    assert rrig.state_of(job.job_id) is JobState.STALE and fake.writes() == [] and not fake.created
    assert rrig.jobs.load(job.job_id).merge_result is None          # never COMPLETED for a merge it did not make


def test_a_draft_pr_is_reported_for_an_operator_and_never_changed(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job, draft=True)
    for apply in (False, True):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == "pr-draft" and not report.ok
        text = report.reasons[0]
        assert "never marks someone else's Draft ready for review" in text and "an operator must do it" in text
    assert all(method == "GET" for method, _path in fake.calls)      # no POST, PATCH or PUT, ever
    assert fake.prs[0]["draft"] is True and not fake.merge_requests
    assert "Draft" in attention(rrig, job.job_id) and rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL
    fake.prs[0]["draft"] = False                                    # the operator marks it ready on GitHub
    assert merge(rrig, job.job_id).outcome == "completed" and not fake.created


def test_github_turning_the_bound_pr_into_a_draft_stops_the_merge(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_state[101] = {"mergeable_state": "draft", "draft": True}
    report = merge(rrig, job.job_id)
    assert report.outcome == "pr-draft" and not fake.merge_requests


# -- crashes, lost and ambiguous answers: never a duplicate ----------------------------------------------------------

def test_a_lost_answer_to_the_pr_creation_is_resolved_by_looking_up_the_pr(rrig: Rig, launchers: Any,
                                                                          github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.lose_create_response = True
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.created) == 1 and len(fake.prs) == 1
    entry = bound(rrig, job.job_id)
    assert entry["origin"] == "created" and entry["attempt"] == of_kind(rrig, job.job_id, "merge-attempt")[0]["id"]


@pytest.mark.parametrize("fault", ["after-pr-intent", "after-pr-created"])
def test_a_crash_around_the_pr_creation_never_creates_a_duplicate(rrig: Rig, launchers: Any, github_origin: Path,
                                                                 fault: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=(fault,))
    created_before = len(fake.created)
    assert created_before == (1 if fault == "after-pr-created" else 0)
    if fault == "after-pr-created":     # dispatched and sent: the PR exists, its number is not recorded
        assert kinds(rrig, job.job_id)[-2:] == ["merge-attempt", "merge-dispatch"]
        assert merge(rrig, job.job_id).outcome == "completed"      # the lookup finds it (its marker) and it is bound
        assert len(fake.created) == len(fake.prs) == 1 and bound(rrig, job.job_id)["origin"] == "created"
    else:                               # the intent only: the request never left (no dispatch entry): ONE creation now
        assert kinds(rrig, job.job_id)[-1] == "merge-attempt"
        assert merge(rrig, job.job_id).outcome == "completed"
        assert len(fake.created) == len(fake.prs) == 1
        outcomes = of_kind(rrig, job.job_id, "merge-outcome")
        assert [(e["status"], e["basis"]) for e in outcomes] == [("not-created", "not-dispatched"),
                                                                 ("merged", "response")]


def test_an_ambiguous_creation_answer_is_never_followed_by_a_second_creation_however_long_it_takes(
        rrig: Rig, launchers: Any, github_origin: Path):
    """Codex F3 (PR creation): a 500 may or may not have created the PR; a listing that shows none proves nothing."""
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.create_failure = ApiResponse(500, {}, {"message": "Server Error"})
    report = merge(rrig, job.job_id)
    assert report.outcome == "pr-uncertain" and "UNKNOWN" in report.reasons[0], (report.outcome, report.reasons)
    assert kinds(rrig, job.job_id)[-1] == "merge-dispatch" and not fake.prs and len(fake.created) == 1
    again = merge(rrig, job.job_id)                                  # within the settle window: no second request
    assert again.outcome == "pr-uncertain" and len(fake.created) == 1
    assert merge(rrig, job.job_id, apply=False).outcome == "pr-uncertain"
    for ahead in (LATER, timedelta(hours=1)):                        # past it: the operator is needed, still no POST
        final = merge(rrig, job.job_id, clock=Clock(ahead))
        assert final.outcome == "pr-unresolved", (final.outcome, final.reasons)
        assert len(fake.created) == 1 and not fake.prs
    expired = merge(rrig, job.job_id, clock=Clock(timedelta(days=3)))   # the approval itself has expired by now
    assert expired.outcome == "no-approval" and len(fake.created) == 1
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and stored.attention
    assert not of_kind(rrig, job.job_id, "merge-outcome")            # elapsed time recorded no outcome
    # the operator's way out is to open the PR on GitHub: the next run finds it, closes the attempt as superseded and
    # merges that PR — never a second creation
    existing_pr(fake, job)
    assert merge(rrig, job.job_id).outcome == "completed" and len(fake.created) == 1
    assert [(e["status"], e["basis"]) for e in of_kind(rrig, job.job_id, "merge-outcome")][0] == \
        ("not-created", "superseded")


def test_a_definite_rejection_of_the_creation_closes_the_attempt_and_reports_it(rrig: Rig, launchers: Any,
                                                                               github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.create_failure = ApiResponse(403, {}, {"message": "Resource not accessible by integration"})
    report = merge(rrig, job.job_id)
    assert report.outcome == "github-refused" and "rejected the PR creation" in report.reasons[0]
    assert not fake.prs and of_kind(rrig, job.job_id, "merge-outcome")[-1]["status"] == "not-created"
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL
    assert merge(rrig, job.job_id).outcome == "completed" and len(fake.created) == 2 and len(fake.prs) == 1


def test_eventual_consistency_finds_the_created_pr_on_the_next_run_and_creates_no_duplicate(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, _fake = ready_job(rrig, launchers, github_origin)
    fake = swap_client(rrig, LaggingGitHub(github_origin))
    fake.lag, fake.lose_create_response = True, True                 # created, the answer lost, the listing lags
    assert merge(rrig, job.job_id).outcome == "pr-uncertain" and len(fake.prs) == 1
    fake.lag = False                                                 # GitHub catches up
    assert merge(rrig, job.job_id).outcome == "completed"
    assert len(fake.created) == len(fake.prs) == 1 and bound(rrig, job.job_id)["origin"] == "created"


def test_a_lagging_listing_after_the_settle_window_never_leads_to_a_second_request(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, _fake = ready_job(rrig, launchers, github_origin)
    fake = swap_client(rrig, LaggingGitHub(github_origin))
    fake.lag, fake.lose_create_response = True, True
    assert merge(rrig, job.job_id).outcome == "pr-uncertain"
    report = merge(rrig, job.job_id, clock=Clock(LATER))             # still lagging: the lookup cannot see the PR
    assert report.outcome == "pr-unresolved", (report.outcome, report.reasons)
    assert len(fake.created) == 1 and len(fake.prs) == 1             # GitHub was not even asked a second time
    assert not of_kind(rrig, job.job_id, "merge-outcome")
    fake.lag = False
    assert merge(rrig, job.job_id, clock=Clock(LATER)).outcome == "completed"    # found at last: reconciled
    assert len(fake.prs) == len(fake.created) == 1 and bound(rrig, job.job_id)["origin"] == "created"


def test_a_pr_created_and_closed_meanwhile_is_not_ignored_and_not_replaced(rrig: Rig, launchers: Any,
                                                                         github_origin: Path):
    """Codex F3: the lost-response creation produced a PR that was closed before the next look; it is reported, and no
    second PR is created in its place."""
    job, _fake = ready_job(rrig, launchers, github_origin)
    fake = swap_client(rrig, LaggingGitHub(github_origin))
    fake.lag, fake.lose_create_response = True, True
    assert merge(rrig, job.job_id).outcome == "pr-uncertain"
    fake.prs[0]["state"] = "closed"
    fake.lag = False
    for ahead in (timedelta(), LATER):
        report = merge(rrig, job.job_id, clock=Clock(ahead))
        assert report.outcome == "pr-blocked", (report.outcome, report.reasons)
        assert len(fake.created) == 1 and len(fake.prs) == 1


def test_a_created_pr_whose_head_moved_makes_the_job_stale(rrig: Rig, launchers: Any, github_origin: Path):
    job, _fake = ready_job(rrig, launchers, github_origin)
    fake = swap_client(rrig, MovingHeadGitHub(github_origin))
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale" and "does not match the approved head" in report.reasons[0], \
        (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and not fake.merge_requests and len(fake.created) == 1
    assert not [e for e in stored.evidence if e["kind"] == "merge-pr"] and stored.pr is None
    assert history(rrig, job.job_id)[-1] == "STALE"


def test_a_dry_run_never_creates_a_pr(rrig: Rig, launchers: Any, github_origin: Path, key: OperatorKey):
    job, fake = ready_job(rrig, launchers, github_origin)
    before = world(rrig, github_origin, job.job_id, fake)
    for _ in range(2):
        report = merge(rrig, job.job_id, apply=False)
        assert report.outcome == "would-create-pr" and not report.github_writes
    assert world(rrig, github_origin, job.job_id, fake) == before and not fake.created
    assert snapshot(rrig.repo, github_origin)["origin"] == before[2]["origin"]
