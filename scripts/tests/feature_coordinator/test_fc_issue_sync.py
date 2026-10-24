"""Issue synchronization, ownership, idempotency, recovery, commands and dependency rules (Phase 4),
against the mock tracker. Everything here is local; the real-GitHub acceptance tests are in
test_fc_github_live.py."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access

from __future__ import annotations

import dataclasses
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import ProjectConfig, default_config, config_from_mapping
from feature_coordinator.demo import DEMO_SCRIPTS, RecordingGates, build_coordinator, demo_spec
from feature_coordinator.errors import (
    ConfigError,
    EvidenceError,
    GitHubUnavailableError,
    LockBusyError,
    OwnershipError,
    PullRequestPolicyError,
    SyncScopeError,
)
from feature_coordinator.github_api import GitHubClient
from feature_coordinator.issue_sync import IssueSync, SimulatedCrash, enqueue_comment, parse_command
from feature_coordinator.issues import (
    BLOCKED_LABEL,
    TEST_LABEL,
    VERIFIED_LABEL,
    MockIssueTracker,
    OwnershipMarker,
    RemoteIssue,
    extract_block,
    parse_op,
    replace_block,
    task_marker,
    verify_ownership,
)
from feature_coordinator.model import Evidence, GateResult, TaskState
from feature_coordinator.workflow import FeatureCoordinator, FeatureSpec, TaskSpec

from fc_helpers import Holder, child_env

OPERATOR = "spyderbg"
CID = "mx-admin-cloud.feature-coordinator"


@pytest.fixture
def tracker(coord: FeatureCoordinator) -> MockIssueTracker:
    tracker = coord.tracker
    assert isinstance(tracker, MockIssueTracker)
    tracker.permissions[OPERATOR] = "admin"
    return tracker


def active(coord: FeatureCoordinator) -> str:
    return coord.activate(coord.plan(demo_spec()).id).id


def labels(tracker: MockIssueTracker, number: int | None) -> set[str]:
    assert number is not None
    return set(tracker.get_issue(number).labels)


def issue_of(coord: FeatureCoordinator, task_id: str) -> int:
    number = coord.store.load_task(task_id).issue_number
    assert number is not None
    return number


# -- markers and ownership ---------------------------------------------------------------

def test_marker_round_trip_and_ambiguity() -> None:
    marker = task_marker(CID, "f", "f.api")
    assert OwnershipMarker.parse(f"x\n{marker.render()}\ny") == marker
    assert OwnershipMarker.parse(marker.render() * 2) is None  # two markers: ambiguous, not owned
    assert OwnershipMarker.parse("<!-- feature-coordinator:v1 coordinator=x -->") is None
    with pytest.raises(ValueError):
        OwnershipMarker(CID, "f", "f api", "component-task", "k")


def remote(body: str, **over) -> RemoteIssue:
    base = {"number": 5, "id": 905, "title": "t", "body": body, "state": "open", "labels": ("feature:backend",),
            "author": "feature-coordinator"}
    base.update(over)
    return RemoteIssue(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("over, reason", [
    ({"is_pull_request": True}, "pull request"),
    ({"labels": ("feature:backend", "mx-auto")}, "bug-workflow"),
    ({"labels": ("severity::high",)}, "bug-workflow"),
    ({"body": "no marker"}, "no single valid ownership marker"),
    ({"body": task_marker(CID, "other", "f.api").render()}, "feature"),
    ({"body": task_marker(CID, "f", "f.cli").render()}, "task"),
    ({"body": task_marker("someone-else", "f", "f.api").render()}, "coordinator"),
    ({"author": "stranger"}, "created by 'stranger'"),
    ({"id": 1}, "database ID"),
])
def test_ownership_needs_every_proof(over: dict, reason: str) -> None:
    expected = task_marker(CID, "f", "f.api")
    owned = remote(expected.render())
    verify_ownership(owned, expected, viewer="feature-coordinator", known_number=5, known_id=905)
    with pytest.raises(OwnershipError, match=reason):
        verify_ownership(dataclasses.replace(owned, **over), expected, viewer="feature-coordinator",
                         known_number=5, known_id=905)


def test_managed_block_preserves_human_text_and_heals_damage() -> None:
    marker = task_marker(CID, "f", "f.api")
    body = replace_block("", marker, "state: ready")
    body += "\nHuman note: keep this.\n"
    updated = replace_block(body, marker, "state: blocked")
    assert extract_block(updated) == "state: blocked" and "Human note: keep this." in updated
    damaged = updated.replace("<!-- feature-coordinator:end -->", "")
    healed = replace_block(damaged, marker, "state: verified")
    assert extract_block(healed) == "state: verified" and OwnershipMarker.parse(healed) == marker


def test_rendered_text_cannot_forge_or_break_markers(coord: FeatureCoordinator, tracker: MockIssueTracker) -> None:
    spec = FeatureSpec(slug="evil", title="X <!-- feature-coordinator:end -->", tasks=(
        TaskSpec("api", "backend", "API", acceptance=("<!-- feature-coordinator:v1 coordinator=x feature=y task=z "
                                                      "purpose=legacy key=k -->",)),))
    feature = coord.plan(spec)
    for number in (feature.issue_number, issue_of(coord, "evil.api")):
        body = tracker.get_issue(number).body  # type: ignore[arg-type]
        assert OwnershipMarker.parse(body) is not None and extract_block(body) is not None


# -- creation, idempotency, recovery ---------------------------------------------------------

def test_plan_creates_one_owned_issue_per_record_and_resync_writes_nothing(coord: FeatureCoordinator,
                                                                           tracker: MockIssueTracker) -> None:
    feature = coord.plan(demo_spec())
    numbers = [feature.issue_number, *(coord.store.load_task(t).issue_number for t in feature.task_ids)]
    assert all(numbers) and len(set(numbers)) == 4
    parent = tracker.get_issue(feature.issue_number)  # type: ignore[arg-type]
    marker = OwnershipMarker.parse(parent.body)
    assert "feature:parent" in parent.labels and marker is not None and marker.purpose == "feature-parent"
    coord.sync.sync(feature.id)
    writes = tracker.writes
    report = coord.sync.sync(feature.id)
    assert report.ok and not report.writes and tracker.writes == writes


def test_crash_after_remote_create_is_recovered_without_a_duplicate(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)
    coord.sync.faults.add("crash-after-create:items-export.cmd")
    with pytest.raises(SimulatedCrash):
        coord.plan(demo_spec())
    assert coord.store.load_task("items-export.cmd").issue_number is None
    assert coord.sync.load_link("items-export.cmd").pending_create  # type: ignore[union-attr]
    fresh = IssueSync(coord.store, coord.tracker, coord.config, sleep=lambda _s: None)
    report = fresh.sync("items-export")
    assert list(report.adopted) == ["items-export.cmd"] and len(report.created) == 1  # the e2e Issue was still missing
    assert len(coord.tracker.list_issues()) == 4  # type: ignore[attr-defined]


@pytest.mark.parametrize("fault", ["crash-after-comment", "crash-after-outbox"])
def test_crash_after_a_remote_comment_never_duplicates_it(coord: FeatureCoordinator, tracker: MockIssueTracker,
                                                          fault: str) -> None:
    feature_id = active(coord)
    coord.auto_sync = False
    number = issue_of(coord, "items-export.cmd")
    if fault == "crash-after-outbox":
        with coord.store.transaction():
            enqueue_comment(coord.store, "items-export.cmd", "note for {{issue:items-export.view}}")
    else:
        coord.hold_task("items-export.cmd", reason="needs a decision")  # a new state change to report
    before = len(tracker.list_comments(number))
    coord.sync.faults.add(f"{fault}:items-export.cmd")
    with pytest.raises(SimulatedCrash):
        coord.sync.sync(feature_id)
    assert len(tracker.list_comments(number)) == before + 1  # posted remotely, not recorded locally
    coord.sync.sync(feature_id)
    coord.sync.sync(feature_id)
    after = tracker.list_comments(number)
    assert len(after) == before + 1
    if fault == "crash-after-outbox":
        assert f"#{issue_of(coord, 'items-export.view')}" in after[-1].body  # placeholder resolved


def test_duplicates_are_detected_and_the_oldest_is_adopted(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)
    coord.auto_sync = False
    feature = coord.plan(demo_spec())
    tracker = coord.tracker
    marker = task_marker(CID, feature.id, "items-export.cmd")
    first = tracker.create_issue("a", marker.render(), ["feature:cli"])  # type: ignore[attr-defined]
    tracker.create_issue("b", marker.render(), ["feature:cli"])  # type: ignore[attr-defined]
    report = coord.sync.sync(feature.id)
    assert report.adopted["items-export.cmd"] == first.number and report.duplicates == {"items-export.cmd": [2]}


def test_forged_and_foreign_issues_are_never_adopted_or_modified(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)
    coord.auto_sync = False
    feature = coord.plan(demo_spec())
    tracker: MockIssueTracker = coord.tracker  # type: ignore[assignment]
    marker = task_marker(CID, feature.id, "items-export.cmd").render()
    forged = tracker.add_foreign_issue("forged", ["feature:cli"], body=marker, author="stranger")
    bug = tracker.add_foreign_issue("bug", ["feature:cli", "mx-auto"], body=marker, author="feature-coordinator")
    snapshot = {n: dict(tracker.issues[n]) for n in (forged.number, bug.number)}
    report = coord.sync.sync(feature.id)
    assert report.created["items-export.cmd"] not in (forged.number, bug.number)
    assert {n: tracker.issues[n] for n in snapshot} == snapshot


def test_a_mapped_issue_that_lost_its_marker_is_protected(coord: FeatureCoordinator, tracker: MockIssueTracker) -> None:
    feature_id = active(coord)
    number = issue_of(coord, "items-export.cmd")
    tracker.tamper(number, body="a human rewrote everything")
    writes = tracker.writes
    report = coord.sync.sync(feature_id)
    assert "items-export.cmd" in report.protected and tracker.writes == writes
    assert tracker.get_issue(number).body == "a human rewrote everything"


def test_drift_is_restored_from_the_local_state(coord: FeatureCoordinator, tracker: MockIssueTracker) -> None:
    feature_id = active(coord)
    coord.request_contract("items-export.cmd", key="api", title="API", contract="GET /x")
    cmd = issue_of(coord, "items-export.cmd")
    body = tracker.get_issue(cmd).body
    tracker.tamper(cmd, labels=["feature:cli", VERIFIED_LABEL, "bug"], state="closed",
                   body=body.replace("**blocked**", "**verified**") + "\nhuman addendum\n")
    report = coord.sync.sync(feature_id)
    issue = tracker.get_issue(cmd)
    assert issue.state == "open" and BLOCKED_LABEL in issue.labels and VERIFIED_LABEL not in issue.labels
    assert "bug" in issue.labels and "human addendum" in issue.body  # non-feature content preserved
    assert coord.store.load_task("items-export.cmd").state is TaskState.BLOCKED
    assert len(report.drift) == 3


# -- scope, failures, locks ---------------------------------------------------------------------

class LiveMock(MockIssueTracker):
    """The mock pretending to be live GitHub (write scope applies)."""

    live = True


def test_write_scope_limits_live_writes_to_test_features(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)
    coord.tracker = LiveMock()
    coord.sync = IssueSync(coord.store, coord.tracker, coord.config)
    feature = coord.plan(demo_spec())  # local planning succeeds; the sync is refused and recorded
    assert feature.issue_number is None and not coord.tracker.issues  # type: ignore[attr-defined]
    assert any(e["kind"] == "tracker-sync-deferred" and "test-features-only" in e["error"]
               for e in coord.store.events())
    with pytest.raises(SyncScopeError):
        coord.sync.sync(feature.id)
    assert coord.sync.sync(feature.id, apply=False).writes  # a dry run is allowed
    test_feature = coord.plan(dataclasses.replace(demo_spec(), slug="test-only", test=True))
    assert test_feature.issue_number
    assert TEST_LABEL in coord.tracker.get_issue(test_feature.issue_number).labels  # type: ignore[attr-defined]


def test_github_failure_never_corrupts_local_state_and_recovers(coord: FeatureCoordinator,
                                                                tracker: MockIssueTracker) -> None:
    feature_id = active(coord)
    tracker.fail.append(("add_labels", GitHubUnavailableError("HTTP 502")))
    coord.request_contract("items-export.cmd", key="api", title="API", contract="GET /x")
    tasks = coord.store.load_tasks(coord.store.load_feature(feature_id))
    assert tasks["items-export.cmd"].state is TaskState.BLOCKED
    assert tasks["items-export.cmd"].depends_on == ("items-export.contract-api",)
    assert any(e["kind"] == "tracker-sync-deferred" for e in coord.store.events())
    assert coord.sync.sync(feature_id).ok
    assert BLOCKED_LABEL in labels(tracker, issue_of(coord, "items-export.cmd"))


class LockProbe(MockIssueTracker):
    """Asserts on every tracker call that no coordinator, project or role lock is held."""

    store = None

    def _op(self, name, *, write):  # type: ignore[override]
        if self.store is not None:
            for lock in (self.store.locks.coordinator(), self.store.locks.project(shared=False),
                         *(self.store.locks.role(r) for r in ("backend", "cli", "frontend"))):
                lock.acquire()  # raises LockBusyError if anyone holds it
                lock.release()
        return super()._op(name, write=write)


def test_no_coordinator_lock_is_held_during_tracker_requests(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)
    probe = LockProbe()
    coord.tracker, coord.sync = probe, IssueSync(coord.store, probe, coord.config)
    probe.store = coord.store
    feature_id = coord.activate(coord.plan(demo_spec()).id).id
    contract = coord.request_contract("items-export.cmd", key="api", title="API", contract="GET /x")
    coord.run_task(contract.id)
    coord.run_task("items-export.cmd")
    assert coord.sync.sync(feature_id).ok and len(probe.calls) > 20


def test_sync_runs_while_a_worker_holds_its_claim_and_one_sync_at_a_time(coord: FeatureCoordinator,
                                                                          state_dir: Path) -> None:
    feature_id = active(coord)
    feature = coord.store.load_feature(feature_id)
    worker = Holder("""
        import sys
        from pathlib import Path
        from feature_coordinator.claims import ClaimManager
        from feature_coordinator.store import StateStore
        claim = ClaimManager(StateStore(Path(sys.argv[1]))).claim(sys.argv[2], role="frontend", branch=sys.argv[3],
                                                                  head_sha="0" * 40)
    """, str(state_dir), "items-export.view", feature.branch)
    try:
        assert coord.sync.sync(feature_id).ok  # a running worker does not block the sync
        assert IN_PROGRESS in labels(coord.tracker, issue_of(coord, "items-export.view"))  # type: ignore[arg-type]
        lock = coord.store.locks.github_sync().acquire()
        try:
            busy = dataclasses.replace(coord.config.github, sync_lock_timeout_seconds=0.1)
            other = IssueSync(coord.store, coord.tracker, dataclasses.replace(coord.config, github=busy))
            with pytest.raises(LockBusyError):
                other.sync(feature_id)
        finally:
            lock.release()
    finally:
        worker.kill()


IN_PROGRESS = "feature:in-progress"


def test_concurrent_sync_processes_never_create_duplicates(repo: Path, state_dir: Path, tmp_path: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)
    coord.auto_sync = False
    coord.plan(demo_spec())
    code = textwrap.dedent("""
        import sys
        from pathlib import Path
        from feature_coordinator.config import default_config
        from feature_coordinator.issue_sync import IssueSync
        from feature_coordinator.issues import MockIssueTracker
        from feature_coordinator.store import StateStore
        report = IssueSync(StateStore(Path(sys.argv[1])), MockIssueTracker(Path(sys.argv[2])),
                           default_config()).sync("items-export")
        assert report.ok, report
    """)
    path = tmp_path / "shared-tracker.json"
    procs = [subprocess.Popen([sys.executable, "-c", code, str(state_dir), str(path)],  # pylint: disable=consider-using-with
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env())
             for _ in range(3)]
    for proc in procs:
        _out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err
    keys = [OwnershipMarker.parse(i.body).key for i in MockIssueTracker(path).list_issues()]  # type: ignore[union-attr]
    assert len(keys) == 4 and len(set(keys)) == 4


# -- dependencies --------------------------------------------------------------------------------

def test_reopened_contract_reblocks_ready_dependents(coord: FeatureCoordinator, tracker: MockIssueTracker) -> None:
    active(coord)
    contract = coord.request_contract("items-export.cmd", key="api", title="API", contract="GET /x")
    coord.run_task(contract.id)
    assert coord.store.load_task("items-export.cmd").state is TaskState.READY
    assert coord.reopen_task(contract.id, "contract changed") == ["items-export.cmd"]
    assert coord.store.load_task("items-export.cmd").state is TaskState.BLOCKED
    assert BLOCKED_LABEL in labels(tracker, issue_of(coord, "items-export.cmd"))


def test_a_consumer_is_never_verified_while_its_dependency_is_unverified(coord: FeatureCoordinator) -> None:
    feature_id = active(coord)
    contract = coord.request_contract("items-export.cmd", key="api", title="API", contract="GET /x")
    coord.run_task(contract.id)
    feature = coord.store.load_feature(feature_id)
    head = coord.git.rev_parse(f"refs/heads/{feature.branch}")
    claim = coord.claims.claim("items-export.cmd", role="cli", branch=feature.branch, head_sha=head)
    coord.reopen_task(contract.id, "contract changed during the consumer's run")
    evidence = Evidence(head_sha=head, gates=(GateResult("coordinator-tests", 0),))
    with pytest.raises(EvidenceError, match="cannot be verified while"):
        coord.verify_task("items-export.cmd", evidence)
    coord.claims.release(claim, yield_task=True)


def test_probe_reports_an_unavailable_dependency_and_never_unblocks(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS, gates=RecordingGates(failing=["contract-check"]))
    feature_id = coord.activate(coord.plan(demo_spec()).id).id
    contract = coord.request_contract("items-export.cmd", key="api", title="API", contract="GET /x")
    available, evidence = coord.probe_dependency(contract.id)
    assert not available and [g.gate for g in evidence.gates] == ["contract-check", "backend-tests"]
    comments = coord.tracker.list_comments(issue_of(coord, "items-export.cmd"))  # type: ignore[attr-defined]
    assert any("**unavailable**" in c.body for c in comments)
    passing = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)
    passing.tracker, passing.sync = coord.tracker, coord.sync
    assert passing.probe_dependency(contract.id)[0] is True
    assert passing.store.load_task("items-export.cmd").state is TaskState.BLOCKED  # evidence only
    assert feature_id


# -- GitHub commands --------------------------------------------------------------------------------

def command(tracker: MockIssueTracker, coord: FeatureCoordinator, record: str, text: str, author: str = OPERATOR):
    number = coord.store.load_feature(record).issue_number if "." not in record else issue_of(coord, record)
    tracker.add_comment_as(number, text, author)  # type: ignore[arg-type]
    report = coord.sync.sync(record.split(".")[0])
    return report.commands[-1]["result"] if report.commands else None


def test_parse_command_grammar() -> None:
    assert parse_command("/feature hold f.cli waiting for UX\nmore text") == ("hold", "f.cli", "waiting for UX")
    assert parse_command("  \n/feature release f.web") == ("release", "f.web", "")
    assert parse_command("/feature delete everything")[0] == "invalid"  # type: ignore[index]
    assert parse_command("please /feature release f.web") is None
    assert parse_command("/feature release f.web; rm -rf /")[0] == "invalid"  # type: ignore[index]
    assert parse_command("/feature hold f.x " + "y" * 3000) is None


def test_human_hold_and_release_through_validated_comments(coord: FeatureCoordinator,
                                                           tracker: MockIssueTracker) -> None:
    active(coord)
    assert command(tracker, coord, "items-export.view", "/feature hold items-export.view UX review").startswith(
        "accepted")
    assert coord.store.load_task("items-export.view").state is TaskState.NEEDS_HUMAN
    coord.sync.sync("items-export")  # re-ingestion never applies a command twice
    events = [e for e in coord.store.events() if e["kind"] == "task-held"]
    assert len(events) == 1
    assert command(tracker, coord, "items-export", "/feature release items-export.view").startswith("accepted")
    assert coord.store.load_task("items-export.view").state is TaskState.READY


@pytest.mark.parametrize("author, permission, text, expected", [
    ("stranger", "admin", "/feature hold items-export.view x", "ignored: stranger is not a configured"),
    (OPERATOR, "read", "/feature hold items-export.view x", "ignored: spyderbg has repository permission 'read'"),
    (OPERATOR, "admin", "/feature hold other.task x", "rejected: `other.task` is not a task"),
    (OPERATOR, "admin", "/feature hold items-export.cmd x", "rejected: a command on a task Issue"),
    (OPERATOR, "admin", "/feature release items-export.view", "rejected: `items-export.view` is ready"),
    (OPERATOR, "admin", "/feature approve items-export.view", "rejected: unrecognized command"),
])
def test_unauthorized_or_invalid_commands_change_nothing(coord: FeatureCoordinator, tracker: MockIssueTracker,
                                                          author: str, permission: str, text: str,
                                                          expected: str) -> None:
    active(coord)
    tracker.permissions[OPERATOR] = permission
    tracker.permissions["stranger"] = permission
    states = {t.id: t.state for t in coord.store.load_tasks(coord.store.load_feature("items-export")).values()}
    assert command(tracker, coord, "items-export.view", text, author).startswith(expected)
    after = {t.id: t.state for t in coord.store.load_tasks(coord.store.load_feature("items-export")).values()}
    assert after == states


def test_a_release_cannot_free_a_dependency_block_or_a_newer_hold(coord: FeatureCoordinator,
                                                                 tracker: MockIssueTracker) -> None:
    active(coord)
    coord.request_contract("items-export.cmd", key="api", title="API", contract="GET /x")
    assert command(tracker, coord, "items-export.cmd", "/feature release items-export.cmd").startswith("rejected")
    assert coord.store.load_task("items-export.cmd").state is TaskState.BLOCKED
    coord.auto_sync = False
    tracker.add_comment_as(issue_of(coord, "items-export.view"), "/feature release items-export.view", OPERATOR)
    coord.hold_task("items-export.view", reason="decision pending")  # a local hold, AFTER that comment was written
    report = coord.sync.sync("items-export")
    assert report.commands[-1]["result"].startswith("rejected: the comment predates the current hold")
    assert coord.store.load_task("items-export.view").state is TaskState.NEEDS_HUMAN


def test_a_human_hold_survives_dependency_verification(coord: FeatureCoordinator) -> None:
    active(coord)
    contract = coord.request_contract("items-export.cmd", key="api", title="API", contract="GET /x")
    coord.hold_task("items-export.cmd", reason="product decision", actor="operator")
    run = coord.run_task(contract.id)
    assert run.unblocked == [] and coord.store.load_task("items-export.cmd").state is TaskState.NEEDS_HUMAN
    assert coord.release_task("items-export.cmd").state is TaskState.READY


# -- pull requests, cleanup, reconciliation ------------------------------------------------------------

def test_the_live_adapter_never_creates_a_pull_request(coord: FeatureCoordinator) -> None:
    feature_id = active(coord)
    contract = coord.request_contract("items-export.cmd", key="api", title="API", contract="GET /x")
    for task_id in (contract.id, "items-export.view", "items-export.cmd", "items-export.e2e"):
        coord.run_task(task_id)
    coord.integrate(feature_id)

    class NoNetwork:  # pylint: disable=too-few-public-methods
        """A transport that fails the test if anything is sent."""

        def request(self, *_a):
            raise AssertionError("no request may be sent")
    live = GitHubClient(NoNetwork(), coord.config.github)
    original, coord.tracker = coord.tracker, live
    coord.tracker.list_pull_requests = lambda head=None, state="all": []  # type: ignore[method-assign]
    with pytest.raises(PullRequestPolicyError, match="not authorized"):
        coord.open_pull_request(feature_id, approved=True)
    # W4.3.3: enabling publication does not open this path either — only the publisher's authorized Draft PR
    coord.tracker = GitHubClient(NoNetwork(), dataclasses.replace(coord.config.github, publish=dataclasses.replace(
        coord.config.github.publish, enabled=True)))
    coord.tracker.list_pull_requests = lambda head=None, state="all": []  # type: ignore[method-assign]
    with pytest.raises(PullRequestPolicyError, match="not authorized"):
        coord.open_pull_request(feature_id, approved=True)
    coord.tracker = original
    assert coord.store.load_feature(feature_id).pull_request.url is None  # type: ignore[union-attr]


def test_cleanup_only_touches_owned_test_issues_and_is_repeatable(coord: FeatureCoordinator,
                                                                  tracker: MockIssueTracker) -> None:
    with pytest.raises(SyncScopeError):
        coord.sync.cleanup_test_feature(coord.plan(demo_spec()).id, "summary", apply=True)
    feature = coord.plan(dataclasses.replace(demo_spec(), slug="t1", test=True))
    unrelated = tracker.add_foreign_issue("unrelated", ["feature:test"], author="feature-coordinator")
    report = coord.sync.cleanup_test_feature(feature.id, "all good", apply=True)
    assert report.ok and all(tracker.get_issue(i["number"]).state == "closed" for i in report.issues.values())
    assert tracker.get_issue(unrelated.number).state == "open"
    again = coord.sync.cleanup_test_feature(feature.id, "all good", apply=True)
    assert again.ok and not again.writes
    assert coord.sync.sync(feature.id).ok  # a later sync keeps them closed
    assert tracker.get_issue(feature.issue_number).state == "closed"  # type: ignore[arg-type]


def test_reconcile_reports_orphans_without_touching_them(coord: FeatureCoordinator, tracker: MockIssueTracker) -> None:
    feature = coord.plan(demo_spec())
    orphan = tracker.create_issue("orphan", task_marker(CID, "gone", "gone.task").render(), ["feature:cli"])
    result = coord.sync.reconcile([feature.id], apply=True)
    assert result["orphans"] == [{"number": orphan.number, "key": "gone/gone.task", "state": "open"}]
    assert not result["duplicates"] and tracker.get_issue(orphan.number).body == orphan.body


# -- configuration and CLI ---------------------------------------------------------------------------

@pytest.mark.parametrize("change, message", [
    ({"repository": "not a repo"}, "owner/name"), ({"write_scope": "everything"}, "write_scope"),
    ({"token": "ghp_x"}, "unknown github keys"), ({"command_authors": ["bad login!"]}, "invalid logins"),
    ({"command_min_permission": "read"}, "command_min_permission"), ({"max_retries": 50}, "max_retries"),
])
def test_github_configuration_is_validated(change: dict, message: str) -> None:
    import yaml  # pylint: disable=import-outside-toplevel
    data = yaml.safe_load((Path(__file__).resolve().parents[3] / "docs_agents/coordination/"
                           "project-config.example.yaml").read_text(encoding="utf-8"))
    data["github"].update(change)
    with pytest.raises(ConfigError, match=message):
        config_from_mapping(data)


def test_cli_local_tracker_commands(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = tmp_path / "cli-state"
    base = ["--repo", str(repo), "--state-dir", str(state), "--tracker", "local"]
    spec = tmp_path / "spec.json"
    spec.write_text('{"slug": "c1", "title": "C", "test": true, "tasks": [{"key": "a", "role": "backend", '
                    '"title": "A"}, {"key": "b", "role": "cli", "title": "B", "depends_on": ["a"]}]}',
                    encoding="utf-8")
    assert cli_main([*base, "plan", str(spec), "--apply"]) == 0
    assert cli_main([*base, "github", "sync", "c1"]) == 0  # dry run
    assert cli_main([*base, "github", "deps", "c1"]) == 0
    assert "c1.b" in capsys.readouterr().out
    assert cli_main([*base, "hold", "c1.a", "--reason", "wait", "--apply"]) == 0
    assert cli_main([*base, "release", "c1.a", "--apply"]) == 0
    assert cli_main([*base, "github", "reconcile", "--apply"]) == 0
    assert cli_main([*base, "github", "status"]) == 0
    assert cli_main([*base, "github", "inspect", "1"]) == 0
    assert "owned: feature `c1`" in capsys.readouterr().out
    assert cli_main([*base, "release", "c1.a", "--apply"]) == 1  # not held: refused, non-zero exit


def test_default_config_github_section_matches_phase4_policy(config: ProjectConfig) -> None:
    assert config.github.write_scope == "test-features-only" and config.github.repository == "spyderbg/mx-admin-cloud"
    assert config == default_config()


# -- the Phase 4 acceptance scenario, rehearsed against the local tracker -----------------------------

def test_lifecycle_rehearsal_with_the_local_tracker(tmp_path: Path, needs_bwrap: None) -> None:
    from feature_coordinator.live_test import run_lifecycle  # pylint: disable=import-outside-toplevel
    del needs_bwrap
    feature_id = "fc-p4-rehearsal"
    state = tmp_path / "work" / feature_id / "state"
    state.mkdir(parents=True, mode=0o700)
    client = MockIssueTracker(state / "tracker.json", permissions={"feature-coordinator": "admin", OPERATOR: "admin"})
    unrelated = client.add_foreign_issue("unrelated bug", ["mx-auto", "source::pytest"], author=OPERATOR)
    report = run_lifecycle(client, tmp_path / "work", tracker_name="local", feature_id=feature_id, log=lambda _l: None,
                           operator_comment=lambda n, body: client.add_comment_as(n, body, OPERATOR))
    failed = [c for c in report["checks"] if not c["passed"]]
    assert report["passed"] and not failed, failed
    assert len(report["checks"]) >= 39 and report["cleanup"]["ok"] and report["cleanup"]["repeat_writes"] == []
    assert client.get(unrelated.number).labels == ["mx-auto", "source::pytest"]
    assert len(report["issues"]) == 4


def test_templates_carry_valid_markers() -> None:
    templates = Path(__file__).resolve().parents[3] / "docs_agents" / "coordination" / "templates"
    feature = (templates / "feature-issue.md").read_text(encoding="utf-8")
    task = (templates / "component-task-issue.md").read_text(encoding="utf-8")
    assert OwnershipMarker.parse(feature).purpose == "feature-parent"  # type: ignore[union-attr]
    assert OwnershipMarker.parse(task).purpose == "component-task"  # type: ignore[union-attr]
    assert extract_block(feature) and extract_block(task)
    for name in ("progress-comment.md", "command-comments.md"):
        assert parse_op((templates / name).read_text(encoding="utf-8"))
