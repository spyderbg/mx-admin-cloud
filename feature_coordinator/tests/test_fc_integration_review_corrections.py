"""W4.7 (M4): Codex functional corrections F1–F4 — regression tests for the four defects the independent Codex
functional review of M4 verified (verdict REQUEST_CHANGES). Each one reproduces the original failure, checks that the
correction prevents it, covers its recovery or reuse path and that the behaviour which already passed is unchanged.

  F1  a source branch, base branch or recorded PR that moved during the last checks before a record (the approval's
      signature verification, after the candidate was rebuilt and authorized) was recorded as a PASS → now STALE and
      nothing is recorded; an unreadable pin at that point defers and never becomes a PASS.
  F2  `github.review.independence` changed to `require` after an attempt started under `prefer` was bypassed by the
      pending answer, by a completed review and by reviewed_for → now `independence-refused`, the history unchanged.
  F3  a reviewer configuration changed between the attempt's record and the reviewer process ran another model under
      the attempt's identity → the reviewer process refuses to run; a run whose facts name another model is refused.
  F4  the schema accepted a review attempt relying on a validation that a newer M3 attempt or gate had revoked.

The SECOND Codex functional review (REQUEST_CHANGES; F1, F3, F4 and F2's recovery/reuse verified fixed) found two more:

  RR1 the reviewer process checked its identity but not its CURRENT independence policy: `require` set after the
      attempt was recorded under `prefer` → it ran and PASS was recorded; a `require` set while it runs → decided →
      now the process does not run (`cancelled`, given back), and the decision reads the policy in effect then.
  RR2 `integration status` showed a PASS the current policy refuses as CURRENT / `reviewed: true` → now `reviewed` is
      the eligibility under the current configuration (reviewed_for); the review stays shown as history.

The rig of test_fc_integration_review.py: real reviewer OS processes with a scripted reviewer, and the real CLI
(`integration review` → `integration review-exec` → ClaudeCodeReviewer) with a fake `claude` executable. No model, no
paid request, no real GitHub, no network, no production Worker."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,unused-import

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from feature_coordinator import worker
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import load_config
from feature_coordinator.errors import ConfigError, StateDirError
from feature_coordinator.integration import IntegrationJob, JobState, current_review, \
    evidence_digest, job_summary, validated
from feature_coordinator.integration_inspect import InspectionResult
from feature_coordinator.integration_commands import cmd_status
from feature_coordinator.integration_review import reviewed_for
from feature_coordinator.integration_review_context import independence_problem, integration_independence, \
    policy_problem, reviewer_identity
from feature_coordinator.integration_review_process import attempt_problem, execution_problem
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.issues import MockIssueTracker
from feature_coordinator.launchers import REQUIRED_FLAGS
from feature_coordinator.providers import ProviderKind
from feature_coordinator.review_process import REVIEW_TAG_ENV

from fc_integration_rig import BRANCH, FakeGitHub, OperatorKey, Rig, change_on, github_origin, live_client, \
    open_issue, origin, pr_json, request_for
from test_fc_integration_review import FAKE_CLAUDE, X_PY, FakeGates, _cli_config, history, launchers, of_kind, \
    raw_record, review, reviewer, rrig, validated_job, with_review
from test_fc_integration_validate import key, rig, seeded, tracker


# == Codex functional corrections F1–F4 ===========================================================================

# -- F1: the pinned identities are read again immediately before every record --------------------------------------

def _pr_job(rig: Rig) -> tuple[IntegrationJob, FakeGitHub]:
    """A validated job whose source has an open PR #123 (the scripted GitHub transport behind the real client)."""
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    transport = FakeGitHub([pr_json(123, head_sha=sha)])
    rig.client = live_client(transport)
    job = rig.approved(request_for(rig.repo, sha, pr_number=123))
    assert rig.inspect(job.job_id).outcome == "passed"
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    return rig.jobs.load(job.job_id), transport


def _move(rig: Rig, moved: str, transport: FakeGitHub | None) -> None:
    if moved == "pr":
        assert transport is not None
        transport.prs[0]["head"]["sha"] = "f" * 40
    else:
        change_on(rig.repo, BRANCH if moved == "source" else "dev",
                  {"docs_agents/coordination/moved.md": "# moved during the final checks\n"})


@pytest.mark.parametrize("record", ["attempt", "decision"])
@pytest.mark.parametrize("moved", ["source", "base", "pr"])
def test_f1_a_pin_that_moves_during_the_final_signature_check_is_stale(rrig: Rig, launchers: Any,
                                                                       monkeypatch: pytest.MonkeyPatch, moved: str,
                                                                       record: str):
    """Codex F1 (integration_review.py:394, :831): the move happens INSIDE the approval's signature verification — the
    last check before the record, after _verify read the pins, rebuilt and authorized the candidate. Before the
    correction this recorded PASS → AWAITING_MERGE_APPROVAL (`reviewed`)."""
    job, transport = _pr_job(rrig) if moved == "pr" else (validated_job(rrig), None)
    launcher = launchers("pass")
    runner = reviewer(rrig, launcher)
    real = runner._approval_confirmed
    moves: list[str] = []

    def confirm_then_move(job_id: str, approval: dict[str, Any], report: Any) -> str:
        verified = real(job_id, approval, report)
        due = not of_kind(rrig, job_id, "review-attempt") if record == "attempt" else \
            bool(of_kind(rrig, job_id, "review-run"))
        if due and not moves:
            _move(rrig, moved, transport)
            moves.append(record)
        return verified
    monkeypatch.setattr(runner, "_approval_confirmed", confirm_then_move)
    report = runner.run(job.job_id, apply=True)
    stored = rrig.jobs.load(job.job_id)
    assert moves == [record] and report.outcome == "stale" and not report.ok, (report.outcome, report.reasons)
    assert any(reason.startswith(f"before the review {record} is recorded") for reason in report.reasons)
    assert stored.state is JobState.STALE and "AWAITING_MERGE_APPROVAL" not in history(rrig, job.job_id)
    assert not of_kind(rrig, job.job_id, "review") and current_review(stored) is None
    assert reviewed_for(stored, rrig.config) is None
    if record == "attempt":
        assert not of_kind(rrig, job.job_id, "review-attempt") and launcher.calls == []
        assert history(rrig, job.job_id)[-2:] == ["VALIDATING", "STALE"]
    else:
        assert of_kind(rrig, job.job_id, "review-run")[-1]["outcome"] == "PASS"    # the answer stays history only
        assert len(launcher.calls) == 1 and history(rrig, job.job_id)[-2:] == ["IN_REVIEW", "STALE"]
    if transport is not None:
        assert transport.writes() == []


