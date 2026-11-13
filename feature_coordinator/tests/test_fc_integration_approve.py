"""W4.7 (M2): operator authorization — `integration approve --job J --stage inspect --record FILE`
(integration_approval.py, the approval schema in integration.py).

Disposable checkout, bare origin, state directory and a throw-away operator SSH key per test (fc_integration_rig).
Signatures are made and checked with the host's real `ssh-keygen -Y sign/verify`."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from feature_coordinator import integration_approval
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.errors import StateDirError
from feature_coordinator.integration import IntegrationError, IntegrationJob, JobState, abort_job, \
    effective_approval, job_summary, parse_record_time
from feature_coordinator.integration_intake import IntegrationIntake

from fc_helpers import run_py
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, KNOWN, OPERATOR, OperatorKey, Rig, change_on, github_origin, integration_config, origin, record_for,
    request_for, stamp,
)


@pytest.fixture
def key(tmp_path: Path) -> OperatorKey:
    return OperatorKey(tmp_path / "operator-home")


@pytest.fixture
def rig(repo: Path, state_dir: Path, github_origin: Path, key: OperatorKey) -> Rig:
    return Rig(repo, state_dir, key)


@pytest.fixture
def source(repo: Path, github_origin: Path) -> str:
    change_on(repo, BRANCH, {"backend/src/first.py": "A = 1\n"}, message="first")
    return change_on(repo, BRANCH, {"backend/src/second.py": "B = 2\n"}, message="second")


@pytest.fixture
def job(rig: Rig, repo: Path, source: str) -> IntegrationJob:
    return rig.accepted(request_for(repo, source))


def kinds(rig: Rig) -> list[str]:
    return [event["kind"] for event in rig.store.events()]


def refused(rig: Rig, job_id: str, match: str, **kwargs: Any) -> dict[str, Any]:
    before = rig.jobs.load(job_id).to_dict()
    result = rig.approve(job_id, **kwargs)
    assert result["outcome"] == "refused" and not result["changed"], result
    assert any(match in reason for reason in result["reasons"]), result["reasons"]
    assert rig.jobs.load(job_id).to_dict() == before  # nothing but the audit event changes
    return result


# -- a valid approval ------------------------------------------------------------------------------------------

def test_a_signed_bound_record_is_verified_and_recorded_with_an_audit_trail(rig: Rig, job: IntegrationJob,
                                                                            key: OperatorKey):
    path = rig.write_record(record_for(job, paths=["feature_coordinator/x.py"], note="first look"))
    preview = rig.approve(job.job_id, path=path, apply=False)
    assert preview["outcome"] == "would-record" and rig.jobs.load(job.job_id).approvals == []
    done = rig.approve(job.job_id, path=path)
    assert done["outcome"] == "recorded" and done["changed"]
    stored = rig.jobs.load(job.job_id)
    [entry] = stored.approvals
    assert entry["id"] == "apr-0001" and entry["stage"] == "inspect" and entry["operator"] == OPERATOR
    assert entry["source_sha"] == job.source["sha"] and entry["base_sha"] == job.base["expected_sha"]
    assert entry["paths"] == ["feature_coordinator/x.py"] and entry["note"] == "first look"
    assert entry["record"] == path.read_text(encoding="utf-8")  # the exact signed text, re-verifiable later
    assert entry["signature"] == Path(f"{path}.sig").read_text(encoding="ascii")
    assert entry["signer"]["principal"] == OPERATOR and entry["signer"]["key"].startswith("SHA256:")
    assert stored.state is JobState.AWAITING_APPROVAL  # an approval never moves the job by itself
    assert kinds(rig)[-1] == "integration-approval"
    assert "record" not in done["approval"] and "signature" not in done["approval"]  # not echoed in status output
    again = rig.approve(job.job_id, path=path)
    assert again["outcome"] == "existing" and not again["changed"]
    assert rig.jobs.load(job.job_id).revision == stored.revision and kinds(rig)[-1] == "integration-approval"


def test_a_second_approval_is_appended_and_becomes_the_effective_one(rig: Rig, job: IntegrationJob):
    assert rig.approve(job.job_id)["outcome"] == "recorded"
    assert rig.approve(job.job_id, paths=["README.md"])["outcome"] == "recorded"
    stored = rig.jobs.load(job.job_id)
    assert [a["id"] for a in stored.approvals] == ["apr-0001", "apr-0002"]
    assert job_summary(stored)["effective_approval"] == "apr-0002"


# -- expired, early, overlong ------------------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize(("times", "match"), [
    ({"issued_at": stamp(NOW - timedelta(hours=3)), "expires_at": stamp(NOW - timedelta(minutes=1))}, "expired at"),
    ({"issued_at": stamp(NOW + timedelta(hours=1)), "expires_at": stamp(NOW + timedelta(hours=2))},
     "issued in the future"),
    ({"issued_at": stamp(NOW - timedelta(minutes=1)), "expires_at": stamp(NOW + timedelta(days=8))},
     "lifetime exceeds 7 days"),
    ({"issued_at": stamp(NOW), "expires_at": stamp(NOW - timedelta(seconds=1))}, "later than issued_at"),
    ({"expires_at": "tomorrow"}, "UTC timestamp"),
    ({"expires_at": "2026-02-30T10:00:00Z"}, "not a valid date"),
], ids=["expired", "issued-in-future", "overlong", "expires-before-issued", "not-a-time", "impossible-date"])
def test_expired_early_and_overlong_records_are_refused(rig: Rig, job: IntegrationJob, times: dict[str, str],
                                                        match: str):
    refused(rig, job.job_id, match, **times)
    assert kinds(rig)[-1] == "integration-approval-refused"


def test_a_clock_skew_of_a_few_minutes_is_tolerated(rig: Rig, job: IntegrationJob):
    early = {"issued_at": stamp(NOW + timedelta(minutes=2)), "expires_at": stamp(NOW + timedelta(hours=1))}
    assert rig.approve(job.job_id, **early)["outcome"] == "recorded"


# -- binding to the job's pinned inputs -------------------------------------------------------------------------

@pytest.mark.parametrize(("change", "match"), [
    (lambda r: r["source"].update(sha="f" * 40), "pinned source SHA"),
    (lambda r: r["base"].update(sha="e" * 40), "pinned base"),
    (lambda r: r["base"].update(ref="main"), "pinned base"),
    (lambda r: r["source"].update(repo="someone/fork"), "its source is"),
    (lambda r: r["source"].update(ref="integration/other"), "its source is"),
    (lambda r: r.update(issue=99), "Issue #99"),
    (lambda r: r.update(pr=5), "PR 5"),
    (lambda r: r.update(job="int-20990101-0001"), "authorizes job int-20990101-0001"),
    (lambda r: r.update(stage="merge"), "merge stage is M5"),
    (lambda r: r.update(operator="mallory"), "not in github.integration.operators"),
], ids=["source-sha", "base-sha", "base-ref", "source-repo", "source-ref", "issue", "pr", "other-job", "merge-stage",
        "unknown-operator"])
def test_a_record_that_does_not_bind_exactly_this_job_is_refused(rig: Rig, job: IntegrationJob, change: Any,
                                                                 match: str):
    data = record_for(job)
    change(data)
    refused(rig, job.job_id, match, path=rig.write_record(data))


def test_an_approval_is_never_inherited_by_a_superseding_job(rig: Rig, repo: Path, source: str, job: IntegrationJob):
    path = rig.write_record(record_for(job))
    assert rig.approve(job.job_id, path=path)["outcome"] == "recorded"
    abort_job(rig.jobs, job.job_id, "start again", actor="operator", apply=True)
    report = IntegrationIntake(rig.store, rig.config, rig.git).run(request_for(repo, source), apply=True,
                                                                    supersede=job.job_id)
    new = rig.jobs.load(report.job["job_id"])
    assert new.job_id != job.job_id and new.state is JobState.AWAITING_APPROVAL
    refused(rig, new.job_id, f"authorizes job {job.job_id}", path=path)  # same SHAs, other job: not inherited


# -- the signature identifies the operator ----------------------------------------------------------------------

def test_an_unsigned_or_tampered_record_is_refused(rig: Rig, job: IntegrationJob):
    refused(rig, job.job_id, "cannot read", path=rig.write_record(record_for(job), sign=False))
    path = rig.write_record(record_for(job))
    path.write_text(path.read_text(encoding="utf-8").replace('"paths": []', '"paths": ["README.md"]'),
                    encoding="utf-8")
    refused(rig, job.job_id, "does not verify", path=path)


def test_a_key_that_is_not_the_operators_or_another_namespace_is_refused(rig: Rig, job: IntegrationJob,
                                                                         key: OperatorKey):
    stranger = key.new_key("intruder", allow=False)
    refused(rig, job.job_id, "does not verify", path=rig.write_record(record_for(job), key=stranger))
    # a git commit signature (namespace "git") made with the operator's own key is not an approval
    refused(rig, job.job_id, "does not verify", path=rig.write_record(record_for(job), namespace="git"))


def test_a_valid_signature_of_someone_who_is_not_an_operator_is_refused(rig: Rig, job: IntegrationJob,
                                                                        key: OperatorKey):
    other = key.new_key("mallory")  # in the allowed-signers file, but not in github.integration.operators
    refused(rig, job.job_id, "not in github.integration.operators",
            path=rig.write_record(record_for(job, operator="mallory"), key=other))
    # and the operator's name on a record signed with mallory's key does not verify
    refused(rig, job.job_id, "does not verify", path=rig.write_record(record_for(job), key=other))


@pytest.mark.parametrize("problem", ["missing", "unconfigured", "group-writable", "symlink", "inside-checkout"])
def test_the_allowed_signers_file_must_be_trustworthy(repo: Path, state_dir: Path, github_origin: Path,
                                                      source: str, key: OperatorKey, tmp_path: Path, problem: str):
    signers: Path | str = key.allowed
    if problem == "missing":
        signers = tmp_path / "nowhere" / "allowed_signers"
    elif problem == "unconfigured":
        signers = ""
    elif problem == "group-writable":
        key.allowed.chmod(0o620)
    elif problem == "symlink":
        signers = tmp_path / "operator-home" / "link"
        Path(signers).symlink_to(key.allowed)
    elif problem == "inside-checkout":
        repo.chmod(0o755)  # so that only the location is wrong
        signers = repo / "allowed_signers"
        Path(signers).write_bytes(key.allowed.read_bytes())
        Path(signers).chmod(0o600)
    rig = Rig(repo, state_dir, key, config=integration_config(signers))
    job = rig.accepted(request_for(repo, source))
    match = {"missing": "cannot be read", "unconfigured": "not configured", "group-writable": "writable by group",
             "symlink": "regular file", "inside-checkout": "inside the repository"}[problem]
    refused(rig, job.job_id, match)


# -- scope -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["content/en/words.json", ".env", "backend/data/db.sqlite", "data/x.csv",
                                  "test_environment/mxfix/run.py"])
def test_a_protected_path_can_never_be_authorized(rig: Rig, job: IntegrationJob, path: str):
    refused(rig, job.job_id, "protected path", paths=[path])


@pytest.mark.parametrize(("text", "match"), [
    ("not json", "not valid JSON"),
    ('{"kind": "w47-integration-approval", "kind": "x"}', "more than once"),
    ("[1, 2]", "must be a JSON object"),
    ('{"kind": NaN}', "not JSON"),
    ("{}", "missing field"),
], ids=["not-json", "duplicate-key", "array", "nan", "empty"])
def test_malformed_records_are_refused(rig: Rig, job: IntegrationJob, text: str, match: str):
    refused(rig, job.job_id, match, path=rig.write_record({}, text=text))


@pytest.mark.parametrize(("change", "match"), [
    (lambda r: r.update(extra=1), "unknown field"),
    (lambda r: r.pop("expires_at"), "missing field"),
    (lambda r: r.update(paths=["../etc/passwd"]), "repository-relative"),
    (lambda r: r.update(paths=["/abs"]), "repository-relative"),
    (lambda r: r.update(paths=["a", "a"]), "more than once"),
    (lambda r: r.update(paths="README.md"), "list of at most"),
    (lambda r: r.update(accept_unverified_provenance="yes"), "true or false"),
    (lambda r: r["source"].update(sha="ABC"), "40-character"),
    (lambda r: r.update(version=2), "version 1"),
    (lambda r: r.update(note="two\nlines"), "one line"),
], ids=["extra-key", "missing-key", "dotdot-path", "absolute-path", "duplicate-path", "paths-text", "accept-text",
        "short-sha", "version", "note-newline"])
def test_records_are_parsed_strictly(rig: Rig, job: IntegrationJob, change: Any, match: str):
    data = record_for(job)
    change(data)
    refused(rig, job.job_id, match, path=rig.write_record(data))


def test_only_a_job_awaiting_approval_or_inspecting_takes_an_approval(rig: Rig, job: IntegrationJob):
    path = rig.write_record(record_for(job))
    abort_job(rig.jobs, job.job_id, "not wanted", actor="operator", apply=True)
    refused(rig, job.job_id, "terminal jobs are never reopened", path=path)


def test_an_unknown_job_is_an_error(rig: Rig, job: IntegrationJob):
    with pytest.raises(IntegrationError, match="unknown integration job"):
        rig.approve("int-20990101-0009", path=rig.write_record(record_for(job)))


# -- the stored approvals are tamper-evident and append-only ------------------------------------------------------

def test_stored_approvals_are_tamper_evident_and_append_only(rig: Rig, job: IntegrationJob):
    assert rig.approve(job.job_id)["outcome"] == "recorded"
    assert rig.approve(job.job_id, paths=["README.md"])["outcome"] == "recorded"
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    pristine = path.read_text(encoding="utf-8")
    for mutate, match in (
            (lambda d: d["approvals"][0].update(paths=["feature_coordinator/evil.py"]), "differ from the signed"),
            (lambda d: d["approvals"][0].update(record=d["approvals"][0]["record"].replace("inspect it", "x")),
             "does not match its SHA-256"),
            (lambda d: d["approvals"][1].update(id="apr-0007"), "apr-0002"),
            (lambda d: d["approvals"][0]["signer"].update(principal="mallory"), "signer must be"),
            (lambda d: d["approvals"][0].update(source_sha="f" * 40), "differ from the signed")):
        data = json.loads(pristine)
        mutate(data)
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(StateDirError, match=match):
            rig.jobs.load(job.job_id)
    path.write_text(pristine, encoding="utf-8")
    stored = rig.jobs.load(job.job_id)
    stored.approvals.pop(0)
    with rig.store.transaction(), pytest.raises(IntegrationError, match="approvals is append-only"):
        rig.jobs.save(stored)


# -- the CLI ----------------------------------------------------------------------------------------------------

def config_file(tmp_path: Path, key: OperatorKey) -> Path:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["integration"] = {"enabled": True, "known_authors": [KNOWN], "operators": [OPERATOR],
                                     "allowed_signers": str(key.allowed), "trusted_logins": [OPERATOR]}
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_cli_approve_dry_run_apply_and_refusal(rig: Rig, job: IntegrationJob, key: OperatorKey, tmp_path: Path,
                                               capsys: pytest.CaptureFixture[str]):
    config = config_file(tmp_path, key)
    base = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config", str(config), "integration"]
    path = rig.write_record(record_for(job))
    argv = [*base, "approve", "--job", job.job_id, "--stage", "inspect", "--record", str(path)]
    assert cli_main(argv) == 0
    out = capsys.readouterr().out
    assert "approve: would-record" in out and "dry run" in out
    assert cli_main([*argv, "--apply", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["outcome"] == "recorded" and data["approval"]["id"] == "apr-0001"
    assert cli_main([*base, "status", "--job", job.job_id]) == 0
    assert "approvals: 1; effective apr-0001 by spyderbg" in capsys.readouterr().out
    expired = rig.write_record(record_for(job, issued_at=stamp(NOW - timedelta(days=2)),
                                          expires_at=stamp(NOW - timedelta(days=1))))
    code = cli_main([*base, "approve", "--job", job.job_id, "--stage", "inspect", "--record", str(expired), "--apply",
                     "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1 and not data["ok"] and data["outcome"] == "refused" and "expired" in data["reasons"][0]
    with pytest.raises(SystemExit) as exc:
        cli_main([*base, "approve", "--job", job.job_id, "--stage", "merge", "--record", str(path)])
    assert exc.value.code == 2  # only the inspect stage exists in M2


def test_cli_approve_with_a_separate_signature_file(rig: Rig, job: IntegrationJob, key: OperatorKey, tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]):
    config = config_file(tmp_path, key)
    path = rig.write_record(record_for(job))
    moved = tmp_path / "elsewhere.sig"
    os.replace(f"{path}.sig", moved)
    argv = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config", str(config), "integration",
            "approve", "--job", job.job_id, "--stage", "inspect", "--record", str(path), "--json"]
    assert cli_main(argv) == 1 and "cannot read" in json.loads(capsys.readouterr().out)["reasons"][0]
    assert cli_main([*argv, "--signature", str(moved), "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "recorded"


def test_config_keys_are_validated(tmp_path: Path):
    from feature_coordinator.config import config_from_mapping  # pylint: disable=import-outside-toplevel
    from feature_coordinator.errors import ConfigError  # pylint: disable=import-outside-toplevel
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    for bad, match in (({"operators": "spyderbg"}, "list of GitHub logins"),
                       ({"operators": ["bad login"]}, "GitHub logins"),
                       ({"trusted_logins": ["a", "A"]}, "more than once"),
                       ({"allowed_signers": "relative/path"}, "absolute file path"),
                       ({"allowed_signers": 5}, "absolute file path")):
        data["github"]["integration"] = bad
        with pytest.raises(ConfigError, match=match):
            config_from_mapping(data)
    data["github"]["integration"] = {"operators": [" spyderbg "], "allowed_signers": " /etc/x ",
                                     "trusted_logins": ["Octo-Cat"]}
    loaded = config_from_mapping(data).github.integration
    assert loaded.operators == ("spyderbg",) and loaded.allowed_signers == "/etc/x"
    assert loaded.trusted_logins == ("Octo-Cat",) and not loaded.enabled


def test_an_older_record_never_replaces_a_newer_effective_approval(rig: Rig, job: IntegrationJob):
    """Review finding 4: a broader record signed earlier but never recorded must not undo a later, narrower one."""
    broad = rig.write_record(record_for(job, paths=["README.md", "feature_coordinator/x.py"],
                                        issued_at=stamp(NOW - timedelta(hours=2))))
    narrow = rig.write_record(record_for(job, paths=[], issued_at=stamp(NOW - timedelta(minutes=5))))
    assert rig.approve(job.job_id, path=narrow)["outcome"] == "recorded"
    result = refused(rig, job.job_id, "never replaced by an older record", path=broad)
    assert result["reasons"] and kinds(rig)[-1] == "integration-approval-refused"
    assert rig.approve(job.job_id, path=narrow)["outcome"] == "existing"  # replaying the newest is still a no-op


def test_stored_approvals_out_of_issue_order_fail_closed(rig: Rig, job: IntegrationJob):
    first = rig.write_record(record_for(job, issued_at=stamp(NOW - timedelta(hours=2))))
    second = rig.write_record(record_for(job, paths=["README.md"], issued_at=stamp(NOW - timedelta(minutes=5))))
    assert rig.approve(job.job_id, path=first)["outcome"] == "recorded"
    assert rig.approve(job.job_id, path=second)["outcome"] == "recorded"
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["approvals"] = list(reversed(data["approvals"]))
    for index, entry in enumerate(data["approvals"], 1):
        entry["id"] = f"apr-{index:04d}"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match="issue order"):
        rig.jobs.load(job.job_id)


# -- Codex review F1: approval ordering at full timestamp precision ---------------------------------------------------

def a_second_ago() -> str:
    """`YYYY-MM-DDTHH:MM:SS` one minute ago: records then differ only in their fraction of a second."""
    return (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")


def test_record_times_keep_their_full_precision():
    whole = parse_record_time("2026-11-12T09:00:00Z", "t")
    assert parse_record_time("2026-11-12T09:00:00.1Z", "t") - whole == timedelta(milliseconds=100)
    assert parse_record_time("2026-11-12T09:00:00.900000Z", "t") - whole == timedelta(milliseconds=900)
    assert parse_record_time("2026-11-12T09:00:00.000001Z", "t") > whole
    assert parse_record_time("2026-11-12T09:00:00.100000Z", "t") == parse_record_time("2026-11-12T09:00:00.1Z", "t")
    for bad in ("2026-11-12T09:00:00.1234567Z", "2026-11-12T09:00:00.Z", "2026-11-12T09:00:00.5"):
        with pytest.raises(IntegrationError, match="UTC timestamp"):
            parse_record_time(bad, "t")


def test_codex_f1_an_older_broader_record_from_the_same_second_cannot_replace_a_newer_narrower_one(
        rig: Rig, job: IntegrationJob):
    """Codex's scenario: broad at T.100000Z authorizes AGENTS.md, narrow at T.900000Z authorizes nothing; the narrow
    one is recorded first. Before the fix both parsed to T and the older broad record became effective."""
    second = a_second_ago()
    broad = rig.write_record(record_for(job, issued_at=f"{second}.100000Z", paths=["AGENTS.md"], note="older broad"))
    narrow = rig.write_record(record_for(job, issued_at=f"{second}.900000Z", paths=[], note="newer narrow"))
    assert rig.approve(job.job_id, path=narrow)["outcome"] == "recorded"
    result = refused(rig, job.job_id, "never replaced by an older record", path=broad)
    assert f"{second}.100000Z" in result["reasons"][0] and result["effective"] is False
    stored = rig.jobs.load(job.job_id)
    assert [a["note"] for a in stored.approvals] == ["newer narrow"]
    assert effective_approval(stored)["paths"] == [] and kinds(rig)[-1] == "integration-approval-refused"


def test_a_newer_record_from_the_same_second_does_supersede_an_older_one(rig: Rig, job: IntegrationJob):
    second = a_second_ago()
    broad = rig.write_record(record_for(job, issued_at=f"{second}.100000Z", paths=["AGENTS.md"], note="older broad"))
    narrow = rig.write_record(record_for(job, issued_at=f"{second}.9Z", paths=[], note="newer narrow"))
    assert rig.approve(job.job_id, path=broad)["outcome"] == "recorded"
    assert rig.approve(job.job_id, path=narrow)["outcome"] == "recorded"
    assert effective_approval(rig.jobs.load(job.job_id))["note"] == "newer narrow"


@pytest.mark.parametrize("spelling", ["Z", ".0Z", ".000000Z"], ids=["identical-text", "short-zero", "long-zero"])
def test_distinct_records_issued_at_the_same_instant_are_ambiguous_and_refused(rig: Rig, job: IntegrationJob,
                                                                               spelling: str):
    second = a_second_ago()
    first = rig.write_record(record_for(job, issued_at=f"{second}Z", paths=[], note="signed first"))
    other = rig.write_record(record_for(job, issued_at=f"{second}{spelling}", paths=["AGENTS.md"],
                                        note="signed second"))
    assert rig.approve(job.job_id, path=first)["outcome"] == "recorded"
    refused(rig, job.job_id, "order of the two decisions is ambiguous", path=other)
    assert rig.approve(job.job_id, path=other, apply=False)["outcome"] == "refused"   # the dry run says so too
    assert effective_approval(rig.jobs.load(job.job_id))["note"] == "signed first"


def test_replaying_a_record_is_idempotent_and_never_revives_a_superseded_approval(rig: Rig, job: IntegrationJob):
    second = a_second_ago()
    broad = rig.write_record(record_for(job, issued_at=f"{second}.100000Z", paths=["AGENTS.md"], note="broad"))
    narrow = rig.write_record(record_for(job, issued_at=f"{second}.900000Z", paths=[], note="narrow"))
    assert rig.approve(job.job_id, path=broad)["effective"] is True
    assert rig.approve(job.job_id, path=narrow)["effective"] is True
    before, events = rig.jobs.load(job.job_id).to_dict(), len(rig.store.events())
    replay = rig.approve(job.job_id, path=broad)
    assert replay["outcome"] == "existing" and not replay["changed"] and replay["effective"] is False
    assert replay["approval"]["id"] == "apr-0001" and "supersedes" in replay["reasons"][0]
    again = rig.approve(job.job_id, path=narrow)
    assert again["outcome"] == "existing" and again["effective"] is True and again["reasons"] == []
    assert rig.jobs.load(job.job_id).to_dict() == before and len(rig.store.events()) == events
    assert effective_approval(rig.jobs.load(job.job_id))["paths"] == []        # the scope did not expand
    # the same content re-serialized is a different signed record issued at the same instant: refused, not replayed
    compact = rig.write_record({}, text=json.dumps(json.loads(narrow.read_text(encoding="utf-8")),
                                                   separators=(",", ":")))
    refused(rig, job.job_id, "ambiguous", path=compact)


def test_cli_shows_whether_a_replayed_record_is_effective(rig: Rig, job: IntegrationJob, key: OperatorKey,
                                                          tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config = config_file(tmp_path, key)
    base = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config", str(config), "integration",
            "approve", "--job", job.job_id, "--stage", "inspect", "--apply"]
    second = a_second_ago()
    broad = rig.write_record(record_for(job, issued_at=f"{second}.1Z", paths=["AGENTS.md"]))
    narrow = rig.write_record(record_for(job, issued_at=f"{second}.2Z"))
    for path in (broad, narrow):
        assert cli_main([*base, "--record", str(path)]) == 0
    capsys.readouterr()
    assert cli_main([*base, "--record", str(broad)]) == 0
    out = capsys.readouterr().out
    assert "approve: existing" in out and "superseded, authorizes nothing" in out and "note: the record" in out
    assert f"issued {second}.1Z" in out


def test_replacement_is_decided_atomically_inside_the_coordinator_transaction(rig: Rig, job: IntegrationJob,
                                                                             monkeypatch: pytest.MonkeyPatch):
    """A newer approval recorded between the pre-check and the transaction (a concurrent approve) still wins."""
    second = a_second_ago()
    older = rig.write_record(record_for(job, issued_at=f"{second}.100000Z", paths=["AGENTS.md"], note="older"))
    newer = rig.write_record(record_for(job, issued_at=f"{second}.900000Z", paths=[], note="newer"))
    real = integration_approval._settled
    raced: list[str] = []

    def settled_then_race(current: IntegrationJob, record: dict[str, Any], record_sha: str) -> Any:
        answer = real(current, record, record_sha)
        if not raced:                      # the outer pre-check of the older record, outside the transaction
            raced.append(record["note"])
            assert rig.approve(job.job_id, path=newer)["outcome"] == "recorded"
        return answer

    monkeypatch.setattr(integration_approval, "_settled", settled_then_race)
    result = rig.approve(job.job_id, path=older)
    assert raced == ["older"] and result["outcome"] == "refused" and not result["changed"]
    reason = result["reasons"][0]
    assert "never replaced by an older record" in reason and "nothing was recorded" in reason
    stored = rig.jobs.load(job.job_id)
    assert [a["note"] for a in stored.approvals] == ["newer"] and kinds(rig)[-1] == "integration-approval-refused"


def test_a_record_that_expires_before_it_is_recorded_is_refused(rig: Rig, job: IntegrationJob):
    now = datetime.now(timezone.utc)
    path = rig.write_record(record_for(job, expires_at=stamp(now + timedelta(hours=1))))
    readings = iter([now, now + timedelta(hours=2)])      # verification, then the transaction (fresh reading)
    result = rig.approve(job.job_id, path=path, clock=lambda: next(readings))
    assert result["outcome"] == "refused" and "expired" in result["reasons"][0]
    assert rig.jobs.load(job.job_id).approvals == [] and kinds(rig)[-1] == "integration-approval-refused"


APPROVE_DRIVER = """
    import sys
    from feature_coordinator.cli import main
    sys.exit(main(sys.argv[1:]))
