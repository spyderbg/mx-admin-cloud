"""W4.7 (M3): the integration candidate and its validation gates — `integration validate --job J`
(integration_validate.py, integration_candidate.py, integration_gates.py) on disposable repositories.

Each test: a temporary checkout with a bare `origin`, a temporary state directory, a throw-away operator SSH key, the
in-memory mock tracker (or the scripted GitHub transport), and a job driven through intake, approval and a passed
inspection. Gates run through a recording fake runner, except the end-to-end tests, which run the real sandboxed gate
runner (bubblewrap) against the materialized candidate. No network, no real GitHub, no production Worker."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from feature_coordinator import integration_candidate
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import ProjectConfig
from feature_coordinator import integration_commands
from feature_coordinator.errors import ConfigError, GitOperationError, StateDirError
from feature_coordinator.gates import GateContext, SandboxedGateRunner
from feature_coordinator.integration import IntegrationError, IntegrationJob, JobState, abort_job, \
    current_gate_evidence, evidence_digest, job_summary, validated
from feature_coordinator.integration_candidate import EMPTY_TREE, CandidateArea, CandidateGit, commit_body, \
    ensure_repository, parse_merge_tree, repository_problem
from feature_coordinator.integration_config import integration_from_mapping
from feature_coordinator.integration_gates import CandidateGateRunner, Toolchain, gate_plan, gate_timeout
from feature_coordinator.integration_validate import IntegrationValidator, validated_for
from feature_coordinator.integration_objects import object_sha
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.issues import MockIssueTracker
from feature_coordinator.locks import FileLock
from feature_coordinator.model import GateResult

from fc_helpers import git
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, KNOWN, OPERATOR, REPOSITORY, FakeGitHub, OperatorKey, Rig, change_on, github_origin, head,
    integration_config, issue_json, live_client, open_issue, origin, pr_json, record_for, request_for, snapshot,
)

OLD_TEXT = "".join(f"LINE_{n} = {n}\n" for n in range(1, 13))       # long enough for Git's rename detection
X_PY = '"""Module x."""\nX = 1\n'
BACKEND_GATES = ["backend-tests", "contract-check", "docs-check", "backend-lint"]


@pytest.fixture
def key(tmp_path: Path) -> OperatorKey:
    return OperatorKey(tmp_path / "operator-home")


@pytest.fixture
def seeded(repo: Path, github_origin: Path) -> Path:
    """dev with files of every role, an unowned and a protected file, pushed."""
    change_on(repo, "dev", {"backend/src/app.py": "A = 1\nB = 2\nC = 3\nD = 4\nE = 5\n", "backend/src/old.py": OLD_TEXT,
                            "cli/src/kiddo_cli/main.py": '"""Main."""\nM = 1\n',
                            "frontend/src/main.ts": "export const x = 1\n", "feature_coordinator/tool.py": "T = 1\n",
                            "docs_agents/coordination/notes.md": "# notes\n", "content/words.json": "{}\n"},
              message="base")
    return repo


@pytest.fixture
def tracker() -> MockIssueTracker:
    tracker = MockIssueTracker(repository=REPOSITORY)
    open_issue(tracker, 63)
    return tracker


@pytest.fixture
def rig(seeded: Path, state_dir: Path, key: OperatorKey, tracker: MockIssueTracker) -> Rig:
    return Rig(seeded, state_dir, key, client=tracker)


class Crash:  # pylint: disable=too-few-public-methods
    """A scripted gate that crashes the validating process once (SimulatedCrash), then passes."""


class FakeGates:
    """A gate runner factory (integration_validate.GateRunnerFactory): records every gate it runs and what the
    workspace held at that moment, writes logs like the real runner, and answers from *script*
    (gate → passed | failed | timeout | blocked | error | Crash() | callable(name, context) -> GateResult)."""

    def __init__(self, script: dict[str, Any] | None = None, *, inspect_git: bool = True) -> None:
        self.script = dict(script or {})
        self.inspect_git = inspect_git
        self.calls: list[str] = []
        self.seen: dict[str, dict[str, Any]] = {}
        self.toolchains: list[Any] = []

    def __call__(self, log_root: Path, toolchain: Any) -> Callable[[str, GateContext], GateResult]:
        self.toolchains.append(toolchain)
        return self.run_gate

    def run_gate(self, name: str, context: GateContext) -> GateResult:
        self.calls.append(name)
        root = Path(context.repo_root)
        if self.inspect_git:   # what a gate's own Git sees (on the host here; a real gate runs in the sandbox)
            self.seen[name] = {"root": root, "files": sorted(git(root, "ls-files").split()),
                               "status": git(root, "status", "--porcelain", "--untracked-files=no"),
                               "head": git(root, "rev-parse", "HEAD").strip(), "base": context.base_sha}
        action = self.script.get(name, "passed")
        if isinstance(action, Crash):
            self.script[name] = "passed"
            raise SimulatedCrash(f"gate {name}")
        if callable(action):
            return action(name, context)
        run_dir = root.parent / "logs" / "gates" / f"{name}.{len(self.calls)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "stdout.log").write_text(f"{name}: {action}\n", encoding="utf-8")
        (run_dir / "stderr.log").write_text("", encoding="utf-8")
        code = {"passed": 0, "failed": 1, "timeout": 124, "blocked": 125, "error": 126}[action]
        return GateResult(name, code, ("fake", name), status=action, duration_seconds=0.25, log=str(run_dir),
                          summary=f"{name} {action}")


def inspected(rig: Rig, edits: dict[str, str | None], **approval: Any) -> IntegrationJob:
    """A job for one commit with *edits* on BRANCH whose inspection passed."""
    sha = change_on(rig.repo, BRANCH, edits)
    job = rig.approved(request_for(rig.repo, sha), **approval)
    report = rig.inspect(job.job_id)
    assert report.outcome == "passed", (report.outcome, report.reasons, report.deferred)
    return rig.jobs.load(job.job_id)


def history(rig: Rig, job_id: str) -> list[str]:
    return [entry["to"] for entry in rig.jobs.load(job_id).history]


def evidence_of(rig: Rig, job_id: str, kind: str) -> list[dict[str, Any]]:
    return [entry for entry in rig.jobs.load(job_id).evidence if entry["kind"] == kind]


def results(rig: Rig, job_id: str) -> dict[str, str]:
    return {name: entry["result"] for name, entry in current_gate_evidence(rig.jobs.load(job_id)).items()}


def with_integration(rig: Rig, **changes: Any) -> Rig:
    """The rig with a changed github.integration section (gate_timeouts, unowned_gates, ...)."""
    github = rig.config.github
    rig.config = dataclasses.replace(rig.config, github=dataclasses.replace(
        github, integration=dataclasses.replace(github.integration, **changes)))
    return rig


def with_config(rig: Rig, **changes: Any) -> Rig:
    """The rig with a changed configuration (role gates, unsafe gates, gates, ...)."""
    config = rig.config
    intake = changes.pop("role_gates", None)
    if intake is not None:
        config = dataclasses.replace(config, github=dataclasses.replace(
            config.github, intake=dataclasses.replace(config.github.intake, role_gates=intake)))
    rig.config = dataclasses.replace(config, **changes)
    return rig


# -- a clean merge -----------------------------------------------------------------------------------------------

def test_a_clean_merge_builds_the_candidate_runs_the_role_gates_and_validates(rig: Rig, github_origin: Path):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    change_on(rig.repo, "dev", {"backend/src/base_only.py": '"""B."""\nB = 1\n'}, push=False)  # local only: no move
    git(rig.repo, "reset", "-q", "--hard", job.base["expected_sha"])
    before = snapshot(rig.repo, github_origin)
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "validated" and report.ok and report.changed, (report.outcome, report.reasons)
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.VALIDATING and validated(stored)
    assert history(rig, job.job_id)[-3:] == ["INSPECTING", "CANDIDATE_BUILT", "VALIDATING"]
    assert gates.calls == BACKEND_GATES                      # registry order; docs-check once for two reasons
    plan = {g.name: g.required_by for g in gate_plan(stored.candidate["paths"], rig.config)}
    assert plan["docs-check"] == ("backend", "integration") and plan["backend-tests"] == ("backend",)
    assert results(rig, job.job_id) == {name: "PASS" for name in BACKEND_GATES}
    candidate = stored.candidate
    # the candidate commit: parents base_sha then source_sha, deterministic, its tree the merge of both
    area = CandidateArea(rig.state, job.job_id)
    raw = git(area.repo.parent, f"--git-dir={area.repo}", "cat-file", "commit", candidate["head_sha"])
    assert f"parent {job.base['expected_sha']}\nparent {job.source['sha']}\n" in raw
    assert candidate["head_sha"] == object_sha("commit", commit_body(stored, candidate["tree_sha"]))
    expected_tree = git(rig.repo, "merge-tree", "--write-tree", job.base["expected_sha"], job.source["sha"]).strip()
    assert candidate["tree_sha"] == expected_tree
    assert [p["path"] for p in candidate["paths"]] == ["backend/src/x.py"]
    # every gate saw exactly the candidate: a clean checkout of the candidate commit
    for seen in gates.seen.values():
        assert seen["head"] == candidate["head_sha"] and seen["status"] == ""
        assert seen["base"] == job.base["expected_sha"]
        assert "backend/src/x.py" in seen["files"] and "backend/src/base_only.py" not in seen["files"]
    # evidence: candidate, the attempt (before any gate), one entry per gate (bound to head and tree), one passed
    # validation decision naming the attempt
    kinds = [entry["kind"] for entry in stored.evidence]
    assert kinds == ["inspection", "candidate", "attempt", "gate", "gate", "gate", "gate", "validation"]
    attempt = evidence_of(rig, job.job_id, "attempt")[0]
    assert attempt["run"] == BACKEND_GATES and evidence_of(rig, job.job_id, "validation")[0]["attempt"] == attempt["id"]
    gate = evidence_of(rig, job.job_id, "gate")[0]
    assert gate["head_sha"] == candidate["head_sha"] and gate["tree_sha"] == candidate["tree_sha"]
    assert gate["exit_code"] == 0 and gate["duration_seconds"] == 0.25 and gate["started_at"]
    assert gate["stdout"] == {"sha256": hashlib.sha256(b"backend-tests: passed\n").hexdigest(), "bytes": 22}
    assert evidence_of(rig, job.job_id, "validation")[0]["result"] == "passed"
    # the checkout and origin are untouched; the candidate lives in the coordinator's state directory only
    assert snapshot(rig.repo, github_origin) == before
    assert (area.repo / "HEAD").read_text(encoding="ascii") == "ref: refs/heads/isolated\n"   # never resolves
    assert repository_problem(area.repo, rig.git.isolated().objects_dir) is None


def test_the_candidate_is_the_merge_of_both_sides_not_the_source_alone(rig: Rig):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    change_on(rig.repo, "dev", {"backend/src/base_only.py": '"""B."""\nB = 1\n'})       # dev moved BEFORE intake
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "passed"
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "validated"
    files = gates.seen["backend-tests"]["files"]
    assert {"backend/src/x.py", "backend/src/base_only.py"} <= set(files)
    assert [p["path"] for p in rig.jobs.load(job.job_id).candidate["paths"]] == ["backend/src/x.py"]


def test_the_candidate_commit_is_deterministic_across_state_directories(rig: Rig, tmp_path: Path):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    first = integration_candidate.build_candidate(CandidateGit(tmp_path / "h1", timeout=30),
                                                  _repo(tmp_path / "a.git", rig), job)
    second = integration_candidate.build_candidate(CandidateGit(tmp_path / "h2", timeout=30),
                                                   _repo(tmp_path / "b.git", rig), job)
    assert first.head == second.head and first.tree == second.tree and first.head is not None
    assert first.head == object_sha("commit", commit_body(job, first.tree))


def _repo(path: Path, rig: Rig) -> Path:
    ensure_repository(path, rig.git.isolated().objects_dir)
    return path


# -- merge conflicts ---------------------------------------------------------------------------------------------

def test_a_merge_conflict_refuses_the_job_and_patches_nothing(rig: Rig, github_origin: Path):
    sha = change_on(rig.repo, BRANCH, {"backend/src/app.py": "A = 100\nB = 2\nC = 3\nD = 4\nE = 5\n"})
    change_on(rig.repo, "dev", {"backend/src/app.py": "A = 200\nB = 2\nC = 3\nD = 4\nE = 5\n"})
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "passed"
    before = snapshot(rig.repo, github_origin)
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "conflict" and not report.ok and report.candidate["conflicts"] == ["backend/src/app.py"]
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.REFUSED and stored.candidate is None and not gates.calls
    assert "conflicts in 1 path(s): backend/src/app.py" in stored.attention["reason"]
    entry = evidence_of(rig, job.job_id, "candidate")[-1]
    assert entry["result"] == "conflict" and entry["head_sha"] is None and entry["conflicts"] == ["backend/src/app.py"]
    assert snapshot(rig.repo, github_origin) == before and git(rig.repo, "rev-parse", BRANCH).strip() == sha
    assert rig.validate(job.job_id, gates=gates).outcome == "terminal"


def test_merge_tree_output_is_parsed_strictly():
    tree = "a" * 40
    assert parse_merge_tree(0, f"{tree}\0\0".encode()) == (tree, [], [])
    out = f"{tree}\0f\0\0001\0f\0Auto-merging\0Auto-merging f\n\0001\0f\0CONFLICT (contents)\0CONFLICT in f\n\0"
    assert parse_merge_tree(1, out.encode()) == (tree, ["f"], ["Auto-merging f", "CONFLICT in f"])
    for code, text in ((1, f"{tree}\0\0"), (0, f"{tree}\0f\0\0"), (2, f"{tree}\0\0"), (0, "garbage\0")):
        with pytest.raises(integration_candidate.HistoryUnverifiable):
            parse_merge_tree(code, text.encode())


# -- stale source / base / PR ------------------------------------------------------------------------------------

@pytest.mark.parametrize("moved", ["base", "source"])
def test_a_moved_base_or_source_makes_the_job_stale_before_anything_is_built(rig: Rig, moved: str):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    change_on(rig.repo, "dev" if moved == "base" else BRANCH, {"backend/src/y.py": "Y = 1\n"})
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "stale" and not gates.calls
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and stored.candidate is None
    assert f"{moved} branch" in stored.attention["reason"] and "moved from the pinned" in stored.attention["reason"]
    assert not CandidateArea(rig.state, job.job_id).repo.exists()


def test_a_base_that_moves_after_validation_makes_the_validated_job_stale(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    area = CandidateArea(rig.state, job.job_id)
    assert area.worktree.is_dir()
    change_on(rig.repo, "dev", {"backend/src/y.py": "Y = 1\n"})     # still mergeable: STALE anyway (design §5.4)
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == "stale" and rig.state_of(job.job_id) is JobState.STALE
    assert history(rig, job.job_id)[-2:] == ["VALIDATING", "STALE"]
    assert not area.worktree.exists() and area.logs.is_dir()            # the workspace goes, the evidence stays


@pytest.mark.parametrize(("change", "match"), [
    ({"head_sha": "f" * 40}, "head moved"), ({"base": "main"}, "now targets"),
], ids=["head-moved", "retargeted"])
def test_a_changed_pr_recorded_at_intake_makes_the_job_stale(rig: Rig, tracker: MockIssueTracker,
                                                             change: dict[str, str], match: str):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=sha)
    job = rig.approved(request_for(rig.repo, sha, pr_number=1))
    assert rig.inspect(job.job_id).outcome == "passed"
    tracker.update_pull_request(1, **change)
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == "stale" and match in rig.jobs.load(job.job_id).attention["reason"]


def test_a_changed_pr_that_the_inspection_decided_to_reuse_makes_the_job_stale(rig: Rig, tracker: MockIssueTracker):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=sha)
    job = rig.approved(request_for(rig.repo, sha))      # no --pr: the inspection finds PR #1 and decides to reuse it
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.jobs.load(job.job_id).evidence[-1]["pr_match"]["result"] == "reuse"
    tracker.update_pull_request(1, base="main")
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == "stale" and "now targets" in rig.jobs.load(job.job_id).attention["reason"]


def test_one_github_answer_about_a_pr_defers_instead_of_stale(seeded: Path, state_dir: Path, key: OperatorKey):
    """Codex review F2 of M2, kept by M3: the single read of the PR reports a moved head, the complete listing does
    not — the run defers and records nothing; the job is never made STALE by one answer."""
    transport = FakeGitHub([pr_json(123, head_sha="0" * 40)])
    rig = Rig(seeded, state_dir, key, client=live_client(transport))
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    transport.prs = [pr_json(123, head_sha=sha)]
    job = rig.approved(request_for(rig.repo, sha, pr_number=123))
    assert rig.inspect(job.job_id).outcome == "passed"
    real = transport.request

    def moved_single_read(method: str, path: str, body: Any = None) -> Any:
        response = real(method, path, body)
        if path.endswith("/pulls/123"):
            data = {**response.data, "head": {**response.data["head"], "sha": "f" * 40}}
            response = dataclasses.replace(response, data=data)
        return response

    transport.request = moved_single_read  # type: ignore[method-assign]
    before = rig.jobs.load(job.job_id).to_dict()
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == "deferred" and rig.jobs.load(job.job_id).to_dict() == before
    assert transport.writes() == []


# -- the candidate's own paths (base tree → candidate tree) ------------------------------------------------------

def _renamed_on_base(rig: Rig, target: str) -> IntegrationJob:
    """The source edits backend/src/old.py; dev renamed that file to *target* after the source forked. Git's merge
    applies the edit at *target* — a path the source's own commits never touched (M2 classified only old.py)."""
    sha = change_on(rig.repo, BRANCH, {"backend/src/old.py": OLD_TEXT.replace("LINE_1 = 1", "LINE_1 = 100")})
    change_on(rig.repo, "dev", {"backend/src/old.py": None, target: OLD_TEXT})
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "passed"
    assert [p["path"] for p in rig.jobs.load(job.job_id).paths] == ["backend/src/old.py"]
    return rig.jobs.load(job.job_id)


