"""W4.7 (M1): integration intake, status and abort on disposable repositories (integration_intake.py,
integration_commands.py, integration_config.py).

Every test uses a temporary checkout with a bare repository as its `origin` (the conftest `repo` fixture plus
`origin` below) and a temporary state directory. The bare remote counts as github.com/<github.repository> only
through a monkeypatch of the URL check. PRs come from the in-memory or JSON-persisted mock tracker. No network, no
real GitHub, and the developer's checkout and coordinator state are never touched. Several tests assert that no
Git ref, object, index or working-tree file changes — in the checkout or in its remote."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,too-many-lines

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import json
import os
import subprocess
import zlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from feature_coordinator import integration_intake
from feature_coordinator.cli import EXAMPLE_CONFIG, build_parser
from feature_coordinator.cli import main as cli_main
from feature_coordinator.commands import cmd_integrate
from feature_coordinator.config import config_from_mapping, default_config, load_config
from feature_coordinator.demo import DEMO_AUTHOR
from feature_coordinator.errors import (ConfigError, GitConfigExecutionError, GitHubUnavailableError,
                                        RemoteUnavailableError)
from feature_coordinator.integration import IntakeRequest, IntegrationError, IntegrationJobs, JobState, abort_job
from feature_coordinator.integration_config import GitHubIntegrationConfig
from feature_coordinator.integration_intake import IntegrationIntake, ReadOnlyGit
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.issues import MockIssueTracker
from feature_coordinator.store import StateStore

from fc_helpers import git, run_py

REPOSITORY = default_config().github.repository  # spyderbg/mx-admin-cloud
KNOWN = DEMO_AUTHOR[1]
STRANGER = ("Somebody Else", "stranger@example.invalid")
BRANCH = "integration/improvements"
# Every Git subcommand the intake may run (ReadOnlyGit, plus GitCoordinator's bounded ls-remote).
READ_ONLY_GIT = {"config", "remote", "ls-remote", "cat-file", "merge-base", "rev-list", "rev-parse"}


def integration_config(**changes: Any):
    config = default_config()
    fields = {"enabled": True, "known_authors": (KNOWN,), **changes}
    return dataclasses.replace(config, github=dataclasses.replace(config.github,
                                                                  integration=GitHubIntegrationConfig(**fields)))


@pytest.fixture
def origin(tmp_path: Path, repo: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=dev", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "dev")
    return bare


@pytest.fixture
def github_origin(monkeypatch: pytest.MonkeyPatch, origin: Path) -> Path:
    """The bare remote counts as github.com/<github.repository> (its URL is a path in the tests)."""
    real = integration_intake.github_repository_of
    monkeypatch.setattr(integration_intake, "github_repository_of",
                        lambda url, host: REPOSITORY if url == str(origin) else real(url, host))
    return origin


def commit_on(repo: Path, branch: str, name: str, *, author: tuple[str, str] = DEMO_AUTHOR,
              committer: tuple[str, str] | None = None, start: str | None = None, push: bool = True) -> str:
    """One commit on *branch* (created from *start*, default dev, when missing), pushed to origin; HEAD stays dev."""
    exists = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                            capture_output=True, check=False).returncode == 0
    git(repo, "checkout", "-q", branch if exists else "-b", *(() if exists else (branch, start or "dev")))
    (repo / "backend" / "src" / f"{name}.py").write_text(f"NAME = {name!r}\n", encoding="utf-8")
    git(repo, "add", "-A")
    who = committer or author
    subprocess.run(["git", "-C", str(repo), "-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}", "commit", "-q",
                    "--author", f"{author[0]} <{author[1]}>", "-m", name], check=True, capture_output=True)
    sha = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "dev")
    if push:
        git(repo, "push", "-q", "origin", branch)
    return sha


def head(repo: Path, ref: str = "dev") -> str:
    return git(repo, "rev-parse", ref).strip()


def req(repo: Path, source_sha: str, **changes: Any) -> IntakeRequest:
    fields = {"source_repo": REPOSITORY, "source_ref": BRANCH, "source_sha": source_sha, "base_ref": "dev",
              "base_sha": head(repo), "issue": 63}
    fields.update(changes)
    return IntakeRequest(**fields)


class Rig:  # pylint: disable=too-few-public-methods
    """An intake over one disposable checkout and state directory."""

    def __init__(self, repo: Path, state: Path, *, config=None, client: Any = None) -> None:
        self.repo, self.store = repo, StateStore(state)
        self.config = config or integration_config()
        self.jobs = IntegrationJobs(self.store)
        self.client = client
        self.git = ReadOnlyGit(repo, self.config, self.store.locks, timeout=30)

    def intake(self, request: IntakeRequest, *, apply: bool = True, faults: tuple[str, ...] = (), **kwargs: Any):
        pr_client = (lambda: self.client) if self.client is not None else None
        return IntegrationIntake(self.store, self.config, self.git, pr_client=pr_client,
                                 faults=faults).run(request, apply=apply, **kwargs)


@pytest.fixture
def rig(repo: Path, state_dir: Path, github_origin: Path) -> Rig:
    del github_origin
    return Rig(repo, state_dir)


@pytest.fixture
def source(repo: Path, github_origin: Path) -> str:
    del github_origin
    commit_on(repo, BRANCH, "first")
    return commit_on(repo, BRANCH, "second")


def tree_digest(git_dir: Path) -> str:
    """SHA-256 over every file (path and content) under a Git directory: refs, objects, index, config, logs."""
    digest = hashlib.sha256()
    for path in sorted(p for p in git_dir.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(git_dir)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def snapshot(repo: Path, bare: Path) -> dict[str, Any]:
    return {"git": tree_digest(repo / ".git"), "origin": tree_digest(bare),
            "status": git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
            "head": git(repo, "symbolic-ref", "HEAD"),
            "files": sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if ".git" not in p.parts)}


# -- valid intake --------------------------------------------------------------------------------------

def test_valid_intake_records_a_job_awaiting_approval(rig: Rig, repo: Path, source: str):
    report = rig.intake(req(repo, source))
    assert report.outcome == "accepted" and report.created and report.ok
    job = rig.jobs.load(report.job["job_id"])
    assert job.state is JobState.AWAITING_APPROVAL
    assert job.source == {"repo": REPOSITORY, "ref": BRANCH, "sha": source}
    assert job.base == {"ref": "dev", "expected_sha": head(repo)}
    assert job.provenance["commits"] == 2 and job.provenance["head_commit"] == source
    assert job.provenance["authors"] == [f"{DEMO_AUTHOR[0]} <{KNOWN}>"]
    assert [(h["from"], h["to"]) for h in job.history] == [(None, "INTAKE"), ("INTAKE", "AWAITING_APPROVAL")]
    assert job.attention is None and job.deferred is None and job.approvals == [] and job.candidate is None
    kinds = [e["kind"] for e in rig.store.events()]
    assert kinds == ["integration-intake", "integration-transition"]
    assert {c["check"]: c["status"] for c in report.checks} == {
        "allowlist": "pass", "base": "pass", "source-ref": "pass", "base-ref": "pass", "objects": "pass",
        "range": "pass", "history": "pass", "authors": "pass"}
    assert job.provenance["history"] == {"boundary_parents": 1, "base_commits_read": 1}


def test_dry_run_checks_but_records_nothing(rig: Rig, repo: Path, source: str):
    report = rig.intake(req(repo, source), apply=False)
    assert report.outcome == "would-accept" and report.ok and report.job is None and not report.created
    assert not (rig.store.root / "integration").exists() and rig.store.events() == []


def test_apply_needs_the_integration_switch_but_a_dry_run_does_not(repo: Path, state_dir: Path, github_origin: Path,
                                                                  source: str):
    del github_origin
    off = Rig(repo, state_dir, config=default_config())
    assert off.intake(req(repo, source), apply=False).outcome == "would-refuse"  # no known author configured
    with pytest.raises(IntegrationError, match="github.integration.enabled is false"):
        off.intake(req(repo, source))
    assert not (state_dir / "integration").exists()


# -- refusals (INTAKE → REFUSED; never STALE) ----------------------------------------------------------------

def refused(rig: Rig, request: IntakeRequest) -> tuple[Any, Any]:
    report = rig.intake(request)
    assert report.outcome == "refused" and not report.ok
    job = rig.jobs.load(report.job["job_id"])
    assert job.state is JobState.REFUSED and job.terminal and job.attention
    assert job.attention["reasons"] == report.reasons
    return report, job


def test_source_ref_not_at_the_stated_sha_is_refused_not_stale(rig: Rig, repo: Path, source: str):
    moved = commit_on(repo, BRANCH, "third")
    _report, job = refused(rig, req(repo, source))
    assert job.attention["observed"]["source_ref_sha"] == moved and job.attention["observed"]["source_sha"] == source
    assert "is at" in job.attention["reason"] and source in job.attention["reason"]
    assert [h["to"] for h in job.history] == ["INTAKE", "REFUSED"]


def test_base_not_at_the_stated_sha_is_refused(rig: Rig, repo: Path, source: str):
    stated = head(repo)
    commit_on(repo, "dev", "base-moved")
    _report, job = refused(rig, req(repo, source, base_sha=stated))
    assert job.attention["observed"]["base_ref_sha"] == head(repo) != stated


def test_missing_source_branch_and_unresolvable_sha_are_refused(rig: Rig, repo: Path, source: str):
    _report, job = refused(rig, req(repo, source, source_ref="integration/missing"))
    assert "does not exist on origin" in job.attention["reason"]
    _report, job = refused(rig, req(repo, "0" * 40, source_ref=BRANCH, issue=64))  # well-formed, resolves to nothing
    assert job.attention["observed"]["source_ref_sha"] == source


def test_unknown_author_is_refused_and_listed(rig: Rig, repo: Path, source: str):
    del source
    tip = commit_on(repo, BRANCH, "foreign", author=STRANGER)
    _report, job = refused(rig, req(repo, tip))
    assert job.attention["observed"]["unknown_authors"] == [STRANGER[1]]
    assert job.provenance is None


def test_an_unknown_committer_alone_is_accepted(rig: Rig, repo: Path):
    tip = commit_on(repo, BRANCH, "web-merge", committer=("GitHub", "noreply@github.com"))
    job = rig.jobs.load(rig.intake(req(repo, tip)).job["job_id"])
    assert job.state is JobState.AWAITING_APPROVAL
    assert job.provenance["committers"] == ["GitHub <noreply@github.com>"]


def test_a_repository_mailmap_cannot_turn_a_stranger_into_a_known_author(rig: Rig, repo: Path):
    (repo / ".mailmap").write_text(f"{DEMO_AUTHOR[0]} <{KNOWN}> <{STRANGER[1]}>\n", encoding="utf-8")
    tip = commit_on(repo, BRANCH, "masked", author=STRANGER)
    _report, job = refused(rig, req(repo, tip))
    assert job.attention["observed"]["unknown_authors"] == [STRANGER[1]]


def test_policy_refusals_need_no_remote_read(rig: Rig, repo: Path, source: str, monkeypatch: pytest.MonkeyPatch):
    def no_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a policy refusal must not read the remote")

    monkeypatch.setattr(rig.git, "remote_head", no_network)
    _report, job = refused(rig, req(repo, source, source_repo="someone/fork"))
    assert "not permitted" in job.attention["reason"]
    _report, job = refused(rig, req(repo, source, base_ref="main", issue=64))
    assert "configured base branch 'dev'" in job.attention["reason"]
    _report, job = refused(rig, req(repo, source, source_ref="dev", issue=65))
    assert "source branch is the base branch" in job.attention["reason"]


def test_a_source_already_contained_in_the_base_is_refused(rig: Rig, repo: Path):
    old = head(repo)
    git(repo, "push", "-q", "origin", f"{old}:refs/heads/{BRANCH}")
    commit_on(repo, "dev", "newer")
    _report, job = refused(rig, req(repo, old))
    assert "already contained" in job.attention["reason"]


def test_a_range_above_the_limit_is_refused_not_truncated(rig: Rig, repo: Path, source: str,
                                                          monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(integration_intake, "MAX_INTAKE_COMMITS", 1)
    _report, job = refused(rig, req(repo, source))
    assert "more than 1 commits" in job.attention["reason"]


# -- deferrals (INTAKE stays; the same command resumes the same job) -----------------------------------------

def test_remote_unavailable_defers_and_a_retry_resumes_the_same_job(rig: Rig, repo: Path, source: str,
                                                                    monkeypatch: pytest.MonkeyPatch):
    real = rig.git._git.remote_head

    def down(*_args: Any, **_kwargs: Any) -> None:
        raise RemoteUnavailableError("git ls-remote origin failed: network down")

    monkeypatch.setattr(rig.git._git, "remote_head", down)
    first = rig.intake(req(repo, source))
    assert first.outcome == "deferred" and not first.ok and "network down" in first.deferred
    job = rig.jobs.load(first.job["job_id"])
    assert job.state is JobState.INTAKE and "network down" in job.deferred["reason"]
    monkeypatch.setattr(rig.git._git, "remote_head", real)
    second = rig.intake(req(repo, source))
    assert second.outcome == "accepted" and not second.created and second.job["job_id"] == job.job_id
    job = rig.jobs.load(job.job_id)
    assert job.deferred is None and [h["to"] for h in job.history] == ["INTAKE", "AWAITING_APPROVAL"]
    assert len(rig.jobs.list()) == 1


def test_commits_not_fetched_defer_and_the_intake_never_fetches(rig: Rig, repo: Path, tmp_path: Path,
                                                                github_origin: Path):
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(github_origin), str(other)], check=True)
    git(other, "config", "user.name", DEMO_AUTHOR[0])
    git(other, "config", "user.email", KNOWN)
    git(other, "checkout", "-q", "-b", BRANCH)
    (other / "remote_only.py").write_text("X = 1\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "pushed elsewhere")
    git(other, "push", "-q", "origin", BRANCH)
    tip = head(other, "HEAD")
    first = rig.intake(req(repo, tip))
    assert first.outcome == "deferred" and "not present in this checkout" in first.deferred
    assert not (repo / ".git" / "FETCH_HEAD").exists()
    git(repo, "fetch", "-q", "origin", BRANCH)  # the operator fetches; the intake never does
    second = rig.intake(req(repo, tip))
    assert second.outcome == "accepted" and second.job["job_id"] == first.job["job_id"]


# -- duplicates, conflicts, supersession -----------------------------------------------------------------------

def test_repeated_intake_returns_the_same_job_without_side_effects(rig: Rig, repo: Path, source: str):
    first = rig.intake(req(repo, source))
    record = rig.jobs.load(first.job["job_id"]).to_dict()
    events = rig.store.events()
    for _ in range(3):
        again = rig.intake(req(repo, source))
        assert again.outcome == "existing" and again.ok and not again.created
        assert again.job["job_id"] == first.job["job_id"]
    assert rig.jobs.load(first.job["job_id"]).to_dict() == record  # not even the revision moved
    assert rig.store.events() == events and len(rig.jobs.list()) == 1
    preview = rig.intake(req(repo, source), apply=False)
    assert preview.outcome == "existing" and preview.job["job_id"] == first.job["job_id"]


def test_a_terminal_job_is_returned_and_only_an_explicit_supersede_starts_a_new_attempt(rig: Rig, repo: Path,
                                                                                         source: str):
    first = rig.intake(req(repo, source)).job["job_id"]
    abort_job(rig.jobs, first, "restart later", actor="operator", apply=True)
    again = rig.intake(req(repo, source))
    assert again.outcome == "existing" and not again.ok and again.job["state"] == "ABORTED"
    assert any(f"--supersede {first}" in m for m in again.messages)
    new = rig.intake(req(repo, source), supersede=first)
    assert new.outcome == "accepted" and new.created and new.job["supersedes"] == first
    repeat = rig.intake(req(repo, source), supersede=first)
    assert repeat.outcome == "existing" and repeat.job["job_id"] == new.job["job_id"]
    assert len(rig.jobs.list()) == 2
    with pytest.raises(IntegrationError, match="different inputs"):
        rig.intake(req(repo, source, issue=99), supersede=first)
    with pytest.raises(IntegrationError, match="unknown integration job"):
        rig.intake(req(repo, source), supersede="int-20000101-0001")


def test_an_active_job_on_the_same_branch_sha_or_pr_blocks_another_request(rig: Rig, repo: Path, source: str):
    first = rig.intake(req(repo, source)).job["job_id"]
    newer = commit_on(repo, BRANCH, "third")
    for other in (req(repo, newer), req(repo, source, issue=99)):
        with pytest.raises(IntegrationError, match=f"active integration job already holds.*{first}"):
            rig.intake(other)
    assert [job.job_id for job in rig.jobs.list()] == [first]
    abort_job(rig.jobs, first, "superseded by a newer head", actor="operator", apply=True)
    assert rig.intake(req(repo, newer)).outcome == "accepted"


# -- environment preconditions (nothing recorded) ---------------------------------------------------------------

def test_origin_must_be_exactly_the_configured_repository(repo: Path, state_dir: Path, origin: Path):
    plain = Rig(repo, state_dir)  # no github_origin patch: a path is not github.com/<repository>
    with pytest.raises(IntegrationError, match="is not github.com/spyderbg/mx-admin-cloud"):
        plain.intake(req(repo, "a" * 40))
    git(repo, "config", "--add", "remote.origin.url", str(origin))
    with pytest.raises(IntegrationError, match="exactly one URL"):
        plain.intake(req(repo, "a" * 40))
    assert not (state_dir / "integration").exists()


def test_a_rewritten_origin_url_is_refused(rig: Rig, repo: Path, github_origin: Path):
    git(repo, "config", f"url.{github_origin}.insteadOf", "rewritten:")
    git(repo, "remote", "set-url", "origin", "rewritten:")
    with pytest.raises(IntegrationError, match="rewritten"):
        rig.intake(req(repo, "a" * 40))


def test_repository_config_that_runs_programs_is_refused_before_any_read(rig: Rig, repo: Path, tmp_path: Path):
    marker = tmp_path / "ran"
    git(repo, "config", "core.sshCommand", f"touch {marker}")
    with pytest.raises(GitConfigExecutionError):
        rig.intake(req(repo, "a" * 40))
    assert not marker.exists() and not (rig.store.root / "integration").exists()


# -- the PR given at intake ---------------------------------------------------------------------------------

def tracker(**pr: Any) -> MockIssueTracker:
    client = MockIssueTracker(repository=REPOSITORY)
    client.add_pull_request_as("spyderbg", pr.pop("head", BRANCH), pr.pop("base", "dev"), head_sha=pr.pop("head_sha"),
                               **pr)
    return client


def test_a_matching_open_pr_is_verified_and_recorded(repo: Path, state_dir: Path, github_origin: Path, source: str):
    del github_origin
    rig = Rig(repo, state_dir, client=tracker(head_sha=source, draft=True))
    job = rig.jobs.load(rig.intake(req(repo, source, pr_number=1)).job["job_id"])
    assert job.state is JobState.AWAITING_APPROVAL
    assert job.pr["number"] == 1 and job.pr["head_sha"] == source and job.pr["head_ref"] == BRANCH
    assert job.pr["base_ref"] == "dev" and job.pr["draft"] is True


@pytest.mark.parametrize(("pr", "match"), [
    ({"head_sha": "moved"}, "head commit is"),
    ({"state": "closed"}, "it is closed"),
    ({"merged": True, "state": "closed"}, "it is merged"),
    ({"base": "main"}, "its base is"),
    ({"head": "other-branch"}, "its head is"),
    ({"head_repo": "someone/fork"}, "its head is"),
], ids=["head-moved", "closed", "merged", "other-base", "other-branch", "other-repo"])
def test_a_mismatching_pr_is_refused_not_stale(repo: Path, state_dir: Path, github_origin: Path, source: str,
                                               pr: dict[str, Any], match: str):
    del github_origin
    fields = {"head_sha": source, **pr}
    if fields["head_sha"] == "moved":
        fields["head_sha"] = "f" * 40
    rig = Rig(repo, state_dir, client=tracker(**fields))
    _report, job = refused(rig, req(repo, source, pr_number=1))
    assert match in job.attention["reason"] and job.attention["observed"]["pr"]["number"] == 1


def test_a_missing_pr_is_refused_and_an_unreachable_github_defers(repo: Path, state_dir: Path, github_origin: Path,
                                                                  source: str):
    del github_origin
    rig = Rig(repo, state_dir, client=MockIssueTracker(repository=REPOSITORY))
    _report, job = refused(rig, req(repo, source, pr_number=7))
    assert "does not exist" in job.attention["reason"]
    rig.client.fail.append(("get_pull_request", GitHubUnavailableError("HTTP 502")))
    report = rig.intake(req(repo, source, pr_number=8))
    assert report.outcome == "deferred" and "HTTP 502" in report.deferred


# -- interruption, recovery and concurrency ---------------------------------------------------------------------

@pytest.mark.parametrize("fault", ["after-create", "after-checks"])
def test_a_crash_during_intake_is_resumed_by_the_same_command(rig: Rig, repo: Path, source: str, fault: str):
    with pytest.raises(SimulatedCrash):
        rig.intake(req(repo, source), faults=(fault,))
    [job] = rig.jobs.list()
    assert job.state is JobState.INTAKE
    report = rig.intake(req(repo, source))
    assert report.outcome == "accepted" and report.job["job_id"] == job.job_id and len(rig.jobs.list()) == 1
    assert [h["to"] for h in rig.jobs.load(job.job_id).history] == ["INTAKE", "AWAITING_APPROVAL"]


def test_an_abort_while_the_checks_run_wins(rig: Rig, repo: Path, source: str, monkeypatch: pytest.MonkeyPatch):
    real = IntegrationIntake.check

    def check_then_abort(self: IntegrationIntake, request: IntakeRequest):
        result = real(self, request)
        [job] = self.jobs.list()
        abort_job(self.jobs, job.job_id, "operator stop during intake", actor="operator", apply=True)
        return result

    monkeypatch.setattr(IntegrationIntake, "check", check_then_abort)
    report = rig.intake(req(repo, source))
    assert report.outcome == "changed-meanwhile" and not report.ok
    job = rig.jobs.load(report.job["job_id"])
    assert job.state is JobState.ABORTED and [h["to"] for h in job.history] == ["INTAKE", "ABORTED"]


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


def test_concurrent_identical_intakes_create_exactly_one_job(repo: Path, state_dir: Path, github_origin: Path,
                                                             source: str, tmp_path: Path):
    config = config_file(tmp_path)
    argv = intake_argv(repo, state_dir, config, source) + ["--apply", "--json"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _n: run_py(DRIVER, str(github_origin), *argv), range(6)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    reports = [json.loads(r.stdout) for r in results]
    assert len({r["job"]["job_id"] for r in reports}) == 1
    assert sum(r["created"] for r in reports) == 1
    [job] = IntegrationJobs(StateStore(state_dir)).list()
    assert [h["to"] for h in job.history] == ["INTAKE", "AWAITING_APPROVAL"]
    assert [e["kind"] for e in StateStore(state_dir).events()] == ["integration-intake", "integration-transition"]


# -- no unauthorized Git writes ---------------------------------------------------------------------------------

def git_subcommand(argv: list[str]) -> str:
    """The subcommand of `git [-C DIR] [-c KEY=VALUE]... [--global-flag]... SUBCOMMAND ...`."""
    rest = iter(argv[1:])
    for arg in rest:
        if arg in ("-C", "-c", "--git-dir", "--work-tree"):
            next(rest)
        elif not arg.startswith("-"):
            return arg
    raise AssertionError(f"no subcommand in {argv}")


def test_intake_status_and_abort_never_write_git(repo: Path, state_dir: Path, github_origin: Path, source: str,
                                                 tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    stranger_tip = commit_on(repo, "integration/foreign", "foreign", author=STRANGER)
    config = config_file(tmp_path)
    before = snapshot(repo, github_origin)
    calls: list[list[str]] = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def record(argv: Any) -> None:
        if isinstance(argv, (list, tuple)) and argv and Path(str(argv[0])).name == "git":
            calls.append([str(a) for a in argv])

    def run(argv: Any, *args: Any, **kwargs: Any):
        record(argv)
        return real_run(argv, *args, **kwargs)  # pylint: disable=subprocess-run-check  # the caller's own check

    class Popen(real_popen):  # type: ignore[misc,valid-type]  # pylint: disable=too-few-public-methods
        """subprocess.Popen that records every git argv first."""

        def __init__(self, argv: Any, *args: Any, **kwargs: Any) -> None:
            record(argv)
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    base = ["--repo", str(repo), "--state-dir", str(state_dir), "--project-config", str(config)]
    assert cli_main(intake_argv(repo, state_dir, config, source)) == 0                          # dry run
    assert cli_main(intake_argv(repo, state_dir, config, source) + ["--apply"]) == 0             # accepted
    assert cli_main(intake_argv(repo, state_dir, config, source) + ["--apply"]) == 0             # existing
    assert cli_main(intake_argv(repo, state_dir, config, stranger_tip, ref="integration/foreign") + ["--apply"]) == 1
    [job_id, _refused] = [j.job_id for j in IntegrationJobs(StateStore(state_dir)).list()]
    assert cli_main([*base, "integration", "status"]) == 0
    assert cli_main([*base, "integration", "status", "--job", job_id, "--json"]) == 0
    assert cli_main([*base, "integration", "abort", "--job", job_id, "--reason", "test", "--apply"]) == 0
    monkeypatch.undo()
    assert calls, "the intake reads Git"
    subcommands = {git_subcommand(argv) for argv in calls}
    assert subcommands <= READ_ONLY_GIT, subcommands
    assert snapshot(repo, github_origin) == before


def test_read_only_git_refuses_a_mutating_subcommand(rig: Rig):
    for args in (("push", "origin", "dev"), ("fetch", "origin"), ("update-ref", "refs/heads/x", "a" * 40),
                 ("checkout", "dev"), ("merge", "x"), ("worktree", "add", "x")):
        with pytest.raises(Exception, match="not a read-only query"):
            rig.git._run(*args)


# -- the CLI -------------------------------------------------------------------------------------------------

def config_file(tmp_path: Path, **integration: Any) -> Path:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["integration"] = {"enabled": True, "known_authors": [KNOWN], **integration}
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def intake_argv(repo: Path, state_dir: Path, config: Path, sha: str, *, ref: str = BRANCH,
                extra: tuple[str, ...] = ()) -> list[str]:
    return ["--repo", str(repo), "--state-dir", str(state_dir), "--project-config", str(config), "integration",
            "intake", "--source-repo", REPOSITORY, "--source-ref", ref, "--source-sha", sha, "--base", "dev",
            "--base-sha", head(repo), "--issue", "63", *extra]


def run_cli(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, Any]:
    code = cli_main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out) if "--json" in argv else out


def test_cli_intake_status_abort_round_trip_with_json(repo: Path, state_dir: Path, github_origin: Path, source: str,
                                                      tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    del github_origin
    config = config_file(tmp_path)
    base = ["--repo", str(repo), "--state-dir", str(state_dir), "--project-config", str(config), "integration"]
    code, out = run_cli(capsys, intake_argv(repo, state_dir, config, source))
    assert code == 0 and "intake: would-accept" in out and "dry run" in out
    code, data = run_cli(capsys, intake_argv(repo, state_dir, config, source) + ["--apply", "--json"])
    assert code == 0 and data["ok"] and data["outcome"] == "accepted" and data["job"]["state"] == "AWAITING_APPROVAL"
    job_id = data["job"]["job_id"]
    code, data = run_cli(capsys, [*base, "status", "--json"])
    assert code == 0 and [j["job_id"] for j in data["jobs"]] == [job_id]
    code, data = run_cli(capsys, [*base, "status", "--job", job_id, "--json"])
    assert code == 0 and data["job"]["source"]["sha"] == source and data["job"]["allowed_next"]
    code, out = run_cli(capsys, [*base, "status", "--job", job_id])
    assert code == 0 and "history: - → INTAKE" in out and "INTAKE → AWAITING_APPROVAL" in out
    code, data = run_cli(capsys, [*base, "abort", "--job", job_id, "--reason", "not now", "--json"])
    assert code == 0 and data["outcome"] == "would-abort" and data["job"]["state"] == "AWAITING_APPROVAL"
    code, data = run_cli(capsys, [*base, "abort", "--job", job_id, "--reason", "not now", "--apply", "--json"])
    assert code == 0 and data["outcome"] == "aborted" and data["job"]["state"] == "ABORTED"
    code, data = run_cli(capsys, [*base, "abort", "--job", job_id, "--reason", "not now", "--apply", "--json"])
    assert code == 0 and data["outcome"] == "already-aborted"
    code, data = run_cli(capsys, intake_argv(repo, state_dir, config, source) + ["--apply", "--json"])
    assert code == 1 and data["outcome"] == "existing" and data["job"]["state"] == "ABORTED"


def test_cli_errors_are_meaningful_and_json(repo: Path, state_dir: Path, github_origin: Path, source: str,
                                            tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    del github_origin
    config = config_file(tmp_path)
    base = ["--repo", str(repo), "--state-dir", str(state_dir), "--project-config", str(config), "integration"]
    code = cli_main(intake_argv(repo, state_dir, config, source[:12]) + ["--json"])
    captured = capsys.readouterr()
    assert code == 1 and "40-character" in captured.err
    assert json.loads(captured.out) == {"ok": False, "error": {"type": "IntegrationError",
                                                               "message": json.loads(captured.out)["error"]["message"]}}
    code, data = run_cli(capsys, [*base, "status", "--job", "int-20990101-0001", "--json"])
    assert code == 1 and data["error"]["type"] == "IntegrationError" and "unknown integration job" in \
        data["error"]["message"]
    code, data = run_cli(capsys, [*base, "abort", "--job", "../etc", "--reason", "x", "--json"])
    assert code == 1 and "invalid integration job ID" in data["error"]["message"]
    assert cli_main([*base, "status", "--job", "int-20990101-0001"]) == 1  # plain: `error: …` on stderr
    assert "unknown integration job" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        cli_main([*base, "intake", "--source-repo", REPOSITORY, "--source-ref", BRANCH])  # missing required flags
    assert exc.value.code == 2
    code, data = run_cli(capsys, intake_argv(repo, state_dir, config, source, extra=("--actor", "bad actor!")) +
                         ["--json"])
    assert code == 1 and "--actor" in data["error"]["message"]


def test_cli_apply_with_the_example_configuration_is_refused(repo: Path, state_dir: Path, github_origin: Path,
                                                             source: str, capsys: pytest.CaptureFixture[str]):
    del github_origin
    argv = ["--repo", str(repo), "--state-dir", str(state_dir), "integration", "intake", "--source-repo", REPOSITORY,
            "--source-ref", BRANCH, "--source-sha", source, "--base", "dev", "--base-sha", head(repo), "--issue",
            "63", "--apply"]
    assert cli_main(argv) == 1
    assert "github.integration.enabled is false" in capsys.readouterr().err
    assert not (state_dir / "integration").exists()


def test_cli_pr_check_reads_the_local_tracker(repo: Path, state_dir: Path, github_origin: Path, source: str,
                                              tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    del github_origin
    persisted = MockIssueTracker(state_dir / "tracker.json", repository=REPOSITORY)
    persisted.add_pull_request_as("spyderbg", BRANCH, "dev", head_sha=source)
    config = config_file(tmp_path)
    argv = ["--tracker", "local", *intake_argv(repo, state_dir, config, source, extra=("--pr", "1")), "--apply",
            "--json"]
    code, data = run_cli(capsys, argv)
    assert code == 0 and data["job"]["pr"]["number"] == 1 and data["job"]["pr"]["head_sha"] == source


def test_existing_integrate_feature_command_is_unchanged():
    args = build_parser().parse_args(["integrate", "issue-63", "--apply"])
    assert args.func is cmd_integrate and args.feature == "issue-63" and args.apply
    args = build_parser().parse_args(["integrate", "status"])  # still a FEATURE named "status", not the job command
    assert args.func is cmd_integrate and args.feature == "status"


# -- configuration ---------------------------------------------------------------------------------------------

def test_example_configuration_is_unchanged_and_integration_is_off_by_default():
    loaded = load_config(EXAMPLE_CONFIG)
    assert loaded == default_config()
    assert loaded.github.integration == GitHubIntegrationConfig(enabled=False, known_authors=())


def test_integration_section_is_parsed_strictly(tmp_path: Path):
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["integration"] = {"enabled": True, "known_authors": ["Dev@Example.Invalid "]}
    assert config_from_mapping(data).github.integration.known_authors == ("dev@example.invalid",)
    for bad, match in (({"enabled": "yes"}, "true or false"), ({"merge": True}, "unknown github.integration keys"),
                       ({"enabled": True}, "at least one"), ({"known_authors": "a@b.c"}, "list of e-mail"),
                       ({"known_authors": ["not-an-email"]}, "e-mail addresses"),
                       ({"known_authors": ["a@b.c", "A@B.C"]}, "more than once")):
        data["github"]["integration"] = bad
        with pytest.raises(ConfigError, match=match):
            config_from_mapping(data)
    assert config_file(tmp_path).exists()


# =============================================================================================================
# Codex review of M1 (REQUEST_CHANGES): regression tests. Each reproduction runs on disposable repositories.
# =============================================================================================================

def ident(name: str, email: str, when: str = "1700000000 +0000") -> bytes:
    return f"{name} <{email}> {when}".encode("utf-8")


KNOWN_IDENT = ident(DEMO_AUTHOR[0], KNOWN)


def raw_commit(repo: Path, *, author: bytes = KNOWN_IDENT, committer: bytes = KNOWN_IDENT, parent: str | None = None,
               extra: bytes = b"", message: bytes = b"crafted\n") -> str:
    """Write a commit object byte for byte (`hash-object --literally`: Git does not normalize it)."""
    parent = parent or head(repo)
    tree = git(repo, "rev-parse", f"{parent}^{{tree}}").strip()
    body = (b"tree %s\nparent %s\nauthor %s\ncommitter %s\n%s\n%s"
            % (tree.encode(), parent.encode(), author, committer, extra, message))
    return subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--literally", "--stdin"],
                          input=body, capture_output=True, check=True).stdout.decode().strip()


def publish(repo: Path, sha: str, branch: str = BRANCH) -> str:
    git(repo, "push", "-q", "-f", "origin", f"{sha}:refs/heads/{branch}")
    return sha


def stranger_in_the_middle(repo: Path) -> tuple[str, str, str]:
    """dev → S1 (unknown author) → S2 → S3 (known authors), pushed: (S1, S2, S3)."""
    first = commit_on(repo, BRANCH, "foreign", author=STRANGER)
    second = commit_on(repo, BRANCH, "second")
    return first, second, commit_on(repo, BRANCH, "third")


def loose_object(repo: Path, sha: str) -> Path:
    path = repo / ".git" / "objects" / sha[:2] / sha[2:]
    assert path.exists(), "the test needs the commit as a loose object"
    return path


def never_accepted(rig: Rig, request: IntakeRequest) -> Any:
    """Dry run and apply: neither may accept; returns the apply report."""
    preview = rig.intake(request, apply=False)
    assert preview.outcome in ("would-refuse", "would-defer") and not preview.ok, preview.outcome
    report = rig.intake(request)
    assert report.outcome in ("refused", "deferred") and not report.ok, report.outcome
    assert rig.jobs.load(report.job["job_id"]).state in (JobState.REFUSED, JobState.INTAKE)
    return report


# -- finding 1: identities are parsed from raw, hash-verified commit objects ---------------------------------

def body(*, author: bytes = KNOWN_IDENT, committer: bytes = KNOWN_IDENT, extra: bytes = b"",
         parents: tuple[str, ...] = ("b" * 40,), header: bytes | None = None) -> bytes:
    if header is not None:
        return header + b"\n\nmessage\n"
    lines = [b"tree " + b"a" * 40] + [b"parent " + p.encode() for p in parents]
    return b"\n".join(lines + [b"author " + author, b"committer " + committer]) + b"\n" + extra + b"\nmessage\n"


def test_parse_commit_reads_the_standard_form_and_its_extra_headers():
    signed = body(extra=b"encoding ISO-8859-1\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n abc\n -----END PGP"
                        b" SIGNATURE-----\nmergetag object " + b"c" * 40 + b"\n type commit\n")
    commit = integration_intake.parse_commit("d" * 40, signed)
    assert commit.parents == ("b" * 40,) and commit.tree == "a" * 40
    assert commit.author.email == KNOWN and commit.author.name == DEMO_AUTHOR[0] and commit.author.time == 1700000000
    merge = integration_intake.parse_commit("d" * 40, body(parents=("b" * 40, "c" * 40)))
    assert merge.parents == ("b" * 40, "c" * 40)
    root = integration_intake.parse_commit("d" * 40, body(parents=()))
    assert not root.parents
    printable = integration_intake.parse_commit("d" * 40, body(author=ident("O'Brien | Ünïcødé; \"x\", %x1f", KNOWN)))
    assert printable.author.name == "O'Brien | Ünïcødé; \"x\", %x1f"


@pytest.mark.parametrize(("raw", "match"), [
    (body(author=ident("Evil\x1eName\x1fX", STRANGER[1])), "control"),
    (body(author=ident("Tab\tName", KNOWN)), "control"),
    (body(author=ident("Evil\u202eemaN", KNOWN)), "bidirectional"),
    (body(author=b"Evil <" + KNOWN.encode() + b"> <stranger@example.invalid> 1700000000 +0000"), "not `Name"),
    (body(author=b"Evil > x <" + KNOWN.encode() + b"> 1700000000 +0000"), "not `Name"),
    (body(author=b"Evil <" + KNOWN.encode() + b"> 1700000000"), "not `Name"),
    (body(author=b"Evil <" + KNOWN.encode() + b">"), "not `Name"),
    (body(author=ident("Evil", "")), "empty"),
    (body(author=ident("Evil", "a b@example.invalid")), "whitespace"),
    (body(author=ident("Evil", "a\x01b@example.invalid")), "control"),
    (body(author=b"Evil <str\xffanger@example.invalid> 1700000000 +0000"), "not UTF-8"),
    (body(committer=ident("Web\x1fFlow", "noreply@github.com")), "committer name"),
    (body(committer=b"GitHub <noreply@github.com>"), "committer is not"),
    (body(extra=b"author Evil <stranger@example.invalid> 1700000000 +0000\n"), "unexpected header"),
    (body(extra=b"committer Evil <stranger@example.invalid> 1700000000 +0000\n"), "unexpected header"),
    (body(extra=b" continuation of the committer\n"), "unexpected header"),
    (body(extra=b"Bad-Key value\n"), "unexpected header"),
    (body(parents=("b" * 40, "b" * 40)), "parent twice"),
    (body(header=b"parent " + b"b" * 40 + b"\ntree " + b"a" * 40), "first header line"),
    (body(header=b"tree " + b"a" * 40 + b"\nparent xyz"), "invalid parent"),
    (body(header=b"tree " + b"a" * 40 + b"\ncommitter " + KNOWN_IDENT + b"\nauthor " + KNOWN_IDENT), "no author"),
    (body(header=b"tree " + b"a" * 40 + b"\nauthor " + KNOWN_IDENT), "no committer"),
    (b"tree " + b"a" * 40 + b"\nauthor " + KNOWN_IDENT + b"\ncommitter " + KNOWN_IDENT + b"\n", "blank line"),
    (body(author=b"Evil\x00 <" + KNOWN.encode() + b"> 1700000000 +0000"), "NUL"),
], ids=["record-and-unit-separators", "tab-in-name", "bidi-in-name", "two-emails", "angle-in-name", "no-zone",
        "no-time", "empty-email", "space-in-email", "control-in-email", "email-not-utf8", "committer-control",
        "committer-without-time", "second-author-line", "second-committer-line", "committer-continuation",
        "bad-extra-key", "duplicate-parent", "parent-before-tree", "bad-parent", "author-after-committer",
        "no-committer", "no-blank-line", "nul-in-header"])
def test_parse_commit_refuses_every_malformed_record(raw: bytes, match: str):
    with pytest.raises(integration_intake.HistoryRefused, match=match):
        integration_intake.parse_commit("d" * 40, raw)


def test_base_commits_are_read_leniently_but_never_structurally_malformed():
    odd = integration_intake.parse_commit("d" * 40, body(author=b"svn-import <> 0"), strict=False)
    assert odd.parents == ("b" * 40,)  # identities are not checked on the base side
    with pytest.raises(integration_intake.HistoryUnverifiable, match="first header line"):
        integration_intake.parse_commit("d" * 40, body(header=b"parent " + b"b" * 40), strict=False)


def test_kelvin_sign_is_not_folded_into_a_known_ascii_address():
    commit = integration_intake.parse_commit("d" * 40, body(author=ident("K", "\u212aate@example.invalid")))
    assert commit.author.key == "\u212aate@example.invalid" != "kate@example.invalid"
    assert integration_intake.parse_commit("d" * 40, body(author=ident("K", "Kate@Example.INVALID"))).author.key \
        == "kate@example.invalid"


def test_git_can_store_delimiters_in_names_and_the_intake_refuses_them(rig: Rig, repo: Path):
    """Codex finding 1: a name with \\x1e/\\x1f (the old %x1e/%x1f log delimiters) made the commit row vanish, so
    an unknown author next to known ones was never checked. Git itself writes such a name."""
    commit_on(repo, BRANCH, "known-first")
    git(repo, "checkout", "-q", BRANCH)
    subprocess.run(["git", "-C", str(repo), "-c", f"user.name={DEMO_AUTHOR[0]}", "-c", f"user.email={KNOWN}", "commit",
                    "-q", "--allow-empty", "-m", "hidden"], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "Evil\x1eName\x1fX", "GIT_AUTHOR_EMAIL": STRANGER[1]})
    git(repo, "checkout", "-q", "dev")
    tip = publish(repo, head(repo, BRANCH))
    commit_on(repo, BRANCH, "known-last", push=False)
    git(repo, "branch", "-q", "-f", BRANCH, tip)
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "refused" and "control" in report.reasons[0]


@pytest.mark.parametrize(("author", "extra", "reason"), [
    (ident("Evil | %x1f; \"q\", 'x'", STRANGER[1]), b"", "not in github.integration.known_authors"),
    (b"Evil <" + KNOWN.encode() + b"> <" + STRANGER[1].encode() + b"> 1700000000 +0000", b"", "malformed"),
    (KNOWN_IDENT, b"author Evil <" + STRANGER[1].encode() + b"> 1700000000 +0000\n", "malformed"),
    (b"Evil <" + KNOWN.encode() + b">\x00 <" + STRANGER[1].encode() + b"> 1 +0000", b"", "malformed"),
    (ident("Evil", "a\tb@example.invalid"), b"", "malformed"),
], ids=["printable-delimiters", "two-emails", "second-author-line", "nul", "tab-in-email"])
def test_crafted_commit_objects_are_never_accepted(rig: Rig, repo: Path, author: bytes, extra: bytes, reason: str):
    known = commit_on(repo, BRANCH, "known")
    crafted = publish(repo, raw_commit(repo, author=author, extra=extra, parent=known))
    tip = publish(repo, raw_commit(repo, parent=crafted, message=b"known on top\n"))
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "refused" and reason in report.reasons[0], report.reasons


def test_a_crafted_committer_is_recorded_safely_or_refused(rig: Rig, repo: Path):
    tip = publish(repo, raw_commit(repo, committer=ident("Web | Flow; \"GitHub\"", "noreply@github.com")))
    job = rig.jobs.load(rig.intake(req(repo, tip)).job["job_id"])
    assert job.state is JobState.AWAITING_APPROVAL
    assert job.provenance["committers"] == ['Web | Flow; "GitHub" <noreply@github.com>']
    bad = publish(repo, raw_commit(repo, committer=ident("Web\x1eFlow", "noreply@github.com")), "integration/other")
    report = never_accepted(rig, req(repo, bad, source_ref="integration/other"))
    assert "committer name" in report.reasons[0]


def test_a_kelvin_sign_address_does_not_pass_for_a_known_one(repo: Path, state_dir: Path, github_origin: Path):
    del github_origin
    rig = Rig(repo, state_dir, config=integration_config(known_authors=("kate@example.invalid",)))
    tip = commit_on(repo, BRANCH, "kelvin", author=("Kate", "\u212aate@example.invalid"))
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "refused"
    assert rig.jobs.load(report.job["job_id"]).attention["observed"]["unknown_authors"] == \
        ["\u212aate@example.invalid"]


def test_a_commit_missing_from_gits_listing_is_detected(rig: Rig, repo: Path, monkeypatch: pytest.MonkeyPatch):
    """Every listed commit must be closed over base: a listing that silently drops a commit cannot pass."""
    first, _second, tip = stranger_in_the_middle(repo)
    real = ReadOnlyGit.range_commits

    def dropping(self: ReadOnlyGit, base: str, head_sha: str, *, limit: int):
        return [row for row in real(self, base, head_sha, limit=limit) if row[0] != first]

    monkeypatch.setattr(ReadOnlyGit, "range_commits", dropping)
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "deferred" and "not contained in base_sha" in report.deferred


@pytest.mark.parametrize("output", ["{tip} {parent}\nzzzz\n", "{tip} {parent}", "{tip}\n",
                                    "{tip} {parent}\n{tip} {parent}\n"],
                         ids=["garbage-line", "no-final-newline", "parents-dropped", "duplicate"])
def test_unexpected_rev_list_output_defers(rig: Rig, repo: Path, monkeypatch: pytest.MonkeyPatch, output: str):
    tip = commit_on(repo, BRANCH, "only")
    real = ReadOnlyGit._run

    def fake(self: ReadOnlyGit, *args: str, **kwargs: Any) -> str:
        if args[0] == "rev-list":
            return output.format(tip=tip, parent=head(repo))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ReadOnlyGit, "_run", fake)
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "deferred"


@pytest.mark.parametrize(("script", "match"), [
    ("read x; echo garbage; echo oops >&2", "unexpected output.*oops"),
    ("read x; printf '%s commit 100\\nabc' $x", "truncated output"),
    ("read x; printf '%s tree 4\\nabcd\\n' $x", "is a tree, not a commit"),
    ("read x; printf '%s commit 4\\nabcd\\n' $x", "does not hash to that SHA"),
    ("read x; printf '%s missing\\n' $x", "not present in this checkout"),
    ("exec sleep 30", "did not finish in time"),
], ids=["garbage", "truncated", "not-a-commit", "hash-mismatch", "missing", "timeout"])
def test_the_commit_reader_fails_closed_on_any_unexpected_answer(script: str, match: str):
    reader = integration_intake._CommitReader(["sh", "-c", script], dict(os.environ), timeout=1.0)
    with reader, pytest.raises(integration_intake.HistoryUnverifiable, match=match):
        reader.read("a" * 40, strict=True)


# -- finding 2: incomplete or locally rewritten history ------------------------------------------------------

def shallow_clone(tmp_path: Path, origin: Path, depth: int) -> Path:
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", f"--depth={depth}", "--branch", BRANCH, f"file://{origin}", str(clone)],
                   check=True, capture_output=True)
    git(clone, "fetch", "-q", "--depth=1", "origin", "dev:refs/remotes/origin/dev")
    git(clone, "remote", "set-url", "origin", str(origin))  # what github_origin maps to the repository
    return clone


def test_a_shallow_checkout_is_refused_and_its_cut_history_never_passes(repo: Path, state_dir: Path, tmp_path: Path,
                                                                        github_origin: Path,
                                                                        monkeypatch: pytest.MonkeyPatch):
    """Codex finding 2a: in a shallow clone `log base..source` stops at the boundary, so S1 (unknown author)
    beyond it was never checked and the intake accepted."""
    _first, second, tip = stranger_in_the_middle(repo)
    clone = shallow_clone(tmp_path, github_origin, depth=2)
    rig = Rig(clone, state_dir)
    with pytest.raises(IntegrationError, match="shallow clone"):
        rig.intake(req(repo, tip))
    assert not (state_dir / "integration").exists()
    monkeypatch.setattr(rig.git, "repository_problem", lambda: None)  # even past the precheck:
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "deferred" and second[:12] in report.deferred and "rewritten" in report.deferred


def test_a_missing_intermediate_commit_defers(rig: Rig, repo: Path):
    first, _second, tip = stranger_in_the_middle(repo)
    loose_object(repo, first).unlink()
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "deferred" and report.job["state"] == "INTAKE"


def test_a_missing_base_side_ancestor_is_never_taken_as_proof(rig: Rig, repo: Path, monkeypatch: pytest.MonkeyPatch):
    """The boundary parent (the fork point) must be reached from base_sha through readable, verified commits."""
    fork = head(repo)
    middle = commit_on(repo, "dev", "base-1", push=False)
    commit_on(repo, "dev", "base-2", push=False)
    git(repo, "push", "-q", "origin", "dev")
    tip = commit_on(repo, BRANCH, "feature", start=fork)
    real = integration_intake._CommitReader.read

    def missing_middle(self: Any, sha: str, *, strict: bool):
        if sha == middle:
            raise integration_intake.HistoryUnverifiable(f"commit {sha[:12]} is not present in this checkout")
        return real(self, sha, strict=strict)

    monkeypatch.setattr(integration_intake._CommitReader, "read", missing_middle)
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "deferred" and middle[:12] in report.deferred


def forged_author(repo: Path, sha: str) -> bytes:
    raw = subprocess.run(["git", "-C", str(repo), "cat-file", "commit", sha], capture_output=True,
                         check=True).stdout
    return raw.replace(STRANGER[1].encode(), KNOWN.encode()).replace(STRANGER[0].encode(), DEMO_AUTHOR[0].encode())


def test_replace_refs_cannot_change_the_author_of_a_pinned_commit(rig: Rig, repo: Path):
    """Codex finding 2b: `git replace` showed a known author for the stranger's commit and the intake accepted."""
    first, _second, tip = stranger_in_the_middle(repo)
    fake = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
                          input=forged_author(repo, first), capture_output=True, check=True).stdout.decode().strip()
    git(repo, "replace", first, fake)
    assert STRANGER[1] not in git(repo, "log", "--format=%ae", f"{head(repo)}..{tip}")  # what Git shows by default
    _report, job = refused(rig, req(repo, tip))
    assert job.attention["observed"]["unknown_authors"] == [STRANGER[1]]


