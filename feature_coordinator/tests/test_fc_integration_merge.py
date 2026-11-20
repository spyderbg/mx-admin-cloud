"""W4.7 (M5): the operator-approved GitHub merge — preconditions, the merge itself, the client's write restrictions
(integration_merge.py, github_api.py) on disposable repositories and a SCRIPTED GitHub behind the real client.

Each test: the M1→M4 chain of the shared rig (intake, inspect approval, inspection, M3 validation with a recording fake
gate runner, M4 review PASS by a fake reviewer process), a signed merge approval, and `FakeMergeGitHub`, which performs
REAL merges on the disposable bare `origin` — never on a real repository. No network, no real GitHub, no model. PR
handling is in test_fc_integration_merge_pr.py."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines

from __future__ import annotations

import dataclasses
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from feature_coordinator import integration_candidate
from feature_coordinator.config import GitHubConfig, GitHubPublishConfig, default_config
from feature_coordinator.errors import IntegrationWriteRefused, PullRequestPolicyError
from feature_coordinator.github_api import ApiResponse, GitHubClient, TransportFailure
from feature_coordinator.integration import IntegrationError, JobState, abort_job
from feature_coordinator.integration_authority import IntegrationAnchors, IntegrationWriteAuthority
from feature_coordinator.integration_config import GitHubIntegrationConfig
from feature_coordinator.locks import FileLock

from fc_integration_merge_rig import FakeMergeGitHub, approve_merge, bare, github_for, merge, ready_job
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, REPOSITORY, OperatorKey, Rig, change_on, github_origin, head, issue_json, origin, pr_json, snapshot, stamp,
)
from test_fc_integration_review import (  # pylint: disable=unused-import
    history, kinds, launchers, of_kind, review, rrig, validated_job, with_review,
)
from test_fc_integration_validate import (  # pylint: disable=unused-import
    X_PY, FakeGates, key, rig, seeded, tracker, with_config, with_integration,
)

LATER = timedelta(minutes=5)


def world(rig: Rig, origin_path: Path, job_id: str, fake: FakeMergeGitHub, *, events: bool = True) -> tuple:
    """Everything a refused or dry run must leave alone: the record, (optionally) the audit log, the checkout and
    origin, and every GitHub write."""
    log = rig.state / "events.jsonl"
    return ((rig.state / "integration" / f"{job_id}.json").read_bytes(),
            log.read_bytes() if events and log.exists() else b"", snapshot(rig.repo, origin_path), list(fake.writes()))


def ready_with(rig: Rig, launchers_: Any, origin_path: Path, *, mode: str = "pass", **overrides: Any) -> tuple:
    """Like ready_job, with another reviewer mode (notes → PASS_WITH_NOTES)."""
    job = validated_job(rig)
    report = review(rig, job.job_id, launchers_(mode))
    assert report.outcome == "reviewed", (report.outcome, report.reasons)
    fake = github_for(rig, origin_path)
    assert approve_merge(rig, job.job_id, **overrides)["outcome"] == "recorded"
    return rig.jobs.load(job.job_id), fake


def existing_pr(fake: FakeMergeGitHub, job: Any, number: int = 101, **changes: Any) -> dict[str, Any]:
    """A compatible open PR of the job's head on the scripted GitHub."""
    pr = pr_json(number, head_ref=job.source["ref"], head_sha=job.source["sha"], **changes)
    fake.prs.append(pr)
    return pr


def attention(rig: Rig, job_id: str) -> str:
    stored = rig.jobs.load(job_id)
    assert stored.attention is not None
    return " ".join(stored.attention["reasons"])


class Clock:  # pylint: disable=too-few-public-methods
    """A clock that can be moved forward (the approval lasts two hours)."""

    def __init__(self, ahead: timedelta = timedelta()) -> None:
        self.ahead = ahead

    def __call__(self) -> datetime:
        return datetime.now(timezone.utc) + self.ahead


# -- a successful merge ---------------------------------------------------------------------------------------------

