"""Revalidation of an approved Issue's EXISTING Draft PR after a person moved its head (revalidate.py).

On the W4.3.3 publication rig (a bare `origin` remote with real pushes and fetches, the persisted mock tracker for
Issues and pull requests, mock runners, recorded gates) with the W4.6.1 review and FAKE reviewers: a person pushes
commits on top of the PR's branch, publication stops, and `github publish revalidate` validates the new head of the
same PR, moves the recorded candidate and requires a new independent review. No model, no real GitHub, no network."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,too-many-lines

from __future__ import annotations

import dataclasses
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from feature_coordinator import revalidate as revalidate_module
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.autonomy import Scheduling
from feature_coordinator.errors import CoordinatorError, HeadMismatchError, ScopeViolationError, WritersActiveError
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.model import FeatureState, TaskState
from feature_coordinator.revalidate import PullRequestRevalidator, load_revalidation, revalidation_gates
from feature_coordinator.review import load_review, review_evidence, review_rows

from feature_coordinator.intake import load_intake

from fc_helpers import git
from test_fc_intake import BODY, outside
from test_fc_publish import (  # pylint: disable=unused-import
    PubRig,
    draft_open,
    github_origin,
    remote,
    remote_sha,
)
from test_fc_review import FakeReviewer, current, finding, ok, pr_comments, review, review_config

FIX = "cli/src/fix.py"  # inside the cli task's write scope


@pytest.fixture
def rig(repo: Path, state_dir: Path, github_origin: Path) -> PubRig:
    return PubRig(repo, state_dir, github_origin, config=review_config())


def push_on_top(rig: PubRig, branch: str, files: dict[str, str] | None = None, *, base: str | None = None,
                message: str = "fix: address the review") -> str:
    """A person pushes one commit to the remote *branch* (on top of it, or on *base*: a rewrite, forced)."""
    work = Path(tempfile.mkdtemp(prefix="person-", dir=rig.state.parent))
    subprocess.run(["git", "clone", "-q", "--branch", branch, str(rig.bare), str(work)], check=True)
    if base:
        git(work, "reset", "-q", "--hard", base)
    for path, text in (files or {FIX: "FIXED = True\n"}).items():
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(text, encoding="utf-8")
        git(work, "add", path)
    user = ("-c", "user.name=Person", "-c", "user.email=person@example.invalid", "-c", "commit.gpgsign=false")
    git(work, *user, "commit", "-qm", message)
    git(work, "push", "-q", *(["--force"] if base else []), "origin", f"HEAD:refs/heads/{branch}")
    return git(work, "rev-parse", "HEAD").strip()


def person_pushes(rig: PubRig, feature_id: str, pr: int, files: dict[str, str] | None = None, **kwargs: Any) -> str:
    """... and GitHub's PR head follows the branch."""
    sha = push_on_top(rig, f"feature/{feature_id}", files, **kwargs)
    rig.tracker.update_pull_request(pr, head_sha=sha)
    return sha


def reviewed(rig: PubRig, result: Any = None) -> tuple[int, str, str, int, str]:
    """A published Draft PR whose verified head got an AI review (REQUEST_CHANGES by default), comment posted."""
    number, task_id, feature_id, pr = draft_open(rig)
    report = review(rig, feature_id, FakeReviewer(result or ok("request_changes", finding("major", title="No CSV"))))
    assert report.ok and current(rig, feature_id)["comment"]["status"] == "posted", report.to_dict()
    return number, task_id, feature_id, pr, rig.candidate(feature_id).head_sha


def moved(rig: PubRig, result: Any = None) -> tuple[str, int, str, str]:
    """... then a person pushed a fix to the PR's branch and publication stopped (the PR no longer matches)."""
    _number, _task_id, feature_id, pr, old = reviewed(rig, result)
    new = person_pushes(rig, feature_id, pr)
    stopped = rig.publish()
    assert "no longer matches" in stopped.attention[0] and rig.candidate(feature_id).attention
    return feature_id, pr, old, new


def revalidate(rig: PubRig, feature_id: str, head: str | None = None, *, apply: bool = True, coord: Any = None,
               faults: tuple[str, ...] = ()):
    return PullRequestRevalidator(coord or rig.coord, faults=faults).run(feature_id, apply=apply, expected_head=head)