@pytest.mark.parametrize("record", ["attempt", "decision"])
def test_f1_an_unreadable_pin_at_the_final_check_records_nothing_and_never_passes(rrig: Rig, launchers: Any,
                                                                                  monkeypatch: pytest.MonkeyPatch,
                                                                                  record: str):
    job = validated_job(rrig)
    launcher = launchers("pass")
    runner = reviewer(rrig, launcher)
    real = runner.inspector.check_pinned
    calls: list[int] = []

    def unreadable_at_the_final_check(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)     # the pins are read: 1 _verify, 2 before the attempt, 3 _verify again, 4 before the decision
        if len(calls) == (2 if record == "attempt" else 4):
            result = InspectionResult()
            result.defer("refs", "simulated: origin could not be read")
            return result
        return real(*args, **kwargs)
    monkeypatch.setattr(runner.inspector, "check_pinned", unreadable_at_the_final_check)
    report = runner.run(job.job_id, apply=True)
    stored = rrig.jobs.load(job.job_id)
    assert report.outcome == "deferred" and "could not be read" in report.reasons[0], (report.outcome, report.reasons)
    assert not of_kind(rrig, job.job_id, "review") and reviewed_for(stored, rrig.config) is None
    if record == "attempt":
        assert stored.state is JobState.VALIDATING and launcher.calls == []
        assert not of_kind(rrig, job.job_id, "review-attempt")
    else:
        assert stored.state is JobState.IN_REVIEW and job_summary(stored)["review_pending"]
    again = review(rrig, job.job_id, launcher)             # readable again: reviewed, the answer not asked for twice
    assert again.outcome == "reviewed" and len(launcher.calls) == 1


# -- F2: the CURRENT independence policy decides wherever a review can become authoritative ------------------------

@pytest.mark.parametrize("stage, mode", [("after-run", "pass"), ("after-start", "pass"), ("after-run", "changes")])
def test_f2_an_answer_started_under_prefer_is_not_decided_once_the_policy_requires_independence(
        rrig: Rig, launchers: Any, stage: str, mode: str):
    """Codex F2 (integration_review.py:582): start under `prefer`, the run recorded (after-run) or the attempt
    interrupted while its reviewer runs (after-start), the configuration changed to `require`, resume. Before the
    correction the resumed run recorded PASS (`reviewed`)."""
    job = validated_job(rrig)
    launcher = launchers(mode)
    with pytest.raises(SimulatedCrash):
        review(rrig, job.job_id, launcher, faults=(stage,))
    launcher.wait()
    assert of_kind(rrig, job.job_id, "review-attempt")[-1]["independence"]["mode"] == "prefer"
    with_review(rrig, independence="require")
    dry = review(rrig, job.job_id, launcher, apply=False)
    assert dry.outcome == ("would-recover" if stage == "after-start" else "independence-refused")
    report = review(rrig, job.job_id, launcher)
    stored = rrig.jobs.load(job.job_id)
    assert report.outcome == "independence-refused" and not report.ok and "cannot be established" in report.reasons[0]
    assert stored.state is JobState.IN_REVIEW and len(launcher.calls) == 1 and report.reviewer_runs == 0
    assert not of_kind(rrig, job.job_id, "review") and reviewed_for(stored, rrig.config) is None
    run = of_kind(rrig, job.job_id, "review-run")[-1]
    assert run["status"] == "ok" and job_summary(stored)["review_pending"]           # the answer is kept as history
    kept = stored.to_dict()["evidence"]
    assert review(rrig, job.job_id, launcher).outcome == "independence-refused"     # stable, nothing more recorded
    assert rrig.jobs.load(job.job_id).to_dict()["evidence"] == kept
    with_review(rrig, independence="prefer")       # a compatible policy again: decided without another reviewer run
    report = review(rrig, job.job_id, launcher)
    stored = rrig.jobs.load(job.job_id)
    assert len(launcher.calls) == 1 and report.reviewer_runs == 0 and stored.to_dict()["evidence"][:len(kept)] == kept
    if mode == "changes":
        assert report.outcome == "changes-requested" and stored.state is JobState.NEEDS_REPAIR
    else:
        assert report.outcome == "reviewed" and stored.state is JobState.AWAITING_MERGE_APPROVAL
        assert reviewed_for(stored, rrig.config) == current_review(stored)