def test_a_successful_merge_is_one_put_with_the_approved_sha_and_nothing_else(rrig: Rig, launchers: Any,
                                                                            github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    dev_before = bare(github_origin, "rev-parse", "refs/heads/dev").strip()
    branch_before = bare(github_origin, "rev-parse", f"refs/heads/{BRANCH}").strip()
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed" and report.ok and report.changed, (report.outcome, report.reasons)
    # exactly one PR creation and one merge request: no squash, no rebase, no other write
    assert [m for m, _p in fake.writes()] == ["POST", "PUT"] and len(fake.created) == 1
    pr = fake.prs[0]
    assert fake.merge_requests == [{"number": pr["number"], "merge_method": "merge", "sha": job.source["sha"]}]
    assert fake.writes()[1][1].endswith(f"/pulls/{pr['number']}/merge")
    # dev advanced only by GitHub's merge commit (parents: approved base, approved source); the branch is untouched
    dev_after = bare(github_origin, "rev-parse", "refs/heads/dev").strip()
    parents = bare(github_origin, "rev-list", "--parents", "-n", "1", dev_after).split()[1:]
    assert parents == [dev_before, job.source["sha"]] and dev_after == pr["merge_commit_sha"]
    assert bare(github_origin, "rev-parse", f"refs/heads/{BRANCH}").strip() == branch_before == job.source["sha"]
    assert bare(github_origin, "rev-list", "--count", f"{dev_before}..{dev_after}").strip() == "2"
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.COMPLETED and stored.pr is None            # the immutable PR input is not edited
    assert history(rrig, job.job_id)[-3:] == ["AWAITING_MERGE_APPROVAL", "MERGED", "COMPLETED"]
    assert kinds(rrig, job.job_id)[-6:] == ["merge-attempt", "merge-dispatch", "merge-pr", "merge-attempt",
                                            "merge-dispatch", "merge-outcome"]
    result = stored.merge_result
    assert result["verified"] is True and result["commit_sha"] == dev_after and result["pr"] == pr["number"]
    assert result["parents"] == [dev_before, job.source["sha"]] and result["tree_sha"] == stored.candidate["tree_sha"]
    assert result["verification"]["state"] == "verified"
    event_kinds = [e["kind"] for e in rrig.store.events()]
    for expected in ("integration-merge-attempt", "integration-merge-pr", "integration-merged",
                     "integration-completed"):
        assert expected in event_kinds


def test_the_merge_path_never_starts_a_reviewer(rrig: Rig, launchers: Any, github_origin: Path):
    launcher = launchers("pass")
    job = validated_job(rrig)
    assert review(rrig, job.job_id, launcher).outcome == "reviewed"
    github_for(rrig, github_origin)
    assert approve_merge(rrig, job.job_id)["outcome"] == "recorded"
    runs = len(launcher.calls)
    assert merge(rrig, job.job_id).outcome == "completed"
    assert len(launcher.calls) == runs == 1                       # only the M4 review ever started a process


@pytest.mark.parametrize("mode,outcome", [("pass", "PASS"), ("notes", "PASS_WITH_NOTES")])
def test_pass_and_pass_with_notes_both_merge(rrig: Rig, launchers: Any, github_origin: Path, mode: str, outcome: str):
    job, _fake = ready_with(rrig, launchers, github_origin, mode=mode)
    assert of_kind(rrig, job.job_id, "review")[-1]["outcome"] == outcome
    assert merge(rrig, job.job_id).outcome == "completed"


def test_a_completed_job_is_reported_and_nothing_is_repeated(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    assert merge(rrig, job.job_id).outcome == "completed"
    writes, record = list(fake.writes()), (rrig.state / "integration" / f"{job.job_id}.json").read_bytes()
    for apply in (True, False):
        again = merge(rrig, job.job_id, apply=apply)
        assert again.outcome == "completed" and again.ok and not again.changed
    assert fake.writes() == writes and (rrig.state / "integration" / f"{job.job_id}.json").read_bytes() == record


# -- preconditions: state, evidence, approval ----------------------------------------------------------------------

def test_a_job_that_is_not_reviewed_yet_is_never_merged(rrig: Rig, launchers: Any, github_origin: Path):
    job = validated_job(rrig)                                      # VALIDATING: M3 passed, no review
    fake = github_for(rrig, github_origin)
    for apply in (True, False):
        with pytest.raises(IntegrationError, match="merge applies to a reviewed job"):
            merge(rrig, job.job_id, apply=apply)
    assert fake.writes() == [] and rrig.state_of(job.job_id) is JobState.VALIDATING
    first = launchers("invalid")                                   # IN_REVIEW: the review ended in ERROR
    assert review(rrig, job.job_id, first).outcome == "error"
    with pytest.raises(IntegrationError, match="merge applies to a reviewed job"):
        merge(rrig, job.job_id)
    assert rrig.state_of(job.job_id) is JobState.IN_REVIEW and fake.writes() == []


def test_a_job_whose_review_requested_changes_is_never_merged(rrig: Rig, launchers: Any, github_origin: Path):
    job = validated_job(rrig)
    assert review(rrig, job.job_id, launchers("changes")).outcome == "changes-requested"
    fake = github_for(rrig, github_origin)
    for apply in (True, False):
        with pytest.raises(IntegrationError, match="merge applies to a reviewed job"):
            merge(rrig, job.job_id, apply=apply)
    assert rrig.state_of(job.job_id) is JobState.NEEDS_REPAIR and fake.writes() == []


def test_a_merge_needs_a_signed_merge_approval(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin, sign=False)
    for apply in (False, True):
        before = world(rrig, github_origin, job.job_id, fake, events=False)
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == "no-approval" and not report.ok and "no merge approval" in report.reasons[0]
        assert world(rrig, github_origin, job.job_id, fake, events=False) == before
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL and not fake.merge_requests


def test_an_inspect_approval_is_not_a_merge_approval(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin, sign=False)
    assert [a["stage"] for a in job.approvals] == ["inspect"]
    assert merge(rrig, job.job_id).outcome == "no-approval" and fake.writes() == []


def test_a_review_the_current_independence_policy_refuses_blocks_the_merge_until_it_is_accepted_again(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    with_review(rrig, independence="require")
    for apply in (False, True):
        before = world(rrig, github_origin, job.job_id, fake, events=False)
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == "not-reviewed" and "independence" in report.reasons[0]
        assert world(rrig, github_origin, job.job_id, fake, events=False) == before
    with_review(rrig, independence="prefer")
    assert merge(rrig, job.job_id).outcome == "completed"


def test_a_changed_gate_plan_after_the_review_blocks_the_merge(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    roles = dict(rrig.config.github.intake.role_gates)
    with_config(rrig, role_gates={**roles, "backend": (*roles["backend"], "frontend-build")})
    for apply in (False, True):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == "not-validated" and "gate plan changed" in report.reasons[0]
    assert fake.writes() == [] and rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL
    with_config(rrig, role_gates=roles)                           # restored: mergeable again
    assert merge(rrig, job.job_id).outcome == "completed"


def test_the_inspect_approval_expiring_does_not_block_a_merge_under_a_valid_merge_approval(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, _fake = ready_job(rrig, launchers, github_origin,
                          expires_at=stamp(datetime.now(timezone.utc) + timedelta(hours=8)))
    inspect = [a for a in job.approvals if a["stage"] == "inspect"][0]
    clock = Clock(timedelta(hours=3))                              # the inspect approval lasted two hours
    assert datetime.fromisoformat(inspect["expires_at"].replace("Z", "+00:00")) < clock()
    assert merge(rrig, job.job_id, clock=clock).outcome == "completed"


def test_an_expired_merge_approval_refuses_also_for_a_dry_run(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    for apply in (False, True):
        report = merge(rrig, job.job_id, apply=apply, clock=Clock(timedelta(hours=3)))
        assert report.outcome == "no-approval" and "expired" in report.reasons[0]
    assert fake.writes() == [] and rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


def test_a_revoked_key_refuses_the_merge(rrig: Rig, launchers: Any, github_origin: Path, key: OperatorKey):
    job, fake = ready_job(rrig, launchers, github_origin)
    key.revoke_all()
    report = merge(rrig, job.job_id)
    assert report.outcome == "no-approval" and "does not verify" in report.reasons[0] and fake.writes() == []


def test_an_approval_that_expires_between_the_checks_and_the_decision_records_no_intent(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    clock = Clock()
    original = fake.request

    def moving(method: str, path: str, body: Any = None) -> ApiResponse:
        if method == "GET" and path.endswith("/pulls/101"):        # GitHub's merge requirements: the last check
            clock.ahead = timedelta(hours=3)
        return original(method, path, body)
    fake.request = moving                                          # type: ignore[method-assign]
    report = merge(rrig, job.job_id, clock=clock)
    assert report.outcome == "no-approval" and "expired" in report.reasons[0], (report.outcome, report.reasons)
    assert fake.writes() == [] and not any(e["kind"] == "merge-attempt" and e["action"] == "merge"
                                           for e in rrig.jobs.load(job.job_id).evidence)
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


def test_the_guard_reads_the_clock_again_inside_the_decision_transaction(rrig: Rig, launchers: Any,
                                                                        github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    clock = Clock()
    run = merger_with(rrig, clock)
    original = run._merge_confirmed
    calls = {"n": 0}

    def confirmed(*args: Any, **kwargs: Any) -> str:
        digest = original(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 2:                                        # the confirmation before the merge intent passed
            clock.ahead = timedelta(hours=3)                       # … and the approval expires before the transaction
        return digest
    run._merge_confirmed = confirmed                               # type: ignore[method-assign]
    report = run.run(job.job_id, apply=True)
    assert report.outcome == "no-approval" and "expired" in report.reasons[0], (report.outcome, report.reasons)
    assert not fake.merge_requests and kinds(rrig, job.job_id)[-1] == "merge-pr"      # bound, but no merge intent


def merger_with(rig: Rig, clock: Callable[[], datetime]) -> Any:
    from fc_integration_merge_rig import merger  # pylint: disable=import-outside-toplevel
    return merger(rig, clock=clock)


def test_a_key_revoked_after_the_confirmation_records_no_intent(rrig: Rig, launchers: Any, github_origin: Path,
                                                                key: OperatorKey):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    run = merger_with(rrig, Clock())
    original = run._merge_confirmed
    calls = {"n": 0}

    def confirmed(*args: Any, **kwargs: Any) -> str:
        digest = original(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 2:
            key.revoke_all()
        return digest
    run._merge_confirmed = confirmed                               # type: ignore[method-assign]
    report = run.run(job.job_id, apply=True)
    assert report.outcome == "no-approval", (report.outcome, report.reasons)
    assert not fake.merge_requests and kinds(rrig, job.job_id)[-1] == "merge-pr"


def test_a_newer_merge_approval_supersedes_the_older_one(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)          # apr-0002 authorizes creating the PR
    newer = approve_merge(rrig, job.job_id, authorizes={"operation": "merge", "method": "merge", "create_pr": False})
    assert newer["outcome"] == "recorded" and newer["effective"]
    report = merge(rrig, job.job_id)
    assert report.outcome == "pr-missing" and "does not authorize creating one" in report.reasons[0]
    assert not fake.created and report.approval == "apr-0003"    # the newer decision is the effective one


def test_a_merge_approval_for_other_evidence_cannot_be_recorded_and_authorizes_nothing(rrig: Rig, launchers: Any,
                                                                                       github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin, sign=False)
    result = approve_merge(rrig, job.job_id, review={"evidence": "rev-0099", "sha256": "0" * 64, "outcome": "PASS"})
    assert result["outcome"] == "refused" and "does not carry" in result["reasons"][0]
    assert merge(rrig, job.job_id).outcome == "no-approval" and fake.writes() == []


# -- preconditions: pinned values ---------------------------------------------------------------------------------

@pytest.mark.parametrize("moved", ["source", "base"])
def test_a_moved_source_or_base_makes_the_job_stale_and_nothing_is_rewritten(rrig: Rig, launchers: Any,
                                                                            github_origin: Path, moved: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    branch = BRANCH if moved == "source" else "dev"
    new = change_on(rrig.repo, branch, {"backend/src/y.py": '"""Y."""\nY = 1\n'})
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale" and not report.ok, (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and "moved from the pinned" in stored.attention["reason"]
    assert fake.writes() == [] and head(rrig.repo, branch) == new                  # no rebase, no regeneration
    assert stored.source == job.source and stored.base == job.base and stored.candidate == job.candidate
    again = merge(rrig, job.job_id)                                                # the old approval authorizes nothing
    assert again.outcome == "terminal" and fake.writes() == []


@pytest.mark.parametrize("moved", ["source", "base"])
def test_a_dry_run_reports_a_moved_pin_without_recording_it(rrig: Rig, launchers: Any, github_origin: Path, moved: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    change_on(rrig.repo, BRANCH if moved == "source" else "dev", {"backend/src/y.py": '"""Y."""\nY = 1\n'})
    before = world(rrig, github_origin, job.job_id, fake)
    report = merge(rrig, job.job_id, apply=False)
    assert report.outcome == "would-stale" and not report.ok
    assert world(rrig, github_origin, job.job_id, fake) == before


def test_a_rebuilt_candidate_with_another_identity_makes_the_job_stale(rrig: Rig, launchers: Any, github_origin: Path,
                                                                     monkeypatch: pytest.MonkeyPatch):
    job, fake = ready_job(rrig, launchers, github_origin)
    monkeypatch.setattr(integration_candidate, "CANDIDATE_IDENTITY", "Someone <someone@example.invalid>")
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale"
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and "candidate identity changed" in stored.attention["reason"]
    assert stored.candidate == job.candidate and fake.writes() == []


def test_a_candidate_path_that_is_no_longer_authorized_is_refused_without_a_state_change(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    rrig.config = dataclasses.replace(rrig.config, protected=(*rrig.config.protected, "backend/src/x.py"))
    before = world(rrig, github_origin, job.job_id, fake, events=False)
    for apply in (False, True):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == "not-current" and "no longer authorized" in report.reasons[0], \
            (report.outcome, report.reasons)
    # AWAITING_MERGE_APPROVAL has no FAILED edge: the job stays, the merge refuses
    assert world(rrig, github_origin, job.job_id, fake, events=False) == before


@pytest.mark.parametrize("state,outcome", [("closed", "issue-blocked")])
def test_a_closed_issue_blocks_the_merge(rrig: Rig, launchers: Any, github_origin: Path, state: str, outcome: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.issues[63] = issue_json(63, state=state)
    for apply in (False, True):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome in (outcome, f"would-{outcome}") and "does not justify" in report.reasons[0]
    assert fake.writes() == [] and rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


@pytest.mark.parametrize("fragment", ["/issues/", "/pulls"])
def test_an_unreadable_github_defers_and_merges_nothing(rrig: Rig, launchers: Any, github_origin: Path, fragment: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.unreadable = {fragment}
    before = world(rrig, github_origin, job.job_id, fake, events=False)
    for apply, outcome in ((False, "would-defer"), (True, "deferred")):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == outcome and not report.ok, (report.outcome, report.reasons)
    assert world(rrig, github_origin, job.job_id, fake, events=False) == before
    fake.unreadable = set()                                        # GitHub is back: the same command goes on
    assert merge(rrig, job.job_id).outcome == "completed"


def test_unreadable_merge_requirements_defer_before_any_merge_request(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.unreadable = {"/pulls/101"}
    report = merge(rrig, job.job_id)
    assert report.outcome == "deferred" and "merge requirements" in report.reasons[0]
    assert fake.writes() == [] and rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


# -- GitHub's merge requirements and the configured checks ---------------------------------------------------------

@pytest.mark.parametrize("state,mergeable,outcome", [
    ("dirty", False, "merge-blocked"), ("blocked", True, "merge-blocked"), ("unknown", None, "checks-pending"),
    ("clean", None, "checks-pending")])
def test_github_merge_requirements_block_or_defer_the_merge(rrig: Rig, launchers: Any, github_origin: Path,
                                                            state: str, mergeable: bool | None, outcome: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_state[101] = {"mergeable_state": state, "mergeable": mergeable}
    before = world(rrig, github_origin, job.job_id, fake, events=False)
    dry = merge(rrig, job.job_id, apply=False)
    assert dry.outcome in (outcome, "would-defer" if outcome == "checks-pending" else outcome) and not dry.ok
    report = merge(rrig, job.job_id)
    assert report.outcome == outcome and not fake.merge_requests
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL
    if outcome == "merge-blocked":
        assert attention(rrig, job.job_id) and dry.outcome == "merge-blocked"
    assert [m for m, _ in fake.writes()] == []
    assert before[2] == world(rrig, github_origin, job.job_id, fake, events=False)[2]


def check_run(name: str, conclusion: str | None, *, ident: int = 1, status: str = "completed") -> dict[str, Any]:
    return {"id": ident, "name": name, "status": status, "conclusion": conclusion}


def required(rig: Rig, *names: str) -> None:
    with_integration(rig, required_checks=names)


def test_without_a_required_check_nothing_is_claimed_and_it_says_so(rrig: Rig, launchers: Any, github_origin: Path):
    job, _fake = ready_job(rrig, launchers, github_origin)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed"
    notes = [c for c in report.checks if c["check"].startswith("required-check")]
    assert [(c["status"], "no CI result is claimed" in c["detail"]) for c in notes] == [("not-configured", True)]
    assert all(c["status"] != "pass" for c in notes)
    post = [c for c in report.checks if c["check"] == "post-merge checks"]
    assert [c["status"] for c in post] == ["not-configured"]


def test_a_required_check_that_is_not_reported_yet_waits(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    required(rrig, "ci")
    for apply, outcome in ((False, "would-defer"), (True, "checks-pending")):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == (outcome if apply else "checks-pending") and not report.ok
        assert "no check run or status of this name" in report.reasons[0]
    assert not fake.merge_requests and rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


def test_a_failing_required_check_blocks_with_attention_and_a_passing_one_merges(rrig: Rig, launchers: Any,
                                                                                github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    required(rrig, "ci")
    fake.checks[job.source["sha"]] = [check_run("ci", "failure")]
    report = merge(rrig, job.job_id)
    assert report.outcome == "checks-failed" and not fake.merge_requests
    assert "failed" in attention(rrig, job.job_id)
    fake.checks[job.source["sha"]] = [check_run("ci", "failure", ident=1), check_run("ci", "success", ident=2)]
    final = merge(rrig, job.job_id)                                # the newest run of the name decides
    assert final.outcome == "completed" and rrig.jobs.load(job.job_id).attention is None


def test_a_running_required_check_waits_and_unreadable_checks_wait_too(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    required(rrig, "ci")
    fake.checks[job.source["sha"]] = [check_run("ci", None, status="in_progress")]
    assert merge(rrig, job.job_id).outcome == "checks-pending"
    fake.checks[job.source["sha"]] = [check_run("ci", "success")]
    fake.unreadable = {"/check-runs"}
    report = merge(rrig, job.job_id)
    assert report.outcome == "checks-pending" and "could not be read" in report.reasons[0]
    assert not fake.merge_requests
    fake.unreadable = set()
    assert merge(rrig, job.job_id).outcome == "completed"


# -- failures of the merge request --------------------------------------------------------------------------------

def moving_head(fake: FakeMergeGitHub) -> Callable[[], None]:
    return lambda: fake.prs[0]["head"].update(sha="c" * 40)


def test_a_pr_head_that_moves_at_the_merge_is_refused_by_github_and_stales_the_job_on_the_next_run(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.on_merge = moving_head(fake)
    report = merge(rrig, job.job_id)
    assert report.outcome == "merge-rejected" and "Head branch was modified" in report.reasons[0]
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and stored.merge_result is None
    assert kinds(rrig, job.job_id)[-4:] == ["merge-pr", "merge-attempt", "merge-dispatch", "merge-outcome"]
    assert of_kind(rrig, job.job_id, "merge-outcome")[-1]["status"] == "not-merged"
    assert of_kind(rrig, job.job_id, "merge-outcome")[-1]["basis"] == "rejected"
    assert "Head branch was modified" in attention(rrig, job.job_id)
    assert fake.merge_requests == [{"number": 101, "merge_method": "merge", "sha": job.source["sha"]}]
    assert bare(github_origin, "rev-parse", "refs/heads/dev").strip() == job.base["expected_sha"]
    nxt = merge(rrig, job.job_id)                                  # the bound PR no longer has the approved head
    assert nxt.outcome == "stale" and rrig.state_of(job.job_id) is JobState.STALE and len(fake.merge_requests) == 1


def test_a_base_that_moves_at_the_merge_is_detected_after_it_and_fails_the_job_without_a_revert(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    moved = {}

    def advance_dev() -> None:                                     # between the last pin check and GitHub's merge
        moved["sha"] = change_on(rrig.repo, "dev", {"backend/src/y.py": '"""Y."""\nY = 1\n'})
    fake.on_merge = advance_dev
    report = merge(rrig, job.job_id)
    assert report.outcome == "failed" and not report.ok, (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.FAILED and "post-merge verification FAILED" in stored.attention["reason"]
    assert stored.merge_result["verified"] is False and stored.merge_result["verification"]["state"] == "failed"
    assert any(c["name"] == "parents" and c["status"] == "fail" for c in stored.merge_result["verification"]["checks"])
    commit = bare(github_origin, "rev-parse", "refs/heads/dev").strip()
    assert commit == stored.merge_result["commit_sha"] != moved["sha"]         # the merge is on dev: never reverted
    assert bare(github_origin, "rev-list", "--parents", "-n", "1", commit).split()[1] == moved["sha"]
    assert len(fake.merge_requests) == 1


@pytest.mark.parametrize("response,fragment", [
    (ApiResponse(405, {}, {"message": "Pull Request is not mergeable"}), "not mergeable"),     # a merge conflict
    (ApiResponse(403, {}, {"message": "Resource not accessible by integration"}), "lacks this capability"),
    (ApiResponse(422, {}, {"message": "Validation Failed"}), "Validation Failed"),
])
def test_a_refused_merge_request_records_nothing_as_merged(rrig: Rig, launchers: Any, github_origin: Path,
                                                          response: ApiResponse, fragment: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_failure = response
    report = merge(rrig, job.job_id)
    assert report.outcome == "merge-rejected" and fragment in report.reasons[0], (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and stored.merge_result is None
    assert of_kind(rrig, job.job_id, "merge-outcome")[-1]["status"] == "not-merged" and stored.attention
    assert bare(github_origin, "rev-parse", "refs/heads/dev").strip() == job.base["expected_sha"]
    again = merge(rrig, job.job_id)                                # the refusal is over: a new attempt is allowed
    assert again.outcome == "completed" and len(fake.merge_requests) == 2


def test_an_api_timeout_leaves_the_attempt_open_and_is_never_repeated_however_long_it_takes(
        rrig: Rig, launchers: Any, github_origin: Path):
    """Codex F3: an elapsed settle window is not proof that the request was not accepted."""
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.merge_failure = TransportFailure("PUT merge: no response within 30s", maybe_applied=True)
    report = merge(rrig, job.job_id)
    assert report.outcome == "merge-uncertain" and "stays open" in report.reasons[0], (report.outcome, report.reasons)
    assert kinds(rrig, job.job_id)[-1] == "merge-dispatch" and rrig.jobs.load(job.job_id).merge_result is None
    with pytest.raises(IntegrationError, match="in flight"):       # an abort cannot hide a request that may have landed
        abort_job(rrig.jobs, job.job_id, "no", actor="operator", apply=False)
    with pytest.raises(IntegrationError, match="in flight"):
        abort_job(rrig.jobs, job.job_id, "no", actor="operator", apply=True)
    again = merge(rrig, job.job_id)                                # within the settle window: reconciled, not repeated
    assert again.outcome == "merge-uncertain" and len(fake.merge_requests) == 1
    for hours in (LATER, timedelta(hours=1), timedelta(days=3)):   # past it: still never a second request
        later = merge(rrig, job.job_id, clock=Clock(hours))
        assert later.outcome == "merge-unresolved" and len(fake.merge_requests) == 1, later.outcome
    unresolved = merge(rrig, job.job_id, clock=Clock(LATER))
    assert unresolved.outcome == "merge-unresolved" and rrig.jobs.load(job.job_id).attention
    assert not of_kind(rrig, job.job_id, "merge-outcome")          # an outcome needs GitHub's state, not elapsed time
    abort_with_clock = abort_job(rrig.jobs, job.job_id, "the PR never merged", actor="operator", apply=True,
                                 now=datetime.now(timezone.utc) + LATER)
    assert abort_with_clock["outcome"] == "aborted" and len(fake.merge_requests) == 1
    assert "never established" in rrig.jobs.load(job.job_id).history[-1]["reason"]


@pytest.mark.parametrize("apply_dry", [False, True])
def test_a_lost_response_after_a_successful_merge_is_reconciled_without_a_second_request(
        rrig: Rig, launchers: Any, github_origin: Path, apply_dry: bool):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.lose_merge_response = True
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.merge_requests) == 1 and rrig.state_of(job.job_id) is JobState.COMPLETED
    assert of_kind(rrig, job.job_id, "merge-outcome")[-1]["status"] == "merged"
    assert merge(rrig, job.job_id, apply=apply_dry).outcome == "completed" and len(fake.merge_requests) == 1


def test_a_different_pr_is_never_merged(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    other = change_on(rrig.repo, "feature/other", {"cli/src/kiddo_cli/other.py": '"""O."""\nO = 1\n'}, start="dev")
    fake.prs.append(pr_json(55, head_ref="feature/other", head_sha=other))        # somebody else's unrelated PR
    existing_pr(fake, job, 101)
    assert merge(rrig, job.job_id).outcome == "completed"
    assert [r["number"] for r in fake.merge_requests] == [101]
    assert [p["number"] for p in fake.prs if p["merged"]] == [101]


# -- dry runs -----------------------------------------------------------------------------------------------------

def test_a_dry_run_says_what_it_would_do_and_writes_nothing(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    before = world(rrig, github_origin, job.job_id, fake)
    report = merge(rrig, job.job_id, apply=False)
    assert report.outcome == "would-create-pr" and report.ok and not report.changed
    assert "would create ONE" in " ".join(report.messages) and not report.github_writes
    assert world(rrig, github_origin, job.job_id, fake) == before
    existing_pr(fake, job)
    before = world(rrig, github_origin, job.job_id, fake)
    report = merge(rrig, job.job_id, apply=False)
    assert report.outcome == "would-merge" and report.ok and report.pr["number"] == 101
    assert "merge-commit method pinned to the approved source SHA" in " ".join(report.messages)
    assert world(rrig, github_origin, job.job_id, fake) == before and not fake.merge_requests


def test_an_applied_run_without_the_integration_switch_refuses(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    with_integration(rrig, enabled=False, known_authors=())
    with pytest.raises(IntegrationError, match="github.integration.enabled is false"):
        merge(rrig, job.job_id)
    assert fake.writes() == []


# -- concurrency and abort -----------------------------------------------------------------------------------------

def test_a_second_merge_command_is_refused_while_the_job_lock_is_held(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    lock = FileLock(rrig.store.locks.dir / f"integration-{job.job_id}.lock")
    lock.acquire(timeout=0)
    try:
        with pytest.raises(IntegrationError, match="another integration operation .* is running"):
            merge(rrig, job.job_id)
        assert merge(rrig, job.job_id, apply=False).outcome == "would-create-pr"     # a dry run takes no lock
    finally:
        lock.release()
    assert fake.writes() == []


def test_two_concurrent_merge_commands_send_exactly_one_merge_request(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    inside, release = threading.Event(), threading.Event()

    def hold() -> None:                                            # the first command is inside the merge request
        inside.set()
        assert release.wait(30)
    fake.on_merge = hold
    results: dict[str, Any] = {}
    first = threading.Thread(target=lambda: results.update(first=merge(rrig, job.job_id)))
    first.start()
    try:
        assert inside.wait(60)
        with pytest.raises(IntegrationError, match="another integration operation .* is running"):
            merge(rrig, job.job_id)
    finally:
        release.set()
        first.join(60)
    assert results["first"].outcome == "completed" and len(fake.merge_requests) == 1


def test_an_operator_abort_before_the_decision_wins_and_nothing_is_merged(rrig: Rig, launchers: Any,
                                                                         github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    original = fake.request

    def aborting(method: str, path: str, body: Any = None) -> ApiResponse:
        if method == "GET" and path.endswith("/pulls/101"):        # during the last checks, before the decision
            abort_job(rrig.jobs, job.job_id, "changed my mind", actor="operator", apply=True)
        return original(method, path, body)
    fake.request = aborting                                        # type: ignore[method-assign]
    report = merge(rrig, job.job_id)
    assert report.outcome == "changed-meanwhile", (report.outcome, report.reasons)
    assert rrig.state_of(job.job_id) is JobState.ABORTED and fake.writes() == [] and not fake.merge_requests
    assert not any(e["kind"] == "merge-attempt" for e in rrig.jobs.load(job.job_id).evidence)


def test_an_abort_after_the_intent_is_refused_while_the_request_is_in_flight(rrig: Rig, launchers: Any,
                                                                            github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    refusals: list[str] = []

    def try_abort() -> None:                                       # inside the merge request: the intent is on disk
        with pytest.raises(IntegrationError, match="in flight") as exc:
            abort_job(rrig.jobs, job.job_id, "too late", actor="operator", apply=True)
        refusals.append(str(exc.value))
    fake.on_merge = try_abort
    assert merge(rrig, job.job_id).outcome == "completed"
    assert refusals and "integration merge" in refusals[0] and rrig.state_of(job.job_id) is JobState.COMPLETED


# -- the client's write restrictions ---------------------------------------------------------------------------------

class Recorder:  # pylint: disable=too-few-public-methods
    """A Transport that records every request and answers the two integration writes like GitHub."""

    def __init__(self, merged: bool = True) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.merged = merged

    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:
        self.calls.append((method, path, body))
        if method == "PUT":
            return ApiResponse(200, {}, {"sha": "d" * 40, "merged": self.merged, "message": "Pull Request merged"})
        return ApiResponse(201, {}, pr_json(7, head_sha="a" * 40))


def client_with(recorder: Recorder, *, enabled: bool = True, publish: bool = False) -> GitHubClient:
    integration = GitHubIntegrationConfig(enabled=enabled, known_authors=("dev@example.invalid",) if enabled else ())
    fields: dict[str, Any] = {"repository": REPOSITORY, "integration": integration, "min_write_interval_seconds": 0.0,
                              "max_retries": 0}
    if publish:
        fields["publish"] = GitHubPublishConfig(enabled=True)
    return GitHubClient(recorder, GitHubConfig(**fields), sleep=lambda _s: None)


JOB, SHA = "int-20261116-0001", "a" * 40


def bind_any(client: GitHubClient, where: Path) -> GitHubClient:
    """Bind trust anchors to a unit-test client (no job lives there: the real boundary would refuse everything)."""
    client._bind_integration(IntegrationAnchors(where / "state", where, configuration=default_config))
    return client


@pytest.fixture
def stub_boundary(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Unit tests of the CLIENT's request shape only: the concrete authority the client builds is replaced, in-process,
    by one that records every question and approves (or refuses with `refuse`). The real authority — the persisted
    job, the signed approvals, the intent, the policy snapshot — is tested in test_fc_integration_merge_authority.py
    and test_fc_integration_merge_boundary.py."""
    state: dict[str, Any] = {"asked": [], "refuse": None}

    def authorize(self: Any, operation: str, **facts: Any) -> None:
        state["asked"].append((operation, facts))
        if state["refuse"]:
            raise IntegrationWriteRefused(state["refuse"])

    monkeypatch.setattr(IntegrationWriteAuthority, "authorize", authorize)
    return state


def test_the_integration_writes_refuse_without_anchors(tmp_path: Path, stub_boundary: dict[str, Any]):
    recorder = Recorder()
    client = client_with(recorder)
    with pytest.raises(IntegrationWriteRefused, match="no integration anchors"):
        client.merge_integration_pull_request(7, SHA, job_id=JOB, attempt="mat-0001")
    with pytest.raises(IntegrationWriteRefused, match="no integration anchors"):
        client.create_integration_pull_request("integration/x", "dev", "t", "b", job_id=JOB, attempt="mat-0001")
    assert not recorder.calls and not stub_boundary["asked"] and not client.integration_bound


def test_the_boundary_asks_about_exactly_what_it_sends_and_sends_the_fixed_request(tmp_path: Path,
                                                                                    stub_boundary: dict[str, Any]):
    recorder = Recorder()
    client = bind_any(client_with(recorder), tmp_path)
    assert client.merge_integration_pull_request(7, SHA, job_id=JOB, attempt="mat-0003").sha == "d" * 40
    assert client.create_integration_pull_request("integration/x", "dev", "title", "body", job_id=JOB,
                                                  attempt="mat-0004").number == 7
    assert stub_boundary["asked"] == [
        ("merge", {"job_id": JOB, "attempt": "mat-0003", "repository": REPOSITORY,
                   "request": {"number": 7, "merge_method": "merge", "sha": SHA},
                   "route": ("PUT", f"repos/{REPOSITORY}/pulls/7/merge")}),
        ("create-pr", {"job_id": JOB, "attempt": "mat-0004", "repository": REPOSITORY,
                       "request": {"head": "integration/x", "base": "dev", "draft": False},
                       "route": ("POST", f"repos/{REPOSITORY}/pulls")})]
    assert recorder.calls == [
        ("PUT", f"repos/{REPOSITORY}/pulls/7/merge", {"merge_method": "merge", "sha": SHA}),
        ("POST", f"repos/{REPOSITORY}/pulls", {"title": "title", "body": "body", "head": "integration/x",
                                               "base": "dev", "draft": False, "maintainer_can_modify": False})]


def test_a_refusing_boundary_sends_nothing_and_the_anchors_are_bound_once(tmp_path: Path,
                                                                          stub_boundary: dict[str, Any]):
    recorder = Recorder()
    client = bind_any(client_with(recorder), tmp_path)
    stub_boundary["refuse"] = ["no signed approval"]
    with pytest.raises(IntegrationWriteRefused, match="no signed approval") as exc:
        client.merge_integration_pull_request(7, SHA, job_id=JOB, attempt="mat-0001")
    assert exc.value.reasons == ["no signed approval"] and isinstance(exc.value, PullRequestPolicyError)
    with pytest.raises(IntegrationWriteRefused, match="no signed approval"):
        client.create_integration_pull_request("integration/x", "dev", "t", "b", job_id=JOB, attempt="mat-0001")
    with pytest.raises(IntegrationWriteRefused, match="bound once and never replaced"):
        client._bind_integration(IntegrationAnchors(tmp_path / "other", tmp_path, configuration=default_config))
    assert not recorder.calls


def test_the_integration_writes_need_the_integration_switch(tmp_path: Path, stub_boundary: dict[str, Any]):
    recorder = Recorder()
    client = bind_any(client_with(recorder, enabled=False), tmp_path)
    with pytest.raises(IntegrationWriteRefused, match="disabled"):    # even with a boundary that approves everything
        client.merge_integration_pull_request(7, SHA, job_id=JOB, attempt="mat-0001")
    with pytest.raises(IntegrationWriteRefused, match="disabled"):
        client.create_integration_pull_request("integration/x", "dev", "t", "b", job_id=JOB, attempt="mat-0001")
    assert not recorder.calls and not stub_boundary["asked"]


def test_no_caller_can_hand_the_client_an_authority(tmp_path: Path):
    """Codex F1 (second round): `integration_writes(<any WriteAuthority subclass>)` let a caller approve every write.
    There is no pluggable authority any more — nothing to subclass, no parameter that takes one: the anchors are data,
    of exactly one type, and the client builds the concrete authority itself."""
    from feature_coordinator import github_api  # pylint: disable=import-outside-toplevel

    class Yes(IntegrationWriteAuthority):  # pylint: disable=too-few-public-methods
        """A caller's 'authority' that approves everything."""

        def __init__(self) -> None:  # pylint: disable=super-init-not-called
            pass

        def authorize(self, *args: Any, **kwargs: Any) -> None:  # pylint: disable=arguments-differ
            """Approves everything."""

    class Anchors(IntegrationAnchors):  # pylint: disable=too-few-public-methods
        """A look-alike of the anchors."""

    recorder = Recorder()
    client = client_with(recorder)
    assert not hasattr(github_api, "WriteAuthority") and not hasattr(client, "integration_writes")
    assert not [name for name in dir(client) if "bind" in name and not name.startswith("_")]   # no public binding
    for impostor in (Yes(), Anchors(tmp_path, tmp_path, configuration=default_config), object()):
        with pytest.raises(IntegrationWriteRefused, match="must be an IntegrationAnchors"):   # nor a private one
            client._bind_integration(impostor)   # type: ignore[arg-type]
    with pytest.raises(TypeError):
        client.merge_integration_pull_request(  # type: ignore[call-arg]  # pylint: disable=unexpected-keyword-arg
            7, SHA, job_id=JOB, attempt="mat-0001", authority=Yes())
    with pytest.raises(TypeError):
        client.create_integration_pull_request(  # type: ignore[call-arg]  # pylint: disable=unexpected-keyword-arg
            "integration/x", "dev", "t", "b", job_id=JOB, attempt="mat-0001", authority=Yes())
    with pytest.raises(IntegrationWriteRefused, match="no integration anchors"):
        client.merge_integration_pull_request(7, SHA, job_id=JOB, attempt="mat-0001")
    assert not recorder.calls and not client.integration_bound


def test_no_other_path_of_the_client_merges_a_pull_request(tmp_path: Path, stub_boundary: dict[str, Any]):
    """Review M2: a merge PUT through the request core, or a GraphQL merge mutation, is refused (the integration method
    sets the one flag that allows it, for the duration of its own request)."""
    recorder = Recorder()
    client = bind_any(client_with(recorder), tmp_path)
    with pytest.raises(IntegrationWriteRefused, match="only through merge_integration_pull_request"):
        client._call("PUT", f"repos/{REPOSITORY}/pulls/7/merge", {"merge_method": "squash"}, idempotent=False)
    for mutation in ('mutation { mergePullRequest(input: {pullRequestId: "PR_x"}) { clientMutationId } }',
                     'mutation { enablePullRequestAutoMerge(input: {pullRequestId: "PR_x"}) { clientMutationId } }'):
        with pytest.raises(IntegrationWriteRefused, match="no GraphQL merge"):
            client.graphql(mutation, mutation=True)
    assert not recorder.calls
    assert client.merge_integration_pull_request(7, SHA, job_id=JOB, attempt="mat-0001").merged
    with pytest.raises(IntegrationWriteRefused, match="only through merge_integration_pull_request"):   # flag reset
        client._call("PUT", f"repos/{REPOSITORY}/pulls/7/merge", {"merge_method": "merge"}, idempotent=False)


def test_an_integration_write_is_one_transmission_a_rate_limit_is_not_resent_inside_the_client(
        tmp_path: Path, stub_boundary: dict[str, Any]):
    """Review M3: the resend inside the request core happens after the dispatch with no new authorization."""
    class RateLimited(Recorder):  # pylint: disable=too-few-public-methods
        """A transport that answers every request with a rate limit."""

        def request(self, method: str, path: str, body: Any = None) -> ApiResponse:
            self.calls.append((method, path, body))
            return ApiResponse(429, {"retry-after": "1"}, {"message": "rate limited"})

    recorder = RateLimited()
    integration = GitHubIntegrationConfig(enabled=True, known_authors=("dev@example.invalid",))
    client = bind_any(GitHubClient(recorder, GitHubConfig(repository=REPOSITORY, integration=integration,
                                                          min_write_interval_seconds=0.0, max_retries=3),
                                   sleep=lambda _s: None), tmp_path)
    from feature_coordinator.errors import GitHubRateLimitError  # pylint: disable=import-outside-toplevel
    with pytest.raises(GitHubRateLimitError):
        client.merge_integration_pull_request(7, SHA, job_id=JOB, attempt="mat-0001")
    with pytest.raises(GitHubRateLimitError):
        client.create_integration_pull_request("integration/x", "dev", "t", "b", job_id=JOB, attempt="mat-0002")
    assert len(recorder.calls) == 2 and len(stub_boundary["asked"]) == 2    # one transmission each, one question each


def test_there_is_no_public_grant_to_obtain():
    """Codex F1: the old public `authorize_integration_write` (job-shaped arguments, no verification) is gone — nothing
    a caller can call yields a working write without the persisted authorization behind it."""
    assert not hasattr(GitHubClient, "authorize_integration_write")
    assert not [name for name in dir(GitHubClient) if "grant" in name.lower()]
    assert not hasattr(client_with(Recorder()), "_integration_grants")


def test_an_answer_that_does_not_confirm_a_merge_commit_is_not_a_merge(tmp_path: Path, stub_boundary: dict[str, Any]):
    client = bind_any(client_with(Recorder(merged=False)), tmp_path)
    from feature_coordinator.errors import GitHubResponseError  # pylint: disable=import-outside-toplevel
    with pytest.raises(GitHubResponseError, match="does not confirm a merge commit"):
        client.merge_integration_pull_request(7, SHA, job_id=JOB, attempt="mat-0001")


def test_the_feature_publisher_rules_are_unchanged_and_there_is_no_other_merge_method(tmp_path: Path,
                                                                                       stub_boundary: dict[str, Any]):
    recorder = Recorder()
    client = client_with(recorder)
    with pytest.raises(PullRequestPolicyError):                                           # publish disabled
        client.authorize_pull_request("feature/x", "dev")
    with pytest.raises(PullRequestPolicyError, match="not authorized"):
        client.create_pull_request("feature/x", "dev", "t", "b")
    bind_any(client, tmp_path)                                     # integration anchors are no publisher authority
    with pytest.raises(PullRequestPolicyError, match="not authorized"):
        client.create_pull_request("feature/x", "dev", "t", "b")
    publisher = client_with(recorder, publish=True)
    publisher.authorize_pull_request("feature/x", "dev")
    with pytest.raises(PullRequestPolicyError, match="Draft"):
        publisher.create_pull_request("feature/x", "dev", "t", "b", draft=False)
    publisher.authorize_pull_request("feature/x", "dev")
    publisher.create_pull_request("feature/x", "dev", "t", "b")
    assert recorder.calls[-1][2]["draft"] is True and recorder.calls[-1][2]["head"] == "feature/x"
    assert [name for name in dir(GitHubClient) if "merge" in name.lower()] == ["merge_integration_pull_request"]
    assert not any("merge" in name.lower() for name in dir(publisher) if name.startswith(("pull", "open", "close")))
    assert not stub_boundary["asked"]


def test_review_r2_the_client_has_no_public_way_to_merge_or_create_around_its_methods(tmp_path: Path):
    """Independent review of the re-review fixes, finding 2: the client's transport was a public attribute (a raw
    merge PUT through it needed no authority), GraphQL `createPullRequest` / `enqueuePullRequest` (the merge queue)
    were not refused, and the request core's merge guard was a suffix match (`…/merge?x=1`)."""
    recorder = Recorder()
    client = client_with(recorder)
    assert not hasattr(client, "transport")
    for mutation in ('mutation { createPullRequest(input: {repositoryId: "R", baseRefName: "dev", headRefName: "x", '
                     'title: "t"}) { clientMutationId } }',
                     'mutation { enqueuePullRequest(input: {pullRequestId: "PR_x"}) { clientMutationId } }',
                     'mutation { q: mergeBranch(input: {repositoryId: "R", base: "dev", head: "x"}) '
                     '{ clientMutationId } }'):
        with pytest.raises(IntegrationWriteRefused, match="no GraphQL merge"):
            client.graphql(mutation, mutation=True)
    for method, path in (("PUT", f"repos/{REPOSITORY}/pulls/7/merge?x=1"),
                         ("PUT", f"repos/{REPOSITORY}/pulls/7/merge/"),
                         ("POST", f"repos/{REPOSITORY}/merges"), ("POST", f"repos/{REPOSITORY}/pulls"),
                         ("POST", f"/repos/{REPOSITORY}/pulls?draft=false")):
        with pytest.raises(IntegrationWriteRefused):
            client._call(method, path, {"head": "x", "base": "dev"}, idempotent=False)
    assert not recorder.calls


class _Shifty(int):
    """An int that compares equal to 101 but is 555 — the request the boundary sees would not be the one sent."""

    def __eq__(self, other: object) -> bool:
        return other == 101 or int.__eq__(self, other)

    __hash__ = int.__hash__


def test_review_r2_the_facts_of_an_integration_write_are_plain_values(tmp_path: Path, stub_boundary: dict[str, Any]):
    """Finding 3: an int (or str) subclass that compares equal to the authorized value but converts to another was
    authorized for PR #101 and sent to PR #555. Only exact str / int facts reach the boundary."""
    recorder = Recorder()
    client = bind_any(client_with(recorder), tmp_path)

    class Text(str):  # pylint: disable=too-few-public-methods
        """A str subclass."""

    with pytest.raises(IntegrationWriteRefused, match="plain int"):
        client.merge_integration_pull_request(_Shifty(555), SHA, job_id=JOB, attempt="mat-0001")
    with pytest.raises(IntegrationWriteRefused, match="plain int"):
        client.merge_integration_pull_request(True, SHA, job_id=JOB, attempt="mat-0001")      # a bool is no PR number
    with pytest.raises(IntegrationWriteRefused, match="plain str"):
        client.merge_integration_pull_request(7, Text(SHA), job_id=JOB, attempt="mat-0001")
    with pytest.raises(IntegrationWriteRefused, match="plain str"):
        client.create_integration_pull_request(Text("integration/x"), "dev", "t", "b", job_id=JOB, attempt="mat-0001")
    assert not recorder.calls and not stub_boundary["asked"]