def test_grafts_are_refused_and_ignored_even_past_the_precheck(rig: Rig, repo: Path, monkeypatch: pytest.MonkeyPatch):
    _first, second, tip = stranger_in_the_middle(repo)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "grafts").write_text(f"{second} {head(repo)}\n", encoding="utf-8")  # hides S1
    with pytest.raises(IntegrationError, match="graft file"):
        rig.intake(req(repo, tip))
    assert not (rig.store.root / "integration").exists()
    monkeypatch.setattr(rig.git, "repository_problem", lambda: None)
    _report, job = refused(rig, req(repo, tip))  # GIT_GRAFT_FILE neutralizes the graft: S1 is checked
    assert job.attention["observed"]["unknown_authors"] == [STRANGER[1]]


def test_a_graft_that_git_honoured_would_be_caught_by_the_parent_check(rig: Rig, repo: Path,
                                                                       monkeypatch: pytest.MonkeyPatch):
    _first, second, tip = stranger_in_the_middle(repo)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "grafts").write_text(f"{second} {head(repo)}\n", encoding="utf-8")
    # Suppose the graft were honoured. (M2: Git no longer reads the checkout's own info/grafts at all — every object
    # query runs in a private repository — so the graft is forced on through GIT_GRAFT_FILE instead.)
    env = {**integration_intake.LOCAL_GIT_ENV, "GIT_GRAFT_FILE": str(repo / ".git" / "info" / "grafts")}
    monkeypatch.setattr(integration_intake, "LOCAL_GIT_ENV", env)
    with pytest.raises(integration_intake.HistoryUnverifiable, match="Git lists the parents"):
        rig.git.verified_range(head(repo), tip)


