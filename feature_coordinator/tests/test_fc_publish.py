"""W4.3.3: automatic integration, branch push, Draft PR, human-merge detection and completion of an approved
GitHub Issue's feature (publish.py).

In-process tests on disposable repositories: a bare repository is the `origin` remote (pushes and fetches are
real Git), the persisted mock tracker stands in for GitHub's Issues and pull requests (a new PR's head commit is
the bare remote's branch, as GitHub reads it), the local board for Project #2 and the protected Project #1, mock
runners for the agents, recorded gates. A person's merge is a real merge commit pushed to the remote's `dev`
plus the mock PR marked merged. No model, no real GitHub, no network. The real GitHub adapter is exercised
through a scripted transport (no request leaves the process)."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,too-many-lines

from __future__ import annotations

import dataclasses
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from feature_coordinator import publish
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import GitHubConfig, GitHubPublishConfig, config_from_mapping, default_config
from feature_coordinator.autonomy import Scheduling
from feature_coordinator.errors import (
    ConfigError,
    CoordinatorError,
    GitHubResponseError,
    GitHubUncertainWriteError,
    GitHubUnavailableError,
    HeadMismatchError,
    PullRequestPolicyError,
    RemoteUnavailableError,
    ScopeViolationError,
)
from feature_coordinator.gitops import GitCoordinator
from feature_coordinator.github_api import parse_pull
from feature_coordinator.intake import feature_id_for, load_intake
from feature_coordinator.intake_poll import IntakePoller
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.model import Feature, FeatureState, PullRequestCandidate, TaskState
from feature_coordinator.publish import PullRequestPublisher, github_repository_of
from feature_coordinator.statedir import ensure_state_dir
from feature_coordinator.workflow import FeatureSpec, TaskSpec

from fc_helpers import git
from test_fc_github_client import Scripted, client_for, ok
from test_fc_intake import BODY, EDIT, OPERATOR, Rig, approved, intake_config, outside
from test_fc_project_sync import board_of, protected_snapshot, values

REPOSITORY = default_config().github.repository  # spyderbg/mx-admin-cloud


def publish_config(**publishing: Any):
    config = intake_config()
    return dataclasses.replace(config, github=dataclasses.replace(config.github, publish=dataclasses.replace(
        config.github.publish, **{"enabled": True, **publishing})))


def remote_sha(bare: Path, branch: str) -> str | None:
    result = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "--verify", "--quiet",
                             f"refs/heads/{branch}^{{commit}}"], capture_output=True, text=True, check=False)
    return result.stdout.strip() or None


@pytest.fixture
def remote(tmp_path: Path, repo: Path) -> Path:
    """A bare repository as the checkout's `origin`, holding `dev`."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=dev", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "dev")
    return bare


@pytest.fixture
def github_origin(monkeypatch: pytest.MonkeyPatch, remote: Path) -> Path:
    """The bare remote counts as github.com/<github.repository> (its URL is a path in the tests)."""
    real = publish.github_repository_of
    monkeypatch.setattr(publish, "github_repository_of",
                        lambda url, host: REPOSITORY if url == str(remote) else real(url, host))
    return remote


class PubRig(Rig):
    """The intake rig plus a real `origin` remote and pull requests in the mock tracker."""

    def __init__(self, repo: Path, state: Path, bare: Path, *, config=None, faults: tuple[str, ...] = ()) -> None:
        self.bare = bare
        super().__init__(repo, state, config=config or publish_config(), faults=faults)

    def make(self, faults: tuple[str, ...] = (), config=None):
        coord = super().make(faults, config)
        coord.tracker.repository = REPOSITORY  # type: ignore[attr-defined]
        coord.tracker.ref_resolver = lambda branch: remote_sha(self.bare, branch)  # type: ignore[attr-defined]
        return coord

    def publisher(self, faults: tuple[str, ...] = ()) -> PullRequestPublisher:
        return PullRequestPublisher(self.coord, faults=faults, sleep=lambda _s: None)

    def publish(self, apply: bool = True, faults: tuple[str, ...] = (), features: tuple[str, ...] = ()):
        return self.publisher(faults).poll(apply=apply, features=features)

    def feature(self, feature_id: str) -> Feature:
        return self.store.load_feature(feature_id)

    def candidate(self, feature_id: str) -> PullRequestCandidate:
        cand = self.feature(feature_id).pull_request
        assert cand is not None
        return cand

    def prs(self) -> list[Any]:
        return self.tracker.list_pull_requests()

    def merge_on_github(self, number: int, *, squash: bool = False) -> str:
        """A person merges PR *number*: a real merge commit on the remote's dev, the PR marked merged."""
        pr = self.tracker.get_pull_request(number)
        work = Path(tempfile.mkdtemp(prefix="merge-", dir=self.state.parent))
        subprocess.run(["git", "clone", "-q", str(self.bare), str(work)], check=True)
        user = ("-c", "user.name=Operator", "-c", "user.email=operator@example.invalid", "-c", "commit.gpgsign=false")
        if squash:
            git(work, *user, "merge", "--squash", "-q", f"origin/{pr.head}")
            git(work, *user, "commit", "-q", "-m", f"Squash #{number}")
        else:
            git(work, *user, "merge", "--no-ff", "-q", "-m", f"Merge pull request #{number}", f"origin/{pr.head}")
        git(work, "push", "-q", "origin", "dev")
        sha = git(work, "rev-parse", "HEAD").strip()
        self.tracker.merge_pull_request(number, sha, merged_by=OPERATOR)
        return sha

    def comments(self, number: int, text: str) -> list[str]:
        return [c for c in self.own_comments(number) if text in c]


@pytest.fixture
def rig(repo: Path, state_dir: Path, github_origin: Path) -> PubRig:
    return PubRig(repo, state_dir, github_origin)


def verified(rig: PubRig) -> tuple[int, str, str]:
    """An approved Issue whose single task is verified on its feature branch (the feature still active)."""
    number, task_id = approved(rig)
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    return number, task_id, feature_id_for(number)


def draft_open(rig: PubRig) -> tuple[int, str, str, int]:
    """... integrated, pushed and opened as its Draft PR by one publication pass."""
    number, task_id, feature_id = verified(rig)
    report = rig.publish()
    assert report.ok and report.created == {feature_id: rig.candidate(feature_id).number}, report.lines()
    return number, task_id, feature_id, report.created[feature_id]


# -- configuration ----------------------------------------------------------------------------------------------

def test_publication_is_off_by_default_and_documented() -> None:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert config_from_mapping(data).github.publish == default_config().github.publish == GitHubPublishConfig()
    assert default_config().github.publish.enabled is False
    assert default_config().github.write_scope == "test-features-only"  # unchanged
    data["github"]["publish"]["enabled"] = True
    assert config_from_mapping(data).github.publish.enabled is True


