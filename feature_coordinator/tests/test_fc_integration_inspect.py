"""W4.7 (M2): the read-only inspection — `integration inspect --job J` (integration_inspect.py, integration_paths.py,
integration_github.py) on disposable repositories.

Each test: a temporary checkout with a bare `origin`, a temporary state directory, a throw-away operator SSH key, and
either the in-memory mock tracker (complete PR listing, no GitHub attribution) or a scripted GitHub transport behind
the real GitHubClient (pagination, attribution, failures). No network, no real GitHub."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from feature_coordinator import integration_paths
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.errors import GitHubUnavailableError, StateDirError
from feature_coordinator.github_api import ApiResponse
from feature_coordinator.integration import IntegrationError, IntegrationJob, IntegrationJobs, JobState, abort_job, \
    current_inspection_passed, effective_approval, parse_record_time, transition_job
from feature_coordinator.integration_approval import SshSignatureVerifier
from feature_coordinator.integration_inspect import IntegrationInspector
from feature_coordinator.integration_paths import matching_prefix, path_class
from feature_coordinator.paths import PathPolicy
from feature_coordinator.issues import MockIssueTracker
from feature_coordinator.locks import FileLock
from feature_coordinator.store import StateStore

from fc_helpers import git, run_py
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, KNOWN, OPERATOR, REPOSITORY, FakeGitHub, OperatorKey, Rig, change_on, commit_node,
    github_origin, head, integration_config, live_client, origin, pr_json, record_for, request_for, snapshot,
    stamp,
)

READ_ONLY_GIT = {"config", "remote", "ls-remote", "cat-file", "merge-base", "rev-list", "rev-parse"}


@pytest.fixture
def key(tmp_path: Path) -> OperatorKey:
    return OperatorKey(tmp_path / "operator-home")


@pytest.fixture
def seeded(repo: Path, github_origin: Path) -> Path:
    """dev with a protected file, an unowned file, an owned file and a script, pushed."""
    change_on(repo, "dev", {"content/words.json": "{}\n", "feature_coordinator/tool.py": "T = 1\n",
                            "backend/src/old.py": "OLD = 1\n", "backend/src/run.sh": "echo hi\n"}, message="base")
    return repo


@pytest.fixture
def tracker() -> MockIssueTracker:
    return MockIssueTracker(repository=REPOSITORY)


@pytest.fixture
def rig(seeded: Path, state_dir: Path, key: OperatorKey, tracker: MockIssueTracker) -> Rig:
    return Rig(seeded, state_dir, key, client=tracker)


def job_with(rig: Rig, edits: dict[str, str | None], **approval: Any) -> IntegrationJob:
    """An accepted job for one commit with *edits* on BRANCH, approved with *approval* overrides."""
    sha = change_on(rig.repo, BRANCH, edits)
    return rig.approved(request_for(rig.repo, sha), **approval)


def history(rig: Rig, job_id: str) -> list[str]:
    return [entry["to"] for entry in rig.jobs.load(job_id).history]


def paths_of(rig: Rig, job_id: str) -> dict[str, dict[str, Any]]:
    return {entry["path"]: entry for entry in rig.jobs.load(job_id).paths}


# -- the approval gate -----------------------------------------------------------------------------------------

def test_inspection_requires_a_valid_inspect_approval(rig: Rig):
    job = rig.accepted(request_for(rig.repo, change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})))
    before = rig.jobs.load(job.job_id).to_dict()
    for apply in (False, True):
        report = rig.inspect(job.job_id, apply=apply)
        assert report.outcome == "no-approval" and not report.ok and "no inspect approval" in report.reasons[0]
    assert rig.jobs.load(job.job_id).to_dict() == before
    assert rig.store.events()[-1]["kind"] == "integration-inspection-refused"


def test_an_expired_approval_authorizes_nothing_and_a_new_one_does(rig: Rig):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    def later() -> datetime:
        return datetime.now(timezone.utc) + timedelta(hours=3)

    report = rig.inspect(job.job_id, clock=later)
    assert report.outcome == "no-approval" and "expired" in report.reasons[0]
    assert rig.state_of(job.job_id) is JobState.AWAITING_APPROVAL  # an expired approval is invalid, not EXPIRED
    fresh = {"expires_at": stamp(datetime.now(timezone.utc) + timedelta(hours=6)), "note": "renewed"}
    assert rig.approve(job.job_id, **fresh)["outcome"] == "recorded"
    assert rig.inspect(job.job_id, clock=later).outcome == "passed"


def test_a_revoked_key_or_a_removed_operator_authorizes_nothing(rig: Rig, key: OperatorKey, seeded: Path,
                                                                state_dir: Path, tracker: MockIssueTracker):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    key.revoke_all()
    report = rig.inspect(job.job_id)
    assert report.outcome == "no-approval" and "does not verify" in report.reasons[0]
    key.new_key(OPERATOR)
    other = Rig(seeded, state_dir, key, client=tracker, config=integration_config(key.allowed, operators=("someone",)))
    report = other.inspect(job.job_id)
    assert report.outcome == "no-approval" and "not in github.integration.operators" in report.reasons[0]
    assert rig.state_of(job.job_id) is JobState.AWAITING_APPROVAL


def test_an_approval_is_never_inferred_from_github_comments_or_prs(rig: Rig, tracker: MockIssueTracker):
    issue = tracker.create_issue("integrate it", "please", ["feature:test"])
    for text in ("/approve inspect", "Approved. LGTM", "integration approve --job int --stage inspect"):
        tracker.create_comment(issue.number, text)
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=sha, title="approved", body="approved by operator")
    job = rig.accepted(request_for(rig.repo, sha))
    tracker.calls.clear()
    report = rig.inspect(job.job_id)
    assert report.outcome == "no-approval" and rig.state_of(job.job_id) is JobState.AWAITING_APPROVAL
    assert tracker.calls == []  # not even read: nothing on GitHub can stand in for the signed record


# -- a passing inspection ----------------------------------------------------------------------------------------

def test_an_owned_change_passes_and_records_paths_and_bound_evidence(rig: Rig, tracker: MockIssueTracker):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n", "backend/src/old.py": "OLD = 2\n"})
    preview = rig.inspect(job.job_id, apply=False)
    assert preview.outcome == "would-pass" and preview.ok
    assert rig.state_of(job.job_id) is JobState.AWAITING_APPROVAL and rig.jobs.load(job.job_id).evidence == []
    writes = tracker.writes
    report = rig.inspect(job.job_id)
    assert report.outcome == "passed" and report.ok and report.changed and report.approval == "apr-0001"
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.INSPECTING
    assert history(rig, job.job_id) == ["INTAKE", "AWAITING_APPROVAL", "INSPECTING"]
    assert paths_of(rig, job.job_id) == {
        "backend/src/old.py": {"path": "backend/src/old.py", "class": "owned", "role": "backend",
                               "changes": ["modified"], "flags": [], "authorization": "not-needed"},
        "backend/src/x.py": {"path": "backend/src/x.py", "class": "owned", "role": "backend", "changes": ["added"],
                             "flags": [], "authorization": "not-needed"}}
    [evidence] = stored.evidence
    assert evidence["id"] == "ins-0001" and evidence["result"] == "passed" and evidence["approval"] == "apr-0001"
    assert evidence["source_sha"] == job.source["sha"] and evidence["base_sha"] == job.base["expected_sha"]
    assert evidence["pr_match"] == {"result": "none", "number": None, "candidates": []}
    assert evidence["provenance"] == {"source": "unavailable", "verified": 0, "unverified": 0, "unverifiable": 1,
                                      "accepted": True}
    assert stored.attention is None and stored.deferred is None
    assert tracker.writes == writes  # read-only towards the tracker
    kinds = [e["kind"] for e in rig.store.events()]
    assert kinds[-2:] == ["integration-transition", "integration-inspection"]
    again = rig.inspect(job.job_id)
    assert again.outcome == "passed" and not again.changed
    assert rig.jobs.load(job.job_id).revision == stored.revision  # identical result: no write, no event


# -- explicit authorization ----------------------------------------------------------------------------------------

def test_unowned_and_approval_required_paths_need_a_per_path_authorization(rig: Rig):
    job = job_with(rig, {"feature_coordinator/new.py": "N = 1\n", "README.md": "# readme\n",
                         "backend/src/x.py": "X = 1\n"})
    report = rig.inspect(job.job_id)
    assert report.outcome == "needs-authorization" and not report.ok
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.INSPECTING and stored.evidence[-1]["result"] == "needs-authorization"
    assert stored.attention["observed"]["paths_requiring_authorization"] == ["README.md", "feature_coordinator/new.py"]
    assert {p: e["authorization"] for p, e in paths_of(rig, job.job_id).items()} == {
        "README.md": "required", "backend/src/x.py": "not-needed", "feature_coordinator/new.py": "required"}
    assert paths_of(rig, job.job_id)["README.md"]["class"] == "approval-required"
    assert paths_of(rig, job.job_id)["feature_coordinator/new.py"]["class"] == "unowned"
    assert rig.approve(job.job_id, paths=["README.md", "feature_coordinator/new.py", "docs/never-changed.md"]
                       )["outcome"] == "recorded"
    report = rig.inspect(job.job_id)
    assert report.outcome == "passed" and any("docs/never-changed.md" in m for m in report.messages)
    stored = rig.jobs.load(job.job_id)
    assert stored.attention is None and [e["result"] for e in stored.evidence] == ["needs-authorization", "passed"]
    assert stored.evidence[-1]["approval"] == "apr-0002" and stored.evidence[-1]["paths"]["authorized"] == 2


def test_unverified_provenance_needs_an_explicit_acceptance_that_stays_distinct(rig: Rig):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"}, accept_unverified_provenance=False)
    report = rig.inspect(job.job_id)
    assert report.outcome == "needs-authorization" and "accept_unverified_provenance" in report.reasons[0]
    assert rig.approve(job.job_id, accept_unverified_provenance=True)["outcome"] == "recorded"
    assert rig.inspect(job.job_id).outcome == "passed"
    provenance = rig.jobs.load(job.job_id).evidence[-1]["provenance"]
    assert provenance["accepted"] is True and provenance["verified"] == 0  # accepted, never "verified"


# -- refused: protected paths and special entries -------------------------------------------------------------

@pytest.mark.parametrize("path", ["content/new.json", ".env", "backend/.env.local", "backend/data/x.db",
                                  "data/seed.csv", "test_environment/mxissues/x.py"])
def test_a_protected_path_refuses_the_job(rig: Rig, path: str):
    job = job_with(rig, {path: "secret\n", "backend/src/x.py": "X = 1\n"}, paths=["backend/src/x.py"])
    report = rig.inspect(job.job_id)
    assert report.outcome == "refused"
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.REFUSED and stored.terminal and "is protected" in stored.attention["reason"]
    assert history(rig, job.job_id)[-2:] == ["INSPECTING", "REFUSED"]
    assert stored.evidence[-1]["result"] == "refused" and paths_of(rig, job.job_id)[path]["authorization"] == "refused"


def test_a_protected_path_touched_only_in_the_history_is_refused(rig: Rig):
    change_on(rig.repo, BRANCH, {"content/leak.json": "{\"key\": 1}\n"}, message="oops")
    change_on(rig.repo, BRANCH, {"content/leak.json": None}, message="remove it again")
    tip = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    job = rig.approved(request_for(rig.repo, tip))
    assert rig.inspect(job.job_id).outcome == "refused"
    entry = paths_of(rig, job.job_id)["content/leak.json"]
    assert entry["class"] == "protected" and "history-only" in entry["flags"]
    assert entry["changes"] == ["added", "deleted"]


def test_renames_deletions_and_mode_changes_are_classified(rig: Rig):
    def chmod(repo: Path) -> None:
        (repo / "backend/src/run.sh").chmod(0o755)

    sha = change_on(rig.repo, BRANCH, {"backend/src/new_name.py": "OLD = 1\n", "backend/src/old.py": None,
                                       "feature_coordinator/tool.py": None}, prepare=chmod)
    job = rig.approved(request_for(rig.repo, sha), paths=["feature_coordinator/tool.py"])
    assert rig.inspect(job.job_id).outcome == "passed"
    paths = paths_of(rig, job.job_id)
    assert paths["backend/src/old.py"]["changes"] == ["deleted"] and "renamed" in paths["backend/src/old.py"]["flags"]
    assert paths["backend/src/new_name.py"]["changes"] == ["added"]
    assert "renamed" in paths["backend/src/new_name.py"]["flags"]
    assert paths["feature_coordinator/tool.py"] == {"path": "feature_coordinator/tool.py", "class": "unowned",
                                                    "role": None, "changes": ["deleted"], "flags": [],
                                                    "authorization": "authorized"}
    assert paths["backend/src/run.sh"]["changes"] == ["mode"]
    assert paths["backend/src/run.sh"]["flags"] == ["executable"]
    assert paths["backend/src/run.sh"]["authorization"] == "not-needed"


def test_a_rename_out_of_a_protected_path_is_refused(rig: Rig):
    job = job_with(rig, {"content/words.json": None, "backend/src/words.json": "{}\n"},
                   paths=["backend/src/words.json"])
    assert rig.inspect(job.job_id).outcome == "refused"
    assert paths_of(rig, job.job_id)["content/words.json"]["flags"] == ["renamed"]


def symlink(name: str, target: str):
    def make(repo: Path) -> None:
        os.symlink(target, repo / name)
    return make


def test_a_symlink_inside_the_repository_needs_an_explicit_authorization(rig: Rig):
    sha = change_on(rig.repo, BRANCH, {"backend/src/real.py": "R = 1\n"},
                    prepare=symlink("backend/src/link.py", "real.py"))
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "needs-authorization"
    entry = paths_of(rig, job.job_id)["backend/src/link.py"]
    assert entry["flags"] == ["symlink"] and entry["class"] == "owned" and entry["authorization"] == "required"
    assert rig.approve(job.job_id, paths=["backend/src/link.py"])["outcome"] == "recorded"
    assert rig.inspect(job.job_id).outcome == "passed"


@pytest.mark.parametrize(("target", "match"), [
    ("../../.env", "protected path"), ("../../content", "protected path"), ("../../../etc/passwd", "outside"),
    ("/etc/passwd", "absolute"), ("../../.git/config", "Git's own directory"),
], ids=["env", "protected-dir", "outside", "absolute", "dot-git"])
def test_a_symlink_leaving_the_repository_or_into_a_protected_path_is_refused(rig: Rig, target: str, match: str):
    sha = change_on(rig.repo, BRANCH, {}, prepare=symlink("backend/src/link", target))
    job = rig.approved(request_for(rig.repo, sha), paths=["backend/src/link"])  # authorizing it does not help
    assert rig.inspect(job.job_id).outcome == "refused"
    assert match in rig.jobs.load(job.job_id).attention["reason"]


def test_a_submodule_gitlink_is_refused(rig: Rig):
    repo = rig.repo
    git(repo, "checkout", "-q", "-b", BRANCH)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head(repo)},backend/src/vendored")
    git(repo, "commit", "-q", "-m", "submodule")
    sha = head(repo, "HEAD")
    git(repo, "checkout", "-q", "dev")
    git(repo, "push", "-q", "origin", BRANCH)
    job = rig.approved(request_for(repo, sha), paths=["backend/src/vendored"])
    assert rig.inspect(job.job_id).outcome == "refused"
    assert "gitlink" in rig.jobs.load(job.job_id).attention["reason"]


def test_git_control_files_need_an_explicit_authorization(rig: Rig):
    job = job_with(rig, {"backend/.gitattributes": "*.py filter=evil\n"})
    assert rig.inspect(job.job_id).outcome == "needs-authorization"
    assert paths_of(rig, job.job_id)["backend/.gitattributes"]["flags"] == ["git-control"]


def crafted_commit(repo: Path, entries: list[tuple[bytes, bytes, str]]) -> str:
    """A commit whose root tree is dev's tree plus raw *entries* (mode, name, sha), written byte for byte."""
    raw = subprocess.run(["git", "-C", str(repo), "cat-file", "tree", "dev^{tree}"], capture_output=True,
                         check=True).stdout
    items, position = [], 0
    while position < len(raw):
        space, nul = raw.index(b" ", position), raw.index(b"\0", position)
        items.append((raw[position:space], raw[space + 1:nul], raw[nul + 1:nul + 21].hex()))
        position = nul + 21
    items += entries
    items.sort(key=lambda e: e[1] + (b"/" if e[0] == b"40000" else b""))
    body = b"".join(mode + b" " + name + b"\0" + bytes.fromhex(sha) for mode, name, sha in items)
    tree = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "tree", "-w", "--literally", "--stdin"],
                          input=body, capture_output=True, check=True).stdout.decode().strip()
    sha = subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-p", head(repo), "-m", "crafted"],
                         capture_output=True, check=True, text=True).stdout.strip()
    git(repo, "push", "-q", "-f", "origin", f"{sha}:refs/heads/{BRANCH}")
    return sha