def test_a_forged_object_under_a_pinned_sha_is_detected(rig: Rig, repo: Path):
    first, _second, tip = stranger_in_the_middle(repo)
    path = loose_object(repo, first)
    forged = forged_author(repo, first)
    path.chmod(0o644)
    path.write_bytes(zlib.compress(b"commit %d\x00" % len(forged) + forged))
    assert KNOWN in git(repo, "cat-file", "commit", first)  # Git serves the forged content without complaint
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "deferred" and "does not hash" in report.deferred


# -- finding 3: no implicit fetch (partial clone / promisor remote) ------------------------------------------

def pushed_elsewhere(tmp_path: Path, origin: Path, *names: str) -> list[str]:
    """Commits pushed to origin's BRANCH from another clone: absent from the checkout. Returns their SHAs."""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    git(other, "config", "user.name", DEMO_AUTHOR[0])
    git(other, "config", "user.email", KNOWN)
    git(other, "checkout", "-q", "-b", BRANCH)
    shas = []
    for name in names:
        (other / f"{name}.py").write_text(f"X = {name!r}\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", name)
        shas.append(head(other, "HEAD"))
    git(other, "push", "-q", "origin", BRANCH)
    return shas


def promisor(repo: Path) -> None:
    git(repo, "config", "remote.origin.promisor", "true")