@pytest.mark.parametrize("mode, outcome", [("pass", "PASS"), ("notes", "PASS_WITH_NOTES")])
def test_f2_a_completed_review_is_neither_reused_nor_exposed_once_the_policy_requires_independence(
        rrig: Rig, launchers: Any, mode: str, outcome: str):
    """Codex F2 (integration_review.py:939): a PASS / PASS_WITH_NOTES decided under `prefer` was still reused by a
    repeated review and returned by reviewed_for after the policy became `require`."""
    job = validated_job(rrig)
    launcher = launchers(mode)
    assert review(rrig, job.job_id, launcher).outcome == "reviewed"
    stored = rrig.jobs.load(job.job_id)
    decision = current_review(stored)
    assert decision is not None and decision["outcome"] == outcome and reviewed_for(stored, rrig.config) == decision
    before = raw_record(rrig, job.job_id)
    with_review(rrig, independence="require")
    for apply in (False, True):
        report = review(rrig, job.job_id, launcher, apply=apply)
        assert report.outcome == "independence-refused" and not report.ok and report.review["id"] == decision["id"]
        assert "cannot be established" in report.reasons[0]
    stored = rrig.jobs.load(job.job_id)
    assert raw_record(rrig, job.job_id) == before                      # the history is untouched, no new edge
    assert stored.state is JobState.AWAITING_MERGE_APPROVAL and current_review(stored) == decision
    assert reviewed_for(stored, rrig.config) is None and len(launcher.calls) == 1
    with_review(rrig, independence="prefer")   # the documented reuse rule: the unchanged record applies again
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "reviewed" and report.review["id"] == decision["id"] and len(launcher.calls) == 1
    assert reviewed_for(rrig.jobs.load(job.job_id), rrig.config) == decision and raw_record(rrig, job.job_id) == before


def test_f2_the_policy_check_never_upgrades_a_recorded_level(rrig: Rig):
    recorded = {"mode": "prefer", "level": "unknown", "author": "", "session": "", "limitation": "x"}
    assert independence_problem(rrig.config) == "" and independence_problem(rrig.config, recorded) == ""
    with_review(rrig, independence="require")
    assert "cannot be established (level unknown" in independence_problem(rrig.config)
    assert "level unknown" in independence_problem(rrig.config, recorded)
    assert "level none" in independence_problem(rrig.config, {**recorded, "level": "none"})
    assert independence_problem(rrig.config, {**recorded, "level": "provider"}) == ""


# -- F3: the reviewer process runs only as the attempt's recorded reviewer; its run facts must agree ----------------

def test_f3_a_reviewer_process_whose_model_changed_before_it_started_does_not_run(rrig: Rig, launchers: Any):
    """Codex F3 (integration_review_process.py:218): the attempt is recorded for opus; the reviewer process's own
    configuration says sonnet. Before the correction it ran sonnet and the PASS named opus."""
    job = validated_job(rrig)
    report = review(rrig, job.job_id, launchers("pass", env={"FAKE_REVIEW_MODEL": "sonnet"}))
    stored = rrig.jobs.load(job.job_id)
    attempt, run = of_kind(rrig, job.job_id, "review-attempt")[-1], of_kind(rrig, job.job_id, "review-run")[-1]
    assert attempt["reviewer"] == reviewer_identity(rrig.config) and attempt["reviewer"]["model"] == "opus"
    assert report.outcome == "cancelled" and not report.ok, (report.outcome, report.reasons)
    assert run["status"] == "cancelled" and not run["counted"] and run["outcome"] is None
    assert "recorded for the reviewer claude/claude-code/opus" in run["error"]
    assert "configuration is claude/claude-code/sonnet" in run["error"] and "did not run" in run["error"]
    assert not (Path(attempt["run_dir"]) / "fake-pid").exists()          # the scripted reviewer was never invoked
    assert stored.state is JobState.IN_REVIEW and not of_kind(rrig, job.job_id, "review")
    assert reviewed_for(stored, rrig.config) is None
    report = review(rrig, job.job_id, launchers("pass"))     # given back (no budget used): an agreeing process reviews
    assert report.outcome == "reviewed"
    run, decision = of_kind(rrig, job.job_id, "review-run")[-1], of_kind(rrig, job.job_id, "review")[-1]
    assert decision["reviewer"] == reviewer_identity(rrig.config)
    assert (run["details"]["model"], run["details"]["reported_model"]) == ("opus", "claude-opus-5-5")


@pytest.mark.parametrize("mode, problem", [("othermodel", "the reviewer process ran 'sonnet'"),
                                           ("nofacts", "does not report which model")])
def test_f3_an_answer_whose_run_facts_are_not_the_attempts_reviewer_is_refused(rrig: Rig, launchers: Any, mode: str,
                                                                               problem: str):
    """A valid PASS answer, but the run's facts say another model ran (or say nothing): never recorded as the attempt's
    reviewer's answer — the run is `refused` (a rule violation), the round ends in ERROR."""
    job = validated_job(rrig)
    report = review(rrig, job.job_id, launchers(mode))
    stored = rrig.jobs.load(job.job_id)
    run, decision = of_kind(rrig, job.job_id, "review-run")[-1], of_kind(rrig, job.job_id, "review")[-1]
    assert report.outcome == "error" and not report.ok, (report.outcome, report.reasons)
    assert run["status"] == "refused" and run["counted"] and problem in run["error"]
    assert "not the attempt's recorded reviewer claude/claude-code/opus" in run["error"]
    assert "its answer is not used" in run["error"]
    assert run["outcome"] is None and run["verdict"] is None and run["findings"] == [] and run["output_sha256"] == ""
    if mode == "othermodel":                                             # the diagnostics are kept
        assert (run["details"]["model"], run["details"]["reported_model"]) == ("sonnet", "claude-sonnet-5-5")
    assert decision["outcome"] == "ERROR" and stored.state is JobState.IN_REVIEW
    assert "AWAITING_MERGE_APPROVAL" not in history(rrig, job.job_id) and reviewed_for(stored, rrig.config) is None


_OPUS = {"provider": "claude", "kind": "claude-code", "model": "opus", "config_sha256": "0" * 64}