@pytest.mark.parametrize("name", [b".GIT", b".git ", b"..", b"git~1", b"a\\b", b"tab\tname", b"\xff\xfe"],
                         ids=["dotgit-upper", "dotgit-space", "dotdot", "ntfs-short", "backslash", "control",
                              "not-utf8"])
def test_crafted_tree_entry_names_are_refused(rig: Rig, name: bytes):
    blob = subprocess.run(["git", "-C", str(rig.repo), "hash-object", "-w", "--stdin"], input=b"x\n",
                          capture_output=True, check=True).stdout.decode().strip()
    sha = crafted_commit(rig.repo, [(b"100644", name, blob)])
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "refused"
    assert "malformed name" in rig.jobs.load(job.job_id).attention["reason"]


def test_a_tree_with_a_duplicated_entry_is_refused(rig: Rig):
    blob = subprocess.run(["git", "-C", str(rig.repo), "hash-object", "-w", "--stdin"], input=b"x\n",
                          capture_output=True, check=True).stdout.decode().strip()
    sha = crafted_commit(rig.repo, [(b"100644", b"zz.py", blob), (b"100644", b"zz.py", blob)])
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "refused"
    assert "duplicated or out of order" in rig.jobs.load(job.job_id).attention["reason"]


def test_more_changed_paths_than_the_limit_refuse_rather_than_truncate(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(integration_paths, "MAX_CHANGED_PATHS", 2)
    job = job_with(rig, {f"backend/src/f{n}.py": f"N = {n}\n" for n in range(3)})
    assert rig.inspect(job.job_id).outcome == "refused"
    assert "more than 2 paths" in rig.jobs.load(job.job_id).attention["reason"]


# -- stale: a pinned value changed ---------------------------------------------------------------------------------

def test_a_moved_source_branch_makes_the_job_stale(rig: Rig):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    moved = change_on(rig.repo, BRANCH, {"backend/src/y.py": "Y = 1\n"})
    report = rig.inspect(job.job_id)
    assert report.outcome == "stale"
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and history(rig, job.job_id)[-2:] == ["INSPECTING", "STALE"]
    assert stored.attention["observed"]["source_ref_sha"] == moved
    assert "moved from the pinned" in stored.attention["reason"]
    assert stored.evidence[-1]["result"] == "stale"


def test_a_moved_base_makes_the_job_stale_even_when_it_would_merge_cleanly(rig: Rig):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    change_on(rig.repo, "dev", {"cli/src/unrelated.py": "U = 1\n"})
    assert rig.inspect(job.job_id).outcome == "stale"
    assert "base branch 'dev' moved" in rig.jobs.load(job.job_id).attention["reason"]


def test_a_deleted_source_branch_makes_the_job_stale(rig: Rig, github_origin: Path):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    git(rig.repo, "push", "-q", "origin", f":refs/heads/{BRANCH}")
    assert rig.inspect(job.job_id).outcome == "stale"
    assert "no longer exists" in rig.jobs.load(job.job_id).attention["reason"]


def test_staleness_wins_over_a_refusal_for_the_same_inspection(rig: Rig):
    job = job_with(rig, {"content/new.json": "{}\n"})
    change_on(rig.repo, BRANCH, {"backend/src/y.py": "Y = 1\n"})
    assert rig.inspect(job.job_id).outcome == "stale"


@pytest.mark.parametrize(("change", "match"), [
    ({"head_sha": "f" * 40}, "head moved"), ({"base": "main"}, "now targets"),
], ids=["head-moved", "retargeted"])
def test_a_change_to_the_pr_recorded_at_intake_makes_the_job_stale(rig: Rig, tracker: MockIssueTracker,
                                                                   change: dict[str, str], match: str):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=sha)
    job = rig.approved(request_for(rig.repo, sha, pr_number=1))
    assert job.pr["head_sha"] == sha
    tracker.update_pull_request(1, **change)
    assert rig.inspect(job.job_id).outcome == "stale"
    stored = rig.jobs.load(job.job_id)
    assert match in stored.attention["reason"] and stored.evidence[-1]["pr_match"]["result"] == "stale"