def local_head(rig: PubRig, feature_id: str) -> str:
    return git(rig.repo, "rev-parse", f"refs/heads/feature/{feature_id}").strip()


def notices(rig: PubRig, pr: int) -> list[str]:
    return [c for c in pr_comments(rig, pr) if "no longer applies" in c]


def unchanged_since(rig: PubRig, feature_id: str, old: str, review_key: str) -> None:
    """Nothing was recorded: the candidate, its stop and the old review are as they were."""
    cand = rig.candidate(feature_id)
    assert cand.head_sha == old and cand.attention and rig.feature(feature_id).integration_evidence.head_sha == old
    entry = current(rig, feature_id)
    assert entry["key"] == review_key and entry["status"] == "complete"
    assert len(rig.prs()) == 1


# -- the successful path ----------------------------------------------------------------------------------------

def test_a_moved_head_is_revalidated_and_needs_a_new_independent_review(rig: PubRig) -> None:  # pylint: disable=too-many-locals,too-many-statements
    feature_id, pr, old, new = moved(rig)
    old_review = current(rig, feature_id)
    assert old_review["outcome"] == "REQUEST_CHANGES"
    writes, calls, authorized = rig.tracker.writes, list(rig.gates.calls), list(rig.tracker.authorized)
    # the dry run: reads only — nothing fetched into a branch, run or written
    preview = revalidate(rig, feature_id, apply=False)
    assert preview.ok and preview.status == "dry-run" and (preview.from_head, preview.to_head) == (old, new)
    assert any("fast-forward" in w for w in preview.writes) and any(f"invalidate review {old_review['key']}" in w
                                                                    for w in preview.writes)
    assert f"--head {new} --apply" in preview.lines[-1] and not preview.commits
    assert "checked after the fetch" in " ".join(preview.lines)  # the dry run fetches nothing
    rig.coord.git.fetch_branch(f"feature/{feature_id}")  # once the commit is local, the dry run checks it too
    preview = revalidate(rig, feature_id, apply=False)
    assert preview.ok and preview.commits == [new] and preview.paths == [FIX]
    assert rig.tracker.writes == writes and rig.gates.calls == calls and local_head(rig, feature_id) == old
    assert load_revalidation(rig.store, feature_id) is None and current(rig, feature_id)["status"] == "complete"
    # --apply with the head the dry run showed
    report = revalidate(rig, feature_id, new)
    assert report.ok and report.status == "revalidated", report.to_dict()
    feature, cand = rig.feature(feature_id), rig.candidate(feature_id)
    assert (cand.head_sha, cand.attention, cand.number, feature.state) == (new, None, pr, FeatureState.REVIEW)
    assert feature.integration_evidence.head_sha == new and cand.pushed["sha"] == new and cand.pushed["before"] == old
    record = load_intake(rig.store, feature_id)
    expected = revalidation_gates(feature, record, rig.store.load_tasks(feature))
    assert {"contract-check", "docs-check"} <= set(expected) and rig.gates.calls[len(calls):] == list(expected)
    assert [g["gate"] for g in report.gates] == list(expected) and all(g["status"] == "passed" for g in report.gates)
    # the shared checkout was fast-forwarded (no reset), nothing was pushed, no PR created or authorized
    assert local_head(rig, feature_id) == new == git(rig.repo, "rev-parse", "HEAD").strip()
    assert git(rig.repo, "rev-list", "--count", f"{old}..{new}").strip() == "1"
    assert remote_sha(rig.bare, f"feature/{feature_id}") == new and len(rig.prs()) == 1
    assert rig.tracker.authorized == authorized and rig.store.load_task(f"{feature_id}.cli").state is TaskState.VERIFIED
    # history: the previous candidate is kept in the attempt record
    (attempt,) = load_revalidation(rig.store, feature_id).entries  # type: ignore[union-attr]
    assert (attempt["status"], attempt["from_head"], attempt["to_head"]) == ("validated", old, new)
    assert attempt["previous"]["head_sha"] == old and attempt["previous"]["integration_evidence"]["head_sha"] == old
    assert "no longer matches" in attempt["previous"]["attention"]["reason"] and attempt["evidence"]["head_sha"] == new
    # the old review: invalidated, kept as history (outcome and findings), one notice, never evidence again
    entry = load_review(rig.store, feature_id).entry(old_review["key"])  # type: ignore[union-attr]
    assert entry["status"] == "invalidated" and entry["outcome"] == "REQUEST_CHANGES" and entry["findings"]
    assert attempt["review_invalidated"] == old_review["key"] == report.review["invalidated"]
    assert report.review["blocking"] == 1 and "superseded" in entry["invalidated"]["reason"]
    assert len(notices(rig, pr)) == 1 and entry["notice"]["status"] == "posted"
    assert review_evidence(rig.store, feature_id, pr, old) is None and review_evidence(rig.store, feature_id, pr,
                                                                                         new) is None
    kinds = [e["kind"] for e in rig.store.events()]
    tail = kinds[kinds.index("publish-revalidation-started"):]
    assert tail[:3] == ["publish-revalidation-started", "review-invalidated", "publish-revalidated"]
    assert "pull-request-create-started" not in tail and "publish-pushed" not in tail
    # publication follows the same PR at the new head again
    after = rig.publish()
    assert after.ok and after.waiting and not (after.created or after.adopted or after.pushed), after.lines()
    # the new head is reviewed from scratch (a new entry), the old outcome is never carried over
    fresh = review(rig, feature_id, FakeReviewer(ok("pass")))
    assert fresh.outcome == "PASS" and fresh.entry == f"pr{pr}-{new[:12]}-g1" and fresh.reviewer_runs == 1
    assert review_evidence(rig.store, feature_id, pr, new)["outcome"] == "PASS"  # type: ignore[index]
    assert len(load_review(rig.store, feature_id).entries) == 2  # type: ignore[union-attr]
    # idempotent: a repeat finds the PR at the candidate and writes nothing
    writes = rig.tracker.writes
    again = revalidate(rig, feature_id, new)
    assert again.ok and again.status == "unchanged" and "revalidated by" in again.lines[0]
    assert rig.tracker.writes == writes and len(load_revalidation(rig.store, feature_id).entries) == 1  # type: ignore