@pytest.mark.parametrize("reviewer_model, details, problem", [
    ("opus", {"model": "opus", "reported_model": "claude-opus-5-5"}, ""),
    ("opus", {"model": "opus", "reported_model": "claude-sonnet-5-5"}, "model substitution"),
    ("opus", {"model": "sonnet", "reported_model": "claude-sonnet-5-5"}, "ran 'sonnet'"),
    ("opus", {"model": "CLI default", "reported_model": "claude-opus-5-5"}, "ran 'CLI default'"),
    ("opus", {"model": "opus"}, "does not report the model Claude Code used"),
    ("opus", {"model": "opus", "reported_model": None}, "does not report the model Claude Code used"),
    ("opus", {"reported_model": "claude-opus-5-5"}, "does not report which model"),
    ("opus", {"model": 7, "reported_model": "claude-opus-5-5"}, "does not report which model"),
    ("default", {"model": "CLI default", "reported_model": "claude-sonnet-5-5"}, ""),
    ("default", {"model": "opus", "reported_model": "claude-opus-5-5"}, "ran 'opus'"),
])
def test_f3_run_facts_must_name_the_attempts_model(reviewer_model: str, details: dict[str, Any], problem: str):
    found = execution_problem({**_OPUS, "model": reviewer_model}, details)
    assert (problem in found) if problem else found == "", found


def test_f3_crash_recovery_with_an_unchanged_identity_still_reviews(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    launcher = launchers("slow:1")
    with pytest.raises(SimulatedCrash):
        review(rrig, job.job_id, launcher, faults=("after-start",))
    report = review(rrig, job.job_id, launcher)            # waits for the same reviewer process and collects it
    assert report.outcome == "reviewed" and len(launcher.calls) == 1 and report.reviewer_runs == 0
    run, decision = of_kind(rrig, job.job_id, "review-run")[-1], of_kind(rrig, job.job_id, "review")[-1]
    assert decision["reviewer"] == of_kind(rrig, job.job_id, "review-attempt")[-1]["reviewer"]
    assert decision["reviewer"] == reviewer_identity(rrig.config) and run["details"]["model"] == "opus"


def test_f3_the_reviewer_identity_binds_the_configuration_that_decides_the_run(rrig: Rig):
    identity = reviewer_identity(rrig.config)
    assert set(identity) == {"provider", "kind", "model", "config_sha256"} and len(identity["config_sha256"]) == 64
    with_review(rrig, model="sonnet")
    assert reviewer_identity(rrig.config)["config_sha256"] != identity["config_sha256"]
    with_review(rrig, model="opus", timeout_seconds=900)      # not what decides which reviewer runs
    assert reviewer_identity(rrig.config) == identity


# -- F3 through the real CLI: `integration review` → `integration review-exec` → ClaudeCodeReviewer → fake claude ----

FOLLOWS_MODEL = '("claude-sonnet-5-5" if "sonnet" in args else "claude-opus-5-5")'   # reports the requested model


def _set_review(config_path: Path, **values: str) -> None:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["github"]["review"].update(values)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _cli_job(rig: Rig, tmp_path: Path, key: OperatorKey, *, reported: str = FOLLOWS_MODEL,
             while_running: dict[str, str] | None = None) -> tuple[Path, Path, IntegrationJob, list[str]]:
    """A job validated through the rig under the CLI's own configuration file (github.review.model opus), whose
    claude provider is a FAKE `claude` reporting *reported* as its model; *while_running*: github.review values the
    fake writes into the configuration file while it runs. (config path, the fake's record, the job, the CLI's argv)."""
    config_path = _cli_config(tmp_path, key)
    record, cli = tmp_path / "fake-claude-run.json", tmp_path / "bin" / "claude-fake"
    cli.parent.mkdir()
    script = FAKE_CLAUDE.format(python=sys.executable, flags=list(REQUIRED_FLAGS[ProviderKind.CLAUDE_CODE]),
                                record=str(record))
    assert script.count('"claude-opus-5-5"') == 1 and script.count("    prompt = sys.stdin.read()\n") == 1
    hook = ("    import yaml\n"
            f"    data = yaml.safe_load(open({str(config_path)!r}).read())\n"
            f"    data['github']['review'].update({dict(while_running or {})!r})\n"
            f"    open({str(config_path)!r}, 'w').write(yaml.safe_dump(data, sort_keys=False))\n"
            if while_running else "")
    script = script.replace('"claude-opus-5-5"', reported).replace("    prompt = sys.stdin.read()\n",
                                                                  "    prompt = sys.stdin.read()\n" + hook)
    cli.write_text(script, encoding="utf-8")
    cli.chmod(0o755)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["providers"]["claude"]["executable"] = str(cli)
    data.setdefault("runtime", {})["terminate_grace_seconds"] = 2
    data["github"].setdefault("review", {})["model"] = "opus"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    rig.config = load_config(config_path)
    local = MockIssueTracker(rig.state / "tracker.json")       # `--tracker local`: the job's Issue #63 is open
    open_issue(local, 63)
    local._save()
    rig.client = local
    job = validated_job(rig)
    return config_path, record, job, ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--tracker", "local",
                                      "--project-config", str(config_path), "integration"]