# -- the PR lookup and the reuse rules ---------------------------------------------------------------------------

def test_an_open_matching_pr_found_by_the_lookup_is_reused_and_nothing_is_created(rig: Rig, tracker: MockIssueTracker):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=sha, draft=True)
    writes = tracker.writes
    job = rig.approved(request_for(rig.repo, sha))  # no --pr at intake: the lookup finds it
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.jobs.load(job.job_id).evidence[-1]["pr_match"] == {"result": "reuse", "number": 1, "candidates": [1]}
    assert tracker.writes == writes and not any(c.startswith("create_pull_request") for c in tracker.calls)


PR_CASES = {
    "closed-by-branch": [{"head_sha": "f" * 40, "state": "closed"}],
    "merged-by-sha": [{"head": "integration/older", "merged": True, "state": "closed"}],
    "open-by-branch-other-sha": [{"head_sha": "f" * 40}],
    "other-base": [{"base": "main"}],
    "other-repository-same-sha": [{"head_repo": "someone/fork", "head": "patch-1"}],
    "deleted-fork-same-sha": [{"head_repo": "", "head": "patch-1"}],
    "duplicate-open-and-closed": [{}, {"head": "integration/older", "state": "closed"}],
    "two-open": [{}, {"head": "integration/copy"}],
}


@pytest.mark.parametrize("case", sorted(PR_CASES))
def test_an_unsuitable_or_duplicated_pr_refuses_the_job_and_creates_no_second_pr(rig: Rig,
                                                                                tracker: MockIssueTracker, case: str):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    for fields in PR_CASES[case]:
        fields = {"head": BRANCH, "base": "dev", "head_sha": sha, **fields}
        tracker.add_pull_request_as(OPERATOR, fields.pop("head"), fields.pop("base"), head_sha=fields.pop("head_sha"),
                                    **fields)
    writes = tracker.writes
    job = rig.approved(request_for(rig.repo, sha))
    report = rig.inspect(job.job_id)
    assert report.outcome == "refused"
    stored = rig.jobs.load(job.job_id)
    numbers = [n for n in (1, 2) if n <= len(PR_CASES[case])]
    assert stored.evidence[-1]["pr_match"] == {"result": "refused", "number": None, "candidates": numbers}
    assert [c["number"] for c in stored.attention["observed"]["pr_candidates"]] == numbers
    assert all(f"#{n}" in " ".join(stored.attention["reasons"]) for n in numbers)
    assert tracker.writes == writes and len(tracker.prs) == len(numbers)  # never a second PR


def test_an_unrelated_pr_is_not_a_candidate(rig: Rig, tracker: MockIssueTracker):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    tracker.add_pull_request_as(OPERATOR, "feature/other", "dev", head_sha="e" * 40, state="closed")
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.jobs.load(job.job_id).evidence[-1]["pr_match"]["result"] == "none"


def full_listing(count: int) -> list[dict[str, Any]]:
    return [pr_json(n, head_ref=f"feature/x-{n}", head_sha=f"{n:040x}", state="closed") for n in range(1, count + 1)]


def live_rig(seeded: Path, state_dir: Path, key: OperatorKey, transport: FakeGitHub, **config: Any) -> Rig:
    return Rig(seeded, state_dir, key, client=live_client(transport), config=integration_config(key.allowed, **config))


def test_an_incomplete_github_listing_defers_and_a_complete_one_decides(seeded: Path, state_dir: Path,
                                                                        key: OperatorKey):
    transport = FakeGitHub(full_listing(100))
    rig = live_rig(seeded, state_dir, key, transport)
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    # page 1 is full and announces no next page, yet page 2 is not empty
    transport.pages = {2: ([pr_json(101, head_ref=BRANCH, head_sha="f" * 40)], "")}
    report = rig.inspect(job.job_id)
    assert report.outcome == "deferred" and "is not empty" in report.deferred
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.INSPECTING and "is not empty" in stored.deferred["reason"]
    assert not stored.evidence and transport.writes() == []
    transport.pages = {}
    report = rig.inspect(job.job_id)
    assert report.outcome == "passed" and rig.jobs.load(job.job_id).deferred is None
    pulls = [p for m, p in transport.calls if "/pulls?" in p and "head=" not in p and "/commits/" not in p]
    assert any("page=2" in p for p in pulls)  # a full first page was confirmed by reading the next one


@pytest.mark.parametrize("failure", ["pulls-502", "graphql-502", "commit-pulls-missing-from-listing"])
def test_github_failures_defer_and_change_nothing_but_the_deferral(seeded: Path, state_dir: Path, key: OperatorKey,
                                                                   failure: str):
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    if failure == "pulls-502":
        transport.fail.append(("GET repos/spyderbg/mx-admin-cloud/pulls?", ApiResponse(502, {}, {"message": "bad"})))
    elif failure == "graphql-502":
        transport.fail.append(("POST graphql", ApiResponse(502, {}, {"message": "bad"})))
    else:
        transport.extra_by_commit.append(pr_json(9, head_ref="ghost", head_sha="d" * 40))
    report = rig.inspect(job.job_id)
    assert report.outcome == "deferred" and rig.state_of(job.job_id) is JobState.INSPECTING
    assert not rig.jobs.load(job.job_id).evidence and transport.writes() == []
    if failure == "commit-pulls-missing-from-listing":
        assert "misses PR(s) [9]" in report.deferred


def test_github_attribution_decides_the_provenance_level(seeded: Path, state_dir: Path, key: OperatorKey):
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"}, accept_unverified_provenance=False)
    assert rig.inspect(job.job_id).outcome == "needs-authorization"   # attributed to a trusted login, unsigned
    assert rig.jobs.load(job.job_id).evidence[-1]["provenance"]["unverified"] == 1
    transport.attribution[job.source["sha"]] = commit_node(job.source["sha"], signature={
        "isValid": True, "state": "VALID", "wasSignedByGitHub": False, "signer": {"login": OPERATOR}})
    assert rig.inspect(job.job_id).outcome == "passed"                # verified: no acceptance needed
    assert rig.jobs.load(job.job_id).evidence[-1]["provenance"] == {
        "source": "github", "verified": 1, "unverified": 0, "unverifiable": 0, "accepted": False}


@pytest.mark.parametrize(("node", "outcome", "match"), [
    ({"author": "mallory"}, "refused", "to the account mallory"),
    ({"signature": {"isValid": True, "state": "VALID", "wasSignedByGitHub": False, "signer": {"login": "mallory"}}},
     "refused", "valid signature of mallory"),
    ({"signature": {"isValid": False, "state": "GPGVERIFY_UNAVAILABLE", "wasSignedByGitHub": False, "signer": None}},
     "deferred", "GPGVERIFY_UNAVAILABLE"),
    ({"unknown": True}, "deferred", "does not know commit"),
], ids=["untrusted-author-account", "untrusted-signer", "transient-verification", "unknown-to-github"])
def test_untrusted_or_unknown_github_attribution(seeded: Path, state_dir: Path, key: OperatorKey,
                                                 node: dict[str, Any], outcome: str, match: str):
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    sha = job.source["sha"]
    transport.attribution[sha] = None if node.get("unknown") else commit_node(
        sha, author=node.get("author", OPERATOR), signature=node.get("signature"))
    report = rig.inspect(job.job_id)
    assert report.outcome == outcome
    text = report.deferred if outcome == "deferred" else rig.jobs.load(job.job_id).attention["reason"]
    assert match in text