def test_a_new_head_needs_its_own_review_whatever_the_old_outcome(rig: PubRig) -> None:
    feature_id, pr, old, new = moved(rig, ok("pass"))
    assert review_evidence(rig.store, feature_id, pr, old)["outcome"] == "PASS"  # type: ignore[index]
    assert review_evidence(rig.store, feature_id, pr, new) is None  # a PASS is evidence for its own head only
    assert revalidate(rig, feature_id, new[:12]).status == "revalidated"  # a 12-character head is enough
    assert review_evidence(rig.store, feature_id, pr, old) is None and review_evidence(rig.store, feature_id, pr,
                                                                                         new) is None
    rows = review_rows(rig.store)
    assert [(r["status"], r["head"]) for r in rows] == [("invalidated", old)]
    plan = review(rig, feature_id, apply=False)  # the next run reviews the new head (a new generation)
    assert plan.status == "dry-run" and any(f"at {new[:12]}" in w for w in plan.writes)
    blocked = review(rig, feature_id, FakeReviewer(ok("request_changes", finding("blocker", path=FIX))))
    assert blocked.outcome == "REQUEST_CHANGES"  # the review policy is unchanged: a blocker never passes


# -- nothing to revalidate ----------------------------------------------------------------------------------------

def test_an_unchanged_head_is_not_revalidated(rig: PubRig) -> None:
    _number, _task_id, feature_id, pr, old = reviewed(rig)
    key, writes, calls = current(rig, feature_id)["key"], rig.tracker.writes, list(rig.gates.calls)
    for apply in (False, True):
        report = revalidate(rig, feature_id, old if apply else None, apply=apply)
        assert report.ok and report.status == "unchanged" and "nothing to revalidate" in report.lines[0]
    assert rig.tracker.writes == writes and rig.gates.calls == calls and load_revalidation(rig.store, feature_id) \
        is None
    assert current(rig, feature_id)["key"] == key and current(rig, feature_id)["status"] == "complete"
    assert rig.candidate(feature_id).head_sha == old and not notices(rig, pr)
    with rig.store.transaction():  # stopped for another reason: reported, never cleared here
        fresh = rig.feature(feature_id)
        fresh.pull_request.attention = {"reason": "origin changed since activation", "since": "earlier"}  # type: ignore
        rig.store.save_feature(fresh)
    stopped = revalidate(rig, feature_id, old)
    assert not stopped.ok and stopped.status == "unchanged" and "publish reset" in stopped.attention[0]
    assert rig.candidate(feature_id).attention