@pytest.mark.parametrize("change, message", [
    ({"enabled": "yes"}, "true or false"),
    ({"poll_interval_seconds": 5}, "poll_interval_seconds"),
    ({"git_timeout_seconds": 5}, "git_timeout_seconds"),
    ({"git_timeout_seconds": 3600}, "git_timeout_seconds"),
    ({"poll_interval_seconds": "60"}, "integer"),
    ({"remote": "evil"}, "unknown github.publish keys"),
    ({"draft": False}, "unknown github.publish keys"),
])
def test_the_publish_configuration_is_validated(change: dict, message: str) -> None:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["publish"].update(change)
    with pytest.raises(ConfigError, match=message):
        config_from_mapping(data)


def test_a_pass_refuses_to_write_while_publication_is_disabled(repo: Path, state_dir: Path,
                                                               github_origin: Path) -> None:
    off = PubRig(repo, state_dir, github_origin, config=intake_config())
    _number, _task_id, feature_id = verified(off)
    with pytest.raises(CoordinatorError, match="publish.enabled is false"):
        off.publish()
    preview = off.publish(apply=False)  # a dry run shows what an operator would get, and writes nothing
    assert any(f"integrate {feature_id}" in w for w in preview.writes)
    assert off.feature(feature_id).state is FeatureState.ACTIVE and remote_sha(github_origin, f"feature/{feature_id}") \
        is None and not off.prs()


# -- the whole lifecycle -----------------------------------------------------------------------------------------

def test_the_full_lifecycle_from_approval_to_done(rig: PubRig) -> None:  # pylint: disable=too-many-statements
    board = board_of(rig.coord)
    protected = protected_snapshot(board)
    number, task_id, feature_id = verified(rig)
    assert values(rig.coord, task_id)["Status"] == "Review"  # verified, but nothing is merged yet
    issues = len(rig.tracker.list_issues())
    report = rig.publish()
    assert report.ok and report.integrated == [feature_id] and report.pushed == [feature_id]
    feature, cand = rig.feature(feature_id), rig.candidate(feature_id)
    assert feature.state is FeatureState.REVIEW and report.created == {feature_id: cand.number}
    # the branch on the remote is exactly the verified head; nothing else was pushed
    assert remote_sha(rig.bare, feature.branch) == cand.head_sha == feature.integration_evidence.head_sha
    assert remote_sha(rig.bare, "dev") == feature.base_sha
    (pr,) = rig.prs()
    assert (pr.number, pr.head, pr.base, pr.draft, pr.head_sha) == (cand.number, feature.branch, "dev", True,
                                                                   cand.head_sha)
    assert rig.tracker.authorized == [(feature.branch, "dev")]
    assert cand.origin == "created" and cand.url == pr.url and cand.pending_create is None and cand.pushed
    # the PR body: Issue and link, feature and task, approved summary, changed files, validation, integration
    record = load_intake(rig.store, feature_id)
    assert record is not None
    for text in (f"GitHub Issue #{number}", record.url, f"`{feature_id}`", f"`{task_id}`", record.digest,
                 "## Approved task", "## Changed files", "`cli/src/commands.py`", "## Validation", "`cli-lint` passed",
                 f"## Integration gates at `{cand.head_sha[:12]}`", "contract-check", "never merges",
                 f"feature={feature_id}"):
        assert text in pr.body, text
    assert "closes #" not in pr.body.lower() and "fixes #" not in pr.body.lower()
    # waiting for a person: the Issue stays open, the card shows Review, one PR link comment
    assert rig.remote(number).state == "open" and values(rig.coord, task_id)["Status"] == "Review"
    assert len(rig.comments(number, "Draft pull request opened")) == 1
    writes = rig.tracker.writes
    again = rig.publish()
    assert again.ok and not (again.created or again.adopted or again.pushed) and again.waiting
    assert "Draft PR" in again.waiting[0] and rig.tracker.writes == writes and again.service_state()[0] == "WAITING"
    rig.tracker.update_pull_request(pr.number, draft=False)  # a person marks it ready
    assert "ready for review" in rig.publish().waiting[0] and rig.candidate(feature_id).draft is False
    merge = rig.merge_on_github(pr.number)
    done = rig.publish()
    assert done.ok and done.completed == {feature_id: merge} and done.closed == {feature_id: number}
    feature = rig.feature(feature_id)
    assert feature.state is FeatureState.DONE and feature.merged_sha == merge
    evidence = feature.pull_request.merge  # type: ignore[union-attr]
    assert evidence["number"] == pr.number and evidence["head_sha"] == cand.head_sha and evidence["base"] == "dev"
    assert evidence["merged_by"] == OPERATOR and evidence["base_tip"] == merge
    issue = rig.remote(number)
    assert issue.state == "closed" and issue.state_reason == "completed"
    assert len(rig.comments(number, "**Completed.**")) == 1 and merge[:12] in rig.comments(number, "Completed")[0]
    assert values(rig.coord, task_id)["Status"] == "Done"
    record = load_intake(rig.store, feature_id)
    assert record is not None and record.status == "done" and record.completed["closed_by"] == "coordinator"
    assert rig.store.load_task(task_id).state is TaskState.VERIFIED  # history preserved, nothing reopened
    assert protected_snapshot(board) == protected and len(rig.tracker.list_issues()) == issues and len(rig.prs()) == 1
    kinds = [e["kind"] for e in rig.store.events()]
    order = ["feature-integrated", "publish-pushed", "pull-request-create-started", "pull-request-opened",
             "pull-request-ready", "feature-done", "intake-issue-closed", "intake-completed"]
    assert [kinds.index(k) for k in order] == sorted(kinds.index(k) for k in order)


def test_the_worker_service_runs_the_whole_flow(rig: PubRig) -> None:
    number = rig.issue()
    task_id = rig.script(number)
    feature_id = feature_id_for(number)
    rig.approve(number)

    def has_pr(_service: Any) -> bool:
        try:
            return rig.candidate(feature_id).number is not None
        except (KeyError, AssertionError):
            return False
    service = rig.service(until=has_pr, max_cycles=60)
    assert service.run() == 0
    assert rig.store.load_task(task_id).state is TaskState.VERIFIED and rig.feature(feature_id).state is \
        FeatureState.REVIEW
    assert service.launched == [(task_id, 1)] and len(rig.prs()) == 1 and rig.prs()[0].draft
    assert service._publication and service._publication[0] == "WAITING"
    waiting = rig.service(until=lambda s: s.cycles >= 6)
    assert waiting.run() == 0 and len(rig.prs()) == 1 and waiting.launched == []  # no second PR or launch
    assert any("waits for a person" in e.get("reason", "") for e in rig.events("worker-state"))
    rig.merge_on_github(rig.candidate(feature_id).number)  # type: ignore[arg-type]
    finisher = rig.service(until=lambda s: (load_intake(rig.store, feature_id) or SimpleNamespace(status="")).status
                           == "done", max_cycles=40)
    assert finisher.run() == 0
    assert rig.feature(feature_id).state is FeatureState.DONE and rig.remote(number).state == "closed"
    assert values(rig.coord, task_id)["Status"] == "Done" and len(rig.runners["cli"].requests) == 1


# -- integration ---------------------------------------------------------------------------------------------------

