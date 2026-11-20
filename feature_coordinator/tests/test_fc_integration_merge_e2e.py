"""W4.7 (M5): end to end — M1 intake → M2 inspect approval and inspection → M3 validation → M4 independent review → M5
signed merge approval → GitHub merge → verified COMPLETED — on disposable repositories with a SCRIPTED GitHub.

The scripted GitHub (fc_integration_merge_rig.FakeMergeGitHub, behind the real `GitHubClient`) serves EVERY step,
including the Issue, the PR lookup and the commit attribution, and performs the merge for real on the disposable bare
`origin`. The gate runner is a recording fake, the reviewer a fake process (fc_fake_integration_reviewer.py): no model,
no paid request, no real GitHub, no real merge, no network. An autouse fixture makes the real `gh` transport raise, so a
test that reached the real GitHub would fail. The last tests run the M5 steps through the real CLI (`integration
approve --stage merge`, `merge`, `status`) with the same configuration the CLI loads."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator import github_api, github_commands
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import load_config
from feature_coordinator.integration import JobState
from feature_coordinator.issue_sync import SimulatedCrash

from fc_helpers import git
from fc_integration_merge_rig import FakeMergeGitHub, approve_merge, bare, github_for, merge, merge_record_for
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, OPERATOR, REPOSITORY, OperatorKey, Rig, change_on, github_origin, head, integration_config, live_client,
    origin, pr_json, request_for, snapshot,
)
from test_fc_integration_review import launchers, review, rrig  # pylint: disable=unused-import
# pylint: disable=unused-import
from test_fc_integration_validate import X_PY, FakeGates, _cli_config, key, rig, seeded, tracker

HISTORY = ["INTAKE", "AWAITING_APPROVAL", "INSPECTING", "CANDIDATE_BUILT", "VALIDATING", "IN_REVIEW",
           "AWAITING_MERGE_APPROVAL", "MERGED", "COMPLETED"]
EVIDENCE = ["inspection", "candidate", "attempt", "gate", "validation", "review-attempt", "review-run", "review",
            "merge-attempt", "merge-pr", "merge-attempt", "merge-outcome"]


@pytest.fixture(autouse=True)
def no_real_github(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real `gh` transport must never run: every GitHub request of these tests goes to the scripted transport."""
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the real gh transport was used: an end-to-end test must stay on the scripted GitHub")
    monkeypatch.setattr(github_api.GhCliTransport, "request", refuse)
    monkeypatch.setattr(github_api.GitHubClient, "from_config", classmethod(refuse))


def dedupe(kinds: list[str]) -> list[str]:
    """The evidence kinds in order of first appearance of each run (consecutive duplicates collapsed)."""
    out: list[str] = []
    for kind in kinds:
        if not out or out[-1] != kind:
            out.append(kind)
    return out


def contains_in_order(haystack: list[str], needles: list[str]) -> bool:
    items = iter(haystack)
    return all(any(item == needle for item in items) for needle in needles)


def drive_m1_to_m4(rig: Rig, launchers_: Any, sha: str, **request: Any) -> str:
    job = rig.approved(request_for(rig.repo, sha, **request))                                 # M1 intake + M2 approval
    inspection = rig.inspect(job.job_id)
    assert inspection.outcome == "passed", (inspection.outcome, inspection.reasons)           # M2
    validation = rig.validate(job.job_id, gates=FakeGates())
    assert validation.outcome == "validated", (validation.outcome, validation.reasons)        # M3
    reviewed = review(rig, job.job_id, launchers_("pass"))
    assert reviewed.outcome == "reviewed", (reviewed.outcome, reviewed.reasons)               # M4
    assert rig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL
    return job.job_id


def assert_merged_into_dev(rig: Rig, origin_path: Path, job_id: str, *, checkout_dev: str) -> dict[str, Any]:
    job = rig.jobs.load(job_id)
    result = job.merge_result
    dev = bare(origin_path, "rev-parse", "refs/heads/dev").strip()
    assert job.state is JobState.COMPLETED and result["verified"] is True
    assert result["commit_sha"] == dev != job.candidate["head_sha"]          # GitHub's merge commit, not the candidate
    assert bare(origin_path, "rev-parse", f"{dev}^{{tree}}").strip() == job.candidate["tree_sha"]
    assert bare(origin_path, "rev-list", "--parents", "-n", "1", dev).split()[1:] == [job.base["expected_sha"],
                                                                                         job.source["sha"]]
    assert bare(origin_path, "rev-parse", f"refs/heads/{BRANCH}").strip() == job.source["sha"]       # source untouched
    # the checkout's dev untouched
    assert git(rig.repo, "rev-parse", "dev").strip() == checkout_dev
    return result