# -- concurrent changes ---------------------------------------------------------------------------------------------

class MovingGates:  # pylint: disable=too-few-public-methods
    """The gates of a revalidation, during which a person pushes again (GitHub's PR head follows, or lags)."""

    def __init__(self, rig: PubRig, feature_id: str, pr: int, *, pr_follows: bool = True) -> None:
        self.rig, self.feature_id, self.pr, self.pr_follows = rig, feature_id, pr, pr_follows
        self.pushed: str | None = None

    def __call__(self, name: str, context: Any) -> int:
        if self.pushed is None:
            self.pushed = push_on_top(self.rig, f"feature/{self.feature_id}", {FIX: "FIXED = 2\n"}, message="again")
            if self.pr_follows:
                self.rig.tracker.update_pull_request(self.pr, head_sha=self.pushed)
        return self.rig.gates(name, context)


@pytest.mark.parametrize("pr_follows", [True, False])
def test_a_head_that_moves_during_validation_is_not_recorded(rig: PubRig, pr_follows: bool) -> None:
    feature_id, pr, old, new = moved(rig)
    key = current(rig, feature_id)["key"]
    coord = rig.make()
    coord.gate_runner = MovingGates(rig, feature_id, pr, pr_follows=pr_follows)
    report = revalidate(rig, feature_id, new, coord=coord)
    assert not report.ok and report.status == "stale" and "nothing was recorded" in report.attention[0]
    assert ("PR #" if pr_follows else "origin moved") in report.attention[0]
    unchanged_since(rig, feature_id, old, key)
    assert not notices(rig, pr) and local_head(rig, feature_id) == new  # fast-forwarded only as far as validated
    (attempt,) = load_revalidation(rig.store, feature_id).entries  # type: ignore[union-attr]
    assert attempt["status"] == "stale" and attempt["previous"] is None and attempt["gate_results"]
    newer = coord.gate_runner.pushed
    if not pr_follows:
        lagging = revalidate(rig, feature_id, new)  # GitHub still reports the older head: origin disagrees
        assert lagging.status == "stale" and "agree" in lagging.attention[0]
        rig.tracker.update_pull_request(pr, head_sha=newer)
    assert revalidate(rig, feature_id, new).status == "stale"  # the head the operator checked is gone
    done = revalidate(rig, feature_id, newer)
    assert done.ok and rig.candidate(feature_id).head_sha == newer == local_head(rig, feature_id)
    assert len(done.commits) == 2 and len(notices(rig, pr)) == 1


def test_active_writers_and_runs_are_refused(rig: PubRig, monkeypatch: pytest.MonkeyPatch) -> None:
    feature_id, pr, old, new = moved(rig)
    key, calls = current(rig, feature_id)["key"], list(rig.gates.calls)
    task_id = f"{feature_id}.cli"
    with rig.store.transaction():
        rig.store.save_claim(task_id, {"task": task_id, "worker": "elsewhere"})
    assert "claims or launches" in revalidate(rig, feature_id, new).deferred[0]
    with rig.store.transaction():
        rig.store.delete_claim(task_id)
        rig.store.save_record("scheduling", Scheduling(id=task_id, feature_id=feature_id,
                                                       in_flight={"launch": 1, "instance": "earlier"}))
    assert "claims or launches" in revalidate(rig, feature_id, new).deferred[0]
    with rig.store.transaction():
        rig.store.save_record("scheduling", Scheduling(id=task_id, feature_id=feature_id, revision=1))
    for lock in (rig.store.locks.publish(), rig.store.locks.review()):  # a publication pass, a review run
        with lock:
            busy = revalidate(rig, feature_id, new)
            assert busy.status == "deferred" and "in progress" in busy.deferred[0]
    with rig.store.locks.project(shared=True):  # a scoped writer holds the checkout
        writer = revalidate(rig, feature_id, new)
        assert writer.status == "deferred" and "WritersActiveError" in writer.deferred[0]
    assert load_revalidation(rig.store, feature_id).entries[-1]["status"] == "deferred"  # type: ignore[union-attr]
    with rig.store.transaction():  # a reviewer process the Worker started is still running
        record = load_review(rig.store, feature_id)
        record.entry(key)["running"] = {"attempt": 2, "tag": "t" * 16, "started_at": "now"}  # type: ignore[index]
        rig.store.save_record("review", record)
    monkeypatch.setattr(revalidate_module, "attempt_processes", lambda running: ("running", [4242]))
    assert "reviewer process runs" in revalidate(rig, feature_id, new).deferred[0]
    unchanged_since(rig, feature_id, old, key)
    assert rig.gates.calls == calls and local_head(rig, feature_id) == old  # nothing ran
    monkeypatch.setattr(revalidate_module, "attempt_processes", lambda running: ("gone", []))
    done = revalidate(rig, feature_id, new)  # a crashed attempt: logged as interrupted in the invalidated review
    assert done.ok and current(rig, feature_id)["attempt_log"][-1]["status"] == "interrupted"
    assert len(rig.prs()) == 1 and len(notices(rig, pr)) == 1