def test_an_integration_gate_failure_publishes_nothing(repo: Path, state_dir: Path, github_origin: Path) -> None:
    rig = PubRig(repo, state_dir, github_origin)
    rig.gates.failing = {"contract-check"}  # an integration gate (the task's own gates pass)
    _number, task_id, feature_id = verified(rig)
    report = rig.publish()
    assert not report.ok and "integration gates failed" in report.attention[0]
    feature = rig.feature(feature_id)
    assert feature.state is FeatureState.ACTIVE and feature.pull_request is None
    assert remote_sha(github_origin, feature.branch) is None and not rig.prs()
    calls = list(rig.gates.calls)
    again = rig.publish()  # not retried automatically: the same gates would fail again
    assert "failed at" in again.attention[0] and rig.gates.calls == calls and not rig.prs()
    rig.gates.failing = set()
    rig.coord.integrate(feature_id)  # the operator fixed it and integrated
    assert rig.publish().created and rig.store.load_task(task_id).state is TaskState.VERIFIED


def test_integration_waits_for_every_task_and_every_writer(rig: PubRig) -> None:
    number, task_id = approved(rig)
    feature_id = feature_id_for(number)
    report = rig.publish()
    assert report.ok and report.features[feature_id]["status"] == "implementing"
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    with rig.store.transaction():  # a launch recorded in flight (another service instance)
        rig.store.save_record("scheduling", Scheduling(id=task_id, feature_id=feature_id,
                                                       in_flight={"launch": 1, "instance": "earlier"}))
    held = rig.publish()
    assert held.features[feature_id]["status"] == "waiting" and rig.feature(feature_id).state is FeatureState.ACTIVE


def test_unapproved_features_are_never_published(rig: PubRig) -> None:
    spec = FeatureSpec(slug="manual", title="A planned feature", tasks=(
        TaskSpec("cmd", "cli", "kiddo-cli content export-items", required_gates=("cli-lint",)),))
    feature = rig.coord.activate(rig.coord.plan(spec).id)
    rig.runners["cli"].script["manual.cmd"] = [EDIT]
    assert rig.coord.run_task("manual.cmd").state is TaskState.VERIFIED
    report = rig.publish()
    assert report.ok and not report.features and not report.integrated
    asked = rig.publish(features=("manual",))
    assert "not an approved GitHub Issue's feature" in asked.attention[0]
    assert rig.feature("manual").state is FeatureState.ACTIVE and remote_sha(rig.bare, feature.branch) is None
    assert not rig.prs() and not rig.tracker.authorized


# -- the remote and the branch ----------------------------------------------------------------------------------------

def integrated(rig: PubRig) -> tuple[int, str, str]:
    number, task_id, feature_id = verified(rig)
    rig.coord.integrate(feature_id)
    return number, task_id, feature_id


def test_a_wrong_remote_stops_publication(repo: Path, state_dir: Path, remote: Path) -> None:
    rig = PubRig(repo, state_dir, remote)  # no monkeypatch: the real check sees a local path, not GitHub
    _number, _task_id, feature_id = integrated(rig)
    report = rig.publish()
    assert "is not github.com/spyderbg/mx-admin-cloud" in report.attention[0]
    assert remote_sha(remote, f"feature/{feature_id}") is None and not rig.prs()
    stop = rig.candidate(feature_id).attention
    assert stop and "is not github.com" in stop["reason"]
    assert "reset" in rig.publish().attention[0]  # sticky: nothing runs again until an operator resets it


def test_a_rewritten_or_changed_origin_stops_publication(rig: PubRig, tmp_path: Path) -> None:
    _number, _task_id, feature_id = integrated(rig)
    git(rig.repo, "config", "remote.origin.pushurl", str(tmp_path / "elsewhere.git"))
    assert "push URL of origin is rewritten" in rig.publish().attention[0]
    git(rig.repo, "config", "--unset", "remote.origin.pushurl")
    assert "push URL of origin is rewritten" in rig.publish().attention[0]  # still stopped: sticky
    state = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--tracker", "local"]
    assert cli_main([*state, "github", "publish", "reset", feature_id, "--apply"]) == 0
    git(rig.repo, "remote", "set-url", "origin", str(tmp_path / "other.git"))
    assert "changed since activation" in rig.publish().attention[0]
    assert not rig.prs() and remote_sha(rig.bare, f"feature/{feature_id}") is None


def test_an_unexpected_branch_head_stops_publication(rig: PubRig) -> None:
    _number, _task_id, feature_id = integrated(rig)
    (rig.repo / "cli/src/late.py").write_text("LATE = 1\n", encoding="utf-8")
    git(rig.repo, "add", "cli/src/late.py")
    git(rig.repo, "commit", "-qm", "a late commit after integration")
    report = rig.publish()
    assert "moved after integration" in report.attention[0]
    assert remote_sha(rig.bare, f"feature/{feature_id}") is None and not rig.prs()


def test_the_checkout_must_be_on_the_validated_head_to_push(rig: PubRig) -> None:
    _number, _task_id, feature_id = integrated(rig)
    git(rig.repo, "switch", "-q", "dev")
    report = rig.publish()
    assert "push refused" in report.attention[0] and "not on" in report.attention[0]
    assert remote_sha(rig.bare, f"feature/{feature_id}") is None and not rig.prs()


def test_an_unclean_checkout_defers_the_push(rig: PubRig) -> None:
    _number, _task_id, feature_id = integrated(rig)
    (rig.repo / "cli/src/scratch.py").write_text("x = 1\n", encoding="utf-8")
    report = rig.publish()
    assert "not clean" in report.attention[0] and rig.candidate(feature_id).attention is None  # not sticky
    (rig.repo / "cli/src/scratch.py").unlink()
    assert rig.publish().created


def push_foreign(rig: PubRig, branch: str) -> str:
    work = Path(tempfile.mkdtemp(prefix="foreign-", dir=rig.state.parent))
    subprocess.run(["git", "clone", "-q", str(rig.bare), str(work)], check=True)
    (work / "foreign.txt").write_text("someone else\n", encoding="utf-8")
    user = ("-c", "user.name=Someone", "-c", "user.email=someone@example.invalid", "-c", "commit.gpgsign=false")
    git(work, "add", "foreign.txt")
    git(work, *user, "commit", "-qm", "foreign")
    git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    return git(work, "rev-parse", "HEAD").strip()


def test_a_non_fast_forward_remote_branch_is_never_forced(rig: PubRig) -> None:
    _number, _task_id, feature_id = integrated(rig)
    branch = f"feature/{feature_id}"
    foreign = push_foreign(rig, branch)
    report = rig.publish()
    assert "not a fast-forward" in report.attention[0] and "never forced" in report.attention[0]
    assert remote_sha(rig.bare, branch) == foreign and not rig.prs()
    assert rig.candidate(feature_id).attention


def test_a_rejection_by_the_remote_itself_is_never_forced(rig: PubRig, monkeypatch: pytest.MonkeyPatch) -> None:
    _number, _task_id, feature_id = integrated(rig)
    branch = f"feature/{feature_id}"
    foreign = push_foreign(rig, branch)
    real = GitCoordinator.remote_head
    stale = iter([None])  # the first read misses the foreign commit (it arrived meanwhile)
    monkeypatch.setattr(GitCoordinator, "remote_head",
                        lambda self, b, **kw: next(stale, real(self, b, **kw)) if b == branch else real(self, b, **kw))
    report = rig.publish()
    assert "rejected the push" in report.attention[0]
    assert remote_sha(rig.bare, branch) == foreign and not rig.prs()