def test_end_to_end_with_a_pr_that_the_coordinator_creates(rrig: Rig, launchers: Any, github_origin: Path):
    fake = github_for(rrig, github_origin)                          # the scripted GitHub serves every step from M1 on
    sha = change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY})
    checkout_dev, checkout = head(rrig.repo), snapshot(rrig.repo, github_origin)["git"]
    job_id = drive_m1_to_m4(rrig, launchers, sha)
    assert not fake.writes() and snapshot(rrig.repo, github_origin)["git"] == checkout           # M1-M4 wrote nothing
    # M5: the signed merge approval is recorded; a dry run changes nothing; then the merge
    result = approve_merge(rrig, job_id)
    assert result["outcome"] == "recorded" and result["approval"]["stage"] == "merge" and result["effective"]
    before = (rrig.state / "integration" / f"{job_id}.json").read_bytes()
    dry = merge(rrig, job_id, apply=False)
    assert dry.outcome == "would-create-pr" and dry.ok and not fake.writes()
    assert (rrig.state / "integration" / f"{job_id}.json").read_bytes() == before
    report = merge(rrig, job_id)
    assert report.outcome == "completed" and report.ok, (report.outcome, report.reasons)
    # the lifecycle, the evidence, the audit trail
    stored = rrig.jobs.load(job_id)
    assert [h["to"] for h in stored.history] == HISTORY
    assert contains_in_order(dedupe([e["kind"] for e in stored.evidence]), dedupe(EVIDENCE))
    assert [e["kind"] for e in stored.evidence if e["kind"].startswith("merge-")] == \
        ["merge-attempt", "merge-dispatch", "merge-pr", "merge-attempt", "merge-dispatch", "merge-outcome"]
    approvals = [(a["id"], a["stage"]) for a in stored.approvals]
    assert approvals == [("apr-0001", "inspect"), ("apr-0002", "merge")]
    kinds = [e["kind"] for e in rrig.store.events()]
    for expected in ("integration-intake", "integration-approval", "integration-inspection",
                     "integration-merge-attempt", "integration-merge-dispatch", "integration-merge-pr",
                     "integration-merged",
                     "integration-completed"):
        assert expected in kinds, expected
    merged = assert_merged_into_dev(rrig, github_origin, job_id, checkout_dev=checkout_dev)
    assert merged["pr"] == 101 and merged["merged_by"] == OPERATOR
    assert {c["name"]: c["status"] for c in merged["verification"]["checks"]}["parents"] == "pass"
    # exactly the two GitHub writes the signed approval authorizes, and every GitHub call was scripted
    assert fake.writes() == [("POST", f"repos/{REPOSITORY}/pulls"), ("PUT", f"repos/{REPOSITORY}/pulls/101/merge")]
    assert fake.merge_requests == [{"number": 101, "merge_method": "merge", "sha": sha}]
    assert fake.created[0]["head"] == BRANCH and fake.created[0]["base"] == "dev" and fake.created[0]["draft"] is False
    assert fake.calls and snapshot(rrig.repo, github_origin)["status"] == ""                      # no checkout change