def test_origin_moving_before_the_fetch_runs_nothing(rig: PubRig, monkeypatch: pytest.MonkeyPatch) -> None:
    feature_id, _pr, old, new = moved(rig)
    key, calls = current(rig, feature_id)["key"], list(rig.gates.calls)
    coord = rig.make()
    real = type(coord.git).fetch_branch

    def racing(self: Any, branch: str, **kwargs: Any) -> str:
        if branch.startswith("feature/"):  # a person pushes again between the PR read and the fetch
            push_on_top(rig, branch, {FIX: "FIXED = 9\n"}, message="race")
        return real(self, branch, **kwargs)

    monkeypatch.setattr(type(coord.git), "fetch_branch", racing)
    report = revalidate(rig, feature_id, new, coord=coord)
    assert report.status == "stale" and "the head moved" in report.attention[0]
    unchanged_since(rig, feature_id, old, key)
    assert rig.gates.calls == calls and local_head(rig, feature_id) == old  # nothing fast-forwarded, nothing run


def test_a_claim_started_during_validation_records_nothing(rig: PubRig) -> None:
    feature_id, pr, old, new = moved(rig)
    key, task_id = current(rig, feature_id)["key"], f"{feature_id}.cli"

    def claiming(name: str, context: Any) -> int:
        with rig.store.transaction():
            rig.store.save_claim(task_id, {"task": task_id, "worker": "elsewhere"})
        return rig.gates(name, context)

    coord = rig.make()
    coord.gate_runner = claiming
    report = revalidate(rig, feature_id, new, coord=coord)
    assert report.status == "stale" and "a claim started" in report.attention[0]
    unchanged_since(rig, feature_id, old, key)
    assert not notices(rig, pr)
    assert load_revalidation(rig.store, feature_id).entries[-1]["status"] == "stale"  # type: ignore[union-attr]


def test_a_withdrawn_approval_or_an_unclean_checkout_is_refused(rig: PubRig) -> None:
    feature_id, _pr, old, new = moved(rig)
    key, calls = current(rig, feature_id)["key"], list(rig.gates.calls)
    number = load_intake(rig.store, feature_id).issue_number  # type: ignore[union-attr]
    outside(rig, number, body="Something completely different")
    for apply in (False, True):
        withdrawn = revalidate(rig, feature_id, new, apply=apply)
        assert withdrawn.status == "refused" and "approval no longer holds" in withdrawn.attention[0]
    outside(rig, number, body=BODY)
    (rig.repo / "cli" / "stray.py").write_text("STRAY = 1\n", encoding="utf-8")  # not committed
    preview = revalidate(rig, feature_id, apply=False)
    assert preview.status == "refused" and "uncommitted or foreign changes" in preview.attention[0]
    dirty = revalidate(rig, feature_id, new)
    assert dirty.status == "deferred" and "cli/stray.py" in dirty.deferred[0]
    unchanged_since(rig, feature_id, old, key)
    assert rig.gates.calls == calls and local_head(rig, feature_id) == old
    (rig.repo / "cli" / "stray.py").unlink()
    assert revalidate(rig, feature_id, new).status == "revalidated"