def test_a_partial_clone_is_refused_when_git_cannot_disable_lazy_fetch(rig: Rig, repo: Path, tmp_path: Path,
                                                                       github_origin: Path,
                                                                       monkeypatch: pytest.MonkeyPatch):
    tip = pushed_elsewhere(tmp_path, github_origin, "remote_only")[0]
    promisor(repo)
    monkeypatch.setattr(integration_intake, "lazy_fetch_switch_supported", lambda: False)
    before = snapshot(repo, github_origin)
    for apply in (False, True):
        with pytest.raises(IntegrationError, match="partial clone"):
            rig.intake(req(repo, tip), apply=apply)
    assert snapshot(repo, github_origin) == before and not (rig.store.root / "integration").exists()


def git_honours_no_lazy_fetch(tmp_path: Path) -> bool:
    """Whether this Git honours GIT_NO_LAZY_FETCH=1 (Git ≥ 2.44, or a distribution backport): a missing-object read in
    a disposable promisor checkout must leave it untouched."""
    server, client = tmp_path / "probe-server.git", tmp_path / "probe-client"
    subprocess.run(["git", "init", "-q", "--bare", str(server)], check=True)
    subprocess.run(["git", "init", "-q", str(client)], check=True)
    git(client, "remote", "add", "origin", str(server))
    git(client, "config", "remote.origin.promisor", "true")
    config = (client / ".git" / "config").read_bytes()
    subprocess.run(["git", "-C", str(client), "cat-file", "-e", "f" * 40], capture_output=True, check=False,
                   env={**os.environ, "GIT_NO_LAZY_FETCH": "1"})
    return (client / ".git" / "config").read_bytes() == config