"""


def test_concurrent_approvals_from_several_processes_leave_the_newest_issued_record_effective(
        rig: Rig, job: IntegrationJob, key: OperatorKey, tmp_path: Path):
    config = config_file(tmp_path, key)
    base = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config", str(config), "integration",
            "approve", "--job", job.job_id, "--stage", "inspect", "--apply", "--json"]
    second = a_second_ago()
    fractions = [".100000Z", ".300000Z", ".500000Z", ".700000Z", ".900000Z"]
    records = {f: rig.write_record(record_for(job, issued_at=second + f, note=f"issued {f}",
                                              paths=[] if f == ".900000Z" else ["AGENTS.md"])) for f in fractions}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(fractions)) as pool:
        results = list(pool.map(lambda f: run_py(APPROVE_DRIVER, *base, "--record", str(records[f])),
                                list(reversed(fractions))))
    answers = [json.loads(result.stdout) for result in results]
    assert {a["outcome"] for a in answers} <= {"recorded", "refused"}, answers
    for answer in answers:
        if answer["outcome"] == "refused":
            assert "older record" in answer["reasons"][0], answer
    stored = rig.jobs.load(job.job_id)
    issued = [parse_record_time(a["issued_at"], "issued_at") for a in stored.approvals]
    assert issued == sorted(issued) and len(set(issued)) == len(issued)
    newest = effective_approval(stored)
    assert newest["note"] == "issued .900000Z" and newest["paths"] == []


def test_stored_approvals_issued_at_the_same_instant_fail_closed(rig: Rig, job: IntegrationJob):
    second = a_second_ago()
    first = rig.write_record(record_for(job, issued_at=f"{second}.1Z"))
    other = rig.write_record(record_for(job, issued_at=f"{second}.9Z", paths=["README.md"]))
    assert rig.approve(job.job_id, path=first)["outcome"] == "recorded"
    assert rig.approve(job.job_id, path=other)["outcome"] == "recorded"
    path = rig.store.root / "integration" / f"{job.job_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data["approvals"][1]                       # rewrite its record so that it ties with the first one
    entry["record"] = entry["record"].replace(f"{second}.9Z", f"{second}.100Z")
    entry["record_sha256"] = hashlib.sha256(entry["record"].encode("utf-8")).hexdigest()
    entry["issued_at"] = f"{second}.100Z"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateDirError, match="strictly increasing issue order"):
        rig.jobs.load(job.job_id)
