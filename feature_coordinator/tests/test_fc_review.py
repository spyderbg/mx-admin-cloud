"""W4.6.1 (R1): the independent AI review of an approved Issue's Draft PR (review.py) end to end — binding,
outcomes, the record, the one PR comment, invalidation, retry, crash recovery — on the W4.3.3 publication rig:
a bare `origin` remote (real pushes and fetches), the persisted mock tracker for Issues and pull requests, mock
runners for the agents, recorded gates and FAKE reviewers. No model, no real GitHub, no network."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,too-many-lines

from __future__ import annotations

import copy
import dataclasses
import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from feature_coordinator import review as review_module
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.errors import (
    CoordinatorError,
    GitHubUnavailableError,
    GitOperationError,
    RemoteUnavailableError,
)
from feature_coordinator.intake import feature_id_for, load_intake
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.issues import parse_op
from feature_coordinator.model import FeatureState, TaskState
from feature_coordinator.review import ReviewRunner, binding_digest, load_review, review_evidence
from feature_coordinator.reviewer import ReviewRequest, ReviewResult

from test_fc_intake import BODY, outside
from test_fc_issue_planning import MULTI, FakePlanner
from test_fc_planned_publication import PlannedPubRig, approved_and_verified, planned_publish_config, scripted
from test_fc_publish import (  # pylint: disable=unused-import
    REPOSITORY,
    PubRig,
    draft_open,
    github_origin,
    publish_config,
    remote,
    remote_sha,
)

CHANGED = "cli/src/commands.py"  # the single task's file (test_fc_intake.EDIT, one line)


def with_review(config, **review: Any):
    return dataclasses.replace(config, github=dataclasses.replace(config.github, review=dataclasses.replace(
        config.github.review, **{"enabled": True, **review})))


def review_config(**review: Any):
    return with_review(publish_config(), **review)


class FakeReviewer:  # pylint: disable=too-few-public-methods
    """Scripted reviewer results (a callable gets the request); records every request."""

    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.requests: list[ReviewRequest] = []

    def review(self, request: ReviewRequest) -> ReviewResult:
        self.requests.append(request)
        result = self.results.pop(0)
        return result(request) if callable(result) else result


def head_of(request: ReviewRequest) -> str:
    match = re.search(r"Reviewed head: ([0-9a-f]{40})", request.prompt)
    assert match is not None
    return match.group(1)


def ok(verdict: str = "pass", *findings: dict[str, Any], limitations: tuple[str, ...] = (),  # pylint: disable=keyword-arg-before-vararg
       **override: Any) -> Callable[[ReviewRequest], ReviewResult]:
    def make(request: ReviewRequest) -> ReviewResult:
        output = {"verdict": verdict, "summary": "The export command is added as specified.",
                  "head_sha": head_of(request), "findings": list(findings), "limitations": list(limitations),
                  **override}
        return ReviewResult("ok", output=output, details={"auth": "claude.ai subscription (pro)", "model": "opus"})
    return make


def finding(severity: str = "minor", path: str = CHANGED, start: int | None = 1, end: int | None = 1,
            title: str = "Name the command list") -> dict[str, Any]:
    return {"severity": severity, "file": path, "line_start": start, "line_end": end, "title": title,
            "explanation": "The list is a bare constant.", "recommendation": "Document it."}


@pytest.fixture
def rig(repo: Path, state_dir: Path, github_origin: Path) -> PubRig:
    return PubRig(repo, state_dir, github_origin, config=review_config())


def runner(rig: PubRig, reviewer: FakeReviewer | None = None, *, config=None, faults: tuple[str, ...] = (),
           clock: Callable[[], float] = lambda: 1000.0) -> ReviewRunner:
    coord = rig.coord if config is None else rig.make(config=config)
    return ReviewRunner(coord, reviewer=reviewer or FakeReviewer(), faults=faults, clock=clock)


def review(rig: PubRig, feature_id: str, reviewer: FakeReviewer | None = None, **kwargs: Any):
    apply, retry = kwargs.pop("apply", True), kwargs.pop("retry", None)
    return runner(rig, reviewer, **kwargs).run(feature_id, apply=apply, retry=retry)


def current(rig: PubRig, feature_id: str) -> dict[str, Any]:
    record = load_review(rig.store, feature_id)
    assert record is not None
    entry = record.entry(record.current)
    assert entry is not None
    return entry


def pr_comments(rig: PubRig, pr: int) -> list[str]:
    return [c.body for c in rig.tracker.list_comments(pr)]


def published(rig: PubRig) -> tuple[int, str, str, int]:
    return draft_open(rig)


# -- the happy paths --------------------------------------------------------------------------------------------

def test_a_passing_review_is_bound_recorded_and_published_once(rig: PubRig) -> None:  # pylint: disable=too-many-locals
    number, task_id, feature_id, pr = published(rig)
    feature, cand, intake = rig.feature(feature_id), rig.candidate(feature_id), load_intake(rig.store, feature_id)
    assert intake is not None
    issue_comments = rig.own_comments(number)
    fake = FakeReviewer(ok("pass"))
    report = review(rig, feature_id, fake)
    assert report.ok and report.outcome == "PASS" and report.reviewer_runs == 1, report.to_dict()
    entry = current(rig, feature_id)
    binding = entry["binding"]
    assert (binding["pr"], binding["head_sha"], binding["merge_base"], binding["spec_sha256"]) == \
        (pr, cand.head_sha, feature.base_sha, intake.digest)
    assert binding["repository"] == REPOSITORY and binding["feature"] == feature_id and binding["issue"] == number
    assert binding["base_tip"] == remote_sha(rig.bare, "dev") and binding["subject"] == "pr-head-diff"
    assert binding["reviewer"] == {"provider": "claude", "kind": "claude-code", "model": "opus"}
    assert binding["plan_sha256"] is None and str(intake.approval_comment_id) in binding["approval_ref"]
    assert entry["binding_sha256"] == binding_digest(binding) and entry["independence"] == "model"
    assert (entry["status"], entry["generation"], entry["attempts"]) == ("complete", 1, 1)
    assert len(entry["input_sha256"]) == 64 and len(entry["diff_sha256"]) == 64 and entry["files"] == 1
    # the reviewer's input: the approved spec, the task, the gates, the diff; data fenced with a nonce
    prompt = fake.requests[0].prompt
    nonce = re.search(r"<<<DIFF-([0-9a-f]{12})", prompt)
    assert nonce is not None and f"DIFF-{nonce.group(1)}>>>" in prompt
    for text in (intake.title, "Approved single task", f"`{task_id}`", CHANGED, "COMMANDS = ['content export-items']",
                 "integration gates at", "role `cli`: owns cli/**", f"Reviewed head: {cand.head_sha}"):
        assert text in prompt, text
    assert fake.requests[0].run_dir == rig.store.root / "runs" / "review" / entry["key"] / "attempt-1"
    # exactly one comment, on the PR (never on the Issue), with the coordinator's operation marker
    (comment,) = pr_comments(rig, pr)
    assert parse_op(comment) == (rig.config.github.coordinator_id, f"{feature_id}/review", f"review-{entry['key']}")
    assert "Independent AI review: PASS" in comment and cand.head_sha in comment and "never merges" in comment
    assert entry["comment"]["status"] == "posted" and rig.own_comments(number) == issue_comments
    assert review_evidence(rig.store, feature_id, pr, cand.head_sha) == entry
    assert review_evidence(rig.store, feature_id, pr, "f" * 40) is None
    # nothing else changed: the feature stays in review, the task verified, the PR a draft
    assert rig.feature(feature_id).state is FeatureState.REVIEW and rig.store.load_task(task_id).state is \
        TaskState.VERIFIED and rig.tracker.get_pull_request(pr).draft
    # the same binding again: no reviewer, no comment — one review per verified head and generation
    again = FakeReviewer()
    report = review(rig, feature_id, again)
    assert report.status == "unchanged" and report.outcome == "PASS" and not again.requests
    assert len(pr_comments(rig, pr)) == 1 and len(load_review(rig.store, feature_id).entries) == 1  # type: ignore
    kinds = [e["kind"] for e in rig.store.events() if e["kind"].startswith("review-")]
    assert kinds == ["review-started", "review-attempt", "review-completed", "review-comment-posted"]


@pytest.mark.parametrize(("result", "outcome", "texts"), [
    (ok("pass_with_notes", finding("minor")), "PASS_WITH_NOTES", ["| minor |", f"`{CHANGED}`:1"]),
    (ok("pass", limitations=("could not run the CLI",)), "PASS_WITH_NOTES", ["could not run the CLI"]),
    (ok("request_changes", finding("major", title="Missing CSV header")), "REQUEST_CHANGES",
     ["| major |", "Missing CSV header"]),
    (ok("request_changes", finding("blocker"), finding("info")), "REQUEST_CHANGES", ["| blocker |", "| info |"]),
])
def test_findings_decide_the_outcome(rig: PubRig, result: Any, outcome: str, texts: list[str]) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    report = review(rig, feature_id, FakeReviewer(result))
    assert report.outcome == outcome and current(rig, feature_id)["outcome"] == outcome
    (comment,) = pr_comments(rig, pr)
    assert f"Independent AI review: {outcome}" in comment
    for text in texts:
        assert text in comment, text
    assert rig.feature(feature_id).state is FeatureState.REVIEW
    evidence = review_evidence(rig.store, feature_id, pr, current(rig, feature_id)["binding"]["head_sha"])
    assert evidence is not None and evidence["outcome"] == outcome  # evidence of a review, never approval to merge


# -- answers that are not a review -------------------------------------------------------------------------------

@pytest.mark.parametrize("result", [
    ok("pass", finding("major")),                         # contradictory: a major finding is never a pass
    ok("pass_with_notes", finding("blocker")),
    ok("request_changes", finding("minor")),              # request_changes without a blocking finding
    ok("pass", finding("minor", path="backend/app.py")),  # not a changed file
    ok("pass", finding("minor", start=2, end=2)),         # the file has one line
    ok("pass", head_sha="f" * 40),                        # another head
    ok("pass", approve=True),                             # unexpected key
    lambda _r: ReviewResult("ok", output="LGTM"),
    lambda _r: ReviewResult("timeout", error="the reviewer run was timeout"),
    lambda _r: ReviewResult("failed", error="Claude Code exited with 1"),
])
def test_invalid_answers_timeouts_and_failures_are_error_never_pass(rig: PubRig, result: Any) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    report = review(rig, feature_id, FakeReviewer(result))
    entry = current(rig, feature_id)
    assert report.outcome == entry["outcome"] == "ERROR" and not report.ok
    assert "no valid review in 1 attempt(s)" in entry["reason"] and entry["attempt_log"][0]["status"] != "ok"
    (comment,) = pr_comments(rig, pr)
    assert "Independent AI review: ERROR" in comment and "could not be completed" in comment
    assert review_evidence(rig.store, feature_id, pr, entry["binding"]["head_sha"]) is None  # completed, no evidence


def test_attempts_are_bounded_and_a_later_valid_answer_counts(repo: Path, state_dir: Path,
                                                              github_origin: Path) -> None:
    rig = PubRig(repo, state_dir, github_origin, config=review_config(max_attempts=2))
    _number, _task_id, feature_id, _pr = published(rig)
    fake = FakeReviewer(ok("pass", head_sha="f" * 40), ok("pass_with_notes", finding("info")))
    assert review(rig, feature_id, fake).outcome == "PASS_WITH_NOTES" and len(fake.requests) == 2
    entry = current(rig, feature_id)
    assert [a["status"] for a in entry["attempt_log"]] == ["invalid", "ok"] and entry["attempts"] == 2
    assert fake.requests[0].run_dir.name == "attempt-1" and fake.requests[1].run_dir.name == "attempt-2"


def test_a_refused_run_is_error_at_once(repo: Path, state_dir: Path, github_origin: Path) -> None:
    rig = PubRig(repo, state_dir, github_origin, config=review_config(max_attempts=3))
    _number, _task_id, feature_id, pr = published(rig)
    fake = FakeReviewer(ReviewResult("refused", error="the reviewer tried to use the tool 'Bash'"))
    report = review(rig, feature_id, fake)
    assert report.outcome == "ERROR" and len(fake.requests) == 1
    assert "refused: the reviewer tried to use the tool 'Bash'" in current(rig, feature_id)["reason"]
    assert "could not be completed" in pr_comments(rig, pr)[0]


def test_an_unavailable_reviewer_defers_then_errors(rig: PubRig) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    unavailable = ReviewResult("unavailable", error="Claude Code is using API-key authentication")
    report = review(rig, feature_id, FakeReviewer(unavailable), clock=lambda: 1000.0)
    entry = current(rig, feature_id)
    assert report.status == "deferred" and report.outcome is None and "no attempt used" in report.deferred[0]
    assert (entry["status"], entry["attempts"], entry["outcome"]) == ("pending", 0, None) and not pr_comments(rig, pr)
    report = review(rig, feature_id, FakeReviewer(unavailable), clock=lambda: 1000.0 + 3599)
    assert report.status == "deferred" and current(rig, feature_id)["attempts"] == 0
    cancelled = review(rig, feature_id, FakeReviewer(ReviewResult("cancelled", error="stop")), clock=lambda: 1500.0)
    assert cancelled.status == "deferred" and current(rig, feature_id)["attempts"] == 0
    report = review(rig, feature_id, FakeReviewer(unavailable), clock=lambda: 1000.0 + 3600)
    entry = current(rig, feature_id)
    assert report.outcome == "ERROR" and "unavailable since" in entry["reason"] and "API-key" in entry["reason"]
    assert [a["status"] for a in entry["attempt_log"]] == ["unavailable", "unavailable", "cancelled", "unavailable"]
    assert len(pr_comments(rig, pr)) == 1


# -- binding: head, base, approval -----------------------------------------------------------------------------

def test_a_changed_pr_head_invalidates_the_review_and_nothing_new_is_reviewed(rig: PubRig) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    review(rig, feature_id, FakeReviewer(ok("pass")))
    rig.tracker.update_pull_request(pr, head_sha="f" * 40)  # someone pushed to the PR on GitHub
    fake = FakeReviewer(ok("pass"))
    report = review(rig, feature_id, fake)
    entry = current(rig, feature_id)
    assert not fake.requests and "is not reviewed" in report.attention[0] and "is not the verified" in \
        report.attention[0]
    assert entry["status"] == "invalidated" and "no longer the verified candidate" in entry["invalidated"]["reason"]
    assert entry["notice"]["status"] == "posted"
    comments = pr_comments(rig, pr)
    assert len(comments) == 2 and "no longer applies" in comments[1]
    assert review_evidence(rig.store, feature_id, pr, rig.candidate(feature_id).head_sha) is None
    review(rig, feature_id, FakeReviewer())  # again: nothing new, no second notice
    assert len(pr_comments(rig, pr)) == 2
    assert "is not the verified" in rig.publish().attention[0]  # the publisher stops, exactly as before


def test_a_base_that_does_not_match_is_error_and_invalidates(rig: PubRig, monkeypatch: pytest.MonkeyPatch) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    review(rig, feature_id, FakeReviewer(ok("pass")))
    monkeypatch.setattr(review_module, "merge_base", lambda _root, _a, _b: "e" * 40)
    fake = FakeReviewer(ok("pass"))
    report = review(rig, feature_id, fake)
    record = load_review(rig.store, feature_id)
    assert record is not None and [e["status"] for e in record.entries] == ["invalidated", "complete"]
    first, second = record.entries
    assert "its binding changed (merge_base)" in first["invalidated"]["reason"]
    assert second["generation"] == 2 and second["outcome"] == "ERROR" and "is not the feature's base commit" in \
        second["reason"]
    assert not fake.requests and report.outcome == "ERROR" and len(pr_comments(rig, pr)) == 3  # review, notice, ERROR


def test_no_merge_base_is_error_without_a_reviewer(rig: PubRig, monkeypatch: pytest.MonkeyPatch) -> None:
    _number, _task_id, feature_id, pr = published(rig)

    def unrelated(_root: Path, _a: str, _b: str) -> str:
        raise GitOperationError("git merge-base failed: no common ancestor")
    monkeypatch.setattr(review_module, "merge_base", unrelated)
    fake = FakeReviewer(ok("pass"))
    report = review(rig, feature_id, fake)
    assert report.outcome == "ERROR" and not fake.requests and "no merge base of dev" in \
        current(rig, feature_id)["reason"]
    assert "could not be completed" in pr_comments(rig, pr)[0]


def test_a_changed_issue_invalidates_a_pass_and_is_error(rig: PubRig) -> None:
    number, _task_id, feature_id, pr = published(rig)
    review(rig, feature_id, FakeReviewer(ok("pass")))
    outside(rig, number, body=BODY + "\nAlso export images.")
    fake = FakeReviewer(ok("pass"))
    report = review(rig, feature_id, fake)
    record = load_review(rig.store, feature_id)
    assert record is not None and [(e["status"], e["outcome"]) for e in record.entries] == \
        [("invalidated", "PASS"), ("complete", "ERROR")]
    assert "the approval no longer holds" in record.entries[0]["invalidated"]["reason"]
    assert "the approval no longer holds" in record.entries[1]["reason"] and not fake.requests
    assert report.outcome == "ERROR" and len(pr_comments(rig, pr)) == 3
    again = review(rig, feature_id, FakeReviewer())  # unchanged: the ERROR stands, nothing is posted again
    assert again.status == "unchanged" and again.outcome == "ERROR" and len(pr_comments(rig, pr)) == 3


def test_a_closed_issue_before_review_is_error_without_a_reviewer(rig: PubRig) -> None:
    number, _task_id, feature_id, _pr = published(rig)
    outside(rig, number, state="closed")
    fake = FakeReviewer(ok("pass"))
    assert review(rig, feature_id, fake).outcome == "ERROR" and not fake.requests
    assert "was closed" in current(rig, feature_id)["reason"] or "closed" in current(rig, feature_id)["reason"]


# -- the binding is checked again when the reviewer has finished ---------------------------------------------------

def _merge_base_moved(_rig: PubRig, _number: int, _pr: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_module, "merge_base", lambda _root, _a, _b: "e" * 40)


CHANGES: dict[str, tuple[Callable[..., Any], str, int]] = {  # what changes while the reviewer runs → why; entries
    "pr-head": (lambda rig, _n, pr, _m: rig.tracker.update_pull_request(pr, head_sha="f" * 40),
                "is not the verified", 1),
    "pr-closed": (lambda rig, _n, pr, _m: rig.tracker.update_pull_request(pr, state="closed"), "is closed", 1),
    "issue-edited": (lambda rig, number, _p, _m: outside(rig, number, body=BODY + "\nAlso export images."),
                     "the approval no longer holds", 2),
    "issue-closed": (lambda rig, number, _p, _m: outside(rig, number, state="closed"),
                     "the approval no longer holds", 2),
    "merge-base": (_merge_base_moved, "the binding changed (merge_base)", 2),
}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_change_during_the_review_keeps_its_answer_as_history_only(rig: PubRig, monkeypatch: pytest.MonkeyPatch,
                                                                     change: str) -> None:
    number, _task_id, feature_id, pr = published(rig)
    head = rig.candidate(feature_id).head_sha
    act, why, entries = CHANGES[change]

    def meanwhile(request: ReviewRequest) -> ReviewResult:
        act(rig, number, pr, monkeypatch)  # while the reviewer runs (minutes, live)
        return ok("pass")(request)
    report = review(rig, feature_id, FakeReviewer(meanwhile))
    entry = current(rig, feature_id)
    assert report.status == "stale" and not report.ok and report.outcome is None and report.reviewer_runs == 1
    assert "history only" in report.attention[0] and why in report.attention[0], report.attention
    assert (entry["status"], entry["outcome"], entry["verdict"]) == ("invalidated", "PASS", "pass")
    assert entry["binding"]["head_sha"] == head and entry["binding_sha256"] == binding_digest(entry["binding"])
    assert "stale when the review finished" in entry["invalidated"]["reason"] and why in entry["invalidated"]["reason"]
    assert not pr_comments(rig, pr) and entry["comment"] is None and entry["notice"] is None  # never published
    assert review_evidence(rig.store, feature_id, pr, head) is None
    assert "review-stale" in [e["kind"] for e in rig.store.events()]
    if change == "pr-head":  # both heads are named; the record keeps the reviewed one
        assert "head commit ffffffffffff is not the verified " + head[:12] in entry["invalidated"]["reason"]
    # the next operator run reviews nothing unverified: an unverified head or a closed PR stops; a withdrawn
    # approval or a moved base is ERROR without a reviewer run — never a PASS
    later = review(rig, feature_id, (fake := FakeReviewer(ok("pass"))))
    record = load_review(rig.store, feature_id)
    assert record is not None and len(record.entries) == entries and not fake.requests
    assert later.outcome == (None if entries == 1 else "ERROR") and not later.ok
    assert record.entries[0]["status"] == "invalidated" and record.entries[0]["outcome"] == "PASS"
    assert review_evidence(rig.store, feature_id, pr, head) is None


def outage(rig: PubRig, patch: pytest.MonkeyPatch, failure: str) -> None:
    """From now on (inside *patch*'s context): GitHub's PR read or the base branch fetch fails transiently."""
    if failure == "github":
        def down(_number: int) -> Any:
            raise GitHubUnavailableError("GitHub returned 502")
        patch.setattr(rig.coord.sync.client, "get_pull_request", down)
    else:
        def offline(*_args: Any, **_kwargs: Any) -> str:
            raise RemoteUnavailableError("git fetch origin dev failed: network is unreachable")
        patch.setattr(rig.coord.git, "fetch_branch", offline)


@pytest.mark.parametrize("failure", ["github", "fetch"])
def test_a_failed_re_read_holds_the_answer_until_a_run_confirms_it(rig: PubRig, monkeypatch: pytest.MonkeyPatch,
                                                                   failure: str) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    head = rig.candidate(feature_id).head_sha
    with monkeypatch.context() as patch:
        def meanwhile(request: ReviewRequest) -> ReviewResult:
            outage(rig, patch, failure)  # while the reviewer runs
            return ok("pass_with_notes", finding("minor"))(request)
        report = review(rig, feature_id, FakeReviewer(meanwhile))
    entry = current(rig, feature_id)
    assert report.status == "deferred" and not report.ok and report.outcome is None and "is held" in \
        report.deferred[0]
    assert (entry["status"], entry["outcome"], entry["attempts"]) == ("pending", None, 1)
    assert entry["held"]["outcome"] == "PASS_WITH_NOTES" and entry["attempt_log"][-1]["status"] == "held"
    assert not pr_comments(rig, pr) and review_evidence(rig.store, feature_id, pr, head) is None
    dry = review(rig, feature_id, FakeReviewer(), apply=False)
    assert any("confirm the held answer" in w for w in dry.writes)
    again = FakeReviewer()
    report = review(rig, feature_id, again)  # this run's own fresh binding check passes: recorded, no reviewer run
    entry = current(rig, feature_id)
    assert report.outcome == "PASS_WITH_NOTES" and report.ok and not again.requests and report.reviewer_runs == 0
    assert entry["status"] == "complete" and entry["findings"][0]["severity"] == "minor" and entry["attempts"] == 1
    (comment,) = pr_comments(rig, pr)
    assert "PASS_WITH_NOTES" in comment and review_evidence(rig.store, feature_id, pr, head) == entry
    assert review(rig, feature_id, FakeReviewer()).status == "unchanged" and len(pr_comments(rig, pr)) == 1


def test_a_held_answer_is_discarded_when_the_approval_no_longer_holds(rig: PubRig,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    number, _task_id, feature_id, pr = published(rig)
    with monkeypatch.context() as patch:
        def meanwhile(request: ReviewRequest) -> ReviewResult:
            outage(rig, patch, "github")
            return ok("pass")(request)
        assert review(rig, feature_id, FakeReviewer(meanwhile)).status == "deferred"
    outside(rig, number, body=BODY + "\nAlso export images.")
    report = review(rig, feature_id, (fake := FakeReviewer(ok("pass"))))
    entry = current(rig, feature_id)
    assert report.outcome == "ERROR" and not fake.requests and "the approval no longer holds" in entry["reason"]
    assert entry["held"]["outcome"] == "PASS" and entry["verdict"] is None  # kept as history only
    (comment,) = pr_comments(rig, pr)
    assert "Independent AI review: ERROR" in comment
    assert review_evidence(rig.store, feature_id, pr, entry["binding"]["head_sha"]) is None


def test_review_evidence_is_a_current_published_review_of_exactly_that_head(rig: PubRig) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    head = rig.candidate(feature_id).head_sha
    with pytest.raises(SimulatedCrash):  # recorded, its comment not posted yet
        review(rig, feature_id, FakeReviewer(ok("request_changes", finding("major"))),
               faults=(f"crash-after-review-recorded:{feature_id}",))
    assert current(rig, feature_id)["outcome"] == "REQUEST_CHANGES"  # a completed outcome …
    assert review_evidence(rig.store, feature_id, pr, head) is None  # … but not published: no evidence yet
    review(rig, feature_id, FakeReviewer())
    evidence = review_evidence(rig.store, feature_id, pr, head)
    assert evidence is not None and evidence["outcome"] == "REQUEST_CHANGES"  # evidence of findings, not approval
    assert review_evidence(rig.store, feature_id, pr + 1, head) is None
    assert review_evidence(rig.store, feature_id, pr, "f" * 40) is None
    with rig.store.transaction():  # a stored binding that no longer matches its digest is not evidence
        record = load_review(rig.store, feature_id)
        assert record is not None
        record.entries[0]["binding"]["merge_base"] = "e" * 40
        rig.store.save_record("review", record)
    assert review_evidence(rig.store, feature_id, pr, head) is None
    assert current(rig, feature_id)["status"] == "complete"  # still the latest completed outcome in the history


# -- planned (W4.5) features --------------------------------------------------------------------------------------

def test_a_planned_feature_binds_its_plan_and_approval(repo: Path, state_dir: Path, github_origin: Path) -> None:
    config = with_review(planned_publish_config())
    rig = scripted(PlannedPubRig(repo, state_dir, github_origin, planner=FakePlanner(MULTI), config=config), MULTI)
    _number, feature_id, plan = approved_and_verified(rig, title="Category export across the stack",
                                                      body="Backend API, UI, CLI and docs.")
    assert rig.publish().created
    intake = load_intake(rig.store, feature_id)
    assert intake is not None and intake.plan is not None
    fake = FakeReviewer(ok("pass"))
    assert review(rig, feature_id, fake).outcome == "PASS"  # type: ignore[arg-type]
    entry = current(rig, feature_id)  # type: ignore[arg-type]
    assert (entry["binding"]["plan_sha256"], entry["binding"]["approval_sha256"]) == \
        (intake.plan["sha256"], intake.plan["approval_sha256"])
    prompt = fake.requests[0].prompt
    assert f"Approved execution plan v1 (plan sha256 {intake.plan['sha256']}" in prompt
    assert all(f'"id": "{t["id"]}"' in prompt for t in plan["tasks"]) and "role `backend`" in prompt
    assert entry["independence"] == "none"  # the backend task also ran claude / opus
    # independence: require refuses a reviewer that is an implementer's provider and model, without a model call
    strict = rig.make(config=with_review(planned_publish_config(), independence="require"))
    report = ReviewRunner(strict, reviewer=(fake := FakeReviewer(ok("pass")))).run(feature_id, apply=True)
    assert report.outcome == "ERROR" and not fake.requests and "independence is `require`" in \
        current(rig, feature_id)["reason"]  # type: ignore[arg-type]
    # a recorded plan that no longer matches its digest is never reviewed
    with rig.store.transaction():
        tampered = load_intake(rig.store, feature_id)
        assert tampered is not None and tampered.plan is not None
        tampered.plan["canonical"]["tasks"][0]["instructions"] = "Also delete the database."
        rig.store.save_record("intake", tampered)
    unchanged = review(rig, feature_id, (fake := FakeReviewer(ok("pass"))))  # type: ignore[arg-type]
    assert unchanged.status == "unchanged" and unchanged.outcome == "ERROR" and not fake.requests  # ERROR stands
    report = review(rig, feature_id, fake, retry={"by": "operator", "reason": "plan check"})  # type: ignore[arg-type]
    assert report.outcome == "ERROR" and not fake.requests
    assert "does not match its digest" in current(rig, feature_id)["reason"]  # type: ignore[arg-type]


# -- sanitized input ----------------------------------------------------------------------------------------------

def test_credentials_in_the_spec_are_withheld_and_rule_out_pass(rig: PubRig) -> None:
    token = "ghp_" + "Z" * 36
    number = rig.issue(body=BODY + f"\nUse the token {token} for the export.")
    task_id = rig.script(number)
    rig.approve(number)
    rig.poll()
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    feature_id = feature_id_for(number)
    assert rig.publish().created
    fake = FakeReviewer(ok("pass"))
    assert review(rig, feature_id, fake).outcome == "PASS_WITH_NOTES"
    assert token not in fake.requests[0].prompt and "[redacted]" in fake.requests[0].prompt
    entry = current(rig, feature_id)
    assert entry["coordinator_notes"] == ["1 credential-like string(s) were withheld from the reviewer"]
    pr = rig.candidate(feature_id).number
    assert pr is not None and "credential-like" in pr_comments(rig, pr)[0] and token not in pr_comments(rig, pr)[0]


def test_credentials_in_the_approved_plan_never_reach_the_prompt(repo: Path, state_dir: Path,
                                                                github_origin: Path) -> None:
    raw = {"summary": "AKIA" + "R" * 16, "criteria": "sk-ant-" + "q" * 30, "title": "github_pat_" + "S" * 30,
           "instructions": "ghp_" + "P" * 36, "task_criteria": "xoxb-" + "4" * 12}
    plan = copy.deepcopy(MULTI)
    plan["feature"]["summary"] += f" Deploy key {raw['summary']}."
    plan["feature"]["acceptance_criteria"].append(f"the key {raw['criteria']} is never logged")
    plan["tasks"][2]["title"] += f" ({raw['title']})"
    plan["tasks"][0]["instructions"] += f" Call the API with the token {raw['instructions']}."
    plan["tasks"][0]["acceptance_criteria"].append(f"the webhook {raw['task_criteria']} is not called")
    config = with_review(planned_publish_config())
    rig = scripted(PlannedPubRig(repo, state_dir, github_origin, planner=FakePlanner(plan), config=config), plan)
    _number, feature_id, approved = approved_and_verified(rig, title="Category export across the stack",
                                                          body="Backend API, UI, CLI and docs.")
    assert raw["instructions"] in approved["tasks"][0]["instructions"]  # the approved plan holds them as written
    assert rig.publish().created
    fake = FakeReviewer(ok("pass"))
    report = review(rig, feature_id, fake)  # type: ignore[arg-type]
    prompt = fake.requests[0].prompt
    for name, value in raw.items():
        assert value not in prompt, name
    # (the cli commit's subject repeats its task title; the coordinator redacted it when it committed)
    assert "Approved execution plan v1" in prompt and "cli: The cli part ([redacted])" in prompt
    assert prompt.count("[redacted]") == len(raw) + 1
    entry = current(rig, feature_id)  # type: ignore[arg-type]
    assert report.outcome == entry["outcome"] == "PASS_WITH_NOTES"  # never a plain PASS
    assert entry["coordinator_notes"] == [f"{len(raw)} credential-like string(s) were withheld from the reviewer"]
    comment = pr_comments(rig, entry["binding"]["pr"])[0]  # type: ignore[arg-type]
    assert "credential-like" in comment and not any(value in comment for value in raw.values())


# -- duplicates, crashes, retry ---------------------------------------------------------------------------------

def test_a_concurrent_run_is_deferred(rig: PubRig) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    lock = rig.store.locks.review()
    lock.acquire()
    try:
        fake = FakeReviewer(ok("pass"))
        report = review(rig, feature_id, fake)
        assert report.status == "deferred" and "in progress" in report.deferred[0] and not fake.requests
    finally:
        lock.release()
    assert load_review(rig.store, feature_id) is None and not pr_comments(rig, pr)


@pytest.mark.parametrize(("fault", "attempts", "second", "outcome", "runs"), [
    ("crash-after-attempt-counted", 1, None, "ERROR", 0),      # the interrupted attempt stays counted
    ("crash-after-attempt-counted", 2, ok("pass"), "PASS", 1),
    ("crash-after-review-recorded", 1, None, "PASS", 0),       # recorded: only the comment is still due
    ("crash-after-review-comment", 1, None, "PASS", 0),        # posted, not recorded: found by its marker
])
def test_crash_recovery_never_duplicates(repo: Path, state_dir: Path, github_origin: Path, fault: str,  # pylint: disable=too-many-arguments
                                         attempts: int, second: Any, outcome: str, runs: int) -> None:
    rig = PubRig(repo, state_dir, github_origin, config=review_config(max_attempts=attempts))
    _number, _task_id, feature_id, pr = published(rig)
    with pytest.raises(SimulatedCrash):
        review(rig, feature_id, FakeReviewer(ok("pass")), faults=(f"{fault}:{feature_id}",))
    fake = FakeReviewer(*([second] if second else []))
    report = review(rig, feature_id, fake)
    entry = current(rig, feature_id)
    assert report.outcome == entry["outcome"] == outcome and len(fake.requests) == runs
    assert len(pr_comments(rig, pr)) == 1 and entry["comment"]["status"] == "posted"
    if fault == "crash-after-attempt-counted":
        assert entry["attempt_log"][0]["status"] == "interrupted"
        assert outcome == "PASS" or "interrupted" in entry["reason"]


def test_retry_supersedes_the_review_and_runs_a_new_generation(rig: PubRig) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    review(rig, feature_id, FakeReviewer(ok("pass")))
    retry = {"by": "operator", "reason": "second opinion"}
    dry = review(rig, feature_id, (fake := FakeReviewer(ok("pass"))), apply=False, retry=retry)
    assert not fake.requests and any("invalidate review" in w for w in dry.writes) and len(pr_comments(rig, pr)) == 1
    with pytest.raises(SimulatedCrash):  # a crash right after the invalidation notice was posted
        review(rig, feature_id, FakeReviewer(ok("pass")), retry=retry,
               faults=(f"crash-after-review-notice:{feature_id}",))
    report = review(rig, feature_id, (fake := FakeReviewer(ok("request_changes", finding("major")))))
    record = load_review(rig.store, feature_id)
    assert record is not None and [(e["generation"], e["status"]) for e in record.entries] == \
        [(1, "invalidated"), (2, "complete")]
    assert "superseded by an operator retry (operator: second opinion)" in record.entries[0]["invalidated"]["reason"]
    assert report.outcome == "REQUEST_CHANGES" and len(fake.requests) == 1 and record.current.endswith("-g2")
    comments = pr_comments(rig, pr)
    assert len(comments) == 3 and "no longer applies" in comments[1] and "REQUEST_CHANGES" in comments[2]


# -- what the review never touches ------------------------------------------------------------------------------

def test_publication_and_completion_are_unchanged_by_a_review(rig: PubRig) -> None:
    number, task_id, feature_id, pr = published(rig)
    before = rig.publish(apply=False).to_dict()
    body = rig.tracker.get_pull_request(pr).body
    issue_comments = rig.own_comments(number)
    review(rig, feature_id, FakeReviewer(ok("request_changes", finding("blocker"))))
    assert rig.publish(apply=False).to_dict() == before
    report = rig.publish()
    assert report.ok and report.waiting and not report.attention
    assert rig.tracker.get_pull_request(pr).body == body and rig.tracker.get_pull_request(pr).draft
    assert rig.own_comments(number) == issue_comments
    merge = rig.merge_on_github(pr)  # a person merges despite the findings: completion works as always
    done = rig.publish()
    assert done.completed == {feature_id: merge} and done.closed == {feature_id: number}
    assert rig.feature(feature_id).state is FeatureState.DONE and rig.store.load_task(task_id).state is \
        TaskState.VERIFIED
    after = review(rig, feature_id, (fake := FakeReviewer(ok("pass"))))
    assert not fake.requests and after.attention and current(rig, feature_id)["status"] == "complete"
    assert len(pr_comments(rig, pr)) == 1


def test_the_review_is_disabled_by_default_and_nothing_runs(repo: Path, state_dir: Path,
                                                            github_origin: Path) -> None:
    rig = PubRig(repo, state_dir, github_origin)  # the publication configuration: review off
    assert not rig.config.github.review.enabled
    _number, _task_id, feature_id, pr = published(rig)
    fake = FakeReviewer(ok("pass"))
    with pytest.raises(CoordinatorError, match="github.review.enabled is false"):
        review(rig, feature_id, fake)
    dry = review(rig, feature_id, fake, apply=False)
    assert dry.status == "dry-run" and any(w.startswith(f"review PR #{pr}") for w in dry.writes)
    assert not fake.requests and load_review(rig.store, feature_id) is None and not pr_comments(rig, pr)
    assert not (rig.store.root / "review").exists() or not list((rig.store.root / "review").iterdir())


def test_only_a_published_feature_is_reviewed(rig: PubRig) -> None:
    from test_fc_publish import verified  # pylint: disable=import-outside-toplevel
    _number, _task_id, feature_id = verified(rig)  # active: integrated and published only by the publisher
    fake = FakeReviewer(ok("pass"))
    report = review(rig, feature_id, fake)
    assert "without a recorded Draft PR" in report.attention[0] and not fake.requests
    rig.publish()
    pr = rig.candidate(feature_id).number
    assert pr is not None
    rig.tracker.update_pull_request(pr, state="closed")
    report = review(rig, feature_id, fake)
    assert "is closed: nothing is reviewed" in report.attention[0] and not fake.requests


# -- commands ---------------------------------------------------------------------------------------------------

def test_the_review_commands(rig: PubRig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture[str]) -> None:
    _number, _task_id, feature_id, pr = published(rig)
    base = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--tracker", "local"]
    assert cli_main([*base, "github", "review", "status"]) == 0
    assert "no reviews" in capsys.readouterr().out
    assert cli_main([*base, "github", "review", "run", feature_id]) == 0  # dry run with the built-in config
    out = capsys.readouterr().out
    assert f"would review PR #{pr}" in out and "dry run" in out and "reviewer runs: 0" in out
    assert cli_main([*base, "github", "review", "run", feature_id, "--apply"]) == 1
    assert "github.review.enabled is false" in capsys.readouterr().err
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    for section in ("intake", "publish", "review"):
        data["github"][section]["enabled"] = True
    enabled = tmp_path / "review.yaml"
    enabled.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    fakes = [FakeReviewer(ok("pass_with_notes", finding("minor"))), FakeReviewer(ok("pass"))]
    monkeypatch.setattr(ReviewRunner, "_default_reviewer", lambda self: fakes.pop(0))
    with_config = [*base, "--project-config", str(enabled), "github", "review"]
    assert cli_main([*with_config, "run", feature_id, "--apply"]) == 0
    assert "PASS_WITH_NOTES" in capsys.readouterr().out
    assert cli_main([*base, "github", "review", "status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["feature"], r["outcome"], r["pr"]) for r in rows] == [(feature_id, "PASS_WITH_NOTES", pr)]
    assert cli_main([*base, "github", "review", "show", feature_id]) == 0
    out = capsys.readouterr().out
    assert "minor: cli/src/commands.py:1" in out and "comment: posted" in out
    assert cli_main([*with_config, "retry", feature_id, "--reason", "check again"]) == 0  # dry run
    assert "would invalidate review" in capsys.readouterr().out
    assert cli_main([*with_config, "retry", feature_id, "--reason", "check again", "--actor", "spyderbg",
                     "--apply"]) == 0
    assert "PASS" in capsys.readouterr().out and not fakes
    record = load_review(rig.store, feature_id)
    assert record is not None and len(record.entries) == 2 and "spyderbg: check again" in \
        record.entries[0]["invalidated"]["reason"]
    assert cli_main([*base, "github", "review", "show", "no-such-feature"]) == 0
    assert "no review record" in capsys.readouterr().out