# -- failed gates ---------------------------------------------------------------------------------------------------

def test_failed_gates_leave_the_candidate_and_the_stop(rig: PubRig) -> None:
    feature_id, pr, old, new = moved(rig)
    key = current(rig, feature_id)["key"]
    rig.gates.failing = {"contract-check"}
    report = revalidate(rig, feature_id, new)
    assert not report.ok and report.status == "failed" and "the gates failed" in report.attention[0]
    unchanged_since(rig, feature_id, old, key)
    assert not notices(rig, pr) and local_head(rig, feature_id) == new
    (attempt,) = load_revalidation(rig.store, feature_id).entries  # type: ignore[union-attr]
    assert attempt["status"] == "failed" and {g["gate"]: g["status"] for g in attempt["gate_results"]}[
        "contract-check"] == "failed"
    assert "no longer matches" in rig.publish().attention[0]  # publication stays stopped
    rig.gates.failing = set()
    fixed = person_pushes(rig, feature_id, pr, {FIX: "FIXED = 3\n"}, message="fix the gate")
    done = revalidate(rig, feature_id, fixed)  # the branch is on the way from the candidate: continued from there
    assert done.ok and rig.candidate(feature_id).head_sha == fixed == local_head(rig, feature_id)
    assert "fast-forwarded" in " ".join(done.lines) and len(done.commits) == 2
    assert [e["status"] for e in load_revalidation(rig.store, feature_id).entries] == ["failed", "validated"]  # type: ignore


# -- identity ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("change", "reason"), [
    ({"base": "main"}, "targets 'main'"),
    ({"head": "feature/other"}, "head branch is 'feature/other'"),
    ({"head_repo": "someone/fork"}, "its head is in 'someone/fork'"),
    ({"base_repo": "someone/other"}, "targets the repository"),
    ({"author": "stranger"}, "opened by 'stranger'"),
    ({"node_id": "PR_another"}, "node ID"),
    ({"url": "mock://pull/999"}, "its URL"),
    ({"state": "closed"}, "closed: only an open PR"),
    ({"state": "closed", "merged": True}, "merged: only an open PR"),
])
def test_another_pr_or_branch_identity_is_refused(rig: PubRig, change: dict[str, Any], reason: str) -> None:
    feature_id, pr, old, _new = moved(rig)
    key, calls = current(rig, feature_id)["key"], list(rig.gates.calls)
    rig.tracker.update_pull_request(pr, **change)
    report = revalidate(rig, feature_id, rig.tracker.get_pull_request(pr).head_sha)
    assert not report.ok and report.status == "refused" and reason in report.attention[0], report.attention
    unchanged_since(rig, feature_id, old, key)
    assert rig.gates.calls == calls and local_head(rig, feature_id) == old
    assert load_revalidation(rig.store, feature_id) is None


@pytest.mark.parametrize(("how", "reason"), [
    ("rewritten", "does not contain the validated head"),
    ("out-of-scope", "no task of"),
    ("protected", "no task of"),
    ("merge", "merge commit"),
])
def test_a_head_that_is_not_the_branch_continuation_is_refused(rig: PubRig, how: str, reason: str) -> None:
    _number, _task_id, feature_id, pr, old = reviewed(rig)
    key, calls = current(rig, feature_id)["key"], list(rig.gates.calls)
    branch = f"feature/{feature_id}"
    if how == "rewritten":
        new = person_pushes(rig, feature_id, pr, base=rig.feature(feature_id).base_sha)  # a forced push
    elif how == "merge":
        side = push_on_top(rig, "dev", {"docs_agents/cli/side.md": "side\n"}, message="side")
        work = Path(tempfile.mkdtemp(prefix="merge-", dir=rig.state.parent))
        subprocess.run(["git", "clone", "-q", "--branch", branch, str(rig.bare), str(work)], check=True)
        user = ("-c", "user.name=Person", "-c", "user.email=person@example.invalid")
        git(work, *user, "merge", "--no-ff", "-q", "-m", "merge dev", side)
        git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        new = git(work, "rev-parse", "HEAD").strip()
        rig.tracker.update_pull_request(pr, head_sha=new)
    else:
        path = "backend/src/main.py" if how == "out-of-scope" else "content/secret.json"
        new = person_pushes(rig, feature_id, pr, {path: "changed\n"})
    preview = revalidate(rig, feature_id, apply=False)  # nothing is fetched by a dry run: checked by --apply
    assert preview.status == "dry-run" and "checked after the fetch" in " ".join(preview.lines)
    report = revalidate(rig, feature_id, new)
    assert not report.ok and report.status == "refused" and reason in report.attention[0], report.attention
    cand = rig.candidate(feature_id)
    assert cand.head_sha == old and current(rig, feature_id)["key"] == key and current(rig, feature_id)[
        "status"] == "complete"
    assert rig.gates.calls == calls and local_head(rig, feature_id) == old and len(rig.prs()) == 1
    assert load_revalidation(rig.store, feature_id).entries[-1]["status"] == "refused"  # type: ignore[union-attr]