def test_end_to_end_with_an_existing_pr(rrig: Rig, launchers: Any, github_origin: Path):
    sha = change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY})
    fake = FakeMergeGitHub(github_origin, [pr_json(7, head_sha=sha)])
    rrig.client = live_client(fake, integration=rrig.config.github.integration)
    checkout_dev = head(rrig.repo)
    job_id = drive_m1_to_m4(rrig, launchers, sha, pr_number=7)
    approval = approve_merge(rrig, job_id)
    assert approval["outcome"] == "recorded" and approval["approval"]["pr"] == 7
    # nothing to create: the PR is known
    assert approval["approval"]["merge"]["authorizes"]["create_pr"] is False
    dry = merge(rrig, job_id, apply=False)
    assert dry.outcome == "would-merge" and dry.pr["number"] == 7 and dry.pr["origin"] == "recorded"
    assert not fake.writes()
    report = merge(rrig, job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    stored = rrig.jobs.load(job_id)
    assert [h["to"] for h in stored.history] == HISTORY
    assert not fake.created and list(fake.writes()) == [("PUT", f"repos/{REPOSITORY}/pulls/7/merge")]
    bound = [e for e in stored.evidence if e["kind"] == "merge-pr"][0]
    assert bound["number"] == 7 and bound["origin"] == "recorded" and bound["attempt"] is None
    assert stored.pr["number"] == 7 and stored.merge_result["pr"] == 7             # the intake input was never edited
    assert assert_merged_into_dev(rrig, github_origin, job_id, checkout_dev=checkout_dev)["commit_sha"] == \
        fake.prs[0]["merge_commit_sha"]


# -- through the real CLI ------------------------------------------------------------------------------------------

@pytest.fixture
def crig(seeded: Path, state_dir: Path, key: OperatorKey, tmp_path: Path) -> Rig:
    """The rig over the configuration the CLI loads (so `validated_for` and the review's gate plan agree)."""
    config = load_config(_cli_config(tmp_path, key))
    config = dataclasses.replace(config, runtime=dataclasses.replace(config.runtime, terminate_grace_seconds=2))
    return Rig(seeded, state_dir, key, config=config)


def fresh_client(rig: Rig, fake: Any) -> Any:
    """What `_tracker` gives each CLI command: a NEW client (over the same scripted GitHub). The merge command binds
    its anchors to its own client once; a client is never re-bound."""
    return live_client(fake, integration=rig.config.github.integration)


def run_cli(base: list[str], capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli_main([*base, *args])
    return code, capsys.readouterr().out


def test_end_to_end_through_the_cli(crig: Rig, launchers: Any, github_origin: Path, key: OperatorKey, tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    rig = crig
    fake = github_for(rig, github_origin)
    monkeypatch.setattr(github_commands, "_tracker", lambda _args, _config, _state: fresh_client(rig, fake))
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    checkout_dev = head(rig.repo)
    job_id = drive_m1_to_m4(rig, launchers, sha)
    base = ["--tracker", "github", "--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config",
            str(tmp_path / "project.yaml"), "integration"]
    # before any merge approval: status says what is missing, merge refuses and writes nothing
    code, out = run_cli(base, capsys, "status", "--job", job_id)
    assert code == 0 and "merge approval: none" in out and "not merge-ready" in out
    assert "no signed merge approval" in out
    assert "merge approval and merge (M5" in out
    code, out = run_cli(base, capsys, "merge", "--job", job_id, "--apply", "--json")
    refused = json.loads(out)
    assert code == 1 and refused["ok"] is False and refused["outcome"] == "no-approval"
    assert "merge approval" in " ".join(refused["reasons"]) and not fake.writes()
    # the operator signs the merge record; verify (dry run), then record
    record = rig.write_record(merge_record_for(rig.jobs.load(job_id)))
    code, out = run_cli(base, capsys, "approve", "--job", job_id, "--stage", "merge", "--record", str(record))
    assert code == 0 and "approve: would-record" in out and "dry run" in out
    assert [a["stage"] for a in rig.jobs.load(job_id).approvals] == ["inspect"]
    code, out = run_cli(base, capsys, "approve", "--job", job_id, "--stage", "merge", "--record", str(record),
                        "--apply")
    assert code == 0 and "approve: recorded" in out and "apr-0002: merge by spyderbg" in out
    code, out = run_cli(base, capsys, "approve", "--job", job_id, "--stage", "merge", "--record", str(record),
                        "--apply")
    assert code == 0 and "approve: existing" in out                                       # repeating is idempotent
    code, out = run_cli(base, capsys, "status", "--job", job_id)
    assert code == 0 and "merge approval apr-0002 by spyderbg" in out and "VALID" in out and "EXPIRES" in out
    assert "MERGE-READY (re-verified at merge time)" in out
    assert f"binds candidate {rig.jobs.load(job_id).candidate['head_sha']}" in out and "PR creation authorized" in out
    status = json.loads(run_cli(base, capsys, "status", "--job", job_id, "--json")[1])["job"]["merge"]
    assert status["ready"] is True and status["approval"]["valid"] is True and status["blockers"] == []
    # the dry run changes nothing and writes nothing; --apply merges and verifies
    before = (rig.state / "integration" / f"{job_id}.json").read_bytes()
    code, out = run_cli(base, capsys, "merge", "--job", job_id)
    assert code == 0 and "merge: would-create-pr" in out and "dry run" in out and not fake.writes()
    assert (rig.state / "integration" / f"{job_id}.json").read_bytes() == before
    code, out = run_cli(base, capsys, "merge", "--job", job_id, "--apply", "--json", "--actor", "operator")
    result = json.loads(out)
    assert code == 0 and result["ok"] and result["outcome"] == "completed" and result["job"]["state"] == "COMPLETED"
    assert result["github_writes"] == ["POST pulls", "PUT pulls/101/merge"]
    assert result["merge_result"]["verified"] is True
    assert {c["name"]: c["status"] for c in result["verification"]["checks"]}["tree"] == "pass"
    merged = assert_merged_into_dev(rig, github_origin, job_id, checkout_dev=checkout_dev)
    # status afterwards, repeated merge and the audit trail
    code, out = run_cli(base, capsys, "status", "--job", job_id)
    assert code == 0 and f"merge_result: commit {merged['commit_sha']}" in out and "VERIFIED" in out
    assert "pass           parents" in out and "not-configured" in out
    code, out = run_cli(base, capsys, "merge", "--job", job_id, "--apply")
    assert code == 0 and "merge: completed (unchanged)" in out and len(fake.merge_requests) == 1
    assert [e["kind"] for e in rig.store.events() if e["kind"] in (
            "integration-merge-attempt", "integration-merge-dispatch", "integration-merge-pr", "integration-merged",
            "integration-completed")] == \
        ["integration-merge-attempt", "integration-merge-dispatch", "integration-merge-pr",
         "integration-merge-attempt", "integration-merge-dispatch", "integration-merged", "integration-completed"]
    code, out = run_cli(base, capsys, "abort", "--job", job_id, "--reason", "too late", "--apply", "--json")
    assert code == 1 and json.loads(out)["ok"] is False                                  # a completed job is final


def test_cli_merge_pending_verification_exits_nonzero_and_the_repeat_completes(
        crig: Rig, launchers: Any, github_origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]):
    rig = crig
    integration = dataclasses.replace(rig.config.github.integration, post_merge_checks=("ci",))
    rig.config = dataclasses.replace(rig.config, github=dataclasses.replace(rig.config.github, integration=integration))
    fake = github_for(rig, github_origin)
    monkeypatch.setattr(github_commands, "_tracker", lambda _args, _config, _state: fresh_client(rig, fake))
    job_id = drive_m1_to_m4(rig, launchers, change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY}))
    assert approve_merge(rig, job_id)["outcome"] == "recorded"
    config_path = tmp_path / "project.yaml"
    text = config_path.read_text(encoding="utf-8").replace("trusted_logins:",
                                                           "post_merge_checks: [ci]\n    trusted_logins:")
    config_path.write_text(text, encoding="utf-8")
    base = ["--tracker", "github", "--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config",
            str(config_path), "integration"]
    code, out = run_cli(base, capsys, "merge", "--job", job_id, "--apply", "--json")
    pending = json.loads(out)
    assert code == 1 and pending["outcome"] == "pending-verification" and pending["job"]["state"] == "MERGED"
    assert pending["ok"] is False and pending["merge_result"]["verified"] is False
    code, out = run_cli(base, capsys, "status", "--job", job_id)
    assert code == 0 and "MERGED" in out and "post-merge verification is pending" in out
    sha = pending["merge_result"]["commit_sha"]
    fake.checks[sha] = [{"id": 1, "name": "ci", "status": "completed", "conclusion": "success"}]
    code, out = run_cli(base, capsys, "merge", "--job", job_id, "--apply", "--json")
    assert code == 0 and json.loads(out)["outcome"] == "completed" and len(fake.merge_requests) == 1