def test_repeated_and_interrupted_pushes_are_safe(repo: Path, state_dir: Path, github_origin: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    rig = PubRig(repo, state_dir, github_origin)
    _number, _task_id, feature_id = integrated(rig)
    branch = f"feature/{feature_id}"
    real = GitCoordinator._network

    def lost_answer(self, *args, timeout):
        result = real(self, *args, timeout=timeout)
        if args[0] == "push":
            raise RemoteUnavailableError("git push did not finish within 1s (it may have reached the remote)")
        return result
    monkeypatch.setattr(GitCoordinator, "_network", lost_answer)
    report = rig.publish()
    assert not report.ok and "may have reached" in report.deferred[0] and not rig.prs()
    assert remote_sha(github_origin, branch) == rig.candidate(feature_id).head_sha  # it did arrive
    monkeypatch.setattr(GitCoordinator, "_network", real)
    again = rig.publish()  # reads the remote first: nothing is pushed twice
    assert again.ok and not again.pushed and len(again.created) == 1
    pushes = rig.events("publish-pushed")
    assert [e["pushed"] for e in pushes] == [False]
    assert rig.coord.git.push_branch(branch, rig.candidate(feature_id).head_sha,
                                     destination=str(github_origin)).pushed is False  # a repeat


def test_an_unreachable_remote_defers_and_recovers(rig: PubRig, tmp_path: Path) -> None:
    _number, _task_id, feature_id = integrated(rig)
    moved = tmp_path / "away.git"
    rig.bare.rename(moved)
    report = rig.publish()
    assert not report.ok and "RemoteUnavailableError" in report.deferred[0] and not rig.prs()
    assert rig.candidate(feature_id).attention is None  # transient: not a stop
    moved.rename(rig.bare)
    assert rig.publish().created


def test_push_branch_guards(rig: PubRig, monkeypatch: pytest.MonkeyPatch) -> None:
    _number, _task_id, feature_id = integrated(rig)
    gc, head, to = rig.coord.git, rig.candidate(feature_id).head_sha, str(rig.bare)
    for branch in ("dev", "main", "master", "release/x", "feature/../dev", f"feature/{feature_id}:dev"):
        with pytest.raises(ScopeViolationError):
            gc.push_branch(branch, head, destination=to)
    with pytest.raises(ScopeViolationError):
        gc.push_branch(f"feature/{feature_id}", "HEAD", destination=to)
    with pytest.raises(ScopeViolationError):
        gc.push_branch(f"feature/{feature_id}", head, destination=to, protected=(f"feature/{feature_id}",))
    with pytest.raises(HeadMismatchError):
        gc.push_branch(f"feature/{feature_id}", rig.feature(feature_id).base_sha, destination=to)
    with pytest.raises(ScopeViolationError, match="not exactly the approved destination"):
        gc.push_branch(f"feature/{feature_id}", head, destination=f"{to}.other")  # origin is not the destination
    seen: list[tuple[str, ...]] = []
    real = GitCoordinator._network
    monkeypatch.setattr(GitCoordinator, "_network", lambda self, *a, timeout: seen.append(a) or real(
        self, *a, timeout=timeout))
    assert gc.push_branch(f"feature/{feature_id}", head, destination=to).pushed
    push = next(a for a in seen if a[0] == "push")
    assert "--force" not in push and "--force-with-lease" not in push and not any(a.startswith("+") for a in push)
    assert push[-1] == f"{head}:refs/heads/feature/{feature_id}" and "--no-verify" in push


def unauthorized_remote(rig: PubRig, how: str) -> Path:
    """A second destination for `git push origin`: another pushurl beside the expected one, or a second url."""
    evil = rig.state.parent / "unauthorized.git"
    subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=dev", str(evil)], check=True)
    if how == "pushurl":
        git(rig.repo, "remote", "set-url", "--add", "--push", "origin", str(rig.bare))
        git(rig.repo, "remote", "set-url", "--add", "--push", "origin", str(evil))
    else:
        git(rig.repo, "remote", "set-url", "--add", "origin", str(evil))
    assert git(rig.repo, "remote", "get-url", "--push", "origin").strip() == str(rig.bare)  # the old check's view
    return evil


def refs_of(bare: Path) -> str:
    return subprocess.run(["git", "--git-dir", str(bare), "for-each-ref"], capture_output=True, text=True,
                          check=True).stdout


@pytest.mark.parametrize("how", ["pushurl", "url"])
def test_several_push_urls_publish_nowhere(rig: PubRig, how: str) -> None:
    _number, _task_id, feature_id = integrated(rig)
    evil = unauthorized_remote(rig, how)
    expected_refs = refs_of(rig.bare)
    report = rig.publish()
    assert "would publish to 2 URLs" in report.attention[0] and str(evil) in report.attention[0]
    assert remote_sha(rig.bare, f"feature/{feature_id}") is None and refs_of(rig.bare) == expected_refs
    assert remote_sha(evil, f"feature/{feature_id}") is None and refs_of(evil) == ""
    assert not rig.prs() and not rig.tracker.authorized and rig.candidate(feature_id).attention  # sticky
    assert not rig.events("publish-pushed")


def test_the_push_destination_is_checked_again_under_the_git_locks(rig: PubRig,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _number, _task_id, feature_id = integrated(rig)
    branch, head, to = f"feature/{feature_id}", rig.candidate(feature_id).head_sha, str(rig.bare)
    evil = unauthorized_remote(rig, "pushurl")
    with pytest.raises(ScopeViolationError, match="would publish to 2 URL"):
        rig.coord.git.push_branch(branch, head, destination=to)
    monkeypatch.setattr(PullRequestPublisher, "_origin", lambda self, feature: (to, None))  # an earlier check passed
    report = rig.publish()
    assert "push refused" in report.attention[0] and "would publish to 2 URL" in report.attention[0]
    assert remote_sha(rig.bare, branch) is None and refs_of(evil) == "" and not rig.prs()
    git(rig.repo, "config", "--unset-all", "remote.origin.pushurl")
    real = GitCoordinator.remote_head

    def added_meanwhile(self, b, **kw):  # the configuration changes while the remote is read
        git(rig.repo, "remote", "set-url", "--add", "--push", "origin", str(evil))
        return real(self, b, **kw)
    monkeypatch.setattr(GitCoordinator, "remote_head", added_meanwhile)
    with pytest.raises(ScopeViolationError, match="would publish to 1 URL"):  # checked again right before the push
        rig.coord.git.push_branch(branch, head, destination=to)
    assert remote_sha(rig.bare, branch) is None and refs_of(evil) == ""


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/spyderbg/mx-admin-cloud.git", "spyderbg/mx-admin-cloud"),
    ("https://github.com/spyderbg/mx-admin-cloud", "spyderbg/mx-admin-cloud"),
    ("git@github.com:spyderbg/mx-admin-cloud.git", "spyderbg/mx-admin-cloud"),
    ("ssh://git@github.com/spyderbg/mx-admin-cloud.git", "spyderbg/mx-admin-cloud"),
    ("https://evil.example/spyderbg/mx-admin-cloud.git", None),
    ("https://github.com.evil.example/spyderbg/mx-admin-cloud.git", None),
    ("https://user:token@github.com/spyderbg/mx-admin-cloud.git", None),
    ("/tmp/origin.git", None),
    ("file:///tmp/origin.git", None),
    ("https://github.com/spyderbg/mx-admin-cloud/extra", None),
])
def test_github_remote_urls_are_parsed_strictly(url: str, expected: str | None) -> None:
    assert github_repository_of(url, "github.com") == expected