def test_an_author_no_longer_known_is_refused_at_inspection(rig: Rig, seeded: Path, state_dir: Path,
                                                            key: OperatorKey, tracker: MockIssueTracker):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    narrowed = Rig(seeded, state_dir, key, client=tracker,
                   config=integration_config(key.allowed, known_authors=("someone@example.invalid",)))
    assert narrowed.inspect(job.job_id).outcome == "refused"
    assert narrowed.jobs.load(job.job_id).attention["observed"]["unknown_authors"] == [KNOWN]


def test_inspection_refuses_to_run_without_trusted_logins(seeded: Path, state_dir: Path, key: OperatorKey,
                                                         tracker: MockIssueTracker):
    rig = Rig(seeded, state_dir, key, client=tracker, config=integration_config(key.allowed, trusted_logins=()))
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    with pytest.raises(IntegrationError, match="trusted_logins is empty"):
        rig.inspect(job.job_id)
    assert rig.state_of(job.job_id) is JobState.AWAITING_APPROVAL


# -- crash recovery, concurrency, corrupt or conflicting state ----------------------------------------------------

@pytest.mark.parametrize("fault", ["after-enter", "after-checks"])
def test_a_crash_during_inspection_is_resumed_by_the_same_command(rig: Rig, fault: str):
    from feature_coordinator.issue_sync import SimulatedCrash  # pylint: disable=import-outside-toplevel
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    with pytest.raises(SimulatedCrash):
        rig.inspect(job.job_id, faults=(fault,))
    assert rig.state_of(job.job_id) is JobState.INSPECTING and rig.jobs.load(job.job_id).evidence == []
    assert rig.inspect(job.job_id).outcome == "passed"
    assert history(rig, job.job_id) == ["INTAKE", "AWAITING_APPROVAL", "INSPECTING"]
    assert len(rig.jobs.load(job.job_id).evidence) == 1
    assert not list((rig.state / "tmp").glob("integration-git-*"))  # the private repository is gone


def test_a_second_inspection_of_the_same_job_is_refused_while_one_runs(rig: Rig):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    holder = FileLock(rig.store.locks.dir / f"integration-{job.job_id}.lock").acquire()
    try:
        with pytest.raises(IntegrationError, match="another inspection"):
            rig.inspect(job.job_id)
        assert rig.inspect(job.job_id, apply=False).outcome == "would-pass"  # a dry run takes no lock
    finally:
        holder.release()
    assert rig.state_of(job.job_id) is JobState.AWAITING_APPROVAL
    assert rig.inspect(job.job_id).outcome == "passed"


def test_an_abort_while_the_inspection_runs_wins(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    real = IntegrationInspector.check

    def check_then_abort(self: IntegrationInspector, current: IntegrationJob, approval: dict[str, Any]):
        result = real(self, current, approval)
        abort_job(self.jobs, current.job_id, "operator stop during inspection", actor="operator", apply=True)
        return result

    monkeypatch.setattr(IntegrationInspector, "check", check_then_abort)
    report = rig.inspect(job.job_id)
    assert report.outcome == "changed-meanwhile" and rig.state_of(job.job_id) is JobState.ABORTED
    assert not rig.jobs.load(job.job_id).evidence


def test_a_newer_approval_or_an_expiry_during_the_inspection_records_nothing(rig: Rig,
                                                                             monkeypatch: pytest.MonkeyPatch):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    real = IntegrationInspector.check

    def check_then_approve(self: IntegrationInspector, current: IntegrationJob, approval: dict[str, Any]):
        result = real(self, current, approval)
        assert rig.approve(current.job_id, paths=["README.md"])["outcome"] == "recorded"
        return result

    monkeypatch.setattr(IntegrationInspector, "check", check_then_approve)
    report = rig.inspect(job.job_id)
    assert report.outcome == "changed-meanwhile" and any("newer approval" in m for m in report.messages)
    assert rig.jobs.load(job.job_id).evidence == []
    monkeypatch.setattr(IntegrationInspector, "check", real)
    times = iter([datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=3)])
    report = rig.inspect(job.job_id, clock=lambda: next(times))
    assert report.outcome == "no-approval" and "expired" in report.reasons[0]
    assert rig.jobs.load(job.job_id).evidence == [] and rig.state_of(job.job_id) is JobState.INSPECTING


def test_a_job_inspecting_without_an_approval_is_conflicting_state(rig: Rig):
    job = rig.accepted(request_for(rig.repo, change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})))
    stored = rig.jobs.load(job.job_id)
    transition_job(stored, JobState.INSPECTING, "forged", actor="someone")
    with rig.store.transaction():
        rig.jobs.save(stored)
    with pytest.raises(IntegrationError, match="conflicting state"):
        rig.inspect(job.job_id)


@pytest.mark.parametrize(("mutate", "match"), [
    (lambda d: d["evidence"][0].update(result="passed", reasons=["edited"]), "SHA-256 does not match"),
    (lambda d: d["evidence"][0].update(approval="apr-0009"), "does not carry"),
    (lambda d: d["evidence"][0].update(source_sha="f" * 40), "other SHAs"),
    (lambda d: d["evidence"][0].update(kind="gate"), "kind must be"),
    (lambda d: d["paths"][0].update(**{"class": "owned-ish"}), "class must be"),
    (lambda d: d["paths"].append(dict(d["paths"][0])), "unique"),
    (lambda d: d.update(state="REFUSED"), "history ends at INSPECTING"),
], ids=["evidence-edited", "evidence-unknown-approval", "evidence-other-sha", "evidence-kind", "path-class",
        "path-duplicate", "state-without-history"])
def test_corrupt_or_conflicting_inspection_records_fail_closed(rig: Rig, mutate: Any, match: str):
    job = job_with(rig, {"README.md": "# x\n"})
    assert rig.inspect(job.job_id).outcome == "needs-authorization"
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match=match):
        rig.jobs.load(job.job_id)
    with pytest.raises(StateDirError):
        rig.inspect(job.job_id)


def test_a_passed_evidence_entry_cannot_hide_unauthorized_paths(rig: Rig):
    job = job_with(rig, {"README.md": "# x\n"})
    rig.inspect(job.job_id)
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    from feature_coordinator.integration import inspection_digest  # pylint: disable=import-outside-toplevel
    data["evidence"][0]["result"] = "passed"
    data["evidence"][0]["sha256"] = inspection_digest(data["evidence"][0])  # even with a recomputed digest
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match="cannot leave a path unauthorized"):
        rig.jobs.load(job.job_id)


# -- read-only: no Git write, no GitHub write --------------------------------------------------------------------

def test_inspection_never_writes_git_or_github(seeded: Path, state_dir: Path, key: OperatorKey, github_origin: Path,
                                               monkeypatch: pytest.MonkeyPatch):
    from test_fc_integration_intake import git_subcommand  # pylint: disable=import-outside-toplevel
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    job = job_with(rig, {"backend/src/x.py": "X = 1\n", "README.md": "# r\n"}, paths=["README.md"])
    before = snapshot(seeded, github_origin)
    calls: list[list[str]] = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def run(argv: Any, *args: Any, **kwargs: Any):
        if isinstance(argv, (list, tuple)) and argv and Path(str(argv[0])).name == "git":
            calls.append([str(a) for a in argv])
        return real_run(argv, *args, **kwargs)  # pylint: disable=subprocess-run-check

    class Popen(real_popen):  # type: ignore[misc,valid-type]  # pylint: disable=too-few-public-methods
        """Records every git argv."""

        def __init__(self, argv: Any, *args: Any, **kwargs: Any) -> None:
            if isinstance(argv, (list, tuple)) and argv and Path(str(argv[0])).name == "git":
                calls.append([str(a) for a in argv])
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    assert rig.inspect(job.job_id, apply=False).outcome == "would-pass"
    assert rig.inspect(job.job_id).outcome == "passed"
    monkeypatch.undo()
    assert {git_subcommand(argv) for argv in calls} <= READ_ONLY_GIT
    objects = [argv for argv in calls if git_subcommand(argv) in ("cat-file", "rev-list", "merge-base", "ls-remote")]
    assert objects and all(any(a.startswith("--git-dir=") for a in argv) for argv in objects)  # never the checkout
    assert snapshot(seeded, github_origin) == before
    assert transport.writes() == []


# -- the CLI and concurrent processes ---------------------------------------------------------------------------

def config_file(tmp_path: Path, key: OperatorKey) -> Path:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["integration"] = {"enabled": True, "known_authors": [KNOWN], "operators": [OPERATOR],
                                     "allowed_signers": str(key.allowed), "trusted_logins": [OPERATOR]}
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


DRIVER = """
    import sys
    from feature_coordinator import integration_intake
    origin = sys.argv[1]
    real = integration_intake.github_repository_of
    integration_intake.github_repository_of = (
        lambda url, host: "spyderbg/mx-admin-cloud" if url == origin else real(url, host))
    from feature_coordinator.cli import main
    sys.exit(main(sys.argv[2:]))
"""