def missing_commit_partial_clone(repo: Path, tmp_path: Path, origin: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    tip = pushed_elsewhere(tmp_path, origin, "remote_only")[0]
    promisor(repo)
    monkeypatch.setattr(integration_intake, "lazy_fetch_switch_supported", lambda: True)  # as with Git ≥ 2.44
    return tip


def test_a_missing_commit_in_a_partial_clone_defers_and_is_never_fetched(rig: Rig, repo: Path, tmp_path: Path,
                                                                         github_origin: Path,
                                                                         monkeypatch: pytest.MonkeyPatch):
    """Codex finding 3: `cat-file -e` in a promisor checkout fetched the missing commit from origin. Where Git can
    disable lazy fetching (the only case in which the intake accepts a partial clone), it defers and the
    checkout — objects, refs, config, index, working tree — and origin stay byte-identical."""
    if not git_honours_no_lazy_fetch(tmp_path):
        pytest.skip("this Git ignores GIT_NO_LAZY_FETCH: the intake refuses partial clones here (test above)")
    tip = missing_commit_partial_clone(repo, tmp_path, github_origin, monkeypatch)
    before = snapshot(repo, github_origin)
    preview = rig.intake(req(repo, tip), apply=False)
    assert preview.outcome == "would-defer" and "not present in this checkout" in preview.deferred
    report = rig.intake(req(repo, tip))
    assert report.outcome == "deferred" and report.job["state"] == "INTAKE"
    assert snapshot(repo, github_origin) == before and not object_present(repo, tip)


def test_refused_transports_alone_download_nothing_but_cannot_stop_gits_config_write(rig: Rig, repo: Path,
                                                                                    tmp_path: Path,
                                                                                    github_origin: Path,
                                                                                    monkeypatch: pytest.MonkeyPatch):
    """Without GIT_NO_LAZY_FETCH, Git still starts its promisor fetch: every transport is refused, so no object or
    ref arrives — but that fetch registers `remote.origin.partialclonefilter` in .git/config before it fails. This
    is why a partial clone is refused unless Git can switch lazy fetching off (repository_problem)."""
    tip = missing_commit_partial_clone(repo, tmp_path, github_origin, monkeypatch)
    monkeypatch.setattr(integration_intake, "LOCAL_GIT_ENV", {
        k: v for k, v in integration_intake.LOCAL_GIT_ENV.items() if k != "GIT_NO_LAZY_FETCH"})
    objects, refs = tree_digest(repo / ".git" / "objects"), git(repo, "for-each-ref")
    report = rig.intake(req(repo, tip))
    assert report.outcome == "deferred" and report.job["state"] == "INTAKE"
    assert tree_digest(repo / ".git" / "objects") == objects and git(repo, "for-each-ref") == refs
    assert not object_present(repo, tip) and not (repo / ".git" / "FETCH_HEAD").exists()


def test_a_missing_ancestor_in_a_partial_clone_is_never_fetched(rig: Rig, repo: Path, tmp_path: Path,
                                                                github_origin: Path, monkeypatch: pytest.MonkeyPatch):
    if not git_honours_no_lazy_fetch(tmp_path):
        pytest.skip("this Git ignores GIT_NO_LAZY_FETCH: the intake refuses partial clones here")
    shas = pushed_elsewhere(tmp_path, github_origin, "one", "two", "three")
    first, tip = shas[0], shas[-1]
    git(repo, "fetch", "-q", "origin", BRANCH)
    loose_object(repo, first).unlink()  # a promisor checkout may lack any object
    promisor(repo)
    monkeypatch.setattr(integration_intake, "lazy_fetch_switch_supported", lambda: True)
    before = snapshot(repo, github_origin)
    report = never_accepted(rig, req(repo, tip))
    assert report.outcome == "deferred"
    assert snapshot(repo, github_origin) == before and not object_present(repo, first)


def object_present(repo: Path, sha: str) -> bool:
    """Whether the object is in the checkout (asked without any chance of fetching it)."""
    result = subprocess.run(["git", "-C", str(repo), "-c", "protocol.allow=never", "cat-file", "-e", sha],
                            capture_output=True, check=False, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"})
    return result.returncode == 0


def test_every_local_git_command_carries_the_history_and_no_fetch_controls(rig: Rig, repo: Path,
                                                                           monkeypatch: pytest.MonkeyPatch):
    tip = commit_on(repo, BRANCH, "only")
    seen: list[tuple[list[str], dict[str, str]]] = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def run(argv: Any, *args: Any, **kwargs: Any):
        seen.append(([str(a) for a in argv], dict(kwargs.get("env") or {})))
        return real_run(argv, *args, **kwargs)  # pylint: disable=subprocess-run-check

    class Popen(real_popen):  # type: ignore[misc,valid-type]  # pylint: disable=too-few-public-methods
        """Records argv and environment."""

        def __init__(self, argv: Any, *args: Any, **kwargs: Any) -> None:
            seen.append(([str(a) for a in argv], dict(kwargs.get("env") or {})))
            super().__init__(argv, *args, **kwargs)

    request = req(repo, tip)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    monkeypatch.setenv("GIT_GRAFT_FILE", str(repo / "planted-grafts"))
    monkeypatch.setenv("GIT_SHALLOW_FILE", str(repo / "planted-shallow"))
    assert rig.intake(request).outcome == "accepted"
    monkeypatch.undo()
    local = [(argv, env) for argv, env in seen if argv[0] == "git" and git_subcommand(argv) != "ls-remote"]
    assert all("core.fsmonitor=" in argv for argv, _env in local)  # every one is a hardened intake command
    assert {git_subcommand(argv) for argv, _env in local} >= {"rev-list", "cat-file", "merge-base", "rev-parse"}
    for argv, env in local:
        if git_subcommand(argv) == "config" and "--show-scope" in argv:
            continue  # gitsafe's own listing (no object access)
        assert "--no-replace-objects" in argv and "core.commitGraph=false" in argv, argv
        assert "protocol.allow=never" in argv and "protocol.file.allow=never" in argv, argv
        assert env["GIT_NO_LAZY_FETCH"] == "1" and env["GIT_NO_REPLACE_OBJECTS"] == "1", argv
        assert env["GIT_GRAFT_FILE"] == integration_intake.NO_GRAFT_FILE and "GIT_SHALLOW_FILE" not in env, argv


# -- finding 5: the dry run reports active-job conflicts exactly like --apply --------------------------------

def state_files(state_dir: Path) -> list[tuple[str, bytes]]:
    return sorted((str(p.relative_to(state_dir)), p.read_bytes()) for p in state_dir.rglob("*") if p.is_file())


def test_dry_run_reports_branch_sha_and_pr_conflicts_and_writes_nothing(repo: Path, state_dir: Path, source: str,
                                                                        github_origin: Path):
    del github_origin
    client = tracker(head_sha=source)
    other_tip = commit_on(repo, "integration/other", "other")
    client.add_pull_request_as("spyderbg", "integration/other", "dev", head_sha=other_tip)
    rig = Rig(repo, state_dir, client=client)
    first = rig.intake(req(repo, source, pr_number=1)).job["job_id"]
    newer = commit_on(repo, BRANCH, "third")
    conflicting = {"same-branch": req(repo, newer, pr_number=1), "same-sha": req(repo, source, issue=99),
                   "same-pr": req(repo, other_tip, source_ref="integration/other", pr_number=1)}
    before = state_files(state_dir)
    for name, request in conflicting.items():
        for apply in (False, True):
            with pytest.raises(IntegrationError, match=f"active integration job already holds.*{first}"):
                rig.intake(request, apply=apply)
        assert state_files(state_dir) == before, name
    assert rig.intake(req(repo, other_tip, source_ref="integration/other", pr_number=2), apply=False).outcome == \
        "would-accept"  # no conflict: another branch, commit and PR (#2); still nothing is recorded
    assert state_files(state_dir) == before
    abort_job(rig.jobs, first, "make room", actor="operator", apply=True)
    assert rig.intake(conflicting["same-branch"], apply=False).outcome in ("would-accept", "would-refuse")


def test_apply_rechecks_conflicts_under_the_lock_after_a_clean_preview(rig: Rig, repo: Path, source: str):
    newer_request = req(repo, source, issue=99)
    assert rig.intake(newer_request, apply=False).outcome == "would-accept"  # nothing active yet
    first = rig.intake(req(repo, source)).job["job_id"]                      # someone else records a job meanwhile
    with pytest.raises(IntegrationError, match=f"already holds.*{first}"):
        rig.intake(newer_request)                                            # the authoritative check refuses
    assert [job.job_id for job in rig.jobs.list()] == [first]


def test_cli_dry_run_conflict_is_a_json_error(repo: Path, state_dir: Path, github_origin: Path, source: str,
                                              tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    del github_origin
    config = config_file(tmp_path)
    assert cli_main(intake_argv(repo, state_dir, config, source) + ["--apply"]) == 0
    capsys.readouterr()
    newer = commit_on(repo, BRANCH, "third")
    code, data = run_cli(capsys, intake_argv(repo, state_dir, config, newer) + ["--json"])
    assert code == 1 and data["ok"] is False and "already holds" in data["error"]["message"]


def test_concurrent_conflicting_intakes_record_exactly_one_job(repo: Path, state_dir: Path, github_origin: Path,
                                                               source: str, tmp_path: Path):
    config = config_file(tmp_path)
    argvs = [intake_argv(repo, state_dir, config, source)[:-1] + [str(issue), "--apply", "--json"]
             for issue in range(70, 76)]  # same branch and commit, six different Issues: pairwise conflicting
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda argv: run_py(DRIVER, str(github_origin), *argv), argvs))
    answers = [json.loads(r.stdout) for r in results]
    assert sorted(r.returncode for r in results) == [0, 1, 1, 1, 1, 1], [r.stderr for r in results]
    assert all("already holds" in a["error"]["message"] for a in answers if not a["ok"])
    [job] = IntegrationJobs(StateStore(state_dir)).list()
    assert job.state is JobState.AWAITING_APPROVAL
