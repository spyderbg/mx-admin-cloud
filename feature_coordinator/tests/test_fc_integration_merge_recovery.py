"""W4.7 (M5): post-merge verification, crash recovery with fault injection, idempotency and concurrency of
`integration merge` (integration_merge.py, integration_postmerge.py, integration_isolation.isolated_fetch).

Every test drives a job M1 → M4 on disposable repositories (a temporary checkout with a bare `origin`, a throw-away
operator key, a recording fake gate runner and a fake reviewer process), signs the MERGE approval, and then merges
through a SCRIPTED GitHub behind the real client (fc_integration_merge_rig.FakeMergeGitHub). The scripted GitHub
performs the merge FOR REAL on the disposable origin with Git plumbing, so the post-merge verification reads genuine
objects; it can be told to perform a wrong merge, lose its answer or fail. Crashes are injected at the merger's fault
points (SimulatedCrash): the next run uses a FRESH merger, like a restarted process. No network, no real
GitHub, no real merge, no model."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines

from __future__ import annotations

import dataclasses
import subprocess
import threading
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from feature_coordinator import integration_postmerge
from feature_coordinator.errors import RemoteUnavailableError
from feature_coordinator.integration import IntegrationError, JobState, abort_job
from feature_coordinator.integration_isolation import FETCHED_REF, isolated_fetch
from feature_coordinator.integration_merge import IntegrationMerger
from feature_coordinator.issue_sync import SimulatedCrash

from fc_helpers import git
from fc_integration_merge_rig import FakeMergeGitHub, approve_merge, bare, merge, merge_record_for, ready_job
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, OPERATOR, REPOSITORY, OperatorKey, Rig, change_on, github_origin, origin, pr_json, snapshot, tree_digest,
)
from test_fc_integration_review import launchers, rrig  # pylint: disable=unused-import
from test_fc_integration_validate import key, rig, seeded, tracker  # pylint: disable=unused-import


# -- helpers -------------------------------------------------------------------------------------------------------

def refs(origin_path: Path) -> dict[str, str]:
    out = bare(origin_path, "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split(" ", 1) for line in out.splitlines())


def checkout_state(repo: Path) -> dict[str, str]:
    return {"git": tree_digest(repo / ".git"), "status": git(repo, "status", "--porcelain=v1", "--untracked-files=all")}


def events(rig: Rig, kind: str) -> list[dict[str, Any]]:
    return [e for e in rig.store.events() if e["kind"] == kind]


def kinds(rig: Rig, job_id: str) -> list[str]:
    return [entry["kind"] for entry in rig.jobs.load(job_id).evidence]


def of_kind(rig: Rig, job_id: str, kind: str) -> list[dict[str, Any]]:
    return [entry for entry in rig.jobs.load(job_id).evidence if entry["kind"] == kind]


def states(rig: Rig, job_id: str) -> list[str]:
    return [entry["to"] for entry in rig.jobs.load(job_id).history]


def raw_record(rig: Rig, job_id: str) -> bytes:
    return (rig.state / "integration" / f"{job_id}.json").read_bytes()


def e_is(entry: dict[str, Any], action: str) -> bool:
    return entry["kind"] == "merge-attempt" and entry["action"] == action


def crash(rig: Rig, job_id: str, fault: str) -> None:
    with pytest.raises(SimulatedCrash):
        merge(rig, job_id, faults=(fault,))


def with_post_checks(rig: Rig, *names: str, required: tuple[str, ...] = ()) -> Rig:
    integration = dataclasses.replace(rig.config.github.integration, post_merge_checks=tuple(names),
                                      required_checks=required)
    rig.config = dataclasses.replace(rig.config, github=dataclasses.replace(rig.config.github, integration=integration))
    return rig


def advance_dev(origin_path: Path, message: str = "another job merged") -> str:
    """ANOTHER integration job moves dev: one more commit on top of origin's dev (plumbing on the disposable origin)."""
    tip = bare(origin_path, "rev-parse", "refs/heads/dev").strip()
    tree = bare(origin_path, "rev-parse", f"{tip}^{{tree}}").strip()
    commit = bare(origin_path, "commit-tree", tree, "-p", tip, "-m", message).strip()
    bare(origin_path, "update-ref", "refs/heads/dev", commit, tip)
    return commit


def custom_merge(fake: FakeMergeGitHub, parents: Callable[[str, str], tuple[str, str]]) -> None:
    """The scripted GitHub performs a merge commit with the merged tree but the parents *parents(dev tip, source)*."""
    def do_merge(pr: dict[str, Any]) -> str:
        dev, source = pr["base"]["ref"], pr["head"]["sha"]
        tip = bare(fake.origin, "rev-parse", f"refs/heads/{dev}").strip()
        tree = bare(fake.origin, "merge-tree", "--write-tree", tip, source).splitlines()[0].strip()
        first, second = parents(tip, source)
        commit = bare(fake.origin, "commit-tree", tree, "-p", first, "-p", second, "-m", "Merge").strip()
        bare(fake.origin, "update-ref", f"refs/heads/{dev}", commit, tip)
        pr.update({"state": "closed", "merged": True, "merged_at": "2026-11-16T10:00:00Z",
                   "merge_commit_sha": commit, "merged_by": {"login": OPERATOR}})
        return commit
    fake.do_merge = do_merge  # type: ignore[method-assign]