def test_an_unowned_candidate_path_needs_authorization_and_then_validates(rig: Rig):
    job = _renamed_on_base(rig, "feature_coordinator/old.py")
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "needs-authorization" and not gates.calls
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.INSPECTING and stored.candidate is None
    assert "feature_coordinator/old.py" in stored.attention["reason"]
    entry = evidence_of(rig, job.job_id, "candidate")[-1]
    assert entry["result"] == "needs-authorization" and entry["paths"]["required"] == 1
    count = len(stored.evidence)
    assert rig.validate(job.job_id, gates=gates).outcome == "needs-authorization"
    assert len(rig.jobs.load(job.job_id).evidence) == count         # the same finding again records nothing
    # the operator authorizes the path, the job is inspected again, and the candidate is built
    assert rig.approve(job.job_id, paths=["feature_coordinator/old.py"])["outcome"] == "recorded"
    assert rig.validate(job.job_id, gates=gates).outcome == "not-inspected"
    assert rig.inspect(job.job_id).outcome == "passed"
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "validated", report.reasons
    paths = {p["path"]: p for p in rig.jobs.load(job.job_id).candidate["paths"]}
    assert paths["feature_coordinator/old.py"]["authorization"] == "authorized"
    assert "coordinator-tests" in gates.calls                        # a path no role owns: the unowned gates


def test_a_protected_candidate_path_refuses_the_job(rig: Rig):
    job = _renamed_on_base(rig, "content/old.py")
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == "refused"
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.REFUSED and "content/old.py is protected" in stored.attention["reason"]
    assert evidence_of(rig, job.job_id, "candidate")[-1]["result"] == "refused"


# -- approvals ---------------------------------------------------------------------------------------------------

def test_validation_needs_a_current_passed_inspection_and_a_valid_approval(rig: Rig, key: OperatorKey):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    job = rig.accepted(request_for(rig.repo, sha))
    with pytest.raises(IntegrationError, match="validate applies to an INSPECTING job"):
        rig.validate(job.job_id, gates=FakeGates())                  # AWAITING_APPROVAL: no approval at all
    assert rig.approve(job.job_id)["outcome"] == "recorded"
    with pytest.raises(IntegrationError, match="validate applies"):
        rig.validate(job.job_id, gates=FakeGates())                  # approved, never inspected
    assert rig.inspect(job.job_id).outcome == "passed"
    before = rig.jobs.load(job.job_id).to_dict()

    def later() -> datetime:
        return datetime.now(timezone.utc) + timedelta(hours=3)

    for apply in (False, True):
        report = rig.validate(job.job_id, gates=FakeGates(), apply=apply, clock=later)
        assert report.outcome == "no-approval" and "expired" in report.reasons[0]
    key.revoke_all()
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == "no-approval" and "does not verify" in report.reasons[0]
    assert rig.jobs.load(job.job_id).to_dict() == before                 # nothing changed
    assert rig.store.events()[-1]["kind"] == "integration-validation-refused"