# -- the pull request: creation, adoption, recovery ------------------------------------------------------------------

def test_a_failed_pr_creation_is_retried_without_duplicates(rig: PubRig) -> None:
    _number, _task_id, feature_id = integrated(rig)
    rig.tracker.fail.append(("create_pull_request", GitHubUnavailableError("POST pulls: HTTP 503")))
    report = rig.publish()
    assert not report.ok and "HTTP 503" in report.deferred[0] and not rig.prs()
    assert rig.candidate(feature_id).pending_create and rig.candidate(feature_id).pushed
    again = rig.publish()
    assert again.ok and len(again.created) == 1 and len(rig.prs()) == 1


def test_a_mismatched_new_pr_is_never_recorded_or_linked(rig: PubRig) -> None:
    number, _task_id, feature_id = integrated(rig)
    rig.tracker.ref_resolver = lambda branch: "1" * 40  # GitHub reports another head (the branch moved meanwhile)
    comments = rig.own_comments(number)
    report = rig.publish()
    assert "does not match the verified candidate" in report.attention[0] and "not recorded" in report.attention[0]
    assert not report.created and len(rig.prs()) == 1
    cand = rig.candidate(feature_id)
    assert cand.number is None and cand.url is None and not cand.origin and cand.attention  # sticky
    assert cand.pending_create  # the attempt stays visible
    assert not rig.events("pull-request-opened") and not rig.events("pull-request-adopted")
    assert rig.store.load_record("outbox", f"pr-{feature_id}") is None and rig.own_comments(number) == comments
    assert "reset" in rig.publish().attention[0] and len(rig.prs()) == 1
    reset = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--tracker", "local"]
    assert cli_main([*reset, "github", "publish", "reset", feature_id, "--apply"]) == 0
    again = rig.publish()  # found again, still refused: never adopted, never a second PR
    assert "cannot be adopted" in again.attention[0] and len(rig.prs()) == 1 and rig.candidate(feature_id).number \
        is None and rig.own_comments(number) == comments


def test_a_lost_create_response_is_adopted_not_repeated(rig: PubRig) -> None:
    _number, _task_id, feature_id = integrated(rig)
    rig.tracker.lost.append(("create_pull_request", GitHubUncertainWriteError("POST pulls: no response")))
    report = rig.publish()
    assert "may have been applied" in report.deferred[0] and len(rig.prs()) == 1
    assert rig.candidate(feature_id).number is None
    again = rig.publish()
    assert again.adopted == {feature_id: rig.prs()[0].number} and not again.created and len(rig.prs()) == 1
    assert rig.candidate(feature_id).origin == "created"  # recognized as the coordinator's own


def test_a_crash_right_after_github_created_the_pr_never_duplicates_it(repo: Path, state_dir: Path,
                                                                        github_origin: Path) -> None:
    rig = PubRig(repo, state_dir, github_origin)
    number, _task_id, feature_id = integrated(rig)
    with pytest.raises(SimulatedCrash):
        rig.publish(faults=(f"crash-after-create:{feature_id}",))
    assert len(rig.prs()) == 1 and rig.candidate(feature_id).number is None
    restarted = PubRig(repo, state_dir, github_origin)  # a new coordinator over the same files
    report = restarted.publish()
    assert report.ok and report.adopted == {feature_id: restarted.prs()[0].number} and len(restarted.prs()) == 1
    assert restarted.candidate(feature_id).origin == "created"
    assert len(restarted.comments(number, "Draft pull request opened")) == 1


FAULTS = ("crash-before-push", "crash-after-push", "crash-before-create", "crash-after-create", "crash-before-done",
          "crash-after-done", "crash-after-comment", "crash-after-close")


@pytest.mark.parametrize("fault", FAULTS)
def test_every_interruption_is_recovered_without_duplicates(repo: Path, state_dir: Path, github_origin: Path,
                                                            fault: str) -> None:
    rig = PubRig(repo, state_dir, github_origin)
    number, task_id, feature_id = verified(rig)
    late = fault in ("crash-before-done", "crash-after-done", "crash-after-comment", "crash-after-close")
    if late:
        assert rig.publish().created
        rig.merge_on_github(rig.candidate(feature_id).number)  # type: ignore[arg-type]
    with pytest.raises(SimulatedCrash):
        rig.publish(faults=(f"{fault}:{feature_id}",))
    for _attempt in range(3):  # restarts: new coordinators over the same files
        restarted = PubRig(repo, state_dir, github_origin)
        restarted.publish()
    if not late:
        assert restarted.candidate(feature_id).number is not None
        restarted.merge_on_github(restarted.candidate(feature_id).number)  # type: ignore[arg-type]
        restarted.publish()
    final = PubRig(repo, state_dir, github_origin)
    assert final.feature(feature_id).state is FeatureState.DONE and len(final.prs()) == 1
    assert len(final.comments(number, "Draft pull request opened")) == 1
    assert len(final.comments(number, "**Completed.**")) == 1 and final.remote(number).state == "closed"
    assert values(final.coord, task_id)["Status"] == "Done"
    record = load_intake(final.store, feature_id)
    assert record is not None and record.status == "done"
    writes = final.tracker.writes
    assert final.publish().ok and PubRig(repo, state_dir, github_origin).tracker.writes == writes


def test_an_existing_matching_pr_is_adopted(rig: PubRig) -> None:
    number, _task_id, feature_id = integrated(rig)
    cand = rig.candidate(feature_id)
    git(rig.repo, "push", "-q", "origin", cand.head)  # a person pushed and opened the PR by hand
    manual = rig.tracker.add_pull_request_as(OPERATOR, cand.head, "dev", head_sha=cand.head_sha)
    report = rig.publish()
    assert report.ok and report.adopted == {feature_id: manual.number} and not report.created
    assert not rig.tracker.authorized and len(rig.prs()) == 1
    assert rig.candidate(feature_id).origin == "adopted" and rig.candidate(feature_id).number == manual.number
    assert len(rig.comments(number, "Pull request found and recorded")) == 1