def cli_job(rig: Rig, tmp_path: Path, key: OperatorKey, capsys: pytest.CaptureFixture[str]) -> tuple[list[str], str]:
    config = config_file(tmp_path, key)
    base = ["--tracker", "local", "--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config",
            str(config), "integration"]
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    assert cli_main([*base, "intake", "--source-repo", REPOSITORY, "--source-ref", BRANCH, "--source-sha", sha,
                     "--base", "dev", "--base-sha", head(rig.repo), "--issue", "63", "--apply", "--json"]) == 0
    job_id = json.loads(capsys.readouterr().out)["job"]["job_id"]
    record = rig.write_record(record_for(rig.jobs.load(job_id)))
    assert cli_main([*base, "approve", "--job", job_id, "--stage", "inspect", "--record", str(record), "--apply"]) == 0
    capsys.readouterr()
    return base, job_id


def test_cli_inspect_round_trip(rig: Rig, key: OperatorKey, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    base, job_id = cli_job(rig, tmp_path, key, capsys)
    assert cli_main([*base, "inspect", "--job", job_id]) == 0
    out = capsys.readouterr().out
    assert "inspect: would-pass" in out and "dry run" in out and "[pass     ] paths" in out
    assert cli_main([*base, "inspect", "--job", job_id, "--apply", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["outcome"] == "passed" and data["job"]["state"] == "INSPECTING"
    assert data["paths"][0]["path"] == "backend/src/x.py" and data["pr"]["result"] == "none"
    assert cli_main([*base, "status", "--job", job_id]) == 0
    assert "inspection ins-0001: passed under apr-0001" in capsys.readouterr().out
    assert cli_main([*base, "inspect", "--job", "int-20990101-0001", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["type"] == "IntegrationError"


def test_concurrent_inspections_from_several_processes_record_one_result(rig: Rig, key: OperatorKey, tmp_path: Path,
                                                                         github_origin: Path,
                                                                         capsys: pytest.CaptureFixture[str]):
    base, job_id = cli_job(rig, tmp_path, key, capsys)
    argv = [*base, "inspect", "--job", job_id, "--apply", "--json"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _n: run_py(DRIVER, str(github_origin), *argv), range(5)))
    answers = [json.loads(r.stdout) for r in results]
    assert any(a.get("outcome") == "passed" and a.get("changed") for a in answers), answers
    for result, answer in zip(results, answers):
        assert result.returncode == 0 or "another inspection" in answer["error"]["message"], answer
    stored = IntegrationJobs(StateStore(rig.state)).load(job_id)
    assert stored.state is JobState.INSPECTING and len(stored.evidence) == 1
    assert [h["to"] for h in stored.history].count("INSPECTING") == 1


def test_unavailable_github_through_the_mock_tracker_defers(rig: Rig, tracker: MockIssueTracker):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    tracker.fail.append(("list_pull_requests", GitHubUnavailableError("HTTP 502")))
    report = rig.inspect(job.job_id)
    assert report.outcome == "deferred" and "HTTP 502" in report.deferred
    assert rig.inspect(job.job_id).outcome == "passed"


# -- independent review of M2: regression tests --------------------------------------------------------------------

def merge_commit(repo: Path, first: str, second: str, take: dict[str, str]) -> str:
    """A merge commit with parents (*first*, *second*) whose tree is *first*'s tree with the paths in *take* taken
    from the commit named there — e.g. content an OLD base commit had and the base deleted since."""
    git(repo, "checkout", "-q", "--detach", first)
    try:
        for path, commit in take.items():
            git(repo, "checkout", commit, "--", path)
        tree = git(repo, "write-tree").strip()
    finally:
        git(repo, "reset", "-q", "--hard")
        git(repo, "checkout", "-q", "dev")
    return subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-p", first, "-p", second, "-m", "merge"],
                          capture_output=True, check=True, text=True).stdout.strip()


@pytest.mark.parametrize(("take", "outcome", "path", "status"), [
    ({".env": "B"}, "refused", ".env", "refused"),
    ({".github/workflows/ci.yml": "B"}, "needs-authorization", ".github/workflows/ci.yml", "required"),
], ids=["protected-file-the-base-deleted", "approval-required-file-reverted"])
def test_a_merge_taking_old_base_content_is_classified_by_its_net_effect(rig: Rig, take: dict[str, str], outcome: str,
                                                                        path: str, status: str):
    """Review finding 1: dev A → B (adds .env, old ci.yml) → C (deletes .env, hardens ci.yml); the source is F2 (child
    of C) merged with B keeping B's version. No commit of the range "changes" those paths by the per-commit rule, but
    the integration would bring them back into dev's tree."""
    repo = rig.repo
    old = change_on(repo, "dev", {".env": "SECRET=1\n", ".github/workflows/ci.yml": "on: push\n"}, message="B")
    change_on(repo, "dev", {".env": None, ".github/workflows/ci.yml": "on: [push]\npermissions: read-all\n"},
              message="C")
    feature = change_on(repo, BRANCH, {"backend/src/app2.py": "A = 1\n"})
    merged = merge_commit(repo, feature, old, {name: old for name in take})
    git(repo, "push", "-q", "-f", "origin", f"{merged}:refs/heads/{BRANCH}")
    job = rig.approved(request_for(repo, merged), paths=["backend/src/app2.py"])
    assert rig.inspect(job.job_id).outcome == outcome
    entry = paths_of(rig, job.job_id)[path]
    assert entry["authorization"] == status


def test_merging_newer_base_commits_into_the_source_is_not_mistaken_for_its_changes(rig: Rig):
    """The net diff is taken from the true merge base, not from the old fork point: a protected file the BASE changed
    after the fork, merged into the source, is not the source's change."""
    repo = rig.repo
    change_on(repo, BRANCH, {"backend/src/feature.py": "F = 1\n"})
    change_on(repo, "dev", {"content/words.json": "{\"new\": true}\n"}, message="dev changes protected content")
    git(repo, "checkout", "-q", BRANCH)
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "--no-edit", "dev"], check=True, capture_output=True)
    tip = head(repo, "HEAD")
    git(repo, "checkout", "-q", "dev")
    git(repo, "push", "-q", "origin", BRANCH)
    job = rig.approved(request_for(repo, tip))
    assert rig.inspect(job.job_id).outcome == "passed"
    assert set(paths_of(rig, job.job_id)) == {"backend/src/feature.py"}


@pytest.mark.parametrize(("link", "target", "match"), [
    ("frontend/linked", "../backend", "contains the protected path"),
    ("frontend/linked", "../cli", "points to a directory"),
    ("backend/src/chain", "link.py", "another symlink"),
    ("backend/src/dangling", "missing.py", "points to nothing"),
], ids=["dir-with-protected-subtree", "plain-directory", "chain", "dangling"])
def test_a_symlink_kept_in_the_tip_must_point_to_a_regular_file(rig: Rig, link: str, target: str, match: str):
    def links(repo: Path) -> None:
        os.symlink("real.py", repo / "backend/src/link.py")
        (repo / link).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, repo / link)

    sha = change_on(rig.repo, BRANCH, {"backend/src/real.py": "R = 1\n"}, prepare=links)
    job = rig.approved(request_for(rig.repo, sha), paths=["backend/src/link.py", link])
    assert rig.inspect(job.job_id).outcome == "refused"
    assert match in " ".join(rig.jobs.load(job.job_id).attention["reasons"])


def test_a_recorded_pr_that_cannot_be_found_defers_instead_of_going_stale(rig: Rig, tracker: MockIssueTracker):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=sha)
    job = rig.approved(request_for(rig.repo, sha, pr_number=1))
    hidden = tracker.prs.pop(1)  # e.g. the login lost access to the repository
    report = rig.inspect(job.job_id)
    assert report.outcome == "deferred" and "HTTP 404" in report.deferred
    tracker.prs[1] = hidden
    assert rig.inspect(job.job_id).outcome == "passed"


def test_an_inconsistent_object_store_defers_and_cannot_force_a_refusal(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    from feature_coordinator.integration_intake import ReadOnlyGit  # pylint: disable=import-outside-toplevel
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    monkeypatch.setattr(ReadOnlyGit, "is_ancestor", lambda self, a, b: True)
    report = rig.inspect(job.job_id)
    assert report.outcome == "deferred" and "inconsistent" in report.deferred
    monkeypatch.setattr(ReadOnlyGit, "object_type", lambda self, sha: "tree")
    assert rig.inspect(job.job_id).outcome == "deferred"
    monkeypatch.undo()


def test_the_current_inspection_predicate_for_m3(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    assert not current_inspection_passed(rig.jobs.load(job.job_id))
    assert rig.inspect(job.job_id).outcome == "passed"
    assert current_inspection_passed(rig.jobs.load(job.job_id))
    assert rig.approve(job.job_id, note="newer decision")["outcome"] == "recorded"
    assert not current_inspection_passed(rig.jobs.load(job.job_id))  # the passed entry is under an older approval
    assert rig.inspect(job.job_id).outcome == "passed"
    assert current_inspection_passed(rig.jobs.load(job.job_id))
    rig.client.fail.append(("list_pull_requests", GitHubUnavailableError("HTTP 503")))
    assert rig.inspect(job.job_id).outcome == "deferred"
    assert not current_inspection_passed(rig.jobs.load(job.job_id))  # a later deferral is not a pass


@pytest.mark.parametrize(("mutate", "match"), [
    (lambda d: d["paths"][0].update(authorization="not-needed"), "contradicts"),
    (lambda d: d["paths"][0].update(changes=["deleted"]), "newest inspection evidence"),
    (lambda d: d["paths"][0].update(path="../escape"), "repository-relative"),
    (lambda d: d["paths"][0].update(role="backend", **{"class": "unowned"}), "names its role"),
], ids=["required-marked-not-needed", "paths-differ-from-evidence", "path-text", "role-on-unowned"])
def test_path_entries_must_agree_with_their_class_and_the_evidence(rig: Rig, mutate: Any, match: str):
    job = job_with(rig, {"README.md": "# x\n"})
    assert rig.inspect(job.job_id).outcome == "needs-authorization"
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match=match):
        rig.jobs.load(job.job_id)


def test_a_protected_entry_can_never_be_stored_as_authorized(rig: Rig):
    job = job_with(rig, {"content/new.json": "{}\n"})
    assert rig.inspect(job.job_id).outcome == "refused"
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    [entry] = [e for e in data["paths"] if e["path"] == "content/new.json"]
    entry["authorization"] = "authorized"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match="contradicts"):
        rig.jobs.load(job.job_id)