def test_a_newer_approval_after_the_inspection_needs_a_new_inspection(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.approve(job.job_id, note="renewed")["outcome"] == "recorded"
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == "not-inspected" and "not under the effective approval apr-0002" in report.reasons[0]
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"


def test_an_approval_that_expires_during_the_build_records_nothing(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    expires = datetime.now(timezone.utc) + timedelta(hours=2)
    readings = iter([expires - timedelta(minutes=5), expires - timedelta(seconds=1), expires + timedelta(seconds=1)])
    report = rig.validate(job.job_id, gates=FakeGates(), clock=lambda: next(readings, expires + timedelta(hours=1)))
    assert report.outcome == "no-approval" and rig.state_of(job.job_id) is JobState.INSPECTING
    assert rig.jobs.load(job.job_id).candidate is None


# -- the Issue ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("state", "pull_request", "outcome", "match"), [
    ("closed", False, "issue-blocked", "it is closed"), ("open", True, "issue-blocked", "pull request"),
    (None, False, "deferred", "cannot be found"),
], ids=["closed", "a-pull-request", "missing"])
def test_the_issue_must_exist_be_open_and_be_an_issue(rig: Rig, tracker: MockIssueTracker, state: str | None,
                                                      pull_request: bool, outcome: str, match: str):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    if state is None:
        del tracker.issues[63]
    else:
        open_issue(tracker, 63, state=state, pull_request=pull_request)
    before = rig.jobs.load(job.job_id).to_dict()
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == outcome and match in report.reasons[0]
    assert rig.jobs.load(job.job_id).to_dict() == before
    open_issue(tracker, 63)
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert evidence_of(rig, job.job_id, "candidate")[0]["issue"] == {"number": 63, "state": "open"}


def test_the_issue_is_read_through_the_real_github_client(seeded: Path, state_dir: Path, key: OperatorKey):
    transport = FakeGitHub()
    rig = Rig(seeded, state_dir, key, client=live_client(transport))
    job = inspected(rig, {"backend/src/x.py": X_PY})
    transport.issues[63] = issue_json(63, state="closed")
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "issue-blocked"
    transport.issues[63] = issue_json(63)
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert ("GET", f"repos/{REPOSITORY}/issues/63") in transport.calls and transport.writes() == []


# -- which gates -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("edits", "expected", "paths"), [
    ({"backend/src/x.py": X_PY}, BACKEND_GATES, []),
    ({"frontend/src/main.ts": "export const x = 2\n"},
     ["contract-check", "docs-check", "frontend-type-check", "frontend-build"], []),
    ({"cli/src/kiddo_cli/main.py": '"""Main."""\nM = 2\n'}, ["contract-check", "docs-check", "cli-lint"], []),
    ({"docs_agents/coordination/notes.md": "# notes 2\n"}, ["contract-check", "docs-check"], []),
    ({"feature_coordinator/tool.py": "T = 2\n"}, ["contract-check", "docs-check", "coordinator-tests"],
     ["feature_coordinator/tool.py"]),
    ({"backend/src/x.py": X_PY, "frontend/src/main.ts": "export const x = 2\n", "cli/src/kiddo_cli/main.py": "M = 3\n",
      "feature_coordinator/tool.py": "T = 2\n"},
     ["backend-tests", "contract-check", "docs-check", "frontend-type-check", "frontend-build", "coordinator-tests",
      "backend-lint", "cli-lint"], ["feature_coordinator/tool.py"]),
], ids=["backend-only", "frontend-only", "cli-only", "docs-only", "coordinator", "multi-role"])
def test_the_gates_of_exactly_the_affected_roles_run(rig: Rig, edits: dict[str, str], expected: list[str],
                                                     paths: list[str]):
    job = inspected(rig, edits, paths=paths)
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "validated", report.reasons
    assert gates.calls == expected and [g["gate"] for g in report.gates] == expected


def test_a_lint_gate_without_changed_python_files_is_skipped_not_passed(rig: Rig):
    job = inspected(rig, {"backend/README.md": "# backend\n"})
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "validated"
    assert "backend-lint" not in gates.calls and results(rig, job.job_id)["backend-lint"] == "SKIPPED"
    lint = current_gate_evidence(rig.jobs.load(job.job_id))["backend-lint"]
    assert lint["status"] == "skipped" and lint["exit_code"] is None and "nothing to lint" in lint["summary"]


# -- failed, blocked and missing gates ---------------------------------------------------------------------------