def test_the_expected_head_and_origin_must_agree(rig: PubRig) -> None:
    feature_id, pr, old, new = moved(rig)
    wrong = revalidate(rig, feature_id, "0" * 12)
    assert wrong.status == "stale" and "not the expected" in wrong.attention[0]
    rig.tracker.update_pull_request(pr, head_sha="1" * 40)  # GitHub reports a head origin does not have
    lagging = revalidate(rig, feature_id, "1" * 40)
    assert lagging.status == "stale" and "origin has" in lagging.attention[0]
    assert rig.candidate(feature_id).head_sha == old and load_revalidation(rig.store, feature_id) is None
    rig.tracker.update_pull_request(pr, head_sha=new)
    with pytest.raises(CoordinatorError, match="needs --head"):
        revalidate(rig, feature_id)
    with pytest.raises(CoordinatorError, match="12 to 40"):
        revalidate(rig, feature_id, "abc")
    github = rig.config.github  # the review needs publication: both off
    off = dataclasses.replace(rig.config, github=dataclasses.replace(
        github, publish=dataclasses.replace(github.publish, enabled=False),
        review=dataclasses.replace(github.review, enabled=False)))
    with pytest.raises(CoordinatorError, match="publish.enabled is false"):
        revalidate(rig, feature_id, new, coord=rig.make(config=off))
    assert revalidate(rig, feature_id, coord=rig.make(config=off), apply=False).status == "dry-run"  # reads only


# -- recovery -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("fault", ["crash-before-fast-forward", "crash-after-fast-forward", "crash-after-gates",
                                   "crash-before-notice"])
def test_an_interrupted_revalidation_is_completed_once(rig: PubRig, fault: str) -> None:
    feature_id, pr, old, new = moved(rig)
    key = current(rig, feature_id)["key"]
    with pytest.raises(SimulatedCrash):
        revalidate(rig, feature_id, new, faults=(f"{fault}:{feature_id}",))
    after_crash = load_revalidation(rig.store, feature_id).entries  # type: ignore[union-attr]
    if fault == "crash-before-notice":  # the transaction was committed: only the notice is still due
        assert rig.candidate(feature_id).head_sha == new and [e["status"] for e in after_crash] == ["validated"]
        assert current(rig, feature_id)["status"] == "invalidated" and not notices(rig, pr)
    else:
        unchanged_since(rig, feature_id, old, key)
        assert [e["status"] for e in after_crash] == ["running"]
    restarted = rig.make()  # a new process over the same repository, state and tracker
    report = revalidate(rig, feature_id, new, coord=restarted)
    assert report.ok and report.status in ("revalidated", "unchanged"), report.to_dict()
    assert rig.candidate(feature_id).head_sha == new == local_head(rig, feature_id) and not rig.candidate(
        feature_id).attention
    statuses = [e["status"] for e in load_revalidation(rig.store, feature_id).entries]  # type: ignore[union-attr]
    assert statuses == (["validated"] if fault == "crash-before-notice" else ["interrupted", "validated"])
    entry = load_review(rig.store, feature_id).entry(key)  # type: ignore[union-attr]
    assert entry["status"] == "invalidated" and entry["outcome"] == "REQUEST_CHANGES"
    assert len(notices(rig, pr)) == 1 and len(rig.prs()) == 1
    again = rig.make()
    writes = again.tracker.writes
    assert revalidate(rig, feature_id, new, coord=again).status == "unchanged"
    assert again.tracker.writes == writes and len(notices(rig, pr)) == 1  # never posted twice
    assert rig.publish().ok