def test_cli_merge_errors_and_arguments(crig: Rig, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    base = ["--tracker", "github", "--repo", str(crig.repo), "--state-dir", str(crig.state), "--project-config",
            str(tmp_path / "project.yaml"), "integration"]
    code, out = run_cli(base, capsys, "merge", "--job", "int-20990101-0001", "--json")
    assert code == 1 and json.loads(out) == {"ok": False, "error": {"type": "IntegrationError",
                                                                    "message": "unknown integration job "
                                                                               "'int-20990101-0001'"}}
    with pytest.raises(SystemExit) as exc:
        cli_main([*base, "merge"])                                                       # --job is required
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        cli_main([*base, "approve", "--job", "int-20990101-0001", "--stage", "bogus", "--record", "x"])
    assert exc.value.code == 2
    capsys.readouterr()


def test_the_crash_recovery_flow_ends_in_the_same_verified_state_as_an_uninterrupted_run(rrig: Rig, launchers: Any,
                                                                                         github_origin: Path):
    """The end-to-end result does not depend on a crash: interrupted after GitHub merged, resumed by a fresh merger."""
    fake = github_for(rrig, github_origin)
    job_id = drive_m1_to_m4(rrig, launchers, change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY}))
    assert approve_merge(rrig, job_id)["outcome"] == "recorded"
    checkout_dev = head(rrig.repo)
    with pytest.raises(SimulatedCrash):
        merge(rrig, job_id, faults=("after-merge-request",))
    assert merge(rrig, job_id).outcome == "completed"
    assert_merged_into_dev(rrig, github_origin, job_id, checkout_dev=checkout_dev)
    assert len(fake.merge_requests) == 1 and len(fake.created) == 1
    assert [h["to"] for h in rrig.jobs.load(job_id).history] == HISTORY