def _cli_review(base: list[str], job_id: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = cli_main([*base, "review", "--job", job_id, "--apply", "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_f3_real_cli_a_model_changed_before_the_reviewer_process_starts_is_never_run_or_relabelled(
        rrig: Rig, tmp_path: Path, key: OperatorKey, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch):
    """Codex F3, as Codex reproduced it: the attempt is recorded for opus, then the configuration file says sonnet
    before the real `integration review-exec` process starts. Before the correction that process ran Claude Code with
    `--model sonnet` and the PASS evidence named opus."""
    config_path, record, job, base = _cli_job(rrig, tmp_path, key)
    original = worker.cli_spawner

    def spawner(*args: Any, **kwargs: Any) -> Any:
        spawn = original(*args, **kwargs)
        real_start = spawn.start

        def start(*start_args: Any, **start_kwargs: Any) -> Any:
            _set_review(config_path, model="sonnet")   # after the attempt's record, before the process starts
            return real_start(*start_args, **start_kwargs)
        spawn.start = start
        return spawn
    monkeypatch.setattr(worker, "cli_spawner", spawner)
    code, report = _cli_review(base, job.job_id, capsys)
    assert code == 1 and report["outcome"] == "cancelled" and not report["ok"], report["reasons"]
    assert not record.exists()                                        # Claude Code never ran, no model request
    attempt, run = of_kind(rrig, job.job_id, "review-attempt")[-1], of_kind(rrig, job.job_id, "review-run")[-1]
    assert attempt["reviewer"]["model"] == "opus" and run["status"] == "cancelled" and not run["counted"]
    assert "configuration is claude/claude-code/sonnet" in run["error"]
    assert not of_kind(rrig, job.job_id, "review") and rrig.jobs.load(job.job_id).state is JobState.IN_REVIEW
    monkeypatch.setattr(worker, "cli_spawner", original)  # run again: the attempt is recorded for sonnet, sonnet runs
    code, report = _cli_review(base, job.job_id, capsys)
    assert code == 0 and report["outcome"] == "reviewed" and report["job"]["state"] == "AWAITING_MERGE_APPROVAL"
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    run, decision = of_kind(rrig, job.job_id, "review-run")[-1], of_kind(rrig, job.job_id, "review")[-1]
    assert argv[argv.index("--model") + 1] == "sonnet" and decision["reviewer"]["model"] == "sonnet"
    assert (run["details"]["model"], run["details"]["reported_model"]) == ("sonnet", "claude-sonnet-5-5")


def test_f3_real_cli_a_model_changed_while_the_reviewer_runs_does_not_change_what_ran(
        rrig: Rig, tmp_path: Path, key: OperatorKey, capsys: pytest.CaptureFixture[str]):
    config_path, record, job, base = _cli_job(rrig, tmp_path, key, while_running={"model": "sonnet"})
    code, report = _cli_review(base, job.job_id, capsys)
    assert code == 0 and report["outcome"] == "reviewed", report["reasons"]
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["github"]["review"]["model"] == "sonnet"
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    run, decision = of_kind(rrig, job.job_id, "review-run")[-1], of_kind(rrig, job.job_id, "review")[-1]
    assert argv[argv.index("--model") + 1] == "opus" and decision["reviewer"]["model"] == "opus"   # what really ran
    assert (run["details"]["model"], run["details"]["reported_model"]) == ("opus", "claude-opus-5-5")


def test_f3_real_cli_claude_code_reporting_another_model_is_refused(rrig: Rig, tmp_path: Path, key: OperatorKey,
                                                                    capsys: pytest.CaptureFixture[str]):
    _config, record, job, base = _cli_job(rrig, tmp_path, key, reported='"claude-sonnet-5-5"')
    code, report = _cli_review(base, job.job_id, capsys)
    assert code == 1 and report["outcome"] == "error" and record.exists()
    run, decision = of_kind(rrig, job.job_id, "review-run")[-1], of_kind(rrig, job.job_id, "review")[-1]
    assert run["status"] == "refused" and "model substitution" in run["error"] and run["outcome"] is None
    assert run["details"]["reported_model"] == "claude-sonnet-5-5" and decision["outcome"] == "ERROR"
    assert rrig.jobs.load(job.job_id).state is JobState.IN_REVIEW


# -- F4: a review attempt (and a positive decision) needs its validation current AT ITS POSITION --------------------

def _reappend(evidence: list[dict[str, Any]], entry: dict[str, Any]) -> dict[str, Any]:
    """Append *entry* as the next evidence entry: its id renumbered to the new position, its digest recomputed."""
    entry["id"] = f"{entry['id'].split('-')[0]}-{len(evidence) + 1:04d}"
    entry["sha256"] = evidence_digest(entry)
    evidence.append(entry)
    return entry


def _newest(evidence: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return copy.deepcopy([entry for entry in evidence if entry["kind"] == kind][-1])


def _record_after(rig: Rig, launchers: Any, fault: str | None) -> dict[str, Any]:
    job = validated_job(rig)
    if fault is None:
        assert review(rig, job.job_id, launchers("pass")).outcome == "reviewed"
    else:
        with pytest.raises(SimulatedCrash):
            review(rig, job.job_id, launchers("pass"), faults=(fault,))
    return rig.jobs.load(job.job_id).to_dict()


def _revoke(evidence: list[dict[str, Any]], revoked_by: str) -> None:
    if revoked_by == "gate":
        _reappend(evidence, _newest(evidence, "gate"))
        return
    m3 = _newest(evidence, "attempt")
    if revoked_by == "open attempt":
        m3["run"] = []
    _reappend(evidence, m3)
    if revoked_by == "newer validation":
        validation = _newest(evidence, "validation")
        validation["attempt"] = m3["id"]
        _reappend(evidence, validation)


@pytest.mark.parametrize("revoked_by", ["open attempt", "gate", "newer validation"])
def test_f4_a_review_attempt_relying_on_a_revoked_validation_is_refused_on_load(rrig: Rig, launchers: Any,
                                                                                revoked_by: str):
    """Codex F4 (integration_schema.py:575): passed validation → newer M3 attempt (or gate, or decision) → a review
    attempt naming the OLD validation, every id and digest valid. Before the correction from_dict accepted it."""
    data = _record_after(rrig, launchers, "after-attempt")
    evidence = data["evidence"]
    attempt = evidence.pop()
    assert attempt["kind"] == "review-attempt"
    _revoke(evidence, revoked_by)
    _reappend(evidence, attempt)
    with pytest.raises(StateDirError, match="is not current here"):
        IntegrationJob.from_dict(data)


def test_f4_a_review_attempt_after_the_current_validation_is_accepted(rrig: Rig, launchers: Any):
    data = _record_after(rrig, launchers, "after-attempt")
    assert IntegrationJob.from_dict(copy.deepcopy(data)).evidence[-1]["kind"] == "review-attempt"
    evidence = data["evidence"]                 # a re-validation (attempt + passed decision), then the review attempt
    attempt = evidence.pop()
    _revoke(evidence, "newer validation")
    attempt["validation"] = evidence[-1]["id"]
    _reappend(evidence, attempt)
    loaded = IntegrationJob.from_dict(data)
    assert loaded.evidence[-1]["validation"] == loaded.evidence[-2]["id"] and validated(loaded)


def test_f4_a_positive_decision_after_a_later_gate_is_refused_on_load(rrig: Rig, launchers: Any):
    data = _record_after(rrig, launchers, None)
    evidence = data["evidence"]
    decision = evidence.pop()
    assert decision["kind"] == "review" and decision["outcome"] == "PASS"
    _revoke(evidence, "gate")
    _reappend(evidence, decision)
    with pytest.raises(StateDirError, match="needs its validation .* still current"):
        IntegrationJob.from_dict(data)


# == Codex functional re-review: RR1 child admission, RR2 status eligibility =======================================

# -- RR1: the reviewer process admits an attempt only under the CURRENT independence policy -------------------------

def test_rr1_a_require_set_before_the_reviewer_process_starts_never_runs_it(rrig: Rig, launchers: Any):
    """Codex re-review, finding 1 (integration_review_process.py:249/277): the attempt recorded under `prefer`, the
    reviewer process's own configuration says `require` (its identity unchanged). Before the correction it ran and the
    waiting side, still under `prefer`, recorded PASS. Then the recovery: the attempt was given back."""
    job = validated_job(rrig)
    report = review(rrig, job.job_id, launchers("pass", env={"FAKE_REVIEW_INDEPENDENCE": "require"}))
    attempt, run = of_kind(rrig, job.job_id, "review-attempt")[-1], of_kind(rrig, job.job_id, "review-run")[-1]
    assert attempt["independence"]["mode"] == "prefer" and attempt["reviewer"] == reviewer_identity(rrig.config)
    assert report.outcome == "cancelled" and not report.ok, (report.outcome, report.reasons)
    assert run["status"] == "cancelled" and not run["counted"] and run["outcome"] is None
    assert f"review attempt {attempt['id']}: github.review.independence is `require`" in run["error"]
    assert "cannot be established (level unknown" in run["error"] and "the reviewer did not run" in run["error"]
    assert "recorded for the reviewer" not in run["error"]            # the identity agrees: the policy refuses
    assert not (Path(attempt["run_dir"]) / "fake-pid").exists()       # the scripted reviewer was never invoked
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.IN_REVIEW and not of_kind(rrig, job.job_id, "review")
    assert reviewed_for(stored, rrig.config) is None
    report = review(rrig, job.job_id, launchers("pass"))               # an agreeing process: reviewed, no budget used
    attempts, runs = of_kind(rrig, job.job_id, "review-attempt"), of_kind(rrig, job.job_id, "review-run")
    assert report.outcome == "reviewed" and [r["counted"] for r in runs] == [False, True]
    assert len(attempts) == 2 and attempts[0]["round"] == attempts[1]["round"]
    assert reviewed_for(rrig.jobs.load(job.job_id), rrig.config) == current_review(rrig.jobs.load(job.job_id))


def test_rr1_admission_checks_the_identity_and_then_the_current_policy(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    with pytest.raises(SimulatedCrash):                    # recorded under `prefer`; its process never started
        review(rrig, job.job_id, launchers("pass"), faults=("after-attempt",))
    attempt = of_kind(rrig, job.job_id, "review-attempt")[-1]
    env = {REVIEW_TAG_ENV: attempt["tag"]}

    def admit(config: Any = None) -> str:
        return attempt_problem(rrig.jobs, job.job_id, Path(attempt["run_dir"]), env, config)
    prefer = rrig.config
    assert admit(prefer) == ""                                                    # unchanged `prefer`: it runs
    require = with_review(rrig, independence="require").config
    assert reviewer_identity(require) == reviewer_identity(prefer)                # the policy is not the identity
    assert "github.review.independence is `require`" in admit(require) and "did not run" in admit(require)
    assert "recorded for the reviewer" in admit(with_review(rrig, model="sonnet").config)   # the identity first
    assert admit() == ""                         # the per-second check while it runs reads the job record only


def test_rr1_an_unchanged_require_policy_records_no_attempt(rrig: Rig, launchers: Any):
    job = validated_job(rrig)
    with_review(rrig, independence="require")
    launcher = launchers("pass")
    report = review(rrig, job.job_id, launcher)
    assert report.outcome == "independence-refused" and "cannot be established" in report.reasons[0]
    assert launcher.calls == [] and not of_kind(rrig, job.job_id, "review-attempt")
    assert rrig.state_of(job.job_id) is JobState.VALIDATING


def test_rr1_a_require_set_while_the_reviewer_runs_never_becomes_an_authoritative_review(rrig: Rig, launchers: Any):
    """The reviewer process admitted under `prefer`; while it runs the configuration in effect becomes `require`
    (read again before the decision: *reload*). Its complete answer is kept as history, never decided under it."""
    job = validated_job(rrig)
    runs_dir = rrig.state / "runs" / "review" / job.job_id

    def stop() -> bool:                 # polled while the waiting side waits: once the reviewer runs, `require`
        if any(runs_dir.glob("attempt-*/fake-pid")):
            with_review(rrig, independence="require")
        return False
    launcher = launchers("slow:1")
    report = review(rrig, job.job_id, launcher, stop=stop, reload=lambda: rrig.config)
    stored = rrig.jobs.load(job.job_id)
    run = of_kind(rrig, job.job_id, "review-run")[-1]
    assert rrig.config.github.review.independence == "require"
    assert report.outcome == "independence-refused" and not report.ok and report.reviewer_runs == 1, report.reasons
    assert "is `require`" in report.reasons[0]
    assert run["status"] == "ok" and run["counted"] and run["outcome"] == "PASS"     # the answer: history only
    assert stored.state is JobState.IN_REVIEW and not of_kind(rrig, job.job_id, "review")
    assert job_summary(stored)["review_pending"] and reviewed_for(stored, rrig.config) is None
    kept = stored.to_dict()["evidence"]
    with_review(rrig, independence="prefer")      # a compatible policy again: decided, no second reviewer run
    report = review(rrig, job.job_id, launcher, reload=lambda: rrig.config)
    assert report.outcome == "reviewed" and report.reviewer_runs == 0 and len(launcher.calls) == 1
    assert rrig.jobs.load(job.job_id).to_dict()["evidence"][:len(kept)] == kept


def test_rr1_the_policy_in_effect_is_the_stricter_one_and_an_unreadable_one_confirms_nothing(rrig: Rig):
    recorded = integration_independence(rrig.config)
    prefer = rrig.config
    require = with_review(rrig, independence="require").config
    assert policy_problem(prefer, None, recorded) == "" and policy_problem(prefer, lambda: prefer, recorded) == ""
    assert "is `require`" in policy_problem(prefer, lambda: require, recorded)      # changed to `require`
    assert "is `require`" in policy_problem(require, lambda: prefer, recorded)      # never downgraded

    def unreadable() -> Any:
        raise ConfigError("cannot read configuration project.yaml: truncated")
    assert "cannot be confirmed" in policy_problem(prefer, unreadable, recorded)
    assert "truncated" in policy_problem(prefer, unreadable, recorded)


# -- RR1 through the real CLI: `integration review` → `integration review-exec` → ClaudeCodeReviewer → fake claude --

def test_rr1_real_cli_a_require_set_before_the_reviewer_process_starts_never_runs_claude(
        rrig: Rig, tmp_path: Path, key: OperatorKey, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch):
    config_path, record, job, base = _cli_job(rrig, tmp_path, key)
    original = worker.cli_spawner

    def spawner(*args: Any, **kwargs: Any) -> Any:
        spawn = original(*args, **kwargs)
        real_start = spawn.start

        def start(*start_args: Any, **start_kwargs: Any) -> Any:
            _set_review(config_path, independence="require")   # after the attempt's record, before the process
            return real_start(*start_args, **start_kwargs)
        spawn.start = start
        return spawn
    monkeypatch.setattr(worker, "cli_spawner", spawner)
    code, report = _cli_review(base, job.job_id, capsys)
    assert code == 1 and report["outcome"] == "cancelled" and not report["ok"], report["reasons"]
    assert not record.exists()                                  # the fake `claude` was never invoked: no request
    attempt, run = of_kind(rrig, job.job_id, "review-attempt")[-1], of_kind(rrig, job.job_id, "review-run")[-1]
    assert attempt["independence"]["mode"] == "prefer" and run["status"] == "cancelled" and not run["counted"]
    assert "github.review.independence is `require`" in run["error"] and "did not run" in run["error"]
    assert not of_kind(rrig, job.job_id, "review") and rrig.jobs.load(job.job_id).state is JobState.IN_REVIEW
    monkeypatch.setattr(worker, "cli_spawner", original)
    code, report = _cli_review(base, job.job_id, capsys)        # `require` stays: refused before any attempt
    assert code == 1 and report["outcome"] == "independence-refused" and not record.exists()
    assert len(of_kind(rrig, job.job_id, "review-attempt")) == 1
    _set_review(config_path, independence="prefer")             # recovery: the given-back attempt cost nothing
    code, report = _cli_review(base, job.job_id, capsys)
    assert code == 0 and report["outcome"] == "reviewed" and record.exists() and report["job"]["reviewed"]
    assert [r["counted"] for r in of_kind(rrig, job.job_id, "review-run")] == [False, True]


def test_rr1_real_cli_a_require_set_while_the_reviewer_runs_keeps_its_answer_as_history(
        rrig: Rig, tmp_path: Path, key: OperatorKey, capsys: pytest.CaptureFixture[str]):
    config_path, record, job, base = _cli_job(rrig, tmp_path, key, while_running={"independence": "require"})
    code, report = _cli_review(base, job.job_id, capsys)
    assert code == 1 and report["outcome"] == "independence-refused" and record.exists(), report["reasons"]
    assert report["job"]["state"] == "IN_REVIEW" and report["job"]["review_pending"] and not report["job"]["reviewed"]
    run = of_kind(rrig, job.job_id, "review-run")[-1]
    assert run["status"] == "ok" and run["outcome"] == "PASS" and not of_kind(rrig, job.job_id, "review")
    before = raw_record(rrig, job.job_id)
    assert cli_main([*base, "status", "--job", job.job_id]) == 0          # read-only: pending, never CURRENT
    out = capsys.readouterr().out
    assert "PENDING: a valid answer awaits its decision" in out and "CURRENT" not in out
    assert raw_record(rrig, job.job_id) == before
    record.unlink()
    _set_review(config_path, independence="prefer")    # decided from the kept answer: claude is not run again
    code, report = _cli_review(base, job.job_id, capsys)
    assert code == 0 and report["outcome"] == "reviewed" and report["reviewer_runs"] == 0 and not record.exists()
    _set_review(config_path, independence="require")   # RR2 through the real CLI: history, not eligible
    assert cli_main([*base, "status", "--job", job.job_id, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["job"]
    assert data["review"]["outcome"] == "PASS" and data["review_bound"] is True and data["reviewed"] is False
    assert "independence requirement is not satisfied" in data["review_blocker"]


# -- RR2: `integration status` reports eligibility under the CURRENT configuration (reviewed_for) -------------------

def _files(rig: Rig) -> dict[str, bytes]:
    return {str(p.relative_to(rig.state)): p.read_bytes() for p in sorted(rig.state.rglob("*")) if p.is_file()}


def _status(rig: Rig, config: Any, job_id: str | None, capsys: pytest.CaptureFixture[str]) -> tuple[str, Any]:
    """`integration status [--job]` (text, then --json) through the CLI command under *config*; it writes nothing."""
    before = _files(rig)
    args = argparse.Namespace(state_dir=str(rig.state), job=job_id, json=False)
    assert cmd_status(args, config) == 0
    text = capsys.readouterr().out
    args.json = True
    assert cmd_status(args, config) == 0
    data = json.loads(capsys.readouterr().out)
    assert _files(rig) == before                                         # read-only: no file of the state changes
    return text, data["job"] if job_id else data["jobs"]


@pytest.mark.parametrize("mode, outcome, findings", [("pass", "PASS", 0), ("notes", "PASS_WITH_NOTES", 2)])
def test_rr2_a_review_the_current_policy_refuses_is_shown_as_history_never_as_current(
        rrig: Rig, launchers: Any, capsys: pytest.CaptureFixture[str], mode: str, outcome: str, findings: int):
    """Codex re-review, finding 2 (integration_commands.py:172/201/439): after `prefer` → `require`, reviewed_for was
    None but `status` said `CURRENT: valid for this candidate`, `reviewed: true` and named only M5 as the blocker."""
    job = validated_job(rrig)
    assert review(rrig, job.job_id, launchers(mode)).outcome == "reviewed"
    text, data = _status(rrig, rrig.config, job.job_id, capsys)                  # eligible PASS / PASS_WITH_NOTES
    assert "(CURRENT: valid for this candidate)" in text and "blockers: merge approval and merge (M5" in text
    assert (data["reviewed"], data["review_bound"], data["review_blocker"]) == (True, True, "")
    assert data["review"]["outcome"] == outcome and len(data["review"]["findings"]) == findings
    recorded = copy.deepcopy(data["review"])
    with_review(rrig, independence="require")
    assert reviewed_for(rrig.jobs.load(job.job_id), rrig.config) is None
    text, data = _status(rrig, rrig.config, job.job_id, capsys)
    assert "CURRENT" not in text and "merge approval and merge (M5" not in text
    assert f"{outcome} (verdict" in text and "(HISTORICAL: not eligible for M5 under the current configuration)" in text
    assert "blockers: the current reviewer independence requirement is not satisfied: github.review.independence " \
           "is `require`" in text and "kept as history" in text
    assert data["reviewed"] is False and data["review_bound"] is True and data["state"] == "AWAITING_MERGE_APPROVAL"
    assert data["review_blocker"].startswith("the current reviewer independence requirement is not satisfied")
    assert data["review"] == recorded                                          # verdict and findings kept
    text, rows = _status(rrig, rrig.config, None, capsys)                       # the list view says the same
    assert rows[0]["reviewed"] is False and f"review {outcome}  (not eligible" in text
    with_review(rrig, independence="prefer")                                    # back: eligible again, unchanged
    text, data = _status(rrig, rrig.config, job.job_id, capsys)
    assert "(CURRENT: valid for this candidate)" in text and data["reviewed"] is True and data["review"] == recorded


def test_rr2_no_review_and_a_request_changes_review_are_never_eligible(rrig: Rig, launchers: Any,
                                                                       capsys: pytest.CaptureFixture[str]):
    job = validated_job(rrig)
    text, data = _status(rrig, rrig.config, job.job_id, capsys)                 # no review evidence
    assert data["review"] is None and (data["reviewed"], data["review_bound"], data["review_blocker"]) == \
        (False, False, "")
    assert "CURRENT" not in text and "HISTORICAL" not in text
    assert review(rrig, job.job_id, launchers("changes")).outcome == "changes-requested"
    for config in (rrig.config, with_review(rrig, independence="require").config):
        text, data = _status(rrig, config, job.job_id, capsys)
        assert data["state"] == "NEEDS_REPAIR" and data["review"]["outcome"] == "REQUEST_CHANGES"
        assert (data["reviewed"], data["review_bound"], data["review_blocker"]) == (False, False, "")
        assert "CURRENT" not in text and "HISTORICAL" not in text and "repair on a new branch" in text


@pytest.mark.parametrize("obsolete", ["gate plan", "stale"])
def test_rr2_a_review_whose_binding_is_obsolete_is_never_current(rrig: Rig, launchers: Any,
                                                                 capsys: pytest.CaptureFixture[str], obsolete: str):
    job = validated_job(rrig)
    launcher = launchers("pass")
    assert review(rrig, job.job_id, launcher).outcome == "reviewed"
    if obsolete == "gate plan":          # the current configuration derives another gate plan than was validated
        roles = dict(rrig.config.github.intake.role_gates)
        intake = dataclasses.replace(rrig.config.github.intake,
                                     role_gates={**roles, "backend": (*roles["backend"], "frontend-build")})
        config = dataclasses.replace(rrig.config, github=dataclasses.replace(rrig.config.github, intake=intake))
        text, data = _status(rrig, config, job.job_id, capsys)
        assert (data["reviewed"], data["review_bound"]) == (False, True) and "HISTORICAL" in text
        assert data["review_blocker"] == "the current configuration derives another gate plan than the review was " \
                                         "made for" and "another gate plan" in text and "CURRENT" not in text
    else:                                # the source moved after the review: the job is STALE
        change_on(rrig.repo, BRANCH, {"backend/src/moved.py": "MOVED = 1\n"})
        assert review(rrig, job.job_id, launcher).outcome == "stale"
        text, data = _status(rrig, rrig.config, job.job_id, capsys)
        assert data["state"] == "STALE" and data["review"]["outcome"] == "PASS"
        assert (data["reviewed"], data["review_bound"], data["review_blocker"]) == (False, False, "")
        assert "CURRENT" not in text and "HISTORICAL" not in text