# -- Codex review F1, end to end ------------------------------------------------------------------------------------

def test_codex_f1_an_older_broader_approval_from_the_same_second_never_becomes_effective(rig: Rig):
    sha = change_on(rig.repo, BRANCH, {"AGENTS.md": "new agent instructions\n"})
    job = rig.accepted(request_for(rig.repo, sha))
    second = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    broad = rig.write_record(record_for(job, issued_at=f"{second}.100000Z", paths=["AGENTS.md"], note="older broad"))
    narrow = rig.write_record(record_for(job, issued_at=f"{second}.900000Z", paths=[], note="newer narrow"))
    assert rig.approve(job.job_id, path=narrow)["outcome"] == "recorded"
    assert rig.inspect(job.job_id).outcome == "needs-authorization"
    assert rig.approve(job.job_id, path=broad)["outcome"] == "refused"
    assert rig.inspect(job.job_id).outcome == "needs-authorization"
    stored = rig.jobs.load(job.job_id)
    assert effective_approval(stored)["note"] == "newer narrow"
    assert all(entry["result"] != "passed" for entry in stored.evidence)


# -- Codex review F2, end to end: contradictory PR snapshots defer ---------------------------------------------------

def scripted_answers(transport: FakeGitHub, *, by_head: list[dict[str, Any]] | None = None,
                     by_commit: list[dict[str, Any]] | None = None) -> None:
    """One-shot answers for the next head query and/or commit query (the full listing stays transport.prs)."""
    if by_head is not None:
        transport.fail.append(("head=", ApiResponse(200, {}, by_head)))
    if by_commit is not None:
        transport.fail.append(("/commits/", ApiResponse(200, {}, by_commit)))


@pytest.mark.parametrize("case", ["codex-a", "codex-a-head-query", "codex-b", "codex-b-merged", "retargeted",
                                  "draft-flipped"])
def test_contradictory_pr_snapshots_defer_and_never_pass(seeded: Path, state_dir: Path, key: OperatorKey, case: str):
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    sha = job.source["sha"]
    listed, answers = {
        # A: the listing reports the PR at SHA B (not a candidate); the SHA lookup reports it at the pinned SHA
        "codex-a": (pr_json(123, head_ref="another-branch", head_sha="b" * 40),
                    {"by_commit": [pr_json(123, head_ref="another-branch", head_sha=sha)]}),
        "codex-a-head-query": (pr_json(123, head_ref="unrelated", head_sha="b" * 40),
                               {"by_head": [pr_json(123, head_sha=sha)]}),
        # B: the listing reports a compatible open PR; the later lookups report it closed (or merged)
        "codex-b": (pr_json(123, head_sha=sha), {"by_head": [pr_json(123, head_sha=sha, state="closed")],
                                                 "by_commit": [pr_json(123, head_sha=sha, state="closed")]}),
        "codex-b-merged": (pr_json(123, head_sha=sha), {"by_commit": [pr_json(123, head_sha=sha, merged=True)]}),
        "retargeted": (pr_json(123, head_sha=sha), {"by_head": [pr_json(123, head_sha=sha, base="main")]}),
        "draft-flipped": (pr_json(123, head_sha=sha), {"by_commit": [pr_json(123, head_sha=sha, draft=True)]}),
    }[case]
    transport.prs = [listed]
    scripted_answers(transport, **answers)
    report = rig.inspect(job.job_id)
    assert report.outcome == "deferred" and "disagree" in report.deferred, (report.outcome, report.reasons)
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.INSPECTING and not stored.evidence and "disagree" in stored.deferred["reason"]
    assert report.pr is None and transport.writes() == []
    # once GitHub answers consistently, the same job is decided from the (consistent) listing
    report = rig.inspect(job.job_id)
    assert report.outcome == "passed" and rig.jobs.load(job.job_id).deferred is None
    assert report.pr["result"] == ("none" if case.startswith("codex-a") else "reuse")


def test_the_intake_pr_read_on_its_own_must_agree_with_the_listing(seeded: Path, state_dir: Path, key: OperatorKey):
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    transport.prs = [pr_json(1, head_sha=sha)]
    job = rig.approved(request_for(rig.repo, sha, pr_number=1))
    assert job.pr["head_sha"] == sha
    transport.fail.append((f"GET repos/{REPOSITORY}/pulls/1", ApiResponse(200, {}, pr_json(1, head_sha=sha,
                                                                                            state="closed"))))
    report = rig.inspect(job.job_id)
    assert report.outcome == "deferred" and "the read of the intake PR disagree on state" in report.deferred
    assert not rig.jobs.load(job.job_id).evidence
    assert rig.inspect(job.job_id).outcome == "passed"


def test_a_pr_answer_missing_a_field_defers(seeded: Path, state_dir: Path, key: OperatorKey):
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    incomplete = pr_json(4, head_ref="feature/other", head_sha="e" * 40)
    del incomplete["draft"]
    transport.prs = [incomplete]
    report = rig.inspect(job.job_id)
    assert report.outcome == "deferred" and "lacks ['draft']" in report.deferred


# -- Codex review F2 (second review): a change to the intake PR is STALE only once every answer agrees -----------

MOVED = "c" * 40
INTAKE_PR_READ = f"GET repos/{REPOSITORY}/pulls/123"


def intake_pr_rig(seeded: Path, state_dir: Path, key: OperatorKey) -> tuple[FakeGitHub, Rig, IntegrationJob]:
    """A live rig whose job pinned PR #123 at intake; the listing serves it at the pinned SHA (calls cleared)."""
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    transport.prs = [pr_json(123, head_sha=sha)]
    job = rig.approved(request_for(rig.repo, sha, pr_number=123))
    assert job.pr["number"] == 123 and job.pr["head_sha"] == sha
    transport.calls.clear()
    return transport, rig, job


def pr_lookups(transport: FakeGitHub) -> list[str]:
    """The PR reads the inspection made, in order: read (/pulls/123), listing, head (query), commit (query)."""
    kinds = []
    for method, path in transport.calls:
        route = path.split("?", 1)[0]
        if method != "GET":
            continue
        if route == f"repos/{REPOSITORY}/pulls/123":
            kinds.append("read")
        elif route == f"repos/{REPOSITORY}/pulls":
            kinds.append("head" if "head=" in path else "listing")
        elif route.endswith("/pulls"):
            kinds.append("commit")
    return kinds


def assert_deferred_and_undecided(rig: Rig, job_id: str, report: Any, match: str) -> None:
    """Deferred: the job stays INSPECTING with no evidence, no PR decision and nothing that counts as a pass."""
    assert report.outcome == "deferred" and match in report.deferred, (report.outcome, report.reasons)
    assert report.pr is None and not any(check["status"] == "stale" for check in report.checks)
    stored = rig.jobs.load(job_id)
    assert stored.state is JobState.INSPECTING and not stored.evidence and match in stored.deferred["reason"]
    assert not current_inspection_passed(stored)


@pytest.mark.parametrize("also", ["read-only", "and-head-query", "and-commit-query"])
def test_codex_f2_a_moved_head_reported_by_the_single_pr_read_defers_not_stale(seeded: Path, state_dir: Path,
                                                                              key: OperatorKey, also: str):
    transport, rig, job = intake_pr_rig(seeded, state_dir, key)
    moved = pr_json(123, head_sha=MOVED)
    transport.fail.append((INTAKE_PR_READ, ApiResponse(200, {}, moved)))   # the listing keeps the pinned SHA
    scripted_answers(transport, **{"read-only": {}, "and-head-query": {"by_head": [moved]},
                                   "and-commit-query": {"by_commit": [moved]}}[also])
    report = rig.inspect(job.job_id)
    assert_deferred_and_undecided(rig, job.job_id, report, "disagree on head_sha")
    assert "cannot be confirmed" in report.deferred and not report.reasons[0].startswith("PR #123's head moved")
    assert pr_lookups(transport) == ["read", "listing", "head", "commit"]   # the other lookups ran, none skipped
    assert transport.writes() == []
    # repeated once GitHub answers consistently: decided from the agreeing answers (the pinned PR is reused)
    report = rig.inspect(job.job_id)
    assert report.outcome == "passed" and report.pr["result"] == "reuse" and report.pr["number"] == 123
    assert rig.jobs.load(job.job_id).deferred is None