@pytest.mark.parametrize("change, reason", [
    ({"head_sha": "1" * 40}, "is not the verified"),
    ({"base": "main"}, "targets 'main'"),
    ({"head_repo": "stranger/mx-admin-cloud"}, "its head is in"),
    ({"base_repo": "stranger/mx-admin-cloud"}, "targets the repository"),
    ({"author": "stranger"}, "opened by 'stranger'"),
])
def test_mismatched_prs_are_never_adopted(rig: PubRig, change: dict, reason: str) -> None:
    _number, _task_id, feature_id = integrated(rig)
    cand = rig.candidate(feature_id)
    fields = {"head_sha": cand.head_sha, **change}
    author = fields.pop("author", OPERATOR)
    base = fields.pop("base", "dev")
    rig.tracker.add_pull_request_as(author, cand.head, base, **fields)
    report = rig.publish()
    assert reason in report.attention[0] and "no PR is created" in report.attention[0]
    assert len(rig.prs()) == 1 and rig.candidate(feature_id).number is None and not rig.tracker.authorized
    assert remote_sha(rig.bare, cand.head) is None  # nothing pushed either


def test_several_matching_or_a_closed_foreign_pr_stop_creation(rig: PubRig) -> None:
    _number, _task_id, feature_id = integrated(rig)
    cand = rig.candidate(feature_id)
    first = rig.tracker.add_pull_request_as(OPERATOR, cand.head, "dev", head_sha=cand.head_sha)
    rig.tracker.add_pull_request_as(OPERATOR, cand.head, "dev", head_sha=cand.head_sha)
    assert "cannot be adopted" in rig.publish().attention[0] and len(rig.prs()) == 2
    rig.tracker.update_pull_request(first.number, state="closed")
    rig.tracker.prs.pop(first.number + 1)
    rig.tracker._save()
    rig.tracker.update_pull_request(first.number, head_sha="2" * 40)  # closed, unmerged, a different head
    reset = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--tracker", "local"]
    assert cli_main([*reset, "github", "publish", "reset", feature_id, "--apply"]) == 0
    report = rig.publish()  # searched open AND closed PRs: the closed one still blocks a second PR
    assert "cannot be adopted" in report.attention[0] and len(rig.prs()) == 1


# -- following the PR: draft, ready, closed, merged -------------------------------------------------------------------

def test_draft_open_and_closed_unmerged_prs_never_complete(rig: PubRig) -> None:
    number, task_id, feature_id, pr = draft_open(rig)
    assert "Draft PR" in rig.publish().waiting[0]
    rig.tracker.update_pull_request(pr, draft=False)
    assert "ready for review" in rig.publish().waiting[0]
    rig.tracker.update_pull_request(pr, state="closed")  # closed without merge
    report = rig.publish()
    assert "closed without merge" in report.attention[0] and report.service_state()[0] == "NEEDS_ATTENTION"
    assert rig.feature(feature_id).state is FeatureState.REVIEW and rig.remote(number).state == "open"
    assert values(rig.coord, task_id)["Status"] == "Review" and rig.candidate(feature_id).attention is None
    assert len(rig.prs()) == 1  # nothing created again
    rig.tracker.update_pull_request(pr, state="open")  # reopened: waiting again
    assert rig.publish().waiting and not rig.publish().attention
    rig.merge_on_github(pr)
    assert rig.publish().completed


def test_a_changed_pr_head_stops_completion(rig: PubRig) -> None:
    number, _task_id, feature_id, pr = draft_open(rig)
    foreign = push_foreign_on_top(rig, f"feature/{feature_id}")
    rig.tracker.update_pull_request(pr, head_sha=foreign)  # someone pushed to the PR branch
    report = rig.publish()
    assert "no longer matches" in report.attention[0] and rig.candidate(feature_id).attention
    rig.merge_on_github(pr)
    after = rig.publish()
    assert not after.completed and rig.feature(feature_id).state is FeatureState.REVIEW
    assert rig.remote(number).state == "open"