def test_a_split_review_invalidation_is_completed_by_the_next_run(rig: PubRig) -> None:
    """A candidate already at the new head while its current review is still bound to the old one (e.g. restored
    from a backup taken in between): the next run invalidates it — never evidence for another head."""
    feature_id, pr, _old, new = moved(rig)
    with rig.store.transaction():
        fresh = rig.feature(feature_id)
        fresh.pull_request.head_sha, fresh.pull_request.attention = new, None  # type: ignore[union-attr]
        rig.store.save_feature(fresh)
    git(rig.repo, "merge", "--ff-only", "-q", rig.coord.git.fetch_branch(f"feature/{feature_id}"))
    report = revalidate(rig, feature_id, new)
    assert report.ok and report.status == "unchanged" and report.review["invalidated"]  # type: ignore[index]
    assert current(rig, feature_id)["status"] == "invalidated" and len(notices(rig, pr)) == 1


# -- no second PR, ever ----------------------------------------------------------------------------------------------

def test_revalidation_never_creates_or_pushes(rig: PubRig, monkeypatch: pytest.MonkeyPatch) -> None:
    feature_id, _pr, _old, new = moved(rig)

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("revalidation must never create a PR or push")

    coord = rig.make()
    monkeypatch.setattr(type(coord.tracker), "create_pull_request", refuse)
    monkeypatch.setattr(type(coord.git), "push_branch", refuse)
    assert revalidate(rig, feature_id, new, coord=coord).status == "revalidated"
    assert len(rig.prs()) == 1 and rig.publish().ok and len(rig.prs()) == 1


# -- the Git guard -----------------------------------------------------------------------------------------------

def test_fast_forward_branch_guards(rig: PubRig) -> None:
    feature_id, _pr, old, new = moved(rig)
    gitc, branch = rig.coord.git, f"feature/{feature_id}"
    gitc.fetch_branch(branch)
    with pytest.raises(ScopeViolationError):
        with rig.store.locks.project(shared=False) as project:
            gitc.fast_forward_branch("dev", since=old, to=new, project=project)
    with pytest.raises(WritersActiveError):
        with rig.store.locks.project(shared=True) as project:
            gitc.fast_forward_branch(branch, since=old, to=new, project=project)
    base = rig.feature(feature_id).base_sha
    with pytest.raises(HeadMismatchError, match="not on the way"):
        with rig.store.locks.project(shared=False) as project:
            gitc.fast_forward_branch(branch, since=new, to=base, project=project)  # backwards: never a reset
    with rig.store.locks.project(shared=False) as project:
        assert gitc.fast_forward_branch(branch, since=old, to=new, project=project) is True
        assert gitc.fast_forward_branch(branch, since=old, to=new, project=project) is False
    assert local_head(rig, feature_id) == new


# -- the command ------------------------------------------------------------------------------------------------

def test_the_revalidate_command(rig: PubRig, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    feature_id, _pr, old, new = moved(rig)
    base = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--tracker", "local"]
    assert cli_main([*base, "github", "publish", "revalidate", feature_id, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["status"], data["from_head"], data["to_head"], data["ok"]) == ("dry-run", old, new, True)
    assert cli_main([*base, "github", "publish", "revalidate", feature_id]) == 0
    out = capsys.readouterr().out
    assert f"--head {new} --apply" in out and "dry run" in out and "would run the gates" in out
    assert cli_main([*base, "github", "publish", "status"]) == 0
    assert f"github publish revalidate {feature_id}" in capsys.readouterr().out
    assert cli_main([*base, "github", "publish", "revalidate", feature_id, "--apply"]) == 1  # built-in: off
    assert "publish.enabled is false" in capsys.readouterr().err
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["publish"]["enabled"] = True
    enabled = tmp_path / "publish.yaml"
    enabled.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert cli_main([*base, "--project-config", str(enabled), "github", "publish", "revalidate", feature_id,
                     "--apply"]) == 1
    assert "needs --head" in capsys.readouterr().err
    assert rig.candidate(feature_id).head_sha == old and load_revalidation(rig.store, feature_id) is None