def test_a_failed_required_gate_routes_the_job_to_needs_repair_after_every_gate_ran(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    gates = FakeGates({"backend-tests": "failed"})
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "failed" and not report.ok and gates.calls == BACKEND_GATES   # no fail-fast
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.NEEDS_REPAIR and not validated(stored)
    assert "backend-tests: FAIL" in stored.attention["reason"]
    assert stored.attention["observed"]["gates"]["backend-tests"]["log"].endswith("backend-tests.1")
    assert evidence_of(rig, job.job_id, "validation")[-1]["result"] == "failed"
    with pytest.raises(IntegrationError, match="validate applies"):
        rig.validate(job.job_id, gates=gates)


@pytest.mark.parametrize("status", ["timeout", "blocked", "error"])
def test_a_gate_that_could_not_run_or_finish_blocks_without_passing_or_failing(rig: Rig, status: str):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    report = rig.validate(job.job_id, gates=FakeGates({"docs-check": status}))
    assert report.outcome == "blocked" and not report.ok
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.VALIDATING and not validated(stored)
    assert "docs-check: BLOCKED" in stored.attention["reason"]
    assert results(rig, job.job_id)["docs-check"] == "BLOCKED"
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "validated"
    assert gates.calls == ["docs-check"] and rig.jobs.load(job.job_id).attention is None   # passed ones reused


def test_a_missing_required_gate_blocks_and_never_counts_as_passed(rig: Rig):
    roles = dict(rig.config.github.intake.role_gates)
    with_config(rig, role_gates={**roles, "backend": ("backend-tests", "backend-perf")})   # not a configured gate
    job = inspected(rig, {"backend/src/x.py": X_PY, "cli/src/kiddo_cli/main.py": "M = 2\n"})
    del roles["cli"]
    with_config(rig, role_gates={**roles, "backend": ("backend-tests", "backend-perf")})   # and cli has no gates
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "blocked"
    entries = current_gate_evidence(rig.jobs.load(job.job_id))
    assert entries["backend-perf"]["status"] == "missing" and entries["backend-perf"]["result"] == "BLOCKED"
    assert entries["role:cli"]["status"] == "missing" and "role cli" in entries["role:cli"]["summary"]
    assert "backend-perf" not in gates.calls
    count = len(rig.jobs.load(job.job_id).evidence)
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "blocked"
    assert len(rig.jobs.load(job.job_id).evidence) == count             # the same blocked result is not appended
    with_config(rig, role_gates=dict(integration_config().github.intake.role_gates))     # the operator fixes it
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "validated"
    assert gates.calls == ["backend-lint", "cli-lint"]                   # only gates that never ran for this candidate


def test_an_unsafe_gate_is_not_run_and_blocks(rig: Rig):
    with_config(rig, unsafe_gates={"backend-tests": "writes content/ (B-7)"})
    job = inspected(rig, {"backend/src/x.py": X_PY})
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "blocked"
    assert "backend-tests" not in gates.calls and "B-7" in current_gate_evidence(
        rig.jobs.load(job.job_id))["backend-tests"]["summary"]


# -- idempotence and recovery ------------------------------------------------------------------------------------

def test_repeated_validation_reuses_the_evidence_and_changes_nothing(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    stored = rig.jobs.load(job.job_id)
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "validated" and not report.changed and not gates.calls
    assert all(g["reused"] for g in report.gates)
    assert rig.jobs.load(job.job_id).to_dict() == stored.to_dict()
    report = rig.validate(job.job_id, gates=gates, apply=False)
    assert report.outcome == "would-validate" and report.ok and {g["action"] for g in report.gates} == {"reuse"}


@pytest.mark.parametrize("fault", ["after-build", "after-candidate", "after-validating", "before-summary"])
def test_a_crash_at_any_step_is_resumed_by_running_validate_again(rig: Rig, fault: str):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    with pytest.raises(SimulatedCrash):
        rig.validate(job.job_id, gates=FakeGates(), faults=(fault,))
    expected = {"after-build": JobState.INSPECTING, "after-candidate": JobState.CANDIDATE_BUILT,
                "after-validating": JobState.VALIDATING, "before-summary": JobState.VALIDATING}[fault]
    assert rig.state_of(job.job_id) is expected
    if fault == "after-build":
        assert rig.jobs.load(job.job_id).candidate is None           # objects written, nothing recorded
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "validated", report.reasons
    assert gates.calls == ([] if fault == "before-summary" else BACKEND_GATES)
    stored = rig.jobs.load(job.job_id)
    assert history(rig, job.job_id).count("CANDIDATE_BUILT") == 1 and history(rig, job.job_id).count("VALIDATING") == 1
    assert stored.candidate["head_sha"] == object_sha("commit", commit_body(stored, stored.candidate["tree_sha"]))


def test_an_interrupted_gate_runs_again_and_finished_gates_are_not_repeated(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    with pytest.raises(SimulatedCrash):
        rig.validate(job.job_id, gates=FakeGates({"docs-check": Crash()}))     # killed while docs-check runs
    assert results(rig, job.job_id) == {"backend-tests": "PASS", "contract-check": "PASS"}
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "validated"
    assert gates.calls == ["docs-check", "backend-lint"]


def test_a_gate_that_finished_but_was_not_recorded_runs_again(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    first = FakeGates()
    with pytest.raises(SimulatedCrash):
        rig.validate(job.job_id, gates=first, faults=("after-gate:contract-check",))
    assert first.calls == ["backend-tests", "contract-check"] and "contract-check" not in results(rig, job.job_id)
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "validated"
    assert gates.calls == ["contract-check", "docs-check", "backend-lint"]


def test_a_lost_candidate_repository_or_workspace_is_rebuilt_identically(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    with pytest.raises(SimulatedCrash):
        rig.validate(job.job_id, gates=FakeGates({"docs-check": Crash()}))
    area = CandidateArea(rig.state, job.job_id)
    import shutil  # pylint: disable=import-outside-toplevel
    shutil.rmtree(area.repo)
    (area.worktree / "backend" / "src" / "x.py").chmod(0o644)
    (area.worktree / "backend" / "src" / "x.py").write_text("tampered\n", encoding="utf-8")
    (area.root / "worktree.tmp-left").mkdir()                                         # an interrupted build
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "validated"
    assert gates.seen["docs-check"]["status"] == "" and not (area.root / "worktree.tmp-left").exists()
    assert (area.worktree / "backend" / "src" / "x.py").read_text(encoding="utf-8") == X_PY


def test_a_rebuilt_candidate_with_another_identity_makes_the_job_stale(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    recorded = rig.jobs.load(job.job_id).candidate
    monkeypatch.setattr(integration_candidate, "CANDIDATE_IDENTITY", "Someone <someone@example.invalid>")
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == "stale"
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and "candidate identity changed" in stored.attention["reason"]
    assert stored.attention["observed"]["recorded"]["head_sha"] == recorded["head_sha"]
    assert stored.attention["observed"]["rebuilt"]["head_sha"] != recorded["head_sha"]
    assert stored.candidate == recorded                                   # the recorded candidate never changes


def test_a_recorded_candidate_cannot_be_replaced(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    stored = rig.jobs.load(job.job_id)
    stored.candidate = {**stored.candidate, "worktree": "/elsewhere"}
    with rig.store.transaction(), pytest.raises(IntegrationError, match="immutable"):
        rig.jobs.save(stored)


# -- concurrency and abort ---------------------------------------------------------------------------------------

def test_a_second_validation_or_inspection_of_the_job_is_refused_while_one_runs(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    holder = FileLock(rig.store.locks.dir / f"integration-{job.job_id}.lock").acquire()
    try:
        with pytest.raises(IntegrationError, match="another integration operation"):
            rig.validate(job.job_id, gates=FakeGates())
        assert rig.validate(job.job_id, gates=FakeGates(), apply=False).outcome == "would-run-gates"   # no lock
    finally:
        holder.release()
    assert rig.state_of(job.job_id) is JobState.INSPECTING


def test_concurrent_validations_of_one_job_run_exactly_once(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    started, release = threading.Event(), threading.Event()

    def slow(name: str, context: GateContext) -> GateResult:
        started.set()
        assert release.wait(30)
        return GateResult(name, 0, ("slow",), status="passed", duration_seconds=0.1)

    outcome: dict[str, Any] = {}
    slow_gates = FakeGates({"backend-tests": slow})
    first = threading.Thread(target=lambda: outcome.update(first=rig.validate(job.job_id, gates=slow_gates)))
    first.start()
    assert started.wait(30)
    with pytest.raises(IntegrationError, match="another integration operation"):
        Rig(rig.repo, rig.state, rig.key, config=rig.config, client=rig.client).validate(job.job_id, gates=FakeGates())
    release.set()
    first.join(60)
    assert outcome["first"].outcome == "validated" and history(rig, job.job_id).count("VALIDATING") == 1


def test_an_operator_abort_during_the_gates_stops_the_run_and_wins(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})

    def abort_meanwhile(name: str, context: GateContext) -> GateResult:
        abort_job(rig.jobs, job.job_id, "operator stop", actor="op", apply=True)
        return GateResult(name, 0, ("x",), status="passed", duration_seconds=0.1)

    gates = FakeGates({"contract-check": abort_meanwhile})
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "changed-meanwhile" and gates.calls == ["backend-tests", "contract-check"]
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.ABORTED and list(current_gate_evidence(stored)) == ["backend-tests"]
    area = CandidateArea(rig.state, job.job_id)
    assert not area.worktree.exists() and not area.repo.exists() and area.logs.is_dir()   # nothing left running
    assert rig.validate(job.job_id, gates=gates).outcome == "terminal"


# -- the toolchain of the workspace (Node dependencies) ----------------------------------------------------------

LOCK = '{"name": "frontend", "lockfileVersion": 3}\n'


@pytest.mark.parametrize("case", ["matching", "lock-differs", "not-installed"])
def test_node_gates_get_the_checkouts_node_modules_only_for_the_same_lock_file(rig: Rig, case: str):
    change_on(rig.repo, "dev", {"frontend/package.json": '{"name": "frontend"}\n', "frontend/package-lock.json": LOCK})
    if case != "not-installed":
        (rig.repo / "frontend" / "node_modules" / "vue").mkdir(parents=True)          # untracked, as npm installs it
    job = inspected(rig, {"frontend/src/main.ts": "export const x = 2\n"}
                    | ({"frontend/package-lock.json": LOCK.replace("3", "4")} if case == "lock-differs" else {}))
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    node_gates = {"frontend-type-check", "frontend-build"}
    entries = current_gate_evidence(rig.jobs.load(job.job_id))
    if case == "matching":
        assert report.outcome == "validated" and node_gates <= set(gates.calls)
        link = CandidateArea(rig.state, job.job_id).worktree / "frontend" / "node_modules"
        assert link.is_symlink() and Path(os.readlink(link)) == (rig.repo / "frontend" / "node_modules").resolve()
        assert git(link.parent.parent, "status", "--porcelain", "--untracked-files=all") == ""   # link excluded
        assert entries["frontend-build"]["toolchain"]["node_modules"] == str(link.resolve())
        assert any(b.source == str(link.resolve()) and b.mode == "ro" for b in gates.toolchains[0].binds)
    else:
        assert report.outcome == "blocked" and not node_gates & set(gates.calls)
        why = "differs from the checkout's" if case == "lock-differs" else "not installed in the checkout"
        assert why in entries["frontend-type-check"]["summary"] and entries["frontend-build"]["result"] == "BLOCKED"


# -- the dry run -------------------------------------------------------------------------------------------------

def test_the_dry_run_reports_what_apply_would_do_and_writes_nothing(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    before = rig.jobs.load(job.job_id).to_dict()
    events = len(rig.store.events())
    report = rig.validate(job.job_id, apply=False, gates=FakeGates())
    assert report.outcome == "would-run-gates" and report.ok and not report.changed
    assert [g["gate"] for g in report.gates] == BACKEND_GATES and {g["action"] for g in report.gates} == {"run"}
    assert report.candidate["head_sha"] and report.candidate["paths"]["count"] == 1
    assert rig.jobs.load(job.job_id).to_dict() == before and len(rig.store.events()) == events
    assert not CandidateArea(rig.state, job.job_id).root.exists()
    assert not list((rig.state / "tmp").glob("integration-validate-*"))


def test_the_dry_run_reports_a_conflict(rig: Rig):
    sha = change_on(rig.repo, BRANCH, {"backend/src/app.py": "A = 100\nB = 2\nC = 3\nD = 4\nE = 5\n"})
    change_on(rig.repo, "dev", {"backend/src/app.py": "A = 200\nB = 2\nC = 3\nD = 4\nE = 5\n"})
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "passed"
    report = rig.validate(job.job_id, apply=False)
    assert report.outcome == "would-refuse" and not report.ok
    assert report.candidate["conflicts"] == ["backend/src/app.py"]
    assert rig.state_of(job.job_id) is JobState.INSPECTING


def test_the_dry_run_reports_a_missing_gate(rig: Rig):
    roles = dict(rig.config.github.intake.role_gates)
    del roles["cli"]
    job = inspected(with_config(rig, role_gates=roles), {"cli/src/kiddo_cli/main.py": "M = 2\n"})
    report = rig.validate(job.job_id, apply=False)
    assert report.outcome == "would-block" and not report.ok
    assert {g["gate"]: g["action"] for g in report.gates}["role:cli"] == "blocked"


# -- Git isolation of candidate construction ---------------------------------------------------------------------

def _evil_global_config(home: Path, marks: Path) -> None:
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".gitconfig").write_text(
        f'[merge "evil"]\n\tdriver = touch {marks}/merge-driver; false\n'
        f'[filter "evil"]\n\tclean = touch {marks}/clean-filter; cat\n\tsmudge = touch {marks}/smudge-filter; cat\n'
        f'[diff "evil"]\n\ttextconv = touch {marks}/textconv; cat\n', encoding="utf-8")
    (home / ".config" / "git" / "attributes").write_text("* merge=evil filter=evil diff=evil\n", encoding="utf-8")


def test_no_filter_or_merge_driver_runs_while_the_candidate_is_built(rig: Rig, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch):
    sha = change_on(rig.repo, BRANCH, {"backend/src/app.py": "A = 1\nB = 2\nC = 3\nD = 4\nE = 500\n",
                                       ".gitattributes": "* merge=evil filter=evil diff=evil\n"})
    change_on(rig.repo, "dev", {"backend/src/app.py": "A = 100\nB = 2\nC = 3\nD = 4\nE = 5\n"})   # a content merge
    job = rig.approved(request_for(rig.repo, sha), paths=[".gitattributes"])
    assert rig.inspect(job.job_id).outcome == "passed"
    marks, home = tmp_path / "marks", tmp_path / "evil-home"
    marks.mkdir()
    _evil_global_config(home, marks)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    calls: list[tuple[list[str], dict[str, str]]] = []
    real_run = subprocess.run

    def run(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(argv, list) and any(str(a).startswith("--attr-source=") for a in argv):
            calls.append(([str(a) for a in argv], dict(kwargs.get("env") or {})))
        return real_run(argv, *args, **kwargs)  # pylint: disable=subprocess-run-check

    monkeypatch.setattr(subprocess, "run", run)
    report = rig.validate(job.job_id, gates=FakeGates(inspect_git=False))
    assert report.outcome == "validated", report.reasons
    assert not list(marks.iterdir())                                     # nothing ran: no driver, no filter
    merged = (CandidateArea(rig.state, job.job_id).worktree / "backend/src/app.py").read_text(encoding="utf-8")
    assert merged == "A = 100\nB = 2\nC = 3\nD = 4\nE = 500\n"             # Git's own text merge did the work
    subcommands = {argv[len(argv) - next(i for i, a in enumerate(reversed(argv)) if a in integration_candidate
                                          .CANDIDATE_SUBCOMMANDS) - 1] for argv, _env in calls}
    assert {"merge-tree", "hash-object", "read-tree", "update-index", "status"} <= subcommands
    for argv, env in calls:
        assert f"--attr-source={EMPTY_TREE}" in argv and "core.attributesFile=/dev/null" in argv
        assert "core.hooksPath=/dev/null" in argv and "protocol.allow=never" in argv
        assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert env["HOME"] == env["XDG_CONFIG_HOME"] == str(CandidateArea(rig.state, job.job_id).home)
        assert any(a.startswith("--git-dir=") and str(rig.state) in a for a in argv)   # never the checkout


def test_positive_control_the_same_merge_without_isolation_runs_the_driver(rig: Rig, tmp_path: Path):
    """Why the candidate repository's HEAD never resolves and attributes come from the empty tree: a bare repository
    whose HEAD names a tree with `.gitattributes` runs a merge driver defined in the global configuration."""
    sha = change_on(rig.repo, BRANCH, {"backend/src/app.py": "A = 1\nB = 2\nC = 3\nD = 4\nE = 500\n",
                                       ".gitattributes": "* merge=evil\n"})
    change_on(rig.repo, "dev", {"backend/src/app.py": "A = 100\nB = 2\nC = 3\nD = 4\nE = 5\n"})
    marks, home = tmp_path / "marks", tmp_path / "evil-home"
    marks.mkdir()
    _evil_global_config(home, marks)
    (home / ".config" / "git" / "attributes").unlink()
    bare = tmp_path / "plain.git"
    ensure_repository(bare, rig.git.isolated().objects_dir)
    (bare / "refs" / "heads").mkdir()
    (bare / "refs" / "heads" / "isolated").write_text(f"{sha}\n", encoding="ascii")     # HEAD now resolves
    assert repository_problem(bare, rig.git.isolated().objects_dir) == "it holds a ref (HEAD must never resolve)"
    env = {**os.environ, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", f"--git-dir={bare}", "merge-tree", "--write-tree", head(rig.repo), sha], env=env,
                   capture_output=True, check=False)
    assert (marks / "merge-driver").exists()                              # the attack works without isolation
    assert ensure_repository(bare, rig.git.isolated().objects_dir) is True  # a repository with a ref is rebuilt
    assert repository_problem(bare, rig.git.isolated().objects_dir) is None


def test_only_allowed_subcommands_run_in_candidate_repositories(tmp_path: Path):
    git_ = CandidateGit(tmp_path / "home", timeout=10)
    for forbidden in ("push", "fetch", "checkout", "worktree", "update-ref", "config", "rebase", "commit"):
        with pytest.raises(integration_candidate.GitOperationError, match="not an allowed command"):
            git_.argv(tmp_path, forbidden)


def test_a_tree_entry_named_like_git_is_never_written_into_a_workspace(rig: Rig, tmp_path: Path):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    area = CandidateArea(rig.state, job.job_id)
    area.ensure(rig.git.isolated().objects_dir)
    cgit = CandidateGit(area.home, timeout=30)
    blob = subprocess.run(["git", f"--git-dir={area.repo}", "hash-object", "-w", "--stdin"], input=b"x\n",
                          capture_output=True, check=True).stdout.decode().strip()
    tree = subprocess.run(["git", f"--git-dir={area.repo}", "mktree"], input=f"100644 blob {blob}\t.GIT\n".encode(),
                          capture_output=True, check=True).stdout.decode().strip()
    with pytest.raises(integration_candidate.HistoryRefused, match="never written"):
        integration_candidate.materialize(cgit, area, tree=tree, head="0" * 40)
    assert not area.worktree.exists() and not list(area.root.glob("worktree.tmp-*"))


# -- the record --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("mutate", "match"), [
    (lambda e: e["gate"].update(head_sha="f" * 40), "must name the built candidate"),
    (lambda e: e["gate"].update(result="FAIL", status="failed", exit_code=1), "contradicts|refer to its earlier"),
    (lambda e: e["gate"].update(result="PASS", status="failed"), "agree with the status"),
    (lambda e: e["validation"].update(result="blocked"), "contradicts its gate results"),
    (lambda e: e["candidate"].update(roles=["zeta", "alpha"]), "sorted"),
], ids=["gate-other-head", "gate-result-edited", "gate-status-mismatch", "validation-result", "candidate-roles"])
def test_tampered_m3_evidence_fails_closed_even_with_a_recomputed_digest(rig: Rig, mutate: Any, match: str):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = {"candidate": next(e for e in data["evidence"] if e["kind"] == "candidate"),
               "gate": next(e for e in data["evidence"] if e["kind"] == "gate"),
               "validation": next(e for e in data["evidence"] if e["kind"] == "validation")}
    mutate(entries)
    for entry in data["evidence"]:
        entry["sha256"] = evidence_digest(entry)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match=match):
        rig.jobs.load(job.job_id)


def test_a_record_with_a_candidate_but_no_candidate_state_fails_closed(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["candidate"]["paths"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match="candidate.paths are not the classified paths"):
        rig.jobs.load(job.job_id)


# -- end to end: the real sandboxed gate runner ------------------------------------------------------------------

def _e2e_config(rig: Rig) -> ProjectConfig:
    checks = {
        # backend/: the merged candidate holds both the source's file and the file the base added after the fork
        "backend-tests": ("python", "-c", "import pathlib,sys; x = pathlib.Path('src/x.py'); ok = x.exists() and "
                          "x.read_text() == '\"\"\"Module x.\"\"\"\\nX = 1\\n' and "
                          "pathlib.Path('src/base_only.py').exists(); "
                          "print('candidate ok' if ok else 'wrong tree'); sys.exit(0 if ok else 3)"),
        # Git inside the sandbox sees a clean checkout of the candidate commit (index, HEAD tree, worktree)
        "contract-check": ("python", "-c", "import subprocess,sys; r = subprocess.run(['git', 'status', '--porcelain', "
                           "'--untracked-files=all'], capture_output=True, text=True); print(r.stdout, r.stderr); "
                           "sys.exit(0 if r.returncode == 0 and r.stdout == '' else 4)"),
        "docs-check": ("python", "-c", "import subprocess,sys; f = subprocess.run(['git', 'ls-files'], "
                       "capture_output=True, text=True, check=True).stdout.split(); sys.exit(0 if "
                       "'backend/src/x.py' in f else 5)"),
    }
    lint = rig.config.lint_gates if importlib.util.find_spec("pylint") else {}
    roles = {**rig.config.github.intake.role_gates, "backend": ("backend-tests", "docs-check",
                                                                *(("backend-lint",) if lint else ()))}
    with_config(rig, role_gates=roles)
    return dataclasses.replace(rig.config, gates={**rig.config.gates, **checks}, lint_gates=dict(lint))


def test_end_to_end_with_the_real_sandboxed_gate_runner(rig: Rig, github_origin: Path, needs_bwrap: None):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    change_on(rig.repo, "dev", {"backend/src/base_only.py": '"""B."""\nB = 1\n'})
    rig.config = _e2e_config(rig)
    job = rig.approved(request_for(rig.repo, sha))
    assert rig.inspect(job.job_id).outcome == "passed"
    before = snapshot(rig.repo, github_origin)
    report = rig.validate(job.job_id)                                     # the real CandidateGateRunner (bwrap)
    assert report.outcome == "validated", (report.reasons, report.gates)
    stored = rig.jobs.load(job.job_id)
    entries = current_gate_evidence(stored)
    assert {name: e["result"] for name, e in entries.items()} == {name: "PASS" for name in entries}
    assert {"backend-tests", "contract-check", "docs-check"} <= set(entries)
    tests = entries["backend-tests"]
    assert tests["exit_code"] == 0 and tests["duration_seconds"] > 0 and "candidate ok" in tests["summary"]
    log = Path(tests["log"])
    assert log.is_relative_to(CandidateArea(rig.state, job.job_id).logs)
    assert tests["stdout"]["sha256"] == hashlib.sha256((log / "stdout.log").read_bytes()).hexdigest()
    assert snapshot(rig.repo, github_origin) == before                    # checkout and origin byte-identical
    # a second run reuses every gate; a failing gate in the sandbox is a FAIL → NEEDS_REPAIR
    assert rig.validate(job.job_id).changed is False
    abort_job(rig.jobs, job.job_id, "next scenario", actor="op", apply=True)
    other = change_on(rig.repo, "integration/other", {"backend/src/z.py": '"""Z."""\nZ = 1\n'})   # no src/x.py
    failing = rig.approved(request_for(rig.repo, other, source_ref="integration/other"))
    assert rig.inspect(failing.job_id).outcome == "passed"
    report = rig.validate(failing.job_id)
    assert report.outcome == "failed" and rig.state_of(failing.job_id) is JobState.NEEDS_REPAIR
    assert current_gate_evidence(rig.jobs.load(failing.job_id))["backend-tests"]["exit_code"] == 3


# -- the CLI -----------------------------------------------------------------------------------------------------

def _cli_config(tmp_path: Path, key: OperatorKey) -> Path:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["integration"] = {"enabled": True, "known_authors": [KNOWN], "operators": [OPERATOR],
                                     "allowed_signers": str(key.allowed), "trusted_logins": [OPERATOR]}
    data["gates"] = {name: ["python", "-c", "print('ok')"] for name in data["gates"]}
    data["lint_gates"] = {}
    data["github"].setdefault("intake", {})["role_gates"] = {"backend": ["backend-tests", "docs-check"],
                                                            "coordination": ["docs-check"]}
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_cli_validate_status_and_abort_round_trip(rig: Rig, key: OperatorKey, tmp_path: Path, needs_bwrap: None,
                                                  capsys: pytest.CaptureFixture[str]):
    MockIssueTracker(rig.state / "tracker.json").create_issue("integrate x", "why", ["feature:test"])   # Issue #1
    base = ["--tracker", "local", "--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config",
            str(_cli_config(tmp_path, key)), "integration"]
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    assert cli_main([*base, "intake", "--source-repo", REPOSITORY, "--source-ref", BRANCH, "--source-sha", sha,
                     "--base", "dev", "--base-sha", head(rig.repo), "--issue", "1", "--apply", "--json"]) == 0
    job_id = json.loads(capsys.readouterr().out)["job"]["job_id"]
    record = rig.write_record(record_for(rig.jobs.load(job_id)))
    assert cli_main([*base, "approve", "--job", job_id, "--stage", "inspect", "--record", str(record), "--apply"]) == 0
    assert cli_main([*base, "inspect", "--job", job_id, "--apply"]) == 0
    capsys.readouterr()
    assert cli_main([*base, "validate", "--job", job_id]) == 0
    out = capsys.readouterr().out
    assert "validate: would-run-gates" in out and "gate backend-tests" in out and "dry run" in out
    assert cli_main([*base, "validate", "--job", job_id, "--apply", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["outcome"] == "validated" and data["job"]["state"] == "VALIDATING"
    assert {g["gate"]: g["result"] for g in data["gates"]} == {"backend-tests": "PASS", "contract-check": "PASS",
                                                              "docs-check": "PASS"}
    assert cli_main([*base, "status", "--job", job_id]) == 0
    out = capsys.readouterr().out
    assert f"candidate can-0002: {data['job']['candidate']['head_sha']}" in out
    assert "VALIDATED: ready for M4 review" in out and "gate backend-tests" in out
    area = CandidateArea(rig.state, job_id)
    assert area.worktree.is_dir() and area.repo.is_dir()
    assert cli_main([*base, "abort", "--job", job_id, "--reason", "done testing", "--apply"]) == 0
    assert not area.worktree.exists() and not area.repo.exists() and area.logs.is_dir()
    assert cli_main([*base, "validate", "--job", job_id, "--apply"]) == 1
    assert "validate: terminal" in capsys.readouterr().out


def test_cli_validate_reports_errors_as_json(rig: Rig, key: OperatorKey, tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]):
    base = ["--tracker", "local", "--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config",
            str(_cli_config(tmp_path, key)), "integration"]
    assert cli_main([*base, "validate", "--job", "int-20990101-0001", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data == {"ok": False, "error": {"type": "IntegrationError",
                                           "message": "unknown integration job 'int-20990101-0001'"}}


# -- independent review of M3: regression tests --------------------------------------------------------------------

def test_review_f1_a_symlinked_node_modules_in_the_checkout_is_never_bound(rig: Rig, tmp_path: Path):
    change_on(rig.repo, "dev", {"frontend/package.json": '{"name": "frontend"}\n', "frontend/package-lock.json": LOCK})
    job = inspected(rig, {"frontend/src/main.ts": "export const x = 2\n"})
    secret = tmp_path / "operator-config"
    secret.mkdir()
    (rig.repo / "frontend" / "node_modules").symlink_to(secret, target_is_directory=True)   # planted by an agent
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "blocked"
    entry = current_gate_evidence(rig.jobs.load(job.job_id))["frontend-build"]
    assert entry["result"] == "BLOCKED" and "symlink or resolves outside the checkout" in entry["summary"]
    assert not any(str(secret) in b.source for b in gates.toolchains[0].binds)
    assert {b.source for b in gates.toolchains[0].binds} == {
        str(Path(os.path.realpath(CandidateArea(rig.state, job.job_id).repo / "objects"))),
        str(rig.git.isolated().objects_dir)}                                       # exactly the two object stores


def test_review_f1_the_checkouts_alternates_are_never_followed_into_the_sandbox(rig: Rig, tmp_path: Path):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    alternates = rig.git.isolated().objects_dir / "info" / "alternates"
    alternates.parent.mkdir(exist_ok=True)
    alternates.write_text(f"{tmp_path}\n", encoding="utf-8")                       # one planted line
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "blocked" and not gates.calls
    assert "names alternates" in current_gate_evidence(rig.jobs.load(job.job_id))["backend-tests"]["summary"]
    alternates.unlink()
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"


def test_review_f1_no_bind_may_expose_home_or_the_state_directory(rig: Rig, tmp_path: Path):
    from feature_coordinator.sandbox import Bind  # pylint: disable=import-outside-toplevel
    runner = CandidateGateRunner(rig.config, tmp_path / "logs", checkout=rig.repo, binds=[], state_root=rig.state)
    home = Path(os.path.expanduser("~"))
    assert runner.bind_problem(Bind(str(home), str(home), "ro")) is not None
    assert runner.bind_problem(Bind("/", "/", "ro")) is not None
    assert runner.bind_problem(Bind(str(rig.state.parent), "/x", "ro")) is not None
    assert runner.bind_problem(Bind(str(rig.state / "integration-candidates" / "j" / "repo.git" / "objects"),
                                    "/y", "ro")) is None                            # inside the state dir is fine
    venv = rig.repo / ".venv"
    (tmp_path / "elsewhere" / "bin").mkdir(parents=True)
    (tmp_path / "elsewhere" / "bin" / "python").symlink_to("/usr/bin/python3")
    venv.symlink_to(tmp_path / "elsewhere", target_is_directory=True)               # a symlinked .venv is not used
    workspace = tmp_path / "workspace"                                             # gates run in the workspace
    workspace.mkdir()
    argv, binds, _dirs = runner.resolve(["python", "-c", "pass"], workspace)
    assert argv[0] != str(venv / "bin" / "python") and not any("elsewhere" in b.source for b in binds)


def test_review_f2_a_gate_rerun_after_validation_invalidates_it_until_decided(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert validated(rig.jobs.load(job.job_id))
    rig.config = dataclasses.replace(rig.config, runtime=dataclasses.replace(rig.config.runtime,
                                                                             gate_timeout_seconds=1200))
    with pytest.raises(SimulatedCrash):          # every fingerprint changed: gates re-run, one fails, no decision
        rig.validate(job.job_id, gates=FakeGates({"backend-tests": "failed"}), faults=("before-summary",))
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.VALIDATING and not validated(stored)
    assert current_gate_evidence(stored)["backend-tests"]["result"] == "FAIL"
    report = rig.validate(job.job_id, gates=FakeGates())                         # the FAIL is current: decided now
    assert report.outcome == "failed" and rig.state_of(job.job_id) is JobState.NEEDS_REPAIR


def test_review_f3_an_approval_that_expires_during_the_gates_decides_nothing(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    with pytest.raises(SimulatedCrash):
        rig.validate(job.job_id, gates=FakeGates(), faults=("after-candidate",))   # CANDIDATE_BUILT: a resumed run
    expires = datetime.now(timezone.utc) + timedelta(hours=2)
    readings = iter([expires - timedelta(minutes=1)])
    report = rig.validate(job.job_id, gates=FakeGates(), clock=lambda: next(readings, expires + timedelta(seconds=1)))
    assert report.outcome == "no-approval" and "expired" in report.reasons[0]
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.VALIDATING and not validated(stored)
    assert len(current_gate_evidence(stored)) == len(BACKEND_GATES) and not evidence_of(rig, job.job_id, "validation")


def test_review_f5_an_empty_unowned_gates_list_is_a_configuration_error():
    for value in (None, []):
        with pytest.raises(ConfigError, match="1 to 20 distinct gate names"):
            integration_from_mapping({"unowned_gates": value})
    assert integration_from_mapping({}).unowned_gates == ("coordinator-tests",)


def test_review_f6_repeated_blocked_runs_with_changing_details_do_not_fill_the_evidence(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    counter = iter(range(100))

    def flaky_timeout(name: str, context: GateContext) -> GateResult:
        return GateResult(name, 124, ("x",), status="timeout", duration_seconds=1.0,
                          summary=f"killed after 900s; tail {next(counter)}")

    for _run in range(4):
        assert rig.validate(job.job_id, gates=FakeGates({"docs-check": flaky_timeout})).outcome == "blocked"
    kinds = [entry["kind"] for entry in rig.jobs.load(job.job_id).evidence]
    assert kinds.count("gate") == len(BACKEND_GATES) and kinds.count("validation") == 1


def test_review_f7_a_lint_gate_never_skips_a_python_file_its_diff_does_not_list(rig: Rig,
                                                                               monkeypatch: pytest.MonkeyPatch):
    from feature_coordinator import integration_gates  # pylint: disable=import-outside-toplevel
    job = inspected(rig, {"backend/src/x.py": X_PY})
    monkeypatch.setattr(integration_gates, "changed_python_files", lambda *_args: [])   # e.g. a type change (T)
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "blocked" and "backend-lint" not in gates.calls
    entry = current_gate_evidence(rig.jobs.load(job.job_id))["backend-lint"]
    assert entry["result"] == "BLOCKED" and "does not list ['backend/src/x.py']" in entry["summary"]


# -- Codex functional review of M3 (REQUEST_CHANGES): regression tests ----------------------------------------------

def _fail_without_decision(rig: Rig) -> IntegrationJob:
    """A job whose backend-tests FAILED and whose run was interrupted before its decision (VALIDATING, FAIL current)."""
    job = inspected(rig, {"backend/src/x.py": X_PY})
    with pytest.raises(SimulatedCrash):
        rig.validate(job.job_id, gates=FakeGates({"backend-tests": "failed"}), faults=("before-summary",))
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.VALIDATING and results(rig, job.job_id)["backend-tests"] == "FAIL"
    return stored


@pytest.mark.parametrize("change", ["runtime-timeout", "gate-timeout", "command"])
def test_codex_f1_a_recorded_fail_is_never_retried_after_a_gate_definition_change(rig: Rig, change: str):
    stored = _fail_without_decision(rig)
    fail = current_gate_evidence(stored)["backend-tests"]
    if change == "runtime-timeout":           # Codex's reproduction: only gate_timeout_seconds changed
        rig.config = dataclasses.replace(rig.config, runtime=dataclasses.replace(rig.config.runtime,
                                                                                 gate_timeout_seconds=1200))
    elif change == "gate-timeout":
        with_integration(rig, gate_timeouts={"coordinator-tests": 1800, "backend-tests": 2400})
    else:
        rig.config = dataclasses.replace(rig.config, gates={**rig.config.gates,
                                                            "backend-tests": ("python", "-m", "pytest", "-x")})
    dry = rig.validate(stored.job_id, apply=False, gates=FakeGates())
    assert dry.outcome == "would-fail" and {g["gate"]: g["action"] for g in dry.gates}["backend-tests"] == "fail"
    gates = FakeGates()                                    # every gate would pass now: the FAIL must not be retried
    report = rig.validate(stored.job_id, gates=gates)
    assert report.outcome == "failed" and "backend-tests" not in gates.calls
    after = rig.jobs.load(stored.job_id)
    assert after.state is JobState.NEEDS_REPAIR and not validated(after)
    decision = evidence_of(rig, stored.job_id, "validation")[-1]
    assert decision["result"] == "failed"
    assert {g["gate"]: g["evidence"] for g in decision["gates"]}["backend-tests"] == fail["id"]
    assert next(e for e in after.evidence if e["id"] == fail["id"]) == fail          # the evidence is immutable


def test_codex_f1_removing_a_failed_gate_from_the_plan_never_validates(rig: Rig):
    stored = _fail_without_decision(rig)
    fail = current_gate_evidence(stored)["backend-tests"]
    roles = dict(rig.config.github.intake.role_gates)
    with_config(rig, role_gates={**roles, "backend": ("backend-lint", "docs-check")})     # the failing gate dropped
    assert "backend-tests" not in {p.name for p in gate_plan(stored.candidate["paths"], rig.config)}
    dry = rig.validate(stored.job_id, apply=False, gates=FakeGates())
    assert dry.outcome == "would-fail" and {g["gate"]: g["action"] for g in dry.gates}["backend-tests"] == "fail"
    gates = FakeGates()
    report = rig.validate(stored.job_id, gates=gates)
    assert report.outcome == "failed" and not gates.calls
    after = rig.jobs.load(stored.job_id)
    assert after.state is JobState.NEEDS_REPAIR and not validated(after)
    assert after.attention["reason"].startswith("backend-tests: FAIL") and "no longer in the gate plan" in \
        after.attention["reason"]
    assert after.attention["observed"]["gates"]["backend-tests"]["evidence"] == fail["id"]


def test_codex_f1_a_record_that_passes_after_a_recorded_fail_fails_closed(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    gate = {**next(e for e in data["evidence"] if e["kind"] == "gate"), "gate": "extra-gate", "result": "FAIL",
            "status": "failed", "exit_code": 1, "id": f"gat-{len(data['evidence']) + 1:04d}"}
    data["evidence"].append(gate)
    decision = {**next(e for e in data["evidence"] if e["kind"] == "validation"),
                "id": f"val-{len(data['evidence']) + 1:04d}"}
    data["evidence"].append(decision)
    for entry in data["evidence"]:
        entry["sha256"] = evidence_digest(entry)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match="failures recorded for its candidate"):
        rig.jobs.load(job.job_id)


def test_codex_f1_a_changed_gate_plan_is_not_validated_until_decided_again(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert validated_for(rig.jobs.load(job.job_id), rig.config)
    with_integration(rig, gate_timeouts={"coordinator-tests": 1800, "backend-tests": 1200})
    stored = rig.jobs.load(job.job_id)
    assert validated(stored) and not validated_for(stored, rig.config)     # the record alone cannot see the plan
    dry = rig.validate(job.job_id, apply=False, gates=FakeGates())
    assert any("the gate plan changed since the decision" in m for m in dry.messages)
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "validated" and gates.calls == ["backend-tests"]
    assert validated_for(rig.jobs.load(job.job_id), rig.config)


@pytest.mark.parametrize("crash", ["after-attempt", "during-gate"])
@pytest.mark.parametrize("then", ["resume", "revert"])
def test_codex_f2_an_interrupted_revalidation_revokes_the_earlier_success(rig: Rig, crash: str, then: str):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    first = evidence_of(rig, job.job_id, "validation")[-1]
    with_integration(rig, gate_timeouts={"coordinator-tests": 1800, "backend-tests": 1200})   # backend-tests re-runs
    with pytest.raises(SimulatedCrash):          # killed before any re-run gate recorded a result
        rig.validate(job.job_id, gates=FakeGates({"backend-tests": Crash()}),
                     faults=("after-attempt",) if crash == "after-attempt" else ())
    stored = rig.jobs.load(job.job_id)
    assert stored.evidence[-1]["kind"] == "attempt" and stored.evidence[-1]["run"] == ["backend-tests"]
    assert evidence_of(rig, job.job_id, "validation")[-1] == first and first["result"] == "passed"
    assert not validated(stored) and job_summary(stored)["attempt_open"] and not job_summary(stored)["validated"]
    if then == "revert":                         # the old evidence fits again — the outdated success is NOT restored
        with_integration(rig, gate_timeouts={"coordinator-tests": 1800})
        assert not validated(rig.jobs.load(job.job_id))
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "validated", report.reasons
    attempts = evidence_of(rig, job.job_id, "attempt")
    decision = evidence_of(rig, job.job_id, "validation")[-1]
    assert decision["id"] != first["id"] and decision["attempt"] == attempts[-1]["id"]
    if then == "resume":                         # the interrupted attempt is resumed: no new attempt
        assert len(attempts) == 2 and gates.calls == ["backend-tests"]
        assert any(f"resuming validation attempt {attempts[-1]['id']}" in m for m in report.messages)
    else:                                        # a new attempt for the restored plan, decided anew
        assert len(attempts) == 3 and not gates.calls
    stored = rig.jobs.load(job.job_id)
    assert validated(stored) and not job_summary(stored)["attempt_open"]


def test_codex_f2_status_distinguishes_an_open_attempt_from_a_decided_one(rig: Rig, key: OperatorKey,
                                                                           tmp_path: Path):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    with pytest.raises(SimulatedCrash):
        rig.validate(job.job_id, gates=FakeGates({"contract-check": Crash()}))
    lines = integration_commands._job_lines(job_summary(rig.jobs.load(job.job_id)))
    assert any("attempt att-0003" in line and "OPEN" in line for line in lines)
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    lines = integration_commands._job_lines(job_summary(rig.jobs.load(job.job_id)))
    assert any("attempt att-0003" in line and line.endswith("decided") for line in lines)


@pytest.mark.parametrize("moved", ["source", "base", "pr"])
def test_codex_f3_a_ref_or_pr_that_moves_during_the_gates_makes_the_job_stale(rig: Rig, tracker: MockIssueTracker,
                                                                             moved: str):
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=sha)
    job = rig.approved(request_for(rig.repo, sha, pr_number=1))
    assert rig.inspect(job.job_id).outcome == "passed"

    def move(name: str, context: GateContext) -> GateResult:
        if moved == "pr":
            tracker.update_pull_request(1, head_sha="f" * 40)
        else:
            change_on(rig.repo, BRANCH if moved == "source" else "dev", {"backend/src/y.py": "Y = 1\n"})
        return GateResult(name, 0, ("x",), status="passed", duration_seconds=0.1)

    gates = FakeGates({"contract-check": move})
    report = rig.validate(job.job_id, gates=gates)
    assert report.outcome == "stale" and gates.calls == BACKEND_GATES, (report.outcome, report.reasons)
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and not validated(stored)
    assert not evidence_of(rig, job.job_id, "validation")                 # never a decision for a stale candidate
    assert history(rig, job.job_id)[-2:] == ["VALIDATING", "STALE"]
    expected = {"source": "source branch", "base": "base branch", "pr": "head moved"}[moved]
    assert stored.attention["reason"].startswith("after the gates:") and expected in stored.attention["reason"]
    assert report.job["state"] == "STALE" and not CandidateArea(rig.state, job.job_id).worktree.exists()


def test_codex_f3_a_failed_gate_of_a_candidate_whose_source_moved_is_stale_not_needs_repair(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})

    def fail_and_move(name: str, context: GateContext) -> GateResult:
        change_on(rig.repo, BRANCH, {"backend/src/y.py": "Y = 1\n"})
        return GateResult(name, 1, ("x",), status="failed", duration_seconds=0.1, summary="assert failed")

    report = rig.validate(job.job_id, gates=FakeGates({"backend-tests": fail_and_move}))
    assert report.outcome == "stale" and rig.state_of(job.job_id) is JobState.STALE


def test_codex_f3_unreadable_refs_after_the_gates_defer_the_decision(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})

    def down(_ref: str) -> str:
        raise GitOperationError("network is down")

    def refs_unreadable(name: str, context: GateContext) -> GateResult:
        rig.git.remote_head = down  # type: ignore[method-assign]
        return GateResult(name, 0, ("x",), status="passed", duration_seconds=0.1)

    report = rig.validate(job.job_id, gates=FakeGates({"backend-lint": refs_unreadable}))
    assert report.outcome == "deferred" and "after the gates" in report.reasons[0]
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.VALIDATING and not validated(stored) and not evidence_of(rig, job.job_id,
                                                                                            "validation")
    del rig.git.remote_head                                # origin readable again
    gates = FakeGates()
    assert rig.validate(job.job_id, gates=gates).outcome == "validated" and not gates.calls


def _sandboxed_backend_config(rig: Rig, tests: tuple[str, ...]) -> None:
    """Real gates for the sandboxed tests: backend-tests as given, the feature integration gates trivially passing."""
    trivial = ("python", "-c", "pass")
    gates = {**rig.config.gates, "backend-tests": tests, "contract-check": trivial, "docs-check": trivial}
    rig.config = dataclasses.replace(rig.config, lint_gates={}, gates=gates)
    with_config(rig, role_gates={**rig.config.github.intake.role_gates, "backend": ("backend-tests",)})


PYTEST = ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider")


def test_codex_f4_a_missing_python_gate_tool_is_blocked_not_failed(rig: Rig, needs_bwrap: None):
    _sandboxed_backend_config(rig, PYTEST)
    job = inspected(rig, {"backend/src/x.py": X_PY, "backend/tests/test_ok.py": "def test_ok():\n    assert True\n"})
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(rig.repo / ".venv")], check=True)  # untracked
    report = rig.validate(job.job_id)                      # the checkout's .venv has no pytest
    assert report.outcome == "blocked", (report.outcome, report.reasons)
    stored = rig.jobs.load(job.job_id)
    entry = current_gate_evidence(stored)["backend-tests"]
    assert stored.state is JobState.VALIDATING and entry["result"] == "BLOCKED" and entry["status"] == "blocked"
    assert "gate tool pytest is not installed" in entry["summary"]
    assert entry["toolchain"]["python"] == str(rig.repo / ".venv" / "bin" / "python")
    import shutil  # pylint: disable=import-outside-toplevel
    shutil.rmtree(rig.repo / ".venv")                     # the operator fixes the toolchain (coordinator's pytest)
    assert rig.validate(job.job_id).outcome == "validated"
    assert current_gate_evidence(rig.jobs.load(job.job_id))["backend-tests"]["toolchain"]["python"] == sys.executable


def test_codex_f4_an_import_error_in_the_tests_is_still_a_failure(rig: Rig, needs_bwrap: None):
    _sandboxed_backend_config(rig, PYTEST)
    job = inspected(rig, {"backend/tests/test_broken.py": "import definitely_not_installed_xyz\n\n\n"
                                                          "def test_x():\n    assert definitely_not_installed_xyz\n"})
    report = rig.validate(job.job_id)
    assert report.outcome == "failed", (report.outcome, report.reasons)
    entry = current_gate_evidence(rig.jobs.load(job.job_id))["backend-tests"]
    assert entry["result"] == "FAIL" and entry["exit_code"] == 2 and rig.state_of(job.job_id) is JobState.NEEDS_REPAIR


def test_codex_f4_the_tool_check_maps_only_a_missing_tool_to_blocked(rig: Rig, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, list[str]]] = []

    def sandboxed(self: Any, name: str, argv: Any, context: GateContext, **_kwargs: Any) -> GateResult:
        calls.append((name, list(argv)))
        if name.endswith(".tool-check"):
            code = {"pytest": 3, "pylint": 0}[argv[-1]]
            return GateResult(name, code, tuple(argv), status="passed" if code == 0 else "failed")
        return GateResult(name, 1, tuple(argv), status="failed", summary="E   ModuleNotFoundError: No module 'app'")

    monkeypatch.setattr(SandboxedGateRunner, "run_command", sandboxed)
    runner = CandidateGateRunner(rig.config, tmp_path / "logs", checkout=tmp_path, binds=[], state_root=rig.state)
    context = GateContext(repo_root=tmp_path / "workspace", head_sha="a" * 40, base_sha="b" * 40, feature_id="j")
    missing = runner.run_command("backend-tests", ["python", "-m", "pytest", "-q"], context, cwd=tmp_path)
    assert missing.status == "blocked" and "gate tool pytest is not installed" in missing.summary
    assert runner.run_command("backend-lint", ["python", "-m", "pylint", "x.py"], context, cwd=tmp_path).status \
        == "failed"                                       # the tool is installed: its own failure stays a failure
    assert runner.run_command("docs-check", ["python", "scripts/check.py"], context, cwd=tmp_path).status == "failed"
    runner.run_command("backend-tests", ["python", "-m", "pytest"], context, cwd=tmp_path)
    probes = [argv for name, argv in calls if name.endswith(".tool-check")]
    assert len(probes) == 2 and all(argv[1:3] == ["-I", "-c"] for argv in probes)    # once per tool, isolated


def test_codex_f5_the_report_after_an_approval_expiry_is_the_persisted_job(rig: Rig):
    job = inspected(rig, {"backend/src/x.py": X_PY})
    with pytest.raises(SimulatedCrash):
        rig.validate(job.job_id, gates=FakeGates(), faults=("after-candidate",))
    expires = datetime.now(timezone.utc) + timedelta(hours=2)
    readings = iter([expires - timedelta(minutes=1)])
    report = rig.validate(job.job_id, gates=FakeGates(), clock=lambda: next(readings, expires + timedelta(minutes=1)))
    assert report.outcome == "no-approval" and "expired" in report.reasons[0]
    persisted = json.loads(json.dumps(job_summary(rig.jobs.load(job.job_id))))
    assert json.loads(json.dumps(report.to_dict()))["job"] == persisted
    assert persisted["state"] == "VALIDATING" and len(persisted["gates"]) == len(BACKEND_GATES)


def test_codex_f5_cli_text_after_an_approval_expiry_shows_the_persisted_job(rig: Rig, key: OperatorKey,
                                                                            tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch,
                                                                            capsys: pytest.CaptureFixture[str]):
    MockIssueTracker(rig.state / "tracker.json").create_issue("integrate x", "why", ["feature:test"])   # Issue #1
    base = ["--tracker", "local", "--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config",
            str(_cli_config(tmp_path, key)), "integration"]
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    assert cli_main([*base, "intake", "--source-repo", REPOSITORY, "--source-ref", BRANCH, "--source-sha", sha,
                     "--base", "dev", "--base-sha", head(rig.repo), "--issue", "1", "--apply", "--json"]) == 0
    job_id = json.loads(capsys.readouterr().out)["job"]["job_id"]
    record = rig.write_record(record_for(rig.jobs.load(job_id)))
    assert cli_main([*base, "approve", "--job", job_id, "--stage", "inspect", "--record", str(record), "--apply"]) == 0
    assert cli_main([*base, "inspect", "--job", job_id, "--apply"]) == 0
    expires = datetime.now(timezone.utc) + timedelta(hours=2)
    readings = iter([expires - timedelta(minutes=1)])
    options: dict[str, Any] = {"faults": ("after-candidate",)}

    def validator(*args: Any, **kwargs: Any) -> IntegrationValidator:
        return IntegrationValidator(*args, **kwargs, gate_runner=FakeGates(), **options)

    monkeypatch.setattr(integration_commands, "IntegrationValidator", validator)
    with pytest.raises(SimulatedCrash):
        cli_main([*base, "validate", "--job", job_id, "--apply"])       # CANDIDATE_BUILT recorded, then a crash
    assert rig.state_of(job_id) is JobState.CANDIDATE_BUILT
    options.update(faults=(), clock=lambda: next(readings, expires + timedelta(minutes=1)))
    capsys.readouterr()
    assert cli_main([*base, "validate", "--job", job_id, "--apply"]) == 1
    out = capsys.readouterr().out
    persisted = json.loads(json.dumps(job_summary(rig.jobs.load(job_id))))
    assert "validate: no-approval" in out and persisted["state"] == "VALIDATING"
    assert "\n".join(integration_commands._job_lines(persisted)) in out          # exactly the persisted job
    assert f"{job_id}: VALIDATING" in out and "CANDIDATE_BUILT —" not in out


def test_codex_f6_coordinator_tests_get_1800_seconds_and_others_keep_the_default(rig: Rig, tmp_path: Path):
    assert integration_from_mapping({}).gate_timeouts == {"coordinator-tests": 1800}
    assert integration_from_mapping({"gate_timeouts": {"docs-check": 60}}).gate_timeouts == {"docs-check": 60}
    for bad in ({"docs-check": 0}, {"docs-check": True}, {"docs-check": "60"}, {"no such gate!": 60},
                {"docs-check": 86401}, ["docs-check"]):
        with pytest.raises(ConfigError, match="gate_timeouts"):
            integration_from_mapping({"gate_timeouts": bad})
    paths = [{"role": None}, {"role": "backend"}]           # an unowned and a backend path
    plan = {p.name: p for p in gate_plan(paths, rig.config)}
    assert plan["coordinator-tests"].timeout == gate_timeout("coordinator-tests", rig.config) == 1800
    assert {p.timeout for name, p in plan.items() if name != "coordinator-tests"} == {900}
    with_integration(rig, gate_timeouts={"coordinator-tests": 1800, "docs-check": 1200})
    changed = {p.name: p for p in gate_plan(paths, rig.config)}
    assert [n for n in plan if plan[n].fingerprint != changed[n].fingerprint] == ["docs-check"]   # fingerprinted
    runner = rig.validator()._sandboxed_runner(tmp_path / "logs", Toolchain())
    assert runner.timeouts == {"coordinator-tests": 1800, "docs-check": 1200}


def test_codex_f6_a_per_gate_timeout_is_enforced_as_blocked(rig: Rig, needs_bwrap: None):
    _sandboxed_backend_config(rig, ("python", "-c", "import time; time.sleep(60)"))
    rig.config = dataclasses.replace(rig.config, runtime=dataclasses.replace(rig.config.runtime,
                                                                             terminate_grace_seconds=1))
    with_integration(rig, gate_timeouts={"coordinator-tests": 1800, "backend-tests": 2})
    job = inspected(rig, {"backend/src/x.py": X_PY})
    report = rig.validate(job.job_id)
    assert report.outcome == "blocked", (report.outcome, report.reasons)
    entry = current_gate_evidence(rig.jobs.load(job.job_id))["backend-tests"]
    assert entry["status"] == "timeout" and entry["result"] == "BLOCKED" and entry["duration_seconds"] < 30
    assert rig.state_of(job.job_id) is JobState.VALIDATING


def test_codex_a_candidate_interpreter_is_never_executed_as_a_gate_tool(rig: Rig, tmp_path: Path):
    checkout, workspace = tmp_path / "checkout-without-venv", tmp_path / "workspace"
    checkout.mkdir()
    planted = workspace / ".venv" / "bin" / "python"          # candidate content
    planted.parent.mkdir(parents=True)
    planted.write_text("#!/bin/sh\necho candidate interpreter\n", encoding="utf-8")
    planted.chmod(0o755)
    runner = CandidateGateRunner(rig.config, tmp_path / "logs", checkout=checkout, binds=[], state_root=rig.state)
    argv, binds, _dirs = runner.resolve(["python", "-m", "pytest"], workspace)
    assert argv == [sys.executable, "-m", "pytest"] and not any(str(workspace) in b.source for b in binds)
    assert SandboxedGateRunner(rig.config, tmp_path / "logs").resolve(["python"], workspace)[0][0] == str(planted)


NODE_LOCK = json.dumps({"name": "frontend", "lockfileVersion": 3, "packages": {
    "": {"name": "frontend", "dependencies": {"vue": "^3.4.0"}}, "node_modules/vue": {"version": "3.4.0"},
    "node_modules/fsevents": {"version": "2.3.3", "optional": True}}}) + "\n"
NODE_PACKAGE = '{"name": "frontend", "dependencies": {"vue": "^3.4.0"}}\n'


@pytest.mark.parametrize("case", ["complete", "scripts-only", "dependencies-differ", "package-missing"])
def test_codex_node_gates_need_the_same_declared_and_installed_dependencies(rig: Rig, case: str):
    change_on(rig.repo, "dev", {"frontend/package.json": NODE_PACKAGE, "frontend/package-lock.json": NODE_LOCK})
    (rig.repo / "frontend" / "node_modules" / ("other" if case == "package-missing" else "vue")).mkdir(parents=True)
    edits = {"frontend/src/main.ts": "export const x = 2\n"}
    if case == "scripts-only":
        edits["frontend/package.json"] = NODE_PACKAGE.replace("}\n", ', "scripts": {"build": "vite build"}}\n')
    elif case == "dependencies-differ":                   # vue removed from package.json, still installed
        edits["frontend/package.json"] = '{"name": "frontend", "dependencies": {}}\n'
    job = inspected(rig, edits)
    gates = FakeGates()
    report = rig.validate(job.job_id, gates=gates)
    entry = current_gate_evidence(rig.jobs.load(job.job_id))["frontend-build"]
    if case in ("complete", "scripts-only"):
        assert report.outcome == "validated" and "frontend-build" in gates.calls, report.reasons
    else:
        assert report.outcome == "blocked" and "frontend-build" not in gates.calls
        why = "declares other dependencies" if case == "dependencies-differ" else "lacks 1 package(s)"
        assert entry["result"] == "BLOCKED" and why in entry["summary"]