@pytest.mark.parametrize(("change", "match"), [
    ({"head_sha": MOVED}, "PR #123's head moved from"),
    ({"base": "main"}, "PR #123 now targets"),
    ({"head_repo": "someone/fork"}, "PR #123's head is now someone/fork"),
], ids=["head-moved", "retargeted", "other-head-repository"])
def test_codex_f2_b_a_change_every_answer_agrees_on_makes_the_job_stale(seeded: Path, state_dir: Path,
                                                                       key: OperatorKey, change: dict[str, str],
                                                                       match: str):
    transport, rig, job = intake_pr_rig(seeded, state_dir, key)
    changed = pr_json(123, **{"head_sha": job.source["sha"], **change})
    transport.prs = [changed]                       # the listing, the single read and the head query (if it matches)
    if changed["head"]["sha"] != job.source["sha"]:
        transport.extra_by_commit = [changed]       # GitHub's commit query also lists a PR that contains the commit
    report = rig.inspect(job.job_id)
    assert report.outcome == "stale", (report.outcome, report.reasons, report.deferred)
    assert pr_lookups(transport) == ["read", "listing", "head", "commit"]
    assert any(c["check"] == "pr-lookup" and "change is confirmed" in c["detail"] for c in report.checks)
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and match in stored.attention["reason"]
    assert stored.evidence[-1]["result"] == "stale" and stored.evidence[-1]["pr_match"]["result"] == "stale"
    assert history(rig, job.job_id)[-2:] == ["INSPECTING", "STALE"]
    assert rig.inspect(job.job_id).outcome == "terminal" and len(rig.jobs.load(job.job_id).evidence) == 1


def test_codex_f2_b_a_deferral_or_a_crash_is_resumed_and_a_confirmed_change_is_stale(seeded: Path, state_dir: Path,
                                                                                   key: OperatorKey):
    from feature_coordinator.issue_sync import SimulatedCrash  # pylint: disable=import-outside-toplevel
    transport, rig, job = intake_pr_rig(seeded, state_dir, key)
    moved = pr_json(123, head_sha=MOVED)
    transport.fail.append((INTAKE_PR_READ, ApiResponse(200, {}, moved)))
    assert_deferred_and_undecided(rig, job.job_id, rig.inspect(job.job_id), "disagree")
    transport.prs, transport.extra_by_commit = [moved], [moved]   # now every answer reports the move
    with pytest.raises(SimulatedCrash):
        rig.inspect(job.job_id, faults=("after-checks",))
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.INSPECTING and not stored.evidence
    assert rig.inspect(job.job_id).outcome == "stale"
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and [e["result"] for e in stored.evidence] == ["stale"]
    assert history(rig, job.job_id) == ["INTAKE", "AWAITING_APPROVAL", "INSPECTING", "STALE"]


@pytest.mark.parametrize("failing", ["single-read-503", "single-read-unavailable", "confirming-listing",
                                     "confirming-head-query", "confirming-commit-query"])
def test_codex_f2_c_a_failed_read_or_a_failed_confirmation_defers(seeded: Path, state_dir: Path, key: OperatorKey,
                                                                 failing: str):
    transport, rig, job = intake_pr_rig(seeded, state_dir, key)
    outage = ApiResponse(503, {}, {"message": "Service Unavailable"})
    if failing == "single-read-503":
        transport.fail.append((INTAKE_PR_READ, outage))
    elif failing == "single-read-unavailable":
        transport.fail.append((INTAKE_PR_READ, GitHubUnavailableError("connection reset")))
    else:   # the single read reports a move; the lookup that would confirm it fails
        moved = pr_json(123, head_sha=MOVED)
        transport.prs, transport.extra_by_commit = [moved], [moved]
        needle = {"confirming-listing": "sort=created", "confirming-head-query": "head=",
                  "confirming-commit-query": "/commits/"}[failing]
        transport.fail.append((needle, outage))
    report = rig.inspect(job.job_id)
    assert_deferred_and_undecided(rig, job.job_id, report, "PR #123")
    assert transport.writes() == []
    expected = "stale" if failing.startswith("confirming") else "passed"
    assert rig.inspect(job.job_id).outcome == expected   # the outage is over: the agreeing answers decide


@pytest.mark.parametrize("metadata", [
    {"state": "closed"}, {"merged": True}, {"draft": True},
    {"base": "main"}, {"base_repo": "someone/elsewhere"}, {"head_repo": "someone/fork"}, {"head_ref": "renamed"},
], ids=["closed", "merged", "draft", "retargeted", "other-base-repository", "other-head-repository", "other-branch"])
def test_codex_f2_d_single_read_metadata_contradicting_the_listing_defers(seeded: Path, state_dir: Path,
                                                                         key: OperatorKey, metadata: dict[str, Any]):
    transport, rig, job = intake_pr_rig(seeded, state_dir, key)
    claim = pr_json(123, head_sha=job.source["sha"], **metadata)   # same number, same head SHA
    transport.fail.append((INTAKE_PR_READ, ApiResponse(200, {}, claim)))
    report = rig.inspect(job.job_id)
    assert_deferred_and_undecided(rig, job.job_id, report, "the listing and the read of the intake PR disagree")
    assert transport.writes() == []
    assert rig.inspect(job.job_id).outcome == "passed"


def test_codex_f2_d_a_single_read_about_another_pr_defers(seeded: Path, state_dir: Path, key: OperatorKey):
    transport, rig, job = intake_pr_rig(seeded, state_dir, key)
    transport.prs.append(pr_json(124, head_ref="feature/other", head_sha="e" * 40))
    transport.fail.append((INTAKE_PR_READ, ApiResponse(200, {}, pr_json(124, head_sha=MOVED))))
    report = rig.inspect(job.job_id)
    assert_deferred_and_undecided(rig, job.job_id, report, "for PR #123 describes PR #124")
    assert rig.inspect(job.job_id).outcome == "passed"


def test_codex_f2_d_the_local_tracker_read_must_agree_with_its_listing(rig: Rig, tracker: MockIssueTracker):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=sha)
    job = rig.approved(request_for(rig.repo, sha, pr_number=1))
    moved = dataclasses.replace(tracker.get_pull_request(1), head_sha=MOVED)
    tracker.get_pull_request = lambda number: moved   # type: ignore[method-assign]  # only the single read moved
    report = rig.inspect(job.job_id)
    assert_deferred_and_undecided(rig, job.job_id, report, "disagree on head_sha")
    del tracker.get_pull_request
    assert rig.inspect(job.job_id).outcome == "passed"


def test_codex_f2_e_agreeing_answers_keep_the_existing_reuse_and_pass(seeded: Path, state_dir: Path,
                                                                     key: OperatorKey):
    transport, rig, job = intake_pr_rig(seeded, state_dir, key)
    report = rig.inspect(job.job_id)
    assert report.outcome == "passed" and report.pr == {"result": "reuse", "number": 123, "candidates": [123],
                                                        "observed": report.pr["observed"]}
    assert pr_lookups(transport) == ["read", "listing", "head", "commit"]   # one lookup each, nothing repeated
    assert any(c["check"] == "recorded-pr" and c["status"] == "pass" for c in report.checks)
    stored = rig.jobs.load(job.job_id)
    assert current_inspection_passed(stored) and stored.evidence[-1]["pr_match"]["number"] == 123
    assert rig.inspect(job.job_id).changed is False                        # repeating it changes nothing


def test_codex_f2_e_without_an_intake_pr_the_lookup_is_unchanged(seeded: Path, state_dir: Path, key: OperatorKey):
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    transport.prs = [pr_json(7, head_sha=job.source["sha"])]
    transport.calls.clear()
    report = rig.inspect(job.job_id)
    assert report.outcome == "passed" and report.pr["result"] == "reuse" and report.pr["number"] == 7
    assert pr_lookups(transport) == ["listing", "head", "commit"]   # no single read: no PR was pinned at intake


@pytest.mark.parametrize("pr_read", ["agrees-with-the-move", "contradicts-the-listing"])
def test_codex_f2_f_a_moved_source_ref_stays_stale_whatever_the_pr_answers_say(seeded: Path, state_dir: Path,
                                                                            key: OperatorKey, pr_read: str):
    transport, rig, job = intake_pr_rig(seeded, state_dir, key)
    moved_sha = change_on(rig.repo, BRANCH, {"backend/src/y.py": "Y = 1\n"})   # origin's source branch moves
    moved = pr_json(123, head_sha=moved_sha)
    if pr_read == "agrees-with-the-move":
        transport.prs, transport.extra_by_commit = [moved], [moved]
    else:
        transport.fail.append((INTAKE_PR_READ, ApiResponse(200, {}, moved)))   # the listing keeps the pinned SHA
    report = rig.inspect(job.job_id)
    assert report.outcome == "stale"
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and stored.attention["observed"]["source_ref_sha"] == moved_sha
    reasons = stored.attention["reasons"]
    assert "moved from the pinned" in reasons[0]
    pr_reason = any("PR #123's head moved" in reason for reason in reasons)
    # the PR's change is a stale reason only when every answer agrees; a contradicted one adds nothing
    assert pr_reason is (pr_read == "agrees-with-the-move")
    assert stored.evidence[-1]["pr_match"]["result"] == ("stale" if pr_reason else "unknown")


def test_codex_f2_f_a_moved_base_ref_is_stale_without_consulting_the_pr_lookup(seeded: Path, state_dir: Path,
                                                                             key: OperatorKey):
    transport, rig, job = intake_pr_rig(seeded, state_dir, key)
    change_on(rig.repo, "dev", {"cli/src/unrelated.py": "U = 1\n"})
    assert rig.inspect(job.job_id).outcome == "stale"
    assert "base branch 'dev' moved" in rig.jobs.load(job.job_id).attention["reason"]
    assert pr_lookups(transport) == ["read"]   # the PR read agreed with the pin: no lookup was needed


