"""W4.7 (M4): the independent AI review of the exact integration candidate — `integration review --job J`
(integration_review.py, integration_review_context.py, integration_review_process.py) on disposable repositories.

Each test: a temporary checkout with a bare `origin`, a temporary state directory, a throw-away operator SSH key, the
in-memory mock tracker (or the scripted GitHub transport behind the real client), and a job driven through intake,
approval, a passed inspection and a passed M3 validation (recording fake gate runner). Reviewer processes are REAL OS
processes running the real `integration_exec` → `run_exec` with a scripted reviewer instead of Claude Code
(fc_fake_integration_reviewer.py); the end-to-end CLI test runs the real `integration review-exec` with
ClaudeCodeReviewer and a fake `claude` executable. No model, no paid request, no real GitHub, no network, no
production Worker."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from feature_coordinator import integration_review
from feature_coordinator.cli import main as cli_main
from feature_coordinator.errors import StateDirError
from feature_coordinator.integration import IntegrationError, IntegrationJob, JobState, abort_job, current_review, \
    evidence_digest, job_summary, validated
from feature_coordinator.integration_candidate import CandidateArea
from feature_coordinator.integration_review import IntegrationReviewer, reviewed_for
from feature_coordinator.integration_review_context import ReviewGit, changed_path_problem, reviewer_identity
from feature_coordinator.integration_review_process import attempt_problem
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.issues import MockIssueTracker
from feature_coordinator.locks import FileLock
from feature_coordinator.review_context import ChangedFile
from feature_coordinator.review_process import REVIEW_TAG_ENV, result_path
from feature_coordinator.supervisor import tagged_processes

from fc_helpers import git
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, KNOWN, OPERATOR, REPOSITORY, FakeGitHub, OperatorKey, Rig, change_on, github_origin, head,
    integration_config, live_client, origin, pr_json, record_for, request_for, snapshot,
)
from test_fc_integration_validate import (  # pylint: disable=unused-import
    X_PY, FakeGates, _cli_config, inspected, key, rig, seeded, tracker, with_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_PROCESS = Path(__file__).resolve().parent / "fc_fake_integration_reviewer.py"


# -- the reviewer processes ----------------------------------------------------------------------------------------

class Launcher:
    """`start` for the tests: `integration review-exec RUN_DIR` → the FAKE reviewer process (a real process).
    *before*: called with the run directory before the process starts (a change made while the reviewer runs).
    *env*: added to the process's environment (FAKE_REVIEW_MODEL / FAKE_REVIEW_INDEPENDENCE: the process's own
    configured model / independence policy)."""

    def __init__(self, state: Path, *modes: str, before: Callable[[Path], None] | None = None,
                 env: dict[str, str] | None = None) -> None:
        self.state = state
        self.modes = list(modes) or ["pass"]
        self.before = before
        self.env = dict(env or {})
        self.calls: list[dict[str, Any]] = []
        self.handles: list[subprocess.Popen] = []

    @property
    def tags(self) -> list[str]:
        return [c["env"][REVIEW_TAG_ENV] for c in self.calls]

    def __call__(self, tail: list[str], log_name: str, env: dict[str, str]) -> subprocess.Popen:
        assert list(tail[:2]) == ["integration", "review-exec"] and len(tail) == 3, tail
        mode = self.modes.pop(0) if len(self.modes) > 1 else self.modes[0]
        self.calls.append({"tail": list(tail), "log": log_name, "env": dict(env), "mode": mode})
        if self.before is not None:
            self.before(Path(tail[2]))
        full = {**env, "PYTHONPATH": str(REPO_ROOT), "FAKE_REVIEW_MODE": mode, "FAKE_STATE_DIR": str(self.state),
                **self.env}
        (self.state / "logs").mkdir(exist_ok=True)
        with open(self.state / "logs" / log_name, "ab") as log:
            handle = subprocess.Popen([sys.executable, str(FAKE_PROCESS), tail[2]], env=full, stdout=log,  # pylint: disable=consider-using-with
                                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                                      cwd=REPO_ROOT)
        self.handles.append(handle)
        return handle

    def wait(self, timeout: float = 60.0) -> None:
        for handle in self.handles:
            handle.wait(timeout=timeout)

    def cleanup(self) -> None:
        for tag in self.tags:
            for pid in tagged_processes(REVIEW_TAG_ENV, tag):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        for handle in self.handles:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                handle.kill()
                handle.wait(timeout=10)


@pytest.fixture
def launchers(state_dir: Path):
    made: list[Launcher] = []

    def make(*modes: str, before: Callable[[Path], None] | None = None,
             env: dict[str, str] | None = None) -> Launcher:
        launcher = Launcher(state_dir, *modes, before=before, env=env)
        made.append(launcher)
        return launcher
    yield make
    for launcher in made:
        launcher.cleanup()


def with_review(rig: Rig, **changes: Any) -> Rig:
    """The rig with a changed github.review section (max_attempts, independence, limits)."""
    github = rig.config.github
    rig.config = dataclasses.replace(rig.config, github=dataclasses.replace(
        github, review=dataclasses.replace(github.review, **changes)))
    return rig


@pytest.fixture
def rrig(rig: Rig) -> Rig:
    """The M3 rig with a short termination grace (reviewer processes are stopped quickly)."""
    rig.config = dataclasses.replace(rig.config, runtime=dataclasses.replace(rig.config.runtime,
                                                                             terminate_grace_seconds=2))
    return rig


def reviewer(rig: Rig, launcher: Any = None, *, preflight: str | Callable[[], str] = "", faults: tuple[str, ...] = (),
             clock: Any = None, wall: Any = None, stop: Any = None, reload: Any = None) -> IntegrationReviewer:
    kwargs: dict[str, Any] = {"reload": reload}
    if clock is not None:
        kwargs["clock"] = clock
    if wall is not None:
        kwargs["wall"] = wall
    kwargs["stop"] = stop or (lambda: False)
    tracker = (lambda: rig.client) if rig.client is not None else None
    return IntegrationReviewer(rig.store, rig.config, rig.git, verifier=rig.verifier, tracker=tracker, start=launcher,
                               preflight=preflight if callable(preflight) else (lambda: preflight), faults=faults,
                               poll_seconds=0.05, **kwargs)


def review(rig: Rig, job_id: str, launcher: Any = None, *, apply: bool = True, retry: dict[str, str] | None = None,
           **options: Any) -> Any:
    return reviewer(rig, launcher, **options).run(job_id, apply=apply, retry=retry)


def inspected_on(rig: Rig, branch: str, edits: dict[str, str | None]) -> IntegrationJob:
    """A job for one commit with *edits* on *branch* (another source branch than BRANCH) whose inspection passed."""
    sha = change_on(rig.repo, branch, edits)
    job = rig.approved(request_for(rig.repo, sha, source_ref=branch))
    assert rig.inspect(job.job_id).outcome == "passed"
    return rig.jobs.load(job.job_id)


def validated_job(rig: Rig, edits: dict[str, str | None] | None = None, **approval: Any) -> IntegrationJob:
    job = inspected(rig, edits or {"backend/src/x.py": X_PY}, **approval)
    report = rig.validate(job.job_id, gates=FakeGates())
    assert report.outcome == "validated", (report.outcome, report.reasons)
    return rig.jobs.load(job.job_id)


def kinds(rig: Rig, job_id: str) -> list[str]:
    return [entry["kind"] for entry in rig.jobs.load(job_id).evidence]


def of_kind(rig: Rig, job_id: str, kind: str) -> list[dict[str, Any]]:
    return [entry for entry in rig.jobs.load(job_id).evidence if entry["kind"] == kind]


def history(rig: Rig, job_id: str) -> list[str]:
    return [entry["to"] for entry in rig.jobs.load(job_id).history]


def seen_prompt(rig: Rig, job_id: str) -> str:
    attempt = of_kind(rig, job_id, "review-attempt")[-1]
    return (Path(attempt["run_dir"]) / "seen-prompt.txt").read_text(encoding="utf-8")


def raw_record(rig: Rig, job_id: str) -> bytes:
    return (rig.state / "integration" / f"{job_id}.json").read_bytes()


# -- success ------------------------------------------------------------------------------------------------------

def test_pass_reviews_the_exact_candidate_and_awaits_merge_approval(rrig: Rig, launchers: Any, github_origin: Path):
    rig = rrig
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})                       # the source forks from dev
    change_on(rig.repo, "dev", {"backend/src/base_only.py": '"""B."""\nB = 2\n'})     # dev moves on (pushed)
    job = rig.approved(request_for(rig.repo, sha))                                      # pinned at the new dev
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    before = snapshot(rig.repo, github_origin)
    job = rig.jobs.load(job.job_id)
    launcher = launchers("pass")
    report = review(rig, job.job_id, launcher)
    assert report.outcome == "reviewed" and report.ok and report.changed, (report.outcome, report.reasons)
    stored = rig.jobs.load(job.job_id)
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and stored.attention is None
    assert history(rig, job.job_id)[-3:] == ["VALIDATING", "IN_REVIEW", "AWAITING_MERGE_APPROVAL"]
    assert kinds(rig, job.job_id)[-3:] == ["review-attempt", "review-run", "review"]
    attempt, run, decision = (of_kind(rig, job.job_id, k)[-1] for k in ("review-attempt", "review-run", "review"))
    candidate = stored.candidate
    for entry in (attempt, run, decision):     # every entry is bound to the candidate's exact head and tree
        assert (entry["head_sha"], entry["tree_sha"]) == (candidate["head_sha"], candidate["tree_sha"])
        assert entry["candidate"] == candidate["evidence"]
    validation = [e for e in stored.evidence if e["kind"] == "validation"][-1]
    assert attempt["validation"] == decision["validation"] == validation["id"]
    assert attempt["source_sha"] == job.source["sha"] and attempt["base_sha"] == job.base["expected_sha"]
    assert decision["outcome"] == "PASS" and decision["run"] == run["id"] and decision["attempt"] == attempt["id"]
    assert run["status"] == "ok" and run["counted"] and run["result_sha256"] and run["output_sha256"]
    assert attempt["reviewer"] == reviewer_identity(rig.config) and attempt["reviewer"]["model"] == "opus"
    assert {k: attempt["reviewer"][k] for k in ("provider", "kind")} == {"provider": "claude", "kind": "claude-code"}
    assert attempt["independence"]["level"] == "unknown" and attempt["independence"]["limitation"]
    assert decision["sha256"] == evidence_digest(decision)
    # the reviewed diff is base_sha → candidate head, NOT base_sha → source: dev's later change is not "removed"
    prompt = seen_prompt(rig, job.job_id)
    assert f"Reviewed head: {candidate['head_sha']}." in prompt and f"Diff base: {job.base['expected_sha']}" in prompt
    assert "backend/src/x.py" in prompt and "base_only.py" not in prompt
    assert git(rig.repo, "diff", "--name-only", job.base["expected_sha"], job.source["sha"]).split() == \
        ["backend/src/base_only.py", "backend/src/x.py"]       # what a base → source review would have shown
    request = json.loads((Path(attempt["run_dir"]) / "request.json").read_text(encoding="utf-8"))
    assert list(request["files"]) == ["backend/src/x.py"] and request["input_sha256"] == \
        attempt["context"]["input_sha256"]
    # M5's view: the review is current for this candidate and the current gate plan
    assert current_review(stored) == decision and reviewed_for(stored, rig.config) == decision
    assert validated(stored)
    summary = job_summary(stored)
    assert summary["reviewed"] and summary["review"]["id"] == decision["id"] and not summary["review_open"]
    # nothing was written to the checkout or origin; the reviewer process got no credential and ran once
    assert snapshot(rig.repo, github_origin) == before
    assert len(launcher.calls) == 1 and report.reviewer_runs == 1
    env = json.loads((Path(attempt["run_dir"]) / "fake-env.json").read_text(encoding="utf-8"))
    assert REVIEW_TAG_ENV in env and not {"GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY"} & set(env)


def test_pass_with_notes_retains_every_non_blocking_finding(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    report = review(rrig, job.job_id, launchers("notes"))
    assert report.outcome == "reviewed" and report.ok
    decision = of_kind(rrig, job.job_id, "review")[-1]
    assert decision["outcome"] == "PASS_WITH_NOTES" and decision["blocking"] == 0
    assert [f["severity"] for f in decision["findings"]] == ["minor", "info"]
    assert decision["findings"] == of_kind(rrig, job.job_id, "review-run")[-1]["findings"]
    assert report.review["findings"] == decision["findings"]
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL
    assert current_review(rrig.jobs.load(job.job_id)) == decision


def test_a_repeated_review_reuses_the_current_evidence(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    launcher = launchers("pass")
    assert review(rrig, job.job_id, launcher).outcome == "reviewed"
    before = raw_record(rrig, job.job_id)
    for apply in (True, False):
        again = review(rrig, job.job_id, launcher, apply=apply)
        assert again.outcome == "reviewed" and again.ok and not again.changed and again.reviewer_runs == 0
        assert again.review["id"] == of_kind(rrig, job.job_id, "review")[-1]["id"]
    assert raw_record(rrig, job.job_id) == before and len(launcher.calls) == 1


def test_an_existing_verified_pr_is_context_and_nothing_is_written_to_github(seeded: Path, state_dir: Path,
                                                                             key: OperatorKey, launchers: Any):
    sha = change_on(seeded, BRANCH, {"backend/src/x.py": X_PY})
    transport = FakeGitHub([pr_json(123, head_sha=sha)])
    rig = Rig(seeded, state_dir, key, client=live_client(transport))
    rig.config = dataclasses.replace(rig.config, runtime=dataclasses.replace(rig.config.runtime,
                                                                             terminate_grace_seconds=2))
    job = rig.approved(request_for(rig.repo, sha, pr_number=123))
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    report = review(rig, job.job_id, launchers("pass"))
    assert report.outcome == "reviewed", (report.outcome, report.reasons)
    assert "Pull request #123 (verified by the coordinator)." in seen_prompt(rig, job.job_id)
    assert transport.writes() == []                       # no PR created, edited or commented on
    assert any(path.endswith("/pulls/123") for _m, path in transport.calls)   # the pinned PR was re-read
    assert rig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


# -- failure ------------------------------------------------------------------------------------------------------

def test_request_changes_moves_the_job_to_needs_repair_and_never_touches_the_source(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    job = validated_job(rrig)
    before = snapshot(rrig.repo, github_origin)
    report = review(rrig, job.job_id, launchers("changes"))
    assert report.outcome == "changes-requested" and not report.ok
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.NEEDS_REPAIR
    decision = of_kind(rrig, job.job_id, "review")[-1]
    assert decision["outcome"] == "REQUEST_CHANGES" and decision["blocking"] == 1
    assert [f["severity"] for f in decision["findings"]] == ["major", "minor"]   # the minor note is retained too
    assert "1 blocking finding" in stored.attention["reason"] and stored.attention["observed"]["review"] == \
        decision["id"]
    assert current_review(stored) is None and reviewed_for(stored, rrig.config) is None
    assert snapshot(rrig.repo, github_origin) == before and head(rrig.repo, BRANCH) == job.source["sha"]
    again = review(rrig, job.job_id, launchers("pass"))
    assert again.outcome == "changes-requested" and not again.changed and again.review["id"] == decision["id"]


def test_invalid_output_with_one_attempt_is_error_and_waits_for_an_operator_retry(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    launcher = launchers("invalid", "pass")
    with pytest.raises(IntegrationError, match="--retry"):        # nothing to retry before an ERROR decision
        review(rrig, job.job_id, launcher, retry={"by": OPERATOR, "reason": "too early"})
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "error" and not report.ok
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.IN_REVIEW and "ERROR" in stored.attention["reason"]
    run, decision = of_kind(rrig, job.job_id, "review-run")[-1], of_kind(rrig, job.job_id, "review")[-1]
    assert run["status"] == "invalid" and run["counted"] and run["outcome"] is None and run["reasons"]
    assert decision["outcome"] == "ERROR" and decision["run"] == run["id"] and decision["findings"] == []
    assert current_review(stored) is None
    # without a retry nothing runs again, also not in a dry run
    before = len(launcher.calls)
    for apply in (True, False):
        again = review(rrig, job.job_id, launcher, apply=apply)
        assert again.outcome == "error" and again.reviewer_runs == 0
    assert len(launcher.calls) == before
    retried = review(rrig, job.job_id, launcher, retry={"by": OPERATOR, "reason": "the reviewer answered badly"})
    assert retried.outcome == "reviewed", (retried.outcome, retried.reasons)
    attempts = of_kind(rrig, job.job_id, "review-attempt")
    assert [a["round"] for a in attempts] == [1, 2] and attempts[1]["retry"] == {
        "by": OPERATOR, "reason": "the reviewer answered badly"} and attempts[0]["retry"] is None
    assert rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


def test_a_contradictory_verdict_is_invalid_and_a_second_attempt_may_pass(rrig: Rig, launchers: Any):
    with_review(rrig, max_attempts=2)
    job = validated_job(rrig)
    launcher = launchers("contradict", "pass")
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "reviewed" and report.reviewer_runs == 2, (report.outcome, report.reasons)
    runs = of_kind(rrig, job.job_id, "review-run")
    assert [r["status"] for r in runs] == ["invalid", "ok"] and "contradicts" in runs[0]["error"]
    assert of_kind(rrig, job.job_id, "review")[-1]["run"] == runs[1]["id"]


@pytest.mark.parametrize("mode, status", [("timeout", "timeout"), ("refused", "refused"), ("failed", "failed"),
                                          ("crash", "interrupted")])
def test_a_reviewer_run_without_an_answer_is_counted_and_ends_the_round_in_error(rrig: Rig, launchers: Any,
                                                                                 mode: str, status: str):
    with_review(rrig, max_attempts=2 if mode != "refused" else 3)
    job = validated_job(rrig)
    launcher = launchers(mode)
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "error", (report.outcome, report.reasons)
    runs = of_kind(rrig, job.job_id, "review-run")
    expected = 1 if mode == "refused" else 2          # a refused run is never retried; others use the budget
    assert [r["status"] for r in runs] == [status] * expected and all(r["counted"] for r in runs)
    assert len(launcher.calls) == expected
    assert of_kind(rrig, job.job_id, "review")[-1]["outcome"] == "ERROR"
    assert rrig.state_of(job.job_id) is JobState.IN_REVIEW


def test_missing_blocked_or_failed_validation_never_starts_a_review(rrig: Rig, launchers: Any):
    launcher = launchers("pass")
    blocked = inspected(rrig, {"backend/src/x.py": X_PY})
    assert rrig.validate(blocked.job_id, gates=FakeGates({"backend-tests": "blocked"})).outcome == "blocked"
    report = review(rrig, blocked.job_id, launcher)
    assert report.outcome == "not-validated" and "is blocked" in report.reasons[0]
    failed = inspected_on(rrig, "integration/failing", {"backend/src/y.py": X_PY})
    assert rrig.validate(failed.job_id, gates=FakeGates({"backend-tests": "failed"})).outcome == "failed"
    report = review(rrig, failed.job_id, launcher)
    assert report.outcome == "not-validated" and "gate FAILED" in report.reasons[0]
    built = inspected_on(rrig, "integration/inspected", {"backend/src/z.py": X_PY})
    with pytest.raises(IntegrationError, match="INSPECTING"):
        review(rrig, built.job_id, launcher)
    assert launcher.calls == [] and rrig.state_of(blocked.job_id) is JobState.VALIDATING
    assert not any(e["kind"].startswith("review") for j in (blocked, failed) for e in rrig.jobs.load(j.job_id).evidence)


def test_a_changed_gate_plan_needs_a_new_validation_before_the_review(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    roles = dict(rrig.config.github.intake.role_gates)
    with_config(rrig, role_gates={**roles, "backend": (*roles["backend"], "frontend-build")})
    launcher = launchers("pass")
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "not-validated" and "gate plan changed" in report.reasons[0] and launcher.calls == []
    assert rrig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert review(rrig, job.job_id, launcher).outcome == "reviewed"
    assert of_kind(rrig, job.job_id, "review")[-1]["validation"] == of_kind(rrig, job.job_id, "validation")[-1]["id"]


def test_an_unavailable_reviewer_uses_no_attempt(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    launcher = launchers("unavailable", "pass")
    report = review(rrig, job.job_id, launcher, preflight="Claude Code is not logged in")
    assert report.outcome == "reviewer-unavailable" and "not logged in" in report.reasons[0]
    assert rrig.state_of(job.job_id) is JobState.VALIDATING and launcher.calls == []
    report = review(rrig, job.job_id, launcher)               # preflight passes; the process itself is unavailable
    assert report.outcome == "reviewer-unavailable"
    run = of_kind(rrig, job.job_id, "review-run")[-1]
    assert run["status"] == "unavailable" and not run["counted"] and rrig.state_of(job.job_id) is JobState.IN_REVIEW
    assert review(rrig, job.job_id, launcher).outcome == "reviewed"     # the given-back attempt used no budget


def test_an_ineligible_reviewer_or_required_independence_refuses(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    launcher = launchers("pass")
    with_review(rrig, provider="codex")
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "reviewer-ineligible" and "Claude Code" in report.reasons[0]
    with_review(rrig, provider="claude", independence="require")
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "independence-refused" and "cannot be established" in report.reasons[0]
    assert launcher.calls == [] and rrig.state_of(job.job_id) is JobState.VALIDATING
    assert not any(e["kind"].startswith("review") for e in rrig.jobs.load(job.job_id).evidence)


def test_a_missing_or_expired_approval_refuses_also_for_a_dry_run(rrig: Rig, launchers: Any, key: OperatorKey):
    job = validated_job(rrig)
    launcher = launchers("pass")
    def later() -> datetime:          # the approval lasts 2 hours
        return datetime.now(timezone.utc) + timedelta(hours=3)
    for apply in (False, True):
        report = review(rrig, job.job_id, launcher, apply=apply, clock=later)
        assert report.outcome == "no-approval" and "expired" in report.reasons[0]
    key.revoke_all()
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "no-approval"
    assert launcher.calls == [] and rrig.state_of(job.job_id) is JobState.VALIDATING


def test_a_diff_above_the_limit_is_never_truncated_and_never_sent(rrig: Rig, launchers: Any):
    big = "".join(f"VALUE_{n} = {n}\n" for n in range(400))
    job = validated_job(rrig, {"backend/src/big.py": big})
    with_review(rrig, max_diff_characters=1000)
    launcher = launchers("pass")
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "unreviewable" and "never truncated" in report.reasons[0] and launcher.calls == []
    assert rrig.state_of(job.job_id) is JobState.VALIDATING


def test_the_change_list_must_be_the_candidates_verified_path_list():
    paths = [{"path": "a.py"}, {"path": "b/c.py"}, {"path": "old.py"}]
    entries = [ChangedFile("M", "a.py"), ChangedFile("R", "b/c.py", "old.py")]
    assert changed_path_problem(entries, paths) == ""
    assert "differs" in changed_path_problem(entries[:1], paths)
    assert "only in Git's list" in changed_path_problem([*entries, ChangedFile("A", "x.py")], paths)


# -- freshness: a change while the reviewer runs ------------------------------------------------------------------

def _no_decision(rig: Rig, job_id: str) -> None:
    assert of_kind(rig, job_id, "review") == [] and current_review(rig.jobs.load(job_id)) is None


@pytest.mark.parametrize("moved", ["source", "base"])
def test_a_ref_that_moves_while_the_reviewer_runs_makes_the_job_stale(rrig: Rig, launchers: Any, moved: str):
    job = validated_job(rrig)
    branch = BRANCH if moved == "source" else "dev"
    launcher = launchers("pass", before=lambda _run: change_on(rrig.repo, branch, {"docs_agents/coordination/x.md":
                                                                                    "# moved\n"}))
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "stale", (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.STALE and f"{moved} branch" in stored.attention["reason"]
    run = of_kind(rrig, job.job_id, "review-run")[-1]
    assert run["status"] == "ok" and run["outcome"] == "PASS"      # the answer stays as history only
    _no_decision(rrig, job.job_id)
    assert history(rrig, job.job_id)[-2:] == ["IN_REVIEW", "STALE"]
    assert not CandidateArea(rrig.state, job.job_id).worktree.exists()


def test_a_pinned_pr_whose_head_moves_while_the_reviewer_runs_makes_the_job_stale(seeded: Path, state_dir: Path,
                                                                                  key: OperatorKey, launchers: Any):
    sha = change_on(seeded, BRANCH, {"backend/src/x.py": X_PY})
    transport = FakeGitHub([pr_json(123, head_sha=sha)])
    rig = Rig(seeded, state_dir, key, client=live_client(transport))
    job = rig.approved(request_for(rig.repo, sha, pr_number=123))
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"

    def move(_run: Path) -> None:
        transport.prs[0]["head"]["sha"] = "f" * 40
    report = review(rig, job.job_id, launchers("pass", before=move))
    assert report.outcome == "stale" and "PR #123" in " ".join(report.reasons), (report.outcome, report.reasons)
    assert rig.state_of(job.job_id) is JobState.STALE and transport.writes() == []
    _no_decision(rig, job.job_id)


def test_a_candidate_that_rebuilds_differently_after_the_review_is_stale(rrig: Rig, launchers: Any,
                                                                         monkeypatch: pytest.MonkeyPatch):
    job = validated_job(rrig)
    real = integration_review.build_candidate

    def other_tree(_run: Path) -> None:
        def build(*args: Any, **kwargs: Any) -> Any:
            built = real(*args, **kwargs)
            return dataclasses.replace(built, tree="e" * 40, head="d" * 40)
        monkeypatch.setattr(integration_review, "build_candidate", build)
    report = review(rrig, job.job_id, launchers("pass", before=other_tree))
    assert report.outcome == "stale" and "candidate identity changed" in report.reasons[0]
    assert rrig.state_of(job.job_id) is JobState.STALE
    _no_decision(rrig, job.job_id)


def test_a_validation_attempt_recorded_while_the_reviewer_runs_blocks_the_decision(rrig: Rig, launchers: Any):
    job = validated_job(rrig)

    def revalidate(_run: Path) -> None:    # another process records a new M3 attempt (it revokes the validation)
        with rrig.store.transaction():
            current = rrig.jobs.load(job.job_id)
            attempt = dict([e for e in current.evidence if e["kind"] == "attempt"][-1])
            attempt.update(id=f"att-{len(current.evidence) + 1:04d}", run=[])
            attempt["sha256"] = evidence_digest(attempt)
            current.evidence.append(attempt)
            rrig.jobs.save(current)
    report = review(rrig, job.job_id, launchers("pass", before=revalidate))
    assert report.outcome == "not-validated" and "is open" in report.reasons[0], (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.IN_REVIEW and not validated(stored)
    assert of_kind(rrig, job.job_id, "review-run")[-1]["status"] == "ok"
    _no_decision(rrig, job.job_id)
    summary = job_summary(stored)
    assert summary["review_pending"] and not summary["reviewed"]


def test_an_approval_revoked_or_expired_while_the_reviewer_runs_records_no_decision(rrig: Rig, launchers: Any,
                                                                                    key: OperatorKey):
    job = validated_job(rrig)
    report = review(rrig, job.job_id, launchers("pass", before=lambda _run: key.revoke_all()))
    assert report.outcome == "no-approval", (report.outcome, report.reasons)
    assert rrig.state_of(job.job_id) is JobState.IN_REVIEW and of_kind(rrig, job.job_id, "review-run")[-1]["outcome"]
    _no_decision(rrig, job.job_id)


def test_an_approval_that_expires_while_the_reviewer_runs_records_no_decision(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    late = {"on": False}

    def clock() -> datetime:
        return datetime.now(timezone.utc) + (timedelta(hours=3) if late["on"] else timedelta())
    report = review(rrig, job.job_id, launchers("pass", before=lambda _run: late.update(on=True)), clock=clock)
    assert report.outcome == "no-approval" and "expired" in " ".join(report.reasons), (report.outcome, report.reasons)
    assert rrig.state_of(job.job_id) is JobState.IN_REVIEW
    _no_decision(rrig, job.job_id)


# -- recovery -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("fault", ["after-attempt", "after-request"])
def test_a_crash_before_the_reviewer_starts_gives_the_attempt_back(rrig: Rig, launchers: Any, fault: str):
    job = validated_job(rrig)
    launcher = launchers("pass")
    with pytest.raises(SimulatedCrash):
        review(rrig, job.job_id, launcher, faults=(fault,))
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.IN_REVIEW and job_summary(stored)["review_open"] and launcher.calls == []
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "reviewed", (report.outcome, report.reasons)
    runs = of_kind(rrig, job.job_id, "review-run")
    assert [(r["status"], r["counted"]) for r in runs] == [("not-started", False), ("ok", True)]
    assert len(launcher.calls) == 1


def test_a_crash_while_the_reviewer_runs_waits_for_it_and_never_starts_a_second(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    launcher = launchers("slow:1.5")
    with pytest.raises(SimulatedCrash):
        review(rrig, job.job_id, launcher, faults=("while-waiting",))
    attempt = of_kind(rrig, job.job_id, "review-attempt")[-1]
    assert tagged_processes(REVIEW_TAG_ENV, attempt["tag"])           # the reviewer survived the crash
    dry = review(rrig, job.job_id, launcher, apply=False)
    assert dry.outcome == "would-recover" and "running" in " ".join(dry.messages)
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "reviewed" and len(launcher.calls) == 1, (report.outcome, report.reasons)
    assert [r["status"] for r in of_kind(rrig, job.job_id, "review-run")] == ["ok"]


def test_a_crash_after_the_reviewer_answered_collects_its_result_without_a_new_run(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    launcher = launchers("notes")
    with pytest.raises(SimulatedCrash):
        review(rrig, job.job_id, launcher, faults=("before-collect",))
    attempt = of_kind(rrig, job.job_id, "review-attempt")[-1]
    assert result_path(attempt["run_dir"]).is_file() and of_kind(rrig, job.job_id, "review-run") == []
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "reviewed" and len(launcher.calls) == 1 and report.reviewer_runs == 0
    assert of_kind(rrig, job.job_id, "review")[-1]["outcome"] == "PASS_WITH_NOTES"


@pytest.mark.parametrize("fault", ["after-run", "before-decision"])
def test_a_crash_after_the_run_is_recorded_decides_without_a_new_run(rrig: Rig, launchers: Any, fault: str):
    job = validated_job(rrig)
    launcher = launchers("pass")
    with pytest.raises(SimulatedCrash):
        review(rrig, job.job_id, launcher, faults=(fault,))
    stored = rrig.jobs.load(job.job_id)
    assert job_summary(stored)["review_pending"] and stored.state is JobState.IN_REVIEW
    assert review(rrig, job.job_id, launcher, apply=False).outcome == "would-decide"
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "reviewed" and len(launcher.calls) == 1 and report.reviewer_runs == 0


def test_concurrent_reviews_of_one_job_start_one_reviewer(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    lock = FileLock(rrig.store.locks.dir / f"integration-{job.job_id}.lock")
    lock.acquire(timeout=0)
    try:
        with pytest.raises(IntegrationError, match="another integration operation"):
            review(rrig, job.job_id, launchers("pass"))
    finally:
        lock.release()
    launcher = launchers("slow:1")
    outcomes: list[str] = []

    def one() -> None:
        try:
            outcomes.append(review(rrig, job.job_id, launcher).outcome)
        except IntegrationError as exc:
            outcomes.append(f"refused: {exc}")
    threads = [threading.Thread(target=one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(o.split(":")[0] for o in outcomes) == ["refused", "reviewed"], outcomes
    assert len(launcher.calls) == 1 and len(of_kind(rrig, job.job_id, "review-attempt")) == 1


def test_an_operator_abort_while_the_reviewer_runs_stops_it_and_records_nothing_more(rrig: Rig, launchers: Any):
    job = validated_job(rrig)

    def abort_soon(_run: Path) -> None:
        def later() -> None:
            time.sleep(0.8)
            abort_job(rrig.jobs, job.job_id, "operator stops the review", actor=OPERATOR, apply=True)
        threading.Thread(target=later, daemon=True).start()
    launcher = launchers("hang", before=abort_soon)
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "changed-meanwhile", (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.ABORTED and of_kind(rrig, job.job_id, "review-run") == []
    assert not tagged_processes(REVIEW_TAG_ENV, launcher.tags[0])
    assert not CandidateArea(rrig.state, job.job_id).repo.exists()


def test_the_attempts_deadline_stops_the_reviewer_and_counts_a_timeout(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    offset = {"seconds": 0.0}

    def overdue_soon(_run: Path) -> None:        # once the CLI has started (its output file exists)
        threading.Timer(0.8, lambda: offset.update(seconds=100_000.0)).start()
    launcher = launchers("hang", before=overdue_soon)
    report = review(rrig, job.job_id, launcher, wall=lambda: time.time() + offset["seconds"])
    assert report.outcome == "error", (report.outcome, report.reasons)
    run = of_kind(rrig, job.job_id, "review-run")[-1]
    assert run["status"] == "timeout" and run["counted"] and not tagged_processes(REVIEW_TAG_ENV, launcher.tags[0])


def test_a_stop_signal_cancels_the_reviewer_and_gives_the_attempt_back(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    stop = {"on": False}

    def stop_soon(_run: Path) -> None:
        threading.Timer(0.8, lambda: stop.update(on=True)).start()
    launcher = launchers("hang", before=stop_soon)
    report = review(rrig, job.job_id, launcher, stop=lambda: stop["on"])
    assert report.outcome == "cancelled", (report.outcome, report.reasons)
    run = of_kind(rrig, job.job_id, "review-run")[-1]
    assert run["status"] == "cancelled" and not run["counted"]
    assert rrig.state_of(job.job_id) is JobState.IN_REVIEW
    assert review(rrig, job.job_id, launchers("pass")).outcome == "reviewed"


def test_the_reviewer_process_runs_only_for_the_open_attempt_with_its_tag(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    with pytest.raises(SimulatedCrash):
        review(rrig, job.job_id, launchers("pass"), faults=("after-request",))
    attempt = of_kind(rrig, job.job_id, "review-attempt")[-1]
    run_dir = Path(attempt["run_dir"])
    assert attempt_problem(rrig.jobs, job.job_id, run_dir, {REVIEW_TAG_ENV: attempt["tag"]}) == ""
    assert "not" in attempt_problem(rrig.jobs, job.job_id, run_dir, {REVIEW_TAG_ENV: "review-" + "0" * 16})
    assert "not" in attempt_problem(rrig.jobs, job.job_id, run_dir.parent / "elsewhere",
                                    {REVIEW_TAG_ENV: attempt["tag"]})
    assert "cannot be read" in attempt_problem(rrig.jobs, "int-20990101-0001", run_dir, {})


def test_the_workers_pause_never_cancels_an_integration_review(rrig: Rig, launchers: Any):
    from feature_coordinator.autonomy import write_control  # pylint: disable=import-outside-toplevel
    write_control(rrig.store, pause=True, actor="operator")          # the production Worker stays PAUSED
    job = validated_job(rrig)
    assert review(rrig, job.job_id, launchers("slow:1.2")).outcome == "reviewed"


# -- the record: SHA-bound, append-only, strictly validated ---------------------------------------------------------

def _tamper(rig: Rig, job_id: str, change: Callable[[list[dict[str, Any]]], None], *, digest: bool = True) -> None:
    path = rig.state / "integration" / f"{job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data["evidence"])
    if digest:
        for entry in data["evidence"]:
            entry["sha256"] = evidence_digest(entry)
    path.write_text(json.dumps(data), encoding="utf-8")


def _last(kind: str) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
    return lambda evidence: [e for e in evidence if e["kind"] == kind][-1]


@pytest.mark.parametrize("name, change, message", [
    ("decision of another head", lambda ev: _last("review")(ev).update(head_sha="a" * 40), "head and tree SHAs"),
    ("run outcome not its answer's", lambda ev: _last("review-run")(ev).update(outcome="PASS_WITH_NOTES"),
     "is not what its answer"),
    ("decision carries another answer", lambda ev: _last("review")(ev)["findings"].append(
        {"severity": "minor", "file": "backend/src/x.py", "line_start": None, "line_end": None, "title": "t",
         "explanation": "e", "recommendation": ""}), "exactly the answer"),
    ("decision of another validation", lambda ev: _last("review")(ev).update(validation="val-0001"), "differ from"),
    ("attempt relies on no passed validation", lambda ev: _last("review-attempt")(ev).update(validation="att-0003"),
     "PASSED validation"),
    ("required independence that was not met", lambda ev: _last("review-attempt")(ev)["independence"].update(
        mode="require"), "was not met"),
    ("an ok run without an answer", lambda ev: _last("review-run")(ev).update(status="invalid"), "an `ok` run"),
    ("an attempt after a PASS decision", lambda ev: ev.append({**_last("review-attempt")(ev), "id":
                                                               f"rva-{len(ev) + 1:04d}"}), "follows the PASS"),
])
def test_inconsistent_review_evidence_is_refused_on_load(rrig: Rig, launchers: Any, name: str,
                                                          change: Callable[[list[dict[str, Any]]], None],
                                                          message: str):
    job = validated_job(rrig)
    assert review(rrig, job.job_id, launchers("pass")).outcome == "reviewed"
    _tamper(rrig, job.job_id, change)
    with pytest.raises(StateDirError, match=message):
        rrig.jobs.load(job.job_id)


def test_review_evidence_is_digest_bound_and_append_only(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    assert review(rrig, job.job_id, launchers("pass")).outcome == "reviewed"
    stored = rrig.jobs.load(job.job_id)
    stored.evidence[-1] = {**stored.evidence[-1], "summary": "rewritten"}
    with pytest.raises(IntegrationError, match="append-only|SHA-256"):
        with rrig.store.transaction():
            rrig.jobs.save(stored)
    _tamper(rrig, job.job_id, lambda ev: _last("review")(ev).update(summary="rewritten"), digest=False)
    with pytest.raises(StateDirError, match="SHA-256 does not match"):
        rrig.jobs.load(job.job_id)


# -- the CLI ------------------------------------------------------------------------------------------------------

FAKE_CLAUDE = r'''#!{python}
import json, os, re, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.318 (Claude Code)")
elif args == ["--help"]:
    print(" ".join({flags!r}))
elif args[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                      "subscriptionType": "pro"}}))
else:
    prompt = sys.stdin.read()
    with open({record!r}, "w") as handle:
        json.dump({{"argv": args, "env": dict(os.environ), "cwd": os.getcwd(), "prompt": prompt}}, handle)
    head = re.search(r"Reviewed head: ([0-9a-f]{{40}})", prompt).group(1)
    answer = {{"verdict": "pass", "summary": "fine", "head_sha": head, "findings": [], "limitations": []}}
    print(json.dumps({{"type": "system", "subtype": "init", "model": "claude-opus-5-5", "apiKeySource": "none",
                      "tools": [], "mcp_servers": [], "permissionMode": "dontAsk"}}))
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "done",
                      "structured_output": answer}}))
'''


def test_end_to_end_intake_inspect_validate_review_through_the_cli(rig: Rig, key: OperatorKey, tmp_path: Path,
                                                                   github_origin: Path, needs_bwrap: None,
                                                                   capsys: pytest.CaptureFixture[str]):
    """M1 intake → M2 inspection → M3 validation (the real gate sandbox) → M4 review: the real `integration
    review-exec` process runs the real ClaudeCodeReviewer against a FAKE `claude` executable. No PR is created, no
    branch is pushed and the checkout is unchanged."""
    from feature_coordinator.launchers import REQUIRED_FLAGS  # pylint: disable=import-outside-toplevel
    from feature_coordinator.providers import ProviderKind  # pylint: disable=import-outside-toplevel
    record = tmp_path / "fake-claude-run.json"
    cli = tmp_path / "bin" / "claude-fake"
    cli.parent.mkdir()
    cli.write_text(FAKE_CLAUDE.format(python=sys.executable, flags=list(REQUIRED_FLAGS[ProviderKind.CLAUDE_CODE]),
                                      record=str(record)), encoding="utf-8")
    cli.chmod(0o755)
    config_path = _cli_config(tmp_path, key)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["providers"]["claude"]["executable"] = str(cli)
    data.setdefault("runtime", {})["terminate_grace_seconds"] = 2
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    tracker_file = rig.state / "tracker.json"
    MockIssueTracker(tracker_file).create_issue("integrate x", "why", ["feature:test"])   # Issue #1
    base = ["--tracker", "local", "--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config",
            str(config_path), "integration"]
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    assert cli_main([*base, "intake", "--source-repo", REPOSITORY, "--source-ref", BRANCH, "--source-sha", sha,
                     "--base", "dev", "--base-sha", head(rig.repo), "--issue", "1", "--apply", "--json"]) == 0
    job_id = json.loads(capsys.readouterr().out)["job"]["job_id"]
    approval = rig.write_record(record_for(rig.jobs.load(job_id)))
    assert cli_main([*base, "approve", "--job", job_id, "--stage", "inspect", "--record", str(approval),
                     "--apply"]) == 0
    assert cli_main([*base, "inspect", "--job", job_id, "--apply"]) == 0
    assert cli_main([*base, "validate", "--job", job_id, "--apply"]) == 0
    capsys.readouterr()
    before, tracked = snapshot(rig.repo, github_origin), tracker_file.read_bytes()
    assert cli_main([*base, "review", "--job", job_id]) == 0                       # dry run
    out = capsys.readouterr().out
    assert "review: would-review" in out and "reviewer: claude / opus" in out and "dry run" in out
    assert not record.exists()                                                       # no reviewer ran
    assert cli_main([*base, "review", "--job", job_id, "--apply", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["outcome"] == "reviewed" and result["job"]["state"] == "AWAITING_MERGE_APPROVAL"
    assert result["review"]["outcome"] == "PASS" and result["reviewer_runs"] == 1
    seen = json.loads(record.read_text(encoding="utf-8"))
    candidate = result["job"]["candidate"]
    assert f"Reviewed head: {candidate['head_sha']}." in seen["prompt"]
    assert seen["env"][REVIEW_TAG_ENV] and not {"GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY"} & set(seen["env"])
    assert seen["argv"][seen["argv"].index("--tools") + 1] == "" and "--strict-mcp-config" in seen["argv"]
    # repeated status inspection is read-only
    stored_before = raw_record(rig, job_id)
    for _ in range(3):
        assert cli_main([*base, "status", "--job", job_id]) == 0
    out = capsys.readouterr().out
    assert "(CURRENT: valid for this candidate)" in out and "merge approval and merge (M5" in out
    assert f"candidate {candidate['head_sha']}" in out and "independence: unknown (prefer)" in out
    assert raw_record(rig, job_id) == stored_before
    # no PR, no push, no checkout change; the reviewer process's own exec is refused outside its attempt
    assert snapshot(rig.repo, github_origin) == before and tracker_file.read_bytes() == tracked
    assert cli_main([*base, "review", "--job", job_id, "--apply"]) == 0               # reused: nothing ran again
    assert "review: reviewed (unchanged)" in capsys.readouterr().out
    assert len(of_kind(rig, job_id, "review-attempt")) == 1


def test_cli_review_errors_and_arguments(rig: Rig, key: OperatorKey, tmp_path: Path,
                                         capsys: pytest.CaptureFixture[str]):
    base = ["--tracker", "local", "--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config",
            str(_cli_config(tmp_path, key)), "integration"]
    assert cli_main([*base, "review", "--job", "int-20990101-0001", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == {"type": "IntegrationError",
                                                            "message": "unknown integration job 'int-20990101-0001'"}
    assert cli_main([*base, "review", "--job", "int-20990101-0001", "--retry", "--json"]) == 1
    assert "--retry needs --reason" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_review_exec_refuses_without_an_open_attempt(rig: Rig, key: OperatorKey, tmp_path: Path):
    run_dir = rig.state / "runs" / "review" / "int-20990101-0001" / "attempt-x"
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text(json.dumps({"feature": "int-20990101-0001"}), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "feature_coordinator", "--repo", str(rig.repo), "--state-dir",
                           str(rig.state), "--project-config", str(_cli_config(tmp_path, key)), "integration",
                           "review-exec", str(run_dir)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(REPO_ROOT), REVIEW_TAG_ENV: "review-" + "1" * 16},
                          check=False)
    assert proc.returncode == 1 and "not a review request" in proc.stderr and not result_path(run_dir).exists()


def test_the_review_git_reader_is_isolated_and_read_only(tmp_path: Path):
    reader = ReviewGit(tmp_path / "home", timeout=10)
    for command in ("merge-tree", "update-index", "push", "fetch", "config", "checkout"):
        with pytest.raises(Exception, match="not an allowed command"):
            reader.argv(tmp_path, command)
    argv = reader.argv(tmp_path, "diff", "a", "b")
    assert argv[1] == f"--git-dir={tmp_path}" and "core.attributesFile=/dev/null" in argv \
        and "core.hooksPath=/dev/null" in argv and "--no-replace-objects" in argv
    assert reader.env()["GIT_CONFIG_NOSYSTEM"] == "1" and reader.env()["GIT_CONFIG_GLOBAL"] == "/dev/null"


def test_an_orphaned_reviewer_cancels_itself_when_the_operator_aborts(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    launcher = launchers("hang")
    with pytest.raises(SimulatedCrash):                     # the waiting command crashes; the reviewer runs on
        review(rrig, job.job_id, launcher, faults=("while-waiting",))
    attempt = of_kind(rrig, job.job_id, "review-attempt")[-1]
    assert tagged_processes(REVIEW_TAG_ENV, attempt["tag"])
    abort_job(rrig.jobs, job.job_id, "operator stops it", actor=OPERATOR, apply=True)
    deadline = time.monotonic() + 20
    while tagged_processes(REVIEW_TAG_ENV, attempt["tag"]) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not tagged_processes(REVIEW_TAG_ENV, attempt["tag"])      # it read the job record and stopped
    result = json.loads(result_path(attempt["run_dir"]).read_text(encoding="utf-8"))
    assert result["status"] == "cancelled" and rrig.state_of(job.job_id) is JobState.ABORTED
    assert review(rrig, job.job_id, launcher).outcome == "terminal"


def test_a_changed_file_whose_stored_content_does_not_hash_to_its_id_is_never_reviewed(rrig: Rig, launchers: Any):
    job = validated_job(rrig, {"backend/src/x.py": X_PY})
    blob = git(rrig.repo, "rev-parse", f"{job.source['sha']}:backend/src/x.py").strip()
    loose = rrig.repo / ".git" / "objects" / blob[:2] / blob[2:]
    assert loose.is_file()
    import zlib  # pylint: disable=import-outside-toplevel
    fake = b'"""Module x."""\nX = 2  # benign-looking\n'
    loose.chmod(0o644)
    loose.write_bytes(zlib.compress(b"blob %d\0" % len(fake) + fake))   # other content under the same ID
    launcher = launchers("pass")
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "unreviewable" and "does not hash to its ID" in report.reasons[0], report.reasons
    assert launcher.calls == [] and rrig.state_of(job.job_id) is JobState.VALIDATING


def test_a_decided_review_stops_applying_once_its_validation_or_plan_changes(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    assert review(rrig, job.job_id, launchers("pass")).outcome == "reviewed"
    stored = rrig.jobs.load(job.job_id)
    decision = current_review(stored)
    assert decision is not None and reviewed_for(stored, rrig.config) == decision
    roles = dict(rrig.config.github.intake.role_gates)
    intake = dataclasses.replace(rrig.config.github.intake,
                                 role_gates={**roles, "backend": (*roles["backend"], "frontend-build")})
    changed = dataclasses.replace(rrig.config, github=dataclasses.replace(rrig.config.github, intake=intake))
    assert current_review(stored) == decision and reviewed_for(stored, changed) is None    # another gate plan
    attempt = dict([e for e in stored.evidence if e["kind"] == "attempt"][-1])
    attempt.update(id=f"att-{len(stored.evidence) + 1:04d}", run=[])
    attempt["sha256"] = evidence_digest(attempt)
    stored.evidence.append(attempt)                     # a new validation attempt revokes the validation it relied on
    assert not validated(stored) and current_review(stored) is None and reviewed_for(stored, rrig.config) is None


def test_the_reviewer_process_refuses_an_attempt_that_is_no_longer_open(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    with pytest.raises(SimulatedCrash):
        review(rrig, job.job_id, launchers("pass"), faults=("after-request",))     # request written, never started
    attempt = of_kind(rrig, job.job_id, "review-attempt")[-1]
    abort_job(rrig.jobs, job.job_id, "operator stops it", actor=OPERATOR, apply=True)
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), REVIEW_TAG_ENV: attempt["tag"], "FAKE_REVIEW_MODE": "pass",
           "FAKE_STATE_DIR": str(rrig.state)}
    proc = subprocess.run([sys.executable, str(FAKE_PROCESS), attempt["run_dir"]], env=env, cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(result_path(attempt["run_dir"]).read_text(encoding="utf-8"))
    assert result["status"] == "cancelled" and "without an open review attempt" in result["error"]
    assert not (Path(attempt["run_dir"]) / "seen-prompt.txt").exists()       # the reviewer never ran


def test_a_change_list_that_is_not_the_verified_path_list_is_never_reviewed(rrig: Rig, launchers: Any,
                                                                          monkeypatch: pytest.MonkeyPatch):
    from feature_coordinator import review_context  # pylint: disable=import-outside-toplevel
    job = validated_job(rrig)
    real = review_context.changed_entries
    monkeypatch.setattr(review_context, "changed_entries",
                        lambda *args: [*real(*args), ChangedFile("A", "backend/src/hidden.py")])
    launcher = launchers("pass")
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "unreviewable" and "verified path list" in report.reasons[0], report.reasons
    assert launcher.calls == [] and rrig.state_of(job.job_id) is JobState.VALIDATING


def test_an_approval_that_expires_inside_the_decision_guard_records_nothing(rrig: Rig, launchers: Any,
                                                                            monkeypatch: pytest.MonkeyPatch):
    job = validated_job(rrig)
    real = integration_review.effective_approval_problems

    def checked_just_before(*args: Any, **kwargs: Any) -> Any:      # the signature check still saw it valid
        return real(*args, **{**kwargs, "now": datetime.now(timezone.utc)})
    monkeypatch.setattr(integration_review, "effective_approval_problems", checked_just_before)

    def expired() -> datetime:             # ... but at the decision point (fresh clock) it has expired
        return datetime.now(timezone.utc) + timedelta(hours=3)
    launcher = launchers("pass")
    report = review(rrig, job.job_id, launcher, clock=expired)
    assert report.outcome == "no-approval", (report.outcome, report.reasons)
    assert launcher.calls == [] and of_kind(rrig, job.job_id, "review-attempt") == []
    assert rrig.state_of(job.job_id) is JobState.VALIDATING


def test_the_waiting_run_itself_stops_a_reviewer_that_ignores_the_abort(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    offset = {"seconds": 0.0}

    def abort_soon(_run: Path) -> None:
        def later() -> None:
            time.sleep(0.8)
            abort_job(rrig.jobs, job.job_id, "operator stops the review", actor=OPERATOR, apply=True)
        threading.Thread(target=later, daemon=True).start()
        threading.Timer(20, lambda: offset.update(seconds=100_000.0)).start()   # safety net: the deadline
    launcher = launchers("deaf", before=abort_soon)
    started = time.monotonic()
    report = review(rrig, job.job_id, launcher, wall=lambda: time.time() + offset["seconds"])
    assert report.outcome == "changed-meanwhile" and time.monotonic() - started < 15, report.outcome
    assert not tagged_processes(REVIEW_TAG_ENV, launcher.tags[0])