def merged_not_verified(rig: Rig, launchers_: Any, origin_path: Path, **kwargs: Any) -> tuple[Any, FakeMergeGitHub]:
    """A job that GitHub merged and the coordinator recorded as MERGED, with the verification not run (a crash)."""
    job, fake = ready_job(rig, launchers_, origin_path, **kwargs)
    crash(rig, job.job_id, "after-merged-record")
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.MERGED and stored.merge_result["verification"]["checks"] == []
    return stored, fake


# -- post-merge verification ---------------------------------------------------------------------------------------

def test_a_correct_merge_completes_and_records_the_verified_merge_result(rrig: Rig, launchers: Any,
                                                                         github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    before, checkout = refs(github_origin), checkout_state(rrig.repo)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed" and report.ok and report.changed
    stored = rrig.jobs.load(job.job_id)
    result = stored.merge_result
    assert stored.state is JobState.COMPLETED and states(rrig, job.job_id)[-3:] == ["AWAITING_MERGE_APPROVAL", "MERGED",
                                                                                   "COMPLETED"]
    assert result["commit_sha"] != stored.candidate["head_sha"]          # GitHub's commit is not the candidate commit
    assert result["parents"] == [job.base["expected_sha"], job.source["sha"]]
    # ... but its tree is the approved candidate tree
    assert result["tree_sha"] == stored.candidate["tree_sha"]
    assert result["pr"] == 101 and result["verified"] is True and result["merged_by"] == OPERATOR
    assert result["verification"]["state"] == "verified"
    checks = {c["name"]: c["status"] for c in result["verification"]["checks"]}
    assert checks == {"pr-merged": "pass", "dev-contains": "pass", "commit": "pass", "parents": "pass", "tree": "pass",
                      "agreement": "pass", "checks": "not-configured"}
    outcome = of_kind(rrig, job.job_id, "merge-outcome")[-1]
    assert outcome["status"] == "merged" and outcome["merge_commit_sha"] == result["commit_sha"]
    assert [e["kind"] for e in rrig.store.events() if e["kind"] in ("integration-merged", "integration-completed")] == \
        ["integration-merged", "integration-completed"]
    # the coordinator changed nothing on origin: dev is exactly the merge GitHub wrote; no other ref moved; no push
    after = refs(github_origin)
    assert {name for name in after if before.get(name) != after[name]} == {"refs/heads/dev"}
    assert after["refs/heads/dev"] == result["commit_sha"] and after[f"refs/heads/{BRANCH}"] == job.source["sha"]
    assert FETCHED_REF not in after and checkout_state(rrig.repo) == checkout
    assert fake.writes() == [("POST", f"repos/{REPOSITORY}/pulls"), ("PUT", f"repos/{REPOSITORY}/pulls/101/merge")]
    assert fake.merge_requests == [{"number": 101, "merge_method": "merge", "sha": job.source["sha"]}]


@pytest.mark.parametrize("mode, check", [("wrong-parent", "parents"), ("squash", "parents"), ("wrong-tree", "tree")])
def test_a_merge_that_is_not_the_approved_one_fails_the_job_and_touches_nothing(rrig: Rig, launchers: Any,
                                                                                 github_origin: Path, mode: str,
                                                                                 check: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.mode = mode
    checkout = checkout_state(rrig.repo)
    report = merge(rrig, job.job_id)
    assert report.outcome == "failed" and not report.ok
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.FAILED and stored.attention is not None
    assert check in stored.attention["reason"] or any(check in r for r in stored.attention["reasons"])
    result = stored.merge_result
    assert result["verified"] is False and result["verification"]["state"] == "failed"
    assert {c["name"]: c["status"] for c in result["verification"]["checks"]}[check] == "fail"
    assert states(rrig, job.job_id)[-2:] == ["MERGED", "FAILED"]
    # the coordinator never reverts, resets or force-pushes: dev is still exactly GitHub's (wrong) merge commit
    now = refs(github_origin)
    assert now["refs/heads/dev"] == result["commit_sha"] and now[f"refs/heads/{BRANCH}"] == job.source["sha"]
    assert fake.writes() == [("POST", f"repos/{REPOSITORY}/pulls"), ("PUT", f"repos/{REPOSITORY}/pulls/101/merge")]
    assert checkout_state(rrig.repo) == checkout
    assert "COMPLETED" not in states(rrig, job.job_id)
    # a failed job is terminal: repeating the command changes nothing and writes nothing
    stored_bytes, calls = raw_record(rrig, job.job_id), list(fake.calls)
    again = merge(rrig, job.job_id)
    assert again.outcome == "terminal" and raw_record(rrig, job.job_id) == stored_bytes and fake.calls == calls


def test_the_source_being_reachable_from_dev_is_not_enough(rrig: Rig, launchers: Any, github_origin: Path):
    """A merge commit whose parents are swapped has BOTH approved SHAs reachable from dev and the approved tree — the
    parents check alone refuses it (design §5.10)."""
    job, fake = ready_job(rrig, launchers, github_origin)
    custom_merge(fake, lambda tip, source: (source, tip))
    report = merge(rrig, job.job_id)
    assert report.outcome == "failed"
    result = rrig.jobs.load(job.job_id).merge_result
    dev = refs(github_origin)["refs/heads/dev"]
    assert subprocess.run(["git", f"--git-dir={github_origin}", "merge-base", "--is-ancestor", job.source["sha"], dev],
                          check=False).returncode == 0
    assert result["tree_sha"] == rrig.jobs.load(job.job_id).candidate["tree_sha"]       # the tree alone is right
    checks = {c["name"]: c["status"] for c in result["verification"]["checks"]}
    assert checks["parents"] == "fail" and checks["tree"] == "pass" and checks["dev-contains"] == "pass"
    # the actual parents are recorded
    assert result["parents"] == [job.source["sha"], job.base["expected_sha"]]


@pytest.mark.parametrize("which", ["first", "second"])
def test_a_wrong_first_or_second_parent_alone_fails_the_job(rrig: Rig, launchers: Any, github_origin: Path, which: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    older = bare(github_origin, "rev-list", "--max-parents=0", "refs/heads/dev").split()[0]
    custom_merge(fake, (lambda tip, source: (older, source)) if which == "first"
                 else (lambda tip, source: (tip, older)))
    report = merge(rrig, job.job_id)
    assert report.outcome == "failed"
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.FAILED and stored.merge_result["verified"] is False
    assert {c["name"]: c["status"] for c in stored.merge_result["verification"]["checks"]}["parents"] == "fail"


def _flip(fake: FakeMergeGitHub, **changes: Any) -> None:
    pr = fake.prs[0]
    for key_, value in changes.items():
        if key_ == "base":
            pr["base"]["ref"] = value
        elif key_ == "head_sha":
            pr["head"]["sha"] = value
        else:
            pr[key_] = value


@pytest.mark.parametrize("change, needle", [
    ({"base": "other"}, "targets"),
    ({"head_sha": "c" * 40}, "was merged from"),
    ({"merge_commit_sha": "d" * 40}, "GitHub reports the merge commit"),
])
def test_a_pr_that_github_does_not_confirm_as_merged_from_the_approved_head_into_the_base_fails(
        rrig: Rig, launchers: Any, github_origin: Path, change: dict[str, Any], needle: str):
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    _flip(fake, **change)
    report = merge(rrig, job.job_id)
    assert report.outcome == "failed", (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.FAILED and needle in " ".join(stored.attention["reasons"])
    assert stored.merge_result["verified"] is False
    assert len(fake.merge_requests) == 1                          # the verification never merges again


def test_a_pr_that_github_does_not_report_as_merged_yet_is_a_lagging_read_not_a_failure(rrig: Rig, launchers: Any,
                                                                                     github_origin: Path):
    """Independent review L1: a read right after the merge may show the PR as it was before it."""
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    _flip(fake, merged=False, state="open", merged_at=None, merge_commit_sha=None)
    report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification", (report.outcome, report.reasons)
    assert "does not (yet) report PR #101 as merged" in " ".join(report.reasons)
    assert rrig.state_of(job.job_id) is JobState.MERGED


def test_a_merge_commit_that_dev_does_not_contain_fails_the_job(rrig: Rig, launchers: Any, github_origin: Path):
    job, _fake = merged_not_verified(rrig, launchers, github_origin)
    # dev was rewritten behind the merge
    bare(github_origin, "update-ref", "refs/heads/dev", job.base["expected_sha"])
    report = merge(rrig, job.job_id)
    assert report.outcome == "failed"
    stored = rrig.jobs.load(job.job_id)
    checks = {c["name"]: c["status"] for c in stored.merge_result["verification"]["checks"]}
    assert stored.state is JobState.FAILED and checks["dev-contains"] == "fail"
    # still not repaired by the coordinator
    assert refs(github_origin)["refs/heads/dev"] == job.base["expected_sha"]


def test_github_and_the_git_object_disagreeing_about_the_merge_commit_fails_the_job(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    sha = job.merge_result["commit_sha"]
    fake.commit_override[sha] = {"sha": sha, "tree": {"sha": "0" * 40},
                                 "parents": [{"sha": job.base["expected_sha"]}, {"sha": job.source["sha"]}]}
    report = merge(rrig, job.job_id)
    assert report.outcome == "failed"
    stored = rrig.jobs.load(job.job_id)
    checks = {c["name"]: c["status"] for c in stored.merge_result["verification"]["checks"]}
    assert stored.state is JobState.FAILED and checks["agreement"] == "fail" and checks["tree"] == "pass"


def test_unreadable_origin_leaves_the_job_merged_pending_verification(rrig: Rig, launchers: Any, github_origin: Path,
                                                                      monkeypatch: pytest.MonkeyPatch):
    job, fake = merged_not_verified(rrig, launchers, github_origin)

    def unreachable(*_args: Any, **_kwargs: Any) -> str:
        raise RemoteUnavailableError("git fetch failed: could not read from the remote repository")
    with monkeypatch.context() as patch:
        patch.setattr(integration_postmerge, "isolated_fetch", unreachable)
        report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification" and not report.ok
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.MERGED and stored.attention is None
    checks = {c["name"]: c["status"] for c in stored.merge_result["verification"]["checks"]}
    # never a pass, never a failure
    assert checks["commit"] == "unavailable" and checks["agreement"] == "unavailable"
    assert stored.merge_result["verified"] is False and "COMPLETED" not in states(rrig, job.job_id)
    # origin reachable again: the repeat completes
    assert merge(rrig, job.job_id).outcome == "completed"
    assert len(fake.merge_requests) == 1


@pytest.mark.parametrize("route, check", [("/pulls/", "pr-merged"), ("/branches/", "dev-contains"),
                                          ("/git/commits/", "commit-github")])
def test_a_github_server_error_during_verification_is_pending_never_completed_or_failed(
        rrig: Rig, launchers: Any, github_origin: Path, route: str, check: str):
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    fake.unreadable.add(route)
    report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification"
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.MERGED
    assert {c["name"]: c["status"] for c in stored.merge_result["verification"]["checks"]}[check] == "unavailable"
    fake.unreadable.clear()
    assert merge(rrig, job.job_id).outcome == "completed"


def test_a_missing_check_run_is_pending_then_success_completes(rrig: Rig, launchers: Any, github_origin: Path):
    with_post_checks(rrig, "ci")
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    sha = job.merge_result["commit_sha"]
    report = merge(rrig, job.job_id)                                     # GitHub reports no check of this name yet
    assert report.outcome == "pending-verification"
    assert {c["name"]: c["status"] for c in rrig.jobs.load(job.job_id).merge_result["verification"]["checks"]}[
        "check ci"] == "pending"
    fake.checks[sha] = [{"id": 1, "name": "ci", "status": "in_progress", "conclusion": None}]
    assert merge(rrig, job.job_id).outcome == "pending-verification"
    assert rrig.state_of(job.job_id) is JobState.MERGED
    fake.checks[sha] = [{"id": 1, "name": "ci", "status": "in_progress", "conclusion": None},
                        {"id": 2, "name": "ci", "status": "completed", "conclusion": "success"}]   # the newest decides
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed"
    stored = rrig.jobs.load(job.job_id)
    assert {c["name"]: c["status"] for c in stored.merge_result["verification"]["checks"]}["check ci"] == "pass"
    assert len(fake.merge_requests) == 1


def test_a_failed_post_merge_check_fails_the_job_and_a_status_context_counts(rrig: Rig, launchers: Any,
                                                                             github_origin: Path):
    with_post_checks(rrig, "ci", "lint")
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    sha = job.merge_result["commit_sha"]
    fake.checks[sha] = [{"id": 1, "name": "ci", "status": "completed", "conclusion": "success"}]
    fake.statuses[sha] = [{"id": 9, "context": "lint", "state": "failure"}]
    report = merge(rrig, job.job_id)
    assert report.outcome == "failed"
    stored = rrig.jobs.load(job.job_id)
    checks = {c["name"]: c["status"] for c in stored.merge_result["verification"]["checks"]}
    assert stored.state is JobState.FAILED and checks["check lint"] == "fail" and checks["check ci"] == "pass"


def test_unreadable_post_merge_checks_are_unavailable_not_a_pass(rrig: Rig, launchers: Any, github_origin: Path):
    with_post_checks(rrig, "ci")
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    fake.unreadable.add("/check-runs")
    report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification"
    checks = {c["name"]: c["status"] for c in rrig.jobs.load(job.job_id).merge_result["verification"]["checks"]}
    assert checks["check ci"] == "unavailable"
    fake.unreadable.clear()
    fake.checks[job.merge_result["commit_sha"]] = [{"id": 1, "name": "ci", "status": "completed",
                                                     "conclusion": "neutral"}]      # GitHub counts neutral as passing
    assert merge(rrig, job.job_id).outcome == "completed"


def test_no_configured_check_is_recorded_as_not_configured_distinct_from_pass_and_unavailable(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    assert merge(rrig, job.job_id).outcome == "completed"
    check = [c for c in rrig.jobs.load(job.job_id).merge_result["verification"]["checks"] if c["name"] == "checks"][0]
    assert check["status"] == "not-configured" and "no CI result is claimed" in check["detail"]
    # nothing was invented or read
    assert not [c for c in fake.calls if "check-runs" in c[1] or "/statuses" in c[1]]


def test_a_repeated_pending_verification_is_idempotent_and_writes_nothing_to_github(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    with_post_checks(rrig, "ci")
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    sha = job.merge_result["commit_sha"]
    fake.checks[sha] = [{"id": 1, "name": "ci", "status": "queued", "conclusion": None}]
    assert merge(rrig, job.job_id).outcome == "pending-verification"
    writes, record = list(fake.writes()), raw_record(rrig, job.job_id)
    for _ in range(3):
        report = merge(rrig, job.job_id)
        assert report.outcome == "pending-verification" and not report.changed
    assert raw_record(rrig, job.job_id) == record and fake.writes() == writes and len(fake.merge_requests) == 1
    fake.checks[sha] = [{"id": 1, "name": "ci", "status": "in_progress", "conclusion": None}]
    assert merge(rrig, job.job_id).outcome == "pending-verification"       # the same status: still unchanged
    fake.statuses[sha] = [{"id": 3, "context": "ci", "state": "pending"}]
    assert merge(rrig, job.job_id).outcome == "pending-verification" and fake.writes() == writes


def test_a_merged_job_repeats_only_the_verification(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    writes = list(fake.writes())
    report = merge(rrig, job.job_id, apply=False)                           # a dry run verifies, records nothing
    assert report.outcome == "would-verify" and rrig.state_of(job.job_id) is JobState.MERGED
    assert rrig.jobs.load(job.job_id).merge_result["verification"]["checks"] == []
    assert merge(rrig, job.job_id).outcome == "completed"
    assert fake.writes() == writes and len(fake.merge_requests) == 1 and len(fake.created) == 1


def test_a_tampered_fetched_object_is_never_completed(rrig: Rig, launchers: Any, github_origin: Path,
                                                      monkeypatch: pytest.MonkeyPatch):
    """The merge commit is read through the hash-verifying reader: an object whose content does not hash to its ID (a
    corrupted or substituted object in the private repository) makes the verification unavailable — never a pass."""
    job, _fake = merged_not_verified(rrig, launchers, github_origin)
    sha = job.merge_result["commit_sha"]
    real = integration_postmerge.isolated_fetch

    def tampering(repo: Any, url: str, branch: str, **kwargs: Any) -> str:
        tip = real(repo, url, branch, **kwargs)
        loose = repo.path / "objects" / sha[:2] / sha[2:]
        assert loose.exists(), "the fetched merge commit is expected as a loose object"
        loose.chmod(0o644)
        loose.write_bytes(zlib.compress(b"commit 5\x00hello"))
        return tip
    with monkeypatch.context() as patch:
        patch.setattr(integration_postmerge, "isolated_fetch", tampering)
        report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification" and rrig.state_of(job.job_id) is JobState.MERGED
    detail = [c for c in rrig.jobs.load(job.job_id).merge_result["verification"]["checks"] if c["name"] == "commit"][0]
    assert detail["status"] == "unavailable" and "does not hash" in detail["detail"]
    assert merge(rrig, job.job_id).outcome == "completed"


def test_isolated_fetch_writes_only_the_private_repository_and_refuses_unsafe_input(rrig: Rig, github_origin: Path,
                                                                                    seeded: Path):
    url = str(github_origin)
    tip = bare(github_origin, "rev-parse", "refs/heads/dev").strip()
    before = {"checkout": checkout_state(rrig.repo), "origin": tree_digest(github_origin)}
    with rrig.git.session():
        private = rrig.git.isolated()
        fetched = isolated_fetch(private, url, "dev", negotiation_tips=(tip,), timeout=30)
        assert fetched == tip
        assert subprocess.run(["git", f"--git-dir={private.path}", "cat-file", "-e", tip], check=False).returncode == 0
        assert bare(private.path, "rev-parse", FETCHED_REF).strip() == tip
        for bad_url in ("", "--upload-pack=touch /tmp/pwned", "https://example.invalid/x\nrepo"):
            with pytest.raises(RemoteUnavailableError):
                isolated_fetch(private, bad_url, "dev", negotiation_tips=(), timeout=30)
        for bad_branch in ("--force", "a b", "dev;rm", ""):
            with pytest.raises(RemoteUnavailableError):
                isolated_fetch(private, url, bad_branch, negotiation_tips=(), timeout=30)
        with pytest.raises(RemoteUnavailableError):
            isolated_fetch(private, url, "dev", negotiation_tips=("--bad",), timeout=30)
        with pytest.raises(RemoteUnavailableError):
            isolated_fetch(private, url, "no-such-branch", negotiation_tips=(), timeout=30)
        path = private.path
    assert not path.exists()                                                      # the private repository is removed
    assert {"checkout": checkout_state(rrig.repo), "origin": tree_digest(github_origin)} == before
    assert FETCHED_REF not in refs(github_origin) and "refs/integration" not in git(rrig.repo, "for-each-ref")


# -- crash recovery ------------------------------------------------------------------------------------------------

def test_a_crash_immediately_before_the_merge_request_never_merges_twice(rrig: Rig, launchers: Any,
                                                                         github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    crash(rrig, job.job_id, "before-merge-request")
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and not fake.merge_requests
    attempts = of_kind(rrig, job.job_id, "merge-attempt")
    assert [a["action"] for a in attempts] == ["create-pr", "merge"]                      # the intent is on disk
    assert not [e for e in stored.evidence if e["kind"] == "merge-outcome" and e["attempt"] == attempts[-1]["id"]]
    # ... but it was never DISPATCHED (the write boundary was not reached): nothing was sent, so nothing is in flight
    assert [e["attempt"] for e in of_kind(rrig, job.job_id, "merge-dispatch")] == [attempts[0]["id"]]
    assert abort_job(rrig.jobs, job.job_id, "operator gives up", actor="operator", apply=False)["outcome"] == \
        "would-abort"
    # the next run closes the intent as never dispatched and makes exactly ONE merge request
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.merge_requests) == 1
    final = rrig.jobs.load(job.job_id)
    outcomes = [(e["attempt"], e["status"], e["basis"]) for e in final.evidence if e["kind"] == "merge-outcome"]
    merges = [a["id"] for a in final.evidence if e_is(a, "merge")]
    assert outcomes == [(merges[0], "not-merged", "not-dispatched"), (merges[1], "merged", "response")]
    assert final.merge_result["attempt"] == merges[1] and final.state is JobState.COMPLETED


def test_a_crash_immediately_after_github_merged_is_reconciled_first_and_not_stale(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    crash(rrig, job.job_id, "after-merge-request")
    assert len(fake.merge_requests) == 1
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and stored.merge_result is None     # nothing recorded
    dev_after_merge = refs(github_origin)["refs/heads/dev"]
    # dev moved by OUR merge
    assert dev_after_merge != job.base["expected_sha"]
    checkout = checkout_state(rrig.repo)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    final = rrig.jobs.load(job.job_id)
    assert final.state is JobState.COMPLETED and "STALE" not in states(rrig, job.job_id)
    assert len(fake.merge_requests) == 1 and fake.writes().count(("PUT", f"repos/{REPOSITORY}/pulls/101/merge")) == 1
    assert final.merge_result["commit_sha"] == dev_after_merge and final.merge_result["verified"] is True
    assert states(rrig, job.job_id)[-2:] == ["MERGED", "COMPLETED"]
    assert [e["how"] for e in events(rrig, "integration-merged")] == ["reconciled"]
    # recovery never rewrites dev or the source branch, and never touches the checkout
    after = refs(github_origin)
    assert after["refs/heads/dev"] == dev_after_merge and after[f"refs/heads/{BRANCH}"] == job.source["sha"]
    assert checkout_state(rrig.repo) == checkout


def test_a_lost_merge_response_and_an_unreadable_pr_leave_the_attempt_open_until_reconciled(rrig: Rig,
                                                                                            launchers: Any,
                                                                                            github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.lose_merge_response = True
    # GitHub merges, the answer is lost, the PR is unreadable
    fake.on_merge = lambda: fake.unreadable.add("/pulls/101")
    report = merge(rrig, job.job_id)
    assert report.outcome == "merge-uncertain"
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and fake.prs[0]["merged"] is True    # it DID merge
    # the outcome is unknown
    assert [e["status"] for e in stored.evidence if e["kind"] == "merge-outcome"] == []
    assert report.job["merge_open"] is True
    with pytest.raises(IntegrationError, match="in flight"):
        abort_job(rrig.jobs, job.job_id, "x", actor="operator", apply=True)
    # GitHub unreadable at the start of the repeat too: nothing is repeated, nothing is claimed
    again = merge(rrig, job.job_id)
    assert again.outcome == "deferred" and len(fake.merge_requests) == 1
    fake.unreadable.clear()
    assert merge(rrig, job.job_id).outcome == "completed" and len(fake.merge_requests) == 1


def test_a_lost_merge_response_is_reconciled_in_the_same_run_when_the_pr_is_readable(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.lose_merge_response = True
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed" and len(fake.merge_requests) == 1
    assert rrig.jobs.load(job.job_id).merge_result["verified"] is True


def test_a_crash_after_the_merged_record_is_persisted_resumes_with_the_verification(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    job, fake = merged_not_verified(rrig, launchers, github_origin)
    assert job.merge_result["parents"] == [] and job.merge_result["tree_sha"] == ""              # not verified yet
    assert job.merge_result["verification"]["state"] == "pending"
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed" and len(fake.merge_requests) == 1
    assert rrig.jobs.load(job.job_id).merge_result["parents"] == [job.base["expected_sha"], job.source["sha"]]


def test_a_crash_before_the_completed_record_is_persisted_resumes_and_completes(rrig: Rig, launchers: Any,
                                                                               github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    crash(rrig, job.job_id, "before-completed")
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.MERGED and stored.merge_result["verified"] is False
    assert merge(rrig, job.job_id).outcome == "completed"
    assert rrig.state_of(job.job_id) is JobState.COMPLETED and len(fake.merge_requests) == 1


def test_a_repeated_merge_of_a_completed_job_is_a_no_op_without_any_request(rrig: Rig, launchers: Any,
                                                                           github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    assert merge(rrig, job.job_id).outcome == "completed"
    record, calls, snapshot_ = raw_record(rrig, job.job_id), list(fake.calls), refs(github_origin)
    for apply in (True, False, True):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome == "completed" and report.ok and not report.changed
        assert report.merge_result["verified"] is True
    assert raw_record(rrig, job.job_id) == record and fake.calls == calls and refs(github_origin) == snapshot_


def test_a_crash_after_the_pr_creation_intent_never_creates_a_second_pr(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    crash(rrig, job.job_id, "after-pr-intent")
    assert not fake.created and [a["action"] for a in of_kind(rrig, job.job_id, "merge-attempt")] == ["create-pr"]
    assert not of_kind(rrig, job.job_id, "merge-dispatch")             # the request never left the coordinator
    report = merge(rrig, job.job_id)                                  # nothing was sent: the intent is closed, ONE PR
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.created) == 1 and len(fake.merge_requests) == 1
    outcome = of_kind(rrig, job.job_id, "merge-outcome")[0]
    first_attempt = of_kind(rrig, job.job_id, "merge-attempt")[0]["id"]
    assert (outcome["status"], outcome["basis"], outcome["attempt"]) == ("not-created", "not-dispatched", first_attempt)


def test_a_crash_after_the_pr_was_created_finds_and_binds_it_never_creating_a_second(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    crash(rrig, job.job_id, "after-pr-created")
    assert len(fake.created) == 1 and not of_kind(rrig, job.job_id, "merge-pr")             # created, never recorded
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.created) == 1 and len(fake.prs) == 1 and len(fake.merge_requests) == 1
    bound = of_kind(rrig, job.job_id, "merge-pr")
    attempt = of_kind(rrig, job.job_id, "merge-attempt")[0]
    assert len(bound) == 1 and bound[0]["origin"] == "created" and bound[0]["attempt"] == attempt["id"]
    assert bound[0]["number"] == 101 and "w47-integration-job" in fake.prs[0]["body"]


def test_a_crash_before_any_intent_is_recorded_leaves_nothing_behind(rrig: Rig, launchers: Any, github_origin: Path,
                                                                    monkeypatch: pytest.MonkeyPatch):
    job, fake = ready_job(rrig, launchers, github_origin)
    original = IntegrationMerger._final_pins
    state = {"crashed": False}

    def dies_once(self: Any, *args: Any, **kwargs: Any) -> None:
        if not state["crashed"]:
            state["crashed"] = True
            raise SimulatedCrash("before the intent")
        original(self, *args, **kwargs)
    monkeypatch.setattr(IntegrationMerger, "_final_pins", dies_once)
    record = raw_record(rrig, job.job_id)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id)
    # nothing recorded, nothing sent
    assert raw_record(rrig, job.job_id) == record and not fake.writes()
    assert merge(rrig, job.job_id).outcome == "completed" and len(fake.created) == 1


def test_a_crash_while_recording_the_merge_approval_records_nothing_and_a_recorded_one_resumes(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin, sign=False)
    path = rrig.write_record(merge_record_for(job))
    real_save = rrig.jobs.save

    def dying(_job: Any) -> None:
        rrig.jobs.save = real_save                               # type: ignore[method-assign]
        raise SimulatedCrash("while recording the approval")
    rrig.jobs.save = dying                                       # type: ignore[method-assign]
    with pytest.raises(SimulatedCrash):
        approve_merge(rrig, job.job_id, path=path)
    stored = rrig.jobs.load(job.job_id)
    assert [a["stage"] for a in stored.approvals] == ["inspect"]                        # atomic: nothing recorded
    report = merge(rrig, job.job_id)
    assert report.outcome == "no-approval" and not fake.writes()
    assert approve_merge(rrig, job.job_id, path=path)["outcome"] == "recorded"          # the same record, later
    # recorded, then a crash-free merge
    assert merge(rrig, job.job_id).outcome == "completed"


def test_dev_advanced_by_another_job_after_our_merge_is_still_verified_and_not_stale(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    crash(rrig, job.job_id, "after-merge-request")
    merge_commit = refs(github_origin)["refs/heads/dev"]
    # another integration job's merge
    other = advance_dev(github_origin)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    final = rrig.jobs.load(job.job_id)
    assert final.merge_result["commit_sha"] == merge_commit != other and "STALE" not in states(rrig, job.job_id)
    assert final.merge_result["parents"] == [job.base["expected_sha"], job.source["sha"]]
    assert refs(github_origin)["refs/heads/dev"] == other and len(fake.merge_requests) == 1


def test_a_pr_merged_outside_the_job_is_stale_and_never_completed(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    foreign = pr_json(7, head_sha=job.source["sha"])
    fake.prs.append(foreign)
    fake.do_merge(foreign)                                                              # somebody merged it by hand
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale", (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and not stored.merge_result and not fake.writes()
    assert "COMPLETED" not in states(rrig, job.job_id) and not of_kind(rrig, job.job_id, "merge-attempt")


# -- races and concurrency -----------------------------------------------------------------------------------------

def test_two_merge_commands_for_one_job_send_one_merge(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    entered, proceed = threading.Event(), threading.Event()
    fake.on_merge = lambda: (entered.set(), proceed.wait(60))
    outcome: dict[str, Any] = {}

    def first() -> None:
        try:
            outcome["report"] = merge(rrig, job.job_id)
        except BaseException as exc:  # pylint: disable=broad-except
            outcome["error"] = exc
    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(60), "the first merge command never reached the merge request"
    with pytest.raises(IntegrationError, match="another integration operation"):
        # the job lock is held: refused, no request
        merge(rrig, job.job_id)
    with pytest.raises(IntegrationError, match="in flight"):
        abort_job(rrig.jobs, job.job_id, "racing abort", actor="operator", apply=True)
    proceed.set()
    thread.join(60)
    assert "error" not in outcome and outcome["report"].outcome == "completed"
    assert len(fake.merge_requests) == 1 and len(fake.created) == 1
    assert rrig.state_of(job.job_id) is JobState.COMPLETED


def _after_confirm(monkeypatch: pytest.MonkeyPatch, action: Callable[[], None], nth: int = 1) -> None:
    """Run *action* right after the *nth* `_merge_confirmed` (the approval verified, the decision not yet recorded)."""
    original, count = IntegrationMerger._merge_confirmed, {"n": 0}

    def confirmed(self: Any, *args: Any, **kwargs: Any) -> str:
        digest = original(self, *args, **kwargs)
        count["n"] += 1
        if count["n"] == nth:
            action()
        return digest
    monkeypatch.setattr(IntegrationMerger, "_merge_confirmed", confirmed)


def test_an_operator_abort_before_the_intent_wins_and_nothing_is_written_to_github(rrig: Rig, launchers: Any,
                                                                                  github_origin: Path,
                                                                                  monkeypatch: pytest.MonkeyPatch):
    job, fake = ready_job(rrig, launchers, github_origin)
    _after_confirm(monkeypatch, lambda: abort_job(rrig.jobs, job.job_id, "operator aborts", actor="operator",
                                                  apply=True))
    report = merge(rrig, job.job_id)
    assert report.outcome == "changed-meanwhile" and rrig.state_of(job.job_id) is JobState.ABORTED
    assert not fake.writes() and not of_kind(rrig, job.job_id, "merge-attempt")


def test_an_approval_revoked_while_the_merge_is_processed_writes_nothing(rrig: Rig, launchers: Any,
                                                                        github_origin: Path, key: OperatorKey,
                                                                        monkeypatch: pytest.MonkeyPatch):
    job, fake = ready_job(rrig, launchers, github_origin)
    _after_confirm(monkeypatch, key.revoke_all)
    report = merge(rrig, job.job_id)
    assert report.outcome == "no-approval" and not fake.writes()
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL
    assert not of_kind(rrig, job.job_id, "merge-attempt")


def test_an_approval_that_expires_while_the_merge_is_processed_writes_nothing(rrig: Rig, launchers: Any,
                                                                             github_origin: Path,
                                                                             monkeypatch: pytest.MonkeyPatch):
    job, fake = ready_job(rrig, launchers, github_origin)
    jumped = {"late": False}
    _after_confirm(monkeypatch, lambda: jumped.update(late=True))
    def clock() -> datetime:
        return datetime.now(timezone.utc) + (timedelta(hours=3) if jumped["late"] else timedelta(0))
    report = merge(rrig, job.job_id, clock=clock)
    assert report.outcome == "no-approval" and not fake.writes() and not of_kind(rrig, job.job_id, "merge-attempt")
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


@pytest.mark.parametrize("existing_pr, nth", [(False, 1), (True, 2)])
def test_another_job_moving_dev_during_the_run_makes_the_job_stale_before_any_github_write(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, existing_pr: bool, nth: int):
    job, fake = ready_job(rrig, launchers, github_origin)
    if existing_pr:
        fake.prs.append(pr_json(7, head_sha=job.source["sha"]))
    _after_confirm(monkeypatch, lambda: advance_dev(github_origin), nth=nth)
    report = merge(rrig, job.job_id)
    assert report.outcome == "stale", (report.outcome, report.reasons)
    assert rrig.state_of(job.job_id) is JobState.STALE and not fake.writes()
    assert not of_kind(rrig, job.job_id, "merge-attempt")