def push_foreign_on_top(rig: PubRig, branch: str) -> str:
    work = Path(tempfile.mkdtemp(prefix="ontop-", dir=rig.state.parent))
    subprocess.run(["git", "clone", "-q", "--branch", branch, str(rig.bare), str(work)], check=True)
    (work / "cli" / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    user = ("-c", "user.name=Someone", "-c", "user.email=someone@example.invalid", "-c", "commit.gpgsign=false")
    git(work, "add", "cli/extra.py")
    git(work, *user, "commit", "-qm", "extra")
    git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    return git(work, "rev-parse", "HEAD").strip()


def test_merge_evidence_that_does_not_hold_stops_completion(rig: PubRig) -> None:
    number, _task_id, feature_id, pr = draft_open(rig)
    base_sha = rig.feature(feature_id).base_sha
    rig.tracker.merge_pull_request(pr, "3" * 40)  # GitHub says merged; the commit is nowhere on dev
    report = rig.publish()
    assert "is not on origin/dev" in report.attention[0] and rig.candidate(feature_id).attention
    assert rig.feature(feature_id).state is FeatureState.REVIEW and rig.remote(number).state == "open"
    rig.tracker.update_pull_request(pr, merge_commit_sha=base_sha)  # an old dev commit: on dev, without the head
    assert cli_main(["--repo", str(rig.repo), "--state-dir", str(rig.state), "--tracker", "local", "github",
                     "publish", "reset", feature_id, "--apply"]) == 0
    assert "does not contain the verified head" in rig.publish().attention[0]
    assert rig.feature(feature_id).merged_sha is None


def test_a_squash_merge_is_not_completed_automatically(rig: PubRig) -> None:
    number, _task_id, feature_id, pr = draft_open(rig)
    rig.merge_on_github(pr, squash=True)
    report = rig.publish()
    assert "squash or rebase" in report.attention[0]
    assert rig.feature(feature_id).state is FeatureState.REVIEW and rig.remote(number).state == "open"


def test_a_missing_recorded_pr_stops(rig: PubRig) -> None:
    _number, _task_id, feature_id, pr = draft_open(rig)
    rig.tracker.prs.pop(pr)
    rig.tracker._save()
    assert "no longer exists" in rig.publish().attention[0] and len(rig.prs()) == 0
    assert rig.candidate(feature_id).attention and "no longer exists" in rig.publish().attention[0]  # never recreated
    assert len(rig.prs()) == 0


def test_github_failures_while_waiting_are_transient(rig: PubRig) -> None:
    _number, _task_id, feature_id, _pr = draft_open(rig)
    rig.tracker.fail.append(("get_pull_request", GitHubUnavailableError("GET pulls: HTTP 502")))
    report = rig.publish()
    assert "HTTP 502" in report.deferred[0] and rig.candidate(feature_id).attention is None
    assert rig.publish().waiting


# -- completion and the W4.3.1 protections -----------------------------------------------------------------------------

def test_completion_never_triggers_the_approval_withdrawal(rig: PubRig) -> None:
    number, task_id, feature_id, pr = draft_open(rig)
    rig.merge_on_github(pr)
    assert rig.publish().completed
    for _ in range(2):
        report = IntakePoller(rig.coord).poll(apply=True)
        assert report.ok and not report.changed
    record = load_intake(rig.store, feature_id)
    assert record is not None and record.spec_changed is None and record.status == "done"
    assert not rig.comments(number, "Approval withdrawn") and rig.store.load_task(task_id).state is TaskState.VERIFIED
    assert not rig.events("intake-spec-changed")


def test_an_issue_closed_by_a_person_after_the_merge_still_completes(rig: PubRig) -> None:
    number, _task_id, feature_id, pr = draft_open(rig)
    rig.merge_on_github(pr)
    outside(rig, number, state="closed")  # a person closes the Issue right after merging
    service = rig.service(until=lambda s: (load_intake(rig.store, feature_id) or SimpleNamespace(status="")).status
                          == "done", max_cycles=6)
    assert service.run() == 0  # publication runs before the intake poll: the merge is seen first
    record = load_intake(rig.store, feature_id)
    assert record is not None and record.completed["closed_by"] == "already-closed"
    assert not rig.comments(number, "Approval withdrawn") and not rig.events("intake-spec-changed")


def test_a_withdrawn_approval_before_publication_publishes_nothing(rig: PubRig) -> None:
    number, _task_id, feature_id = integrated(rig)
    outside(rig, number, body="Something completely different")
    report = rig.publish()
    assert "approval no longer holds" in report.attention[0]
    assert remote_sha(rig.bare, f"feature/{feature_id}") is None and not rig.prs()
    outside(rig, number, body=BODY)  # the approved text is restored ...
    assert "reset" in rig.publish().attention[0]  # ... the stop stays until an operator resets it
    assert cli_main(["--repo", str(rig.repo), "--state-dir", str(rig.state), "--tracker", "local", "github",
                     "publish", "reset", feature_id, "--apply"]) == 0
    assert rig.publish().created


def test_only_the_approved_issue_is_closed_and_project_one_is_untouched(rig: PubRig) -> None:
    board = board_of(rig.coord)
    other = rig.issue("Unrelated idea")
    before, protected = rig.snapshot(other), protected_snapshot(board)
    number, _task_id, feature_id, pr = draft_open(rig)
    rig.tracker.live = True  # type: ignore[misc]  # as the real adapter: write_scope test-features-only applies
    board.live = True  # type: ignore[misc]
    rig.merge_on_github(pr)
    assert rig.publish().closed == {feature_id: number}
    assert rig.snapshot(other) == before and protected_snapshot(board) == protected
    closes = [c for c in rig.tracker.calls if c.startswith("update_issue:")]
    assert closes == [f"update_issue:{number}"]


def test_a_transferred_issue_is_not_closed(rig: PubRig) -> None:
    number, _task_id, feature_id, pr = draft_open(rig)
    rig.merge_on_github(pr)
    rig.tracker.lost.append(("update_issue", GitHubUnavailableError("PATCH issue: HTTP 502")))  # a lost answer
    report = rig.publish()
    assert report.completed and "HTTP 502" in report.deferred[0]
    assert rig.feature(feature_id).state is FeatureState.DONE
    rig.tracker.tamper(number, id=123)  # it is no longer the approved Issue (database ID differs)
    report = rig.publish()
    assert "is not closed" in report.attention[0]
    record = load_intake(rig.store, feature_id)
    assert record is not None and record.status != "done"


def test_repeated_completion_and_restarts_write_nothing(repo: Path, state_dir: Path, github_origin: Path) -> None:
    rig = PubRig(repo, state_dir, github_origin)
    _number, _task_id, feature_id, pr = draft_open(rig)
    rig.merge_on_github(pr)
    rig.publish()
    board = board_of(rig.coord)
    tracker_writes, board_writes = rig.tracker.writes, board.writes
    for _ in range(3):
        again = PubRig(repo, state_dir, github_origin).publish()
        assert again.ok and again.features[feature_id]["status"] == "done" and not again.writes
    assert rig.tracker.writes == tracker_writes and board_of(rig.coord).writes == board_writes


# -- Issue #25 / PR #27: a PR opened and merged by hand before W4.3.3 -------------------------------------------------

def test_a_manually_opened_and_merged_pr_completes_issue_25(repo: Path, state_dir: Path,
                                                             github_origin: Path) -> None:
    rig = PubRig(repo, state_dir, github_origin)
    for index in range(24):  # Issues #1-#24 exist: the approved one is #25 and the manual PR #27
        rig.tracker.tamper(rig.issue(f"Old idea {index}"), state="closed")
    number, task_id, feature_id = verified(rig)
    assert (number, feature_id, task_id) == (25, "issue-25", "issue-25.cli")
    rig.tracker.tamper(rig.issue("Something else"), state="closed")  # #26
    rig.coord.integrate(feature_id)  # the operator integrated by hand (W4.3.1): review, candidate not opened
    cand = rig.candidate(feature_id)
    assert rig.feature(feature_id).state is FeatureState.REVIEW and cand.url is None and cand.number is None
    git(repo, "push", "-q", "origin", "feature/issue-25")  # the operator pushed and opened PR #27 by hand ...
    manual = rig.tracker.add_pull_request_as(OPERATOR, "feature/issue-25", "dev", head_sha=cand.head_sha,
                                             title="W4.3: Autonomous Worker smoke test (#25)")
    assert manual.number == 27
    merge = rig.merge_on_github(27)  # ... and merged it; Issue #25 stays open
    assert rig.remote(25).state == "open"
    issues, writes = len(rig.tracker.list_issues()), rig.tracker.writes
    preview = rig.publish(apply=False)
    assert preview.adopted == {"issue-25": 27} and any("complete issue-25" in w for w in preview.writes)
    assert rig.tracker.writes == writes and rig.candidate(feature_id).number is None  # a dry run writes nothing
    report = rig.publish()
    assert report.ok and report.adopted == {"issue-25": 27} and report.completed == {"issue-25": merge}
    assert report.closed == {"issue-25": 25} and not report.created and not report.pushed
    feature = rig.feature(feature_id)
    assert feature.state is FeatureState.DONE and feature.merged_sha == merge
    assert feature.pull_request.number == 27 and feature.pull_request.origin == "adopted"  # type: ignore[union-attr]
    assert feature.pull_request.merge["merged_by"] == OPERATOR  # type: ignore[union-attr]
    assert rig.remote(25).state == "closed" and "#27" in rig.comments(25, "**Completed.**")[0]
    assert values(rig.coord, task_id)["Status"] == "Done"
    assert len(rig.tracker.list_issues()) == issues and len(rig.prs()) == 1  # no new Issue, no new PR
    assert not rig.events("publish-pushed") and not rig.tracker.authorized
    assert not rig.comments(25, "Pull request found and recorded")  # already merged: only the completion


PR27 = {  # the shape GitHub returns for PR #27 (GET pulls/27, read 2026-10-24)
    "number": 27, "id": 4992842966, "node_id": "PR_kwDOSDRM8s8AAAABKZi81g", "state": "closed", "draft": False,
    "merged": True, "merged_at": "2026-10-24T05:40:17Z", "merge_commit_sha": "f631c571180365d61b86d1b649a1d13d4f7be5b0",
    "html_url": "https://github.com/spyderbg/mx-admin-cloud/pull/27",
    "title": "W4.3: Autonomous Worker smoke test (#25)",
    "body": "Smoke test of Issue #25.", "user": {"login": "spyderbg"}, "merged_by": {"login": "spyderbg"},
    "head": {"ref": "feature/issue-25", "sha": "8094a9c57a645730f8d1ddfec39764dc7a45f050",
             "repo": {"full_name": "spyderbg/mx-admin-cloud", "id": 1211387122}},
    "base": {"ref": "dev", "sha": "ff425199c0b3000b0d8208eb2487b872006992eb",
             "repo": {"full_name": "spyderbg/mx-admin-cloud", "id": 1211387122}},
}


def test_the_real_pr_27_matches_the_issue_25_candidate() -> None:
    listed = {k: v for k, v in PR27.items() if k not in ("merged", "merged_by")}  # the list endpoint's shape
    transport = Scripted(ok(PR27), ok([listed]), ok({"login": "spyderbg"}))
    client, _clock = client_for(transport, GitHubConfig())
    pr, (in_list,) = client.get_pull_request(27), client.list_pull_requests("feature/issue-25")
    assert in_list.merged and in_list.merged_by == "" and pr.merged_by == "spyderbg"
    assert "head=spyderbg%3Afeature%2Fissue-25" in transport.requests[1][1] and "state=all" in transport.requests[1][1]
    feature = Feature(id="issue-25", slug="issue-25", title="[W4.3] GitHub → Autonomous Worker smoke test",
                      base_branch="dev", branch="feature/issue-25", state=FeatureState.REVIEW,
                      base_sha="ff425199c0b3000b0d8208eb2487b872006992eb")
    cand = PullRequestCandidate(head="feature/issue-25", base="dev", title=feature.title, body="",
                                head_sha="8094a9c57a645730f8d1ddfec39764dc7a45f050")
    sync = SimpleNamespace(client=client, viewer=client.viewer_login)
    publisher = PullRequestPublisher(SimpleNamespace(store=None, config=default_config(), git=None, sync=sync))
    assert publisher._pr_problem(feature, cand, pr) is None and publisher._pr_problem(feature, cand, in_list) is None
    assert (pr.merge_commit_sha, pr.merged_at, pr.base, pr.head_repo) == (
        "f631c571180365d61b86d1b649a1d13d4f7be5b0", "2026-10-24T05:40:17Z", "dev", "spyderbg/mx-admin-cloud")
    moved = dataclasses.replace(cand, head_sha="1" * 40)
    assert "is not the verified" in (publisher._pr_problem(feature, moved, pr) or "")


# -- the real GitHub adapter (scripted transport) ----------------------------------------------------------------

def test_the_live_client_creates_only_authorized_draft_prs() -> None:
    off = GitHubConfig(repository="octo/repo", min_write_interval_seconds=0.0)
    transport = Scripted()
    client, _clock = client_for(transport, off)
    with pytest.raises(PullRequestPolicyError, match="publish.enabled is false"):
        client.authorize_pull_request("feature/x", "dev")
    with pytest.raises(PullRequestPolicyError, match="not authorized"):
        client.create_pull_request("feature/x", "dev", "T", "B")
    on = dataclasses.replace(off, publish=GitHubPublishConfig(enabled=True))
    created = {**PR27, "state": "open", "merged": False, "merged_at": None, "merge_commit_sha": None, "draft": True,
               "merged_by": None, "head": {**PR27["head"], "ref": "feature/x", "repo": {"full_name": "octo/repo"}},
               "base": {**PR27["base"], "repo": {"full_name": "octo/repo"}}}
    transport = Scripted(ok(created, 201))
    client, _clock = client_for(transport, on)
    for head, base in (("dev", "main"), ("main", "dev"), ("feature/x", "feature/y"), ("ai/42", "dev")):
        with pytest.raises(PullRequestPolicyError, match="only go from a feature branch"):
            client.authorize_pull_request(head, base)
    with pytest.raises(PullRequestPolicyError, match="not authorized"):
        client.create_pull_request("feature/x", "dev", "T", "B")  # enabled, but not authorized for this pair
    client.authorize_pull_request("feature/x", "dev")
    with pytest.raises(PullRequestPolicyError, match="Draft pull requests only"):
        client.create_pull_request("feature/x", "dev", "T", "B", draft=False)
    pr = client.create_pull_request("feature/x", "dev", "T", "B")
    assert pr.draft and pr.head == "feature/x" and not transport.responses
    assert len(transport.requests) == 1
    method, path, body = transport.requests[0]
    assert (method, path) == ("POST", "repos/octo/repo/pulls") and body["draft"] is True
    assert body["maintainer_can_modify"] is False and (body["head"], body["base"]) == ("feature/x", "dev")
    with pytest.raises(PullRequestPolicyError, match="not authorized"):  # one authorization, one creation
        client.create_pull_request("feature/x", "dev", "T", "B")
    assert not any(hasattr(client, name) for name in ("merge_pull_request", "update_pull_request", "mark_ready"))


@pytest.mark.parametrize("change", [
    {"state": "merged"}, {"number": 0}, {"head": {"ref": "feature/x", "sha": "abc"}},
    {"head": {"ref": "bad ref", "sha": "8" * 40}}, {"merge_commit_sha": "xyz"}, {"merged": "yes"},
    {"html_url": "http://evil"}, {"draft": "no"}, {"user": {"login": "bad login"}}, {"merged_at": "yesterday"},
    {"base": {"ref": "dev", "sha": "f" * 40, "repo": {"full_name": "not a repo"}}}, {"body": 5}, {"id": "1"},
])
def test_pull_request_responses_are_validated(change: dict) -> None:
    with pytest.raises(GitHubResponseError):
        parse_pull({**PR27, **change})


def test_a_deleted_fork_head_is_parsed_but_never_matches() -> None:
    pr = parse_pull({**PR27, "head": {**PR27["head"], "repo": None}})
    assert pr.head_repo == "" and pr.merged


# -- commands ----------------------------------------------------------------------------------------------------

def test_the_publish_commands(rig: PubRig, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _number, _task_id, feature_id, pr = draft_open(rig)
    base = ["--repo", str(rig.repo), "--state-dir", str(rig.state), "--tracker", "local"]
    assert cli_main([*base, "github", "publish", "status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["feature"], r["state"], r["pr"], r["origin"], r["draft"]) for r in rows] == \
        [(feature_id, "review", pr, "created", True)]
    assert cli_main([*base, "github", "publish", "poll"]) == 0  # dry run (reads the local tracker)
    out = capsys.readouterr().out
    assert "waits for a person" in out and "dry run" in out
    assert cli_main([*base, "github", "publish", "poll", "--apply"]) == 1  # the built-in configuration: off
    assert "publish.enabled is false" in capsys.readouterr().err
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["publish"]["enabled"] = True
    enabled = tmp_path / "publish.yaml"
    enabled.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert cli_main([*base, "--project-config", str(enabled), "github", "publish", "poll", feature_id, "--apply"]) == 0
    assert cli_main([*base, "github", "publish", "reset", feature_id]) == 0
    assert "not stopped" in capsys.readouterr().out
    assert cli_main([*base, "github", "publish", "poll", "no-such-feature"]) == 1
    assert "no such feature" in capsys.readouterr().out
    state = ensure_state_dir(tmp_path / "empty-state")
    assert cli_main(["--repo", str(rig.repo), "--state-dir", str(state), "github", "publish", "status"]) == 0
    assert "no approved GitHub Issue features" in capsys.readouterr().out