# -- Codex review F3: directory entries and protected ancestors -------------------------------------------------------

def empty_tree(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "tree", "-w", "--stdin"], input=b"",
                          capture_output=True, check=True).stdout.decode().strip()


def tree_of(repo: Path, entries: list[tuple[bytes, bytes, str]]) -> str:
    body = b"".join(mode + b" " + name + b"\0" + bytes.fromhex(sha)
                    for mode, name, sha in sorted(entries, key=lambda e: e[1] + (b"/" if e[0] == b"40000" else b"")))
    return subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "tree", "-w", "--literally", "--stdin"],
                          input=body, capture_output=True, check=True).stdout.decode().strip()


@pytest.mark.parametrize(("name", "match"), [
    (b".env", ".env is protected"),
    (b".git", "malformed name"),
    (b".env.local", ".env.local is protected"),
    (b"empty_dir", "empty_dir is an added empty directory"),
], ids=["protected-env", "dotgit", "protected-env-variant", "unprotected"])
def test_an_added_empty_tree_cannot_escape_classification(rig: Rig, name: bytes, match: str):
    sha = crafted_commit(rig.repo, [(b"40000", name, empty_tree(rig.repo))])
    job = rig.approved(request_for(rig.repo, sha))
    report = rig.inspect(job.job_id)
    assert report.outcome == "refused", (report.outcome, report.reasons)
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.REFUSED and match in " ".join(stored.attention["reasons"])
    if name != b".git":
        entry = paths_of(rig, job.job_id)[name.decode()]
        assert entry["changes"] == ["added"] and "empty-tree" in entry["flags"] and entry["authorization"] == "refused"


def test_a_nested_empty_protected_tree_is_found(rig: Rig):
    inner = tree_of(rig.repo, [(b"40000", b".env", empty_tree(rig.repo))])
    sha = crafted_commit(rig.repo, [(b"40000", b"newdir", inner)])
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "refused"
    entry = paths_of(rig, job.job_id)["newdir/.env"]
    assert entry["class"] == "protected" and "empty-tree" in entry["flags"]


@pytest.mark.parametrize(("path", "through"), [
    ("backend/src/.env/secret.py", "backend/src/.env"),
    ("backend/src/.env/deeper/x.py", "backend/src/.env"),
    ("frontend/.env.local/config.ts", "frontend/.env.local"),
    (".env/notes.md", ".env"),
], ids=["codex", "deeper", "env-variant", "root"])
def test_a_file_inside_a_protected_directory_name_is_protected(rig: Rig, path: str, through: str):
    job = job_with(rig, {path: "SECRET=1\n"})
    assert rig.inspect(job.job_id).outcome == "refused"
    entry = paths_of(rig, job.job_id)[path]
    assert entry["class"] == "protected" and entry["role"] is None and entry["authorization"] == "refused"
    assert f"inside the protected directory {through}" in rig.jobs.load(job.job_id).attention["reason"]


def test_a_path_inside_a_protected_directory_can_never_be_authorized(rig: Rig):
    sha = change_on(rig.repo, BRANCH, {"backend/src/.env/secret.py": "SECRET=1\n"})
    job = rig.accepted(request_for(rig.repo, sha))
    result = rig.approve(job.job_id, paths=["backend/src/.env/secret.py"])
    assert result["outcome"] == "refused" and "protected path" in result["reasons"][0]


def test_a_symlink_to_a_file_inside_a_protected_directory_is_refused(rig: Rig):
    sha = change_on(rig.repo, BRANCH, {}, prepare=symlink("backend/src/link", ".env/secret.py"))
    job = rig.approved(request_for(rig.repo, sha), paths=["backend/src/link"])  # authorizing it does not help
    assert rig.inspect(job.job_id).outcome == "refused"
    assert "points to the protected path 'backend/src/.env/secret.py'" in rig.jobs.load(job.job_id).attention["reason"]


def test_ordinary_directories_are_not_restricted_by_the_ancestor_rule(rig: Rig):
    paths = ["backend/src/data/loader.py", "backend/src/environment/settings.py", "backend/src/.envrc_tools/x.py",
             "backend/src/dotenv/y.py"]
    job = job_with(rig, {path: "X = 1\n" for path in paths})
    assert rig.inspect(job.job_id).outcome == "passed"
    assert {entry["path"]: entry["class"] for entry in rig.jobs.load(job.job_id).paths} == dict.fromkeys(paths, "owned")


def test_path_classes_consider_every_ancestor_directory(rig: Rig):
    policy = PathPolicy(rig.repo, rig.config)
    assert path_class(policy, rig.config, "backend/src/.env/secret.py") == ("protected", None)
    assert matching_prefix(rig.config.protected, "backend/src/.env/a/b.py") == "backend/src/.env"
    assert path_class(policy, rig.config, "content/sub/words.json") == ("protected", None)
    assert path_class(policy, rig.config, ".github/workflows/ci.yml")[0] == "approval-required"
    assert path_class(policy, rig.config, "backend/src/data/x.py")[0] == "owned"
    assert matching_prefix(rig.config.protected, "backend/src/environment/x.py") is None


# -- Codex review F4: authorization expiry at the decision point -----------------------------------------------------

def test_an_approval_that_expires_before_the_final_decision_authorizes_nothing(rig: Rig):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    expires = parse_record_time(effective_approval(job)["expires_at"], "expires_at")
    readings = [expires - timedelta(minutes=5), expires - timedelta(seconds=1), expires]   # start, pre-lock, decision
    calls: list[datetime] = []

    def clock() -> datetime:
        calls.append(readings[min(len(calls), len(readings) - 1)])
        return calls[-1]

    report = rig.inspect(job.job_id, clock=clock)
    assert len(calls) == 3 and report.outcome == "no-approval"
    assert "expired" in report.reasons[0] and "at the decision" in report.reasons[0]
    stored = rig.jobs.load(job.job_id)
    assert stored.evidence == [] and stored.paths == [] and stored.state is JobState.INSPECTING
    assert rig.store.events()[-1]["kind"] == "integration-inspection-refused"
    # the same inspection with a clock that stays inside the window passes; the evidence carries the decision time
    inside = expires - timedelta(minutes=1)
    assert rig.inspect(job.job_id, clock=lambda: inside).outcome == "passed"
    assert rig.jobs.load(job.job_id).evidence[-1]["at"] == inside.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_a_slow_final_signature_check_cannot_carry_an_expired_approval_to_passed(rig: Rig,
                                                                                monkeypatch: pytest.MonkeyPatch):
    """Codex's scenario, deterministic: the final signature verification (the second one) takes until after the
    expiry; the time is read again at the decision, so nothing is recorded."""
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    expires = parse_record_time(effective_approval(job)["expires_at"], "expires_at")
    real = SshSignatureVerifier.verify
    verifications: list[str] = []

    def slow(self: SshSignatureVerifier, record: bytes, signature: str, principal: str) -> str:
        fingerprint = real(self, record, signature, principal)
        verifications.append(fingerprint)
        return fingerprint

    monkeypatch.setattr(SshSignatureVerifier, "verify", slow)
    report = rig.inspect(job.job_id, clock=lambda: expires - timedelta(seconds=1) if len(verifications) < 2
                         else expires + timedelta(milliseconds=150))
    assert len(verifications) == 2 and report.outcome == "no-approval" and "expired" in report.reasons[0]
    assert rig.jobs.load(job.job_id).evidence == []


def test_a_key_revoked_after_the_final_signature_check_records_nothing(rig: Rig, key: OperatorKey,
                                                                       monkeypatch: pytest.MonkeyPatch):
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"})
    real = SshSignatureVerifier.verify
    verifications: list[str] = []

    def verify_then_revoke(self: SshSignatureVerifier, record: bytes, signature: str, principal: str) -> str:
        fingerprint = real(self, record, signature, principal)
        verifications.append(fingerprint)
        if len(verifications) == 2:
            key.revoke_all()
        return fingerprint

    monkeypatch.setattr(SshSignatureVerifier, "verify", verify_then_revoke)
    report = rig.inspect(job.job_id)
    assert report.outcome == "changed-meanwhile" and any("allowed-signers file changed" in m for m in report.messages)
    assert rig.jobs.load(job.job_id).evidence == []
    monkeypatch.setattr(SshSignatureVerifier, "verify", real)
    assert rig.inspect(job.job_id).outcome == "no-approval"     # the revocation is seen by the next run


# -- Codex review F5, end to end ----------------------------------------------------------------------------------

def test_incomplete_github_attribution_defers_and_never_becomes_verified(seeded: Path, state_dir: Path,
                                                                         key: OperatorKey):
    transport = FakeGitHub()
    rig = live_rig(seeded, state_dir, key, transport)
    job = job_with(rig, {"backend/src/x.py": "X = 1\n"}, accept_unverified_provenance=False)
    sha = job.source["sha"]
    signature = {"isValid": True, "state": "VALID", "signer": {"login": OPERATOR}}   # wasSignedByGitHub omitted
    transport.attribution[sha] = commit_node(sha, signature=signature)
    report = rig.inspect(job.job_id)
    assert report.outcome == "deferred" and "wasSignedByGitHub" in report.deferred
    assert not rig.jobs.load(job.job_id).evidence
    transport.attribution[sha] = commit_node(sha, signature={**signature, "wasSignedByGitHub": False})
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.jobs.load(job.job_id).evidence[-1]["provenance"]["verified"] == 1
