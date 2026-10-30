"""W4.3.1: GitHub Issue intake — an approved Issue becomes a Worker task without a YAML file.

In-process tests on a disposable repository: the JSON mock tracker stands in for GitHub's Issue and
comment responses (persisted, so a "restart" is a new coordinator over the same files), the local
board for Project #2 (and the protected Project #1), mock runners for the agents, recorded gates.
No model, no real GitHub, no network."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,too-many-lines

from __future__ import annotations

import copy
import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import GitHubIntakeConfig, config_from_mapping, default_config
from feature_coordinator.demo import RecordingGates, build_coordinator, demo_spec
from feature_coordinator.errors import (
    ClaimError,
    ConfigError,
    CoordinatorError,
    EvidenceError,
    GitHubUnavailableError,
    OwnershipError,
    PullRequestPolicyError,
    SyncScopeError,
)
from feature_coordinator.intake import (
    IntakeRecord,
    feature_id_for,
    load_intake,
    parse_worker_command,
    spec_digest,
    verify_intake_issue,
)
from feature_coordinator.intake_poll import IntakePoller
from feature_coordinator.issue_sync import IssueLink, IssueSync, SimulatedCrash
from feature_coordinator.issues import MockIssueTracker, RemoteIssue, normalize_body, parse_op
from feature_coordinator.model import Evidence, FeatureState, TaskState, transition_feature
from feature_coordinator.runners import FileEdit, RunResult
from feature_coordinator.service import WorkerService
from feature_coordinator.statedir import ensure_state_dir

from fc_helpers import git
from test_fc_autonomy import Clock
from test_fc_project_sync import CFG, attach, board_of, protected_snapshot, values
from test_fc_worker_loop import ThreadHandle

OPERATOR = "spyderbg"   # the configured command author (github.command_authors)
TITLE = "Export category items as CSV"
BODY = ("## Goal\r\n\r\nAdd `kiddo-cli content export-items`.   \r\n\r\n"
        "- [ ] it prints one CSV line per item\r\n- [ ] it exits 0\r\n")
EDIT = FileEdit("cli/src/commands.py", "COMMANDS = ['content export-items']\n")


def intake_config(**intake: Any):
    config = default_config()
    return dataclasses.replace(
        config, worker=dataclasses.replace(config.worker, autonomous_enabled=True),
        github=dataclasses.replace(config.github, intake=dataclasses.replace(
            config.github.intake, **{"enabled": True, **intake})))


class Rig:  # pylint: disable=too-many-instance-attributes
    """A coordinator over one disposable repository with a persisted mock tracker and board."""

    def __init__(self, repo: Path, state: Path, *, config=None, faults: tuple[str, ...] = ()) -> None:
        self.repo, self.state = repo, state
        self.config = config or intake_config()
        self.gates = RecordingGates()
        self.runners: dict[str, Any] | None = None  # shared by every coordinator of this rig once set
        self.coord = self.make(faults)
        self.runners = self.coord.runners
        self.out: list[str] = []
        self.clock = Clock()

    def make(self, faults: tuple[str, ...] = (), config=None):
        """A new coordinator over the same repository, state, tracker and board files (a restart)."""
        config = config or self.config
        coord = build_coordinator(self.repo, self.state, config=config, gates=self.gates)
        tracker = MockIssueTracker(self.state / "tracker.json",
                                   permissions={"feature-coordinator": "admin", OPERATOR: "admin"})
        coord.tracker, coord.sync = tracker, IssueSync(coord.store, tracker, config)
        attach(coord, path=self.state / "board.json", faults=faults)
        if self.runners is not None:
            coord.runners = self.runners
        return coord

    @property
    def tracker(self) -> MockIssueTracker:
        return self.coord.tracker

    @property
    def store(self):
        return self.coord.store

    def issue(self, title: str = TITLE, body: str = BODY, *, author: str = "someone",
              labels: tuple[str, ...] = ()) -> int:
        return self.tracker.add_foreign_issue(title, labels, body=body, author=author).number

    def remote(self, number: int) -> RemoteIssue:
        return self.tracker.get_issue(number)

    def digest(self, number: int) -> str:
        issue = self.remote(number)
        return spec_digest(issue.title, issue.body)

    def approve(self, number: int, role: str = "cli", *, digest: str | None = None, author: str = OPERATOR) -> int:
        return self.tracker.add_comment_as(number, f"/worker approve {role} {digest or self.digest(number)}",
                                           author).id

    def poll(self, apply: bool = True, faults: tuple[str, ...] = ()):
        return IntakePoller(self.coord, faults=faults).poll(apply=apply)

    def snapshot(self, number: int) -> dict[str, Any]:
        self.tracker.get_issue(number)  # reload the file
        return copy.deepcopy(self.tracker.issues[number])

    def own_comments(self, number: int) -> list[str]:
        return [c.body for c in self.tracker.list_comments(number) if parse_op(c.body)]

    def items(self, number: int) -> list[Any]:
        return board_of(self.coord).issue_items(self.config.github.repository, number)

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.store.events() if e["kind"] == kind]

    def script(self, number: int, role: str = "cli", edit: FileEdit = EDIT) -> str:
        task_id = f"{feature_id_for(number)}.{role}"
        self.runners[role].script[task_id] = [edit]
        return task_id

    def spawn(self, task_id: str, launch: int, env: dict | None = None) -> ThreadHandle:
        del launch, env
        own = self.make()  # its own coordinator, like a `python -m feature_coordinator launch` process
        return ThreadHandle(lambda: own.run_task(task_id))

    def service(self, *, until, config=None, max_cycles: int = 40) -> WorkerService:
        config = config or self.config
        clock = self.clock = Clock()
        coord = self.coord if config is self.config else self.make(config=config)
        return WorkerService(config, self.repo, self.state, coord=coord, spawn=self.spawn, clock=clock,
                             idle_sleep=clock.sleep, poll_sleep=lambda _s: time.sleep(0.002), echo=self.out.append,
                             max_cycles=max_cycles, stop_when=until)


@pytest.fixture
def rig(repo: Path, state_dir: Path) -> Rig:
    return Rig(repo, state_dir)


# -- the approval command and the spec digest ----------------------------------------------------------

HEX = "0123456789abcdef" * 4


@pytest.mark.parametrize("body, expected", [
    (f"/worker approve cli {HEX}", ("approve", "cli", HEX)),
    (f"\n  /worker approve backend {HEX}  \nreviewed, thanks", ("approve", "backend", HEX)),
    (f"/worker approve cli {HEX} now", ("invalid", f"/worker approve cli {HEX} now", "")),
    (f"/worker approve {HEX}", ("invalid", f"/worker approve {HEX}", "")),
    ("/worker approve cli abc123", ("invalid", "/worker approve cli abc123", "")),
    (f"/worker approve CLI {HEX}", ("invalid", f"/worker approve CLI {HEX}", "")),
    (f"/worker approve cli {HEX.upper()}", ("invalid", f"/worker approve cli {HEX.upper()}", "")),
    ("/worker", ("invalid", "/worker", "")),
    (f"/workers approve cli {HEX}", None),
    (f"please /worker approve cli {HEX}", None),
    ("/feature hold a.b reason", None),
    (f"/worker approve cli {HEX}" + " " * 2000, None),
])
def test_the_approval_command_grammar_is_strict(body: str, expected: Any) -> None:
    assert parse_worker_command(body) == expected


def test_the_spec_digest_covers_title_and_body_but_not_line_endings() -> None:
    base = spec_digest(TITLE, BODY)
    assert base == spec_digest(f"  {TITLE} ", BODY.replace("\r\n", "\n").replace("   \n", "\n"))
    assert base != spec_digest(TITLE + "!", BODY)
    assert base != spec_digest(TITLE, BODY.replace("exits 0", "exits 1"))
    assert len(base) == 64


# -- before approval -------------------------------------------------------------------------------------

def test_an_unapproved_issue_is_ignored_and_never_written(rig: Rig) -> None:
    quiet = rig.issue()
    talked = rig.issue("Another idea")
    rig.tracker.add_comment_as(talked, "Looks good to me, but do not run it yet.", OPERATOR)
    rig.tracker.add_comment_as(talked, "/feature hold issue-2.cli not yet", OPERATOR)
    before = {n: rig.snapshot(n) for n in (quiet, talked)}
    writes = rig.tracker.writes
    for apply in (False, True, True):
        report = rig.poll(apply=apply)
        assert report.ok and not report.approved and not report.rejected and not report.planned
    assert {n: rig.snapshot(n) for n in (quiet, talked)} == before
    assert rig.tracker.writes == writes                       # nothing written to GitHub
    assert not rig.store.list_records("intake") and not rig.store.list_features()
    assert not rig.items(quiet) and not rig.items(talked)     # not on Project #2
    assert not board_of(rig.coord).list_items(CFG.node_id)


def test_an_authorized_approval_becomes_one_task_on_project_two(rig: Rig) -> None:
    number = rig.issue()
    issues_before = len(rig.tracker.list_issues())
    comment = rig.approve(number)
    report = rig.poll()
    feature_id = feature_id_for(number)
    assert report.ok and [a["feature"] for a in report.approved] == [feature_id]
    assert report.planned == [feature_id] and report.activated == [feature_id]
    record = load_intake(rig.store, feature_id)
    assert record is not None and record.status == "active"
    remote = rig.remote(number)
    assert (record.issue_number, record.issue_id, record.node_id) == (number, remote.id, remote.node_id)
    assert record.title == TITLE and record.body == normalize_body(BODY) and "\r" not in record.body
    assert record.digest == spec_digest(TITLE, BODY) and record.role == "cli"
    assert (record.approved_by, record.approval_comment_id) == (OPERATOR, comment)
    assert record.gates == list(rig.config.github.intake.role_gates["cli"]) == ["cli-lint", "docs-check"]
    feature = rig.store.load_feature(feature_id)
    assert feature.state is FeatureState.ACTIVE and feature.branch == f"feature/{feature_id}"
    assert feature.task_ids == [f"{feature_id}.cli"] and not feature.test and feature.issue_number is None
    task = rig.store.load_task(f"{feature_id}.cli")
    assert task.state is TaskState.READY and task.role == "cli" and task.issue_number == number
    assert task.required_gates == ("cli-lint", "docs-check") and "it prints one CSV line" in task.acceptance[0]
    # the approved Issue IS the task's Issue: no parent or component Issue was created
    assert len(rig.tracker.list_issues()) == issues_before
    ack = rig.own_comments(number)
    assert len(ack) == 1 and "Approved for the Worker" in ack[0] and record.digest[:16] in ack[0]
    remote_after = rig.remote(number)
    assert (remote_after.title, remote_after.body, remote_after.labels) == (TITLE, BODY, ())
    assert values(rig.coord, task.id) == {"Status": "Ready", "Agent": "CLI", "Feature ID": feature_id,
                                          "Blocked Reason": ""}
    assert rig.events("intake-approved")[0]["approver"] == OPERATOR


def test_the_issue_reaches_only_project_two_and_only_after_approval(rig: Rig) -> None:
    board = board_of(rig.coord)
    protected = protected_snapshot(board)
    number = rig.issue()
    rig.poll()
    assert not rig.items(number)
    rig.approve(number)
    rig.poll()
    refs = rig.items(number)
    assert [r.project_number for r in refs] == [CFG.number] and refs[0].project_id == CFG.node_id
    feature_id = feature_id_for(number)
    assert rig.coord.board is not None
    link = rig.coord.board.load_link(f"{feature_id}.cli")
    assert link is not None and link.item_id == refs[0].id and link.issue_number == number
    report = rig.coord.board.sync(feature_id, apply=True)
    assert report.ok and "approved Issue" in report.skipped[feature_id]  # the feature has no card of its own
    assert protected_snapshot(board) == protected                        # Project #1 untouched


def test_the_initial_status_is_backlog_while_another_feature_is_active(rig: Rig) -> None:
    other = rig.coord.activate(rig.coord.plan(demo_spec()).id)
    number = rig.issue()
    rig.approve(number)
    report = rig.poll()
    feature_id = feature_id_for(number)
    assert report.planned == [feature_id] and not report.activated
    assert any("stays in Backlog" in w for w in report.waiting)
    assert rig.store.load_feature(feature_id).state is FeatureState.PLANNED
    assert rig.store.load_task(f"{feature_id}.cli").state is TaskState.PROPOSED
    assert values(rig.coord, f"{feature_id}.cli")["Status"] == "Backlog"
    with rig.store.transaction():  # the active feature leaves the checkout (here: blocked)
        feature = rig.store.load_feature(other.id)
        transition_feature(feature, FeatureState.BLOCKED, "test")
        rig.store.save_feature(feature)
    assert rig.poll().activated == [feature_id]
    assert values(rig.coord, f"{feature_id}.cli")["Status"] == "Ready"


# -- who may approve, and what ------------------------------------------------------------------------------

@pytest.mark.parametrize("author, permission", [
    ("stranger", "admin"),     # not a configured command author, whatever their permission
    (OPERATOR, "read"),        # a command author without write permission
    (OPERATOR, "triage"),
    (OPERATOR, "none"),
])
def test_unauthorized_approvals_are_ignored(rig: Rig, author: str, permission: str) -> None:
    number = rig.issue()
    rig.tracker.permissions[author] = permission
    rig.approve(number, author=author)
    before = rig.snapshot(number)
    report = rig.poll()
    assert not report.approved and [i["author"] for i in report.ignored] == [author]
    assert ("not a configured command author" if author != OPERATOR else f"permission {permission!r}") \
        in report.ignored[0]["reason"]
    assert not rig.store.list_records("intake") and not rig.store.list_features()
    assert rig.snapshot(number) == before and not rig.items(number)   # no reply, no card
    assert rig.events("intake-approval-ignored")[0]["author"] == author
    rig.tracker.permissions[OPERATOR] = "admin"
    rig.approve(number)
    assert rig.poll().approved  # a later authorized approval is accepted


def test_the_approval_is_bound_to_the_text_the_operator_reviewed(rig: Rig) -> None:
    number = rig.issue()
    reviewed = rig.digest(number)
    rig.tracker.tamper(number, body=BODY + "\n- [ ] and also delete the database\n")  # edited after review
    rig.approve(number, digest=reviewed)
    before = rig.snapshot(number)
    report = rig.poll()
    assert not report.approved and "does not match" in report.rejected[0]["reason"]
    assert rig.snapshot(number) == before and not rig.store.list_features()
    rig.approve(number)  # reviewed again: the digest of the current text
    assert rig.poll().approved
    record = load_intake(rig.store, feature_id_for(number))
    assert record is not None and "delete the database" in record.body and record.digest == rig.digest(number)


@pytest.mark.parametrize("command, body, reason", [
    ("/worker approve wizard {digest}", BODY, "unknown role"),
    ("/worker approve automation {digest}", BODY, "disabled"),
    ("/worker please run it", BODY, "unrecognized command"),
    ("/worker approve cli {digest}", "   \r\n", "empty"),
    ("/worker approve cli {digest}", "x" * 20_001, "at most 20000"),
    ("/worker approve cli {digest}", "bell \x07 inside", "control characters"),
])
def test_invalid_approvals_are_rejected(rig: Rig, command: str, body: str, reason: str) -> None:
    number = rig.issue(body=body)
    rig.tracker.add_comment_as(number, command.format(digest=rig.digest(number)), OPERATOR)
    report = rig.poll()
    assert not report.approved and reason in report.rejected[0]["reason"]
    assert not rig.store.list_features() and rig.events("intake-approval-rejected")


def test_an_existing_feature_or_branch_is_never_reused(rig: Rig) -> None:
    number = rig.issue()
    git(rig.repo, "branch", f"feature/{feature_id_for(number)}", "dev")
    rig.approve(number)
    report = rig.poll()
    assert not report.approved and "already exists" in report.rejected[0]["reason"]


# -- after approval -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", [{"body": BODY + "\nAlso rewrite the backend.\n"}, {"title": "Something else"},
                                    {"labels": ["mx-auto"]}])
def test_edits_after_approval_fail_closed_and_the_approved_text_is_kept(rig: Rig, change: dict) -> None:
    number = rig.issue()
    rig.approve(number)
    rig.poll()
    task_id = f"{feature_id_for(number)}.cli"
    approved = rig.store.load_task(task_id).acceptance
    original = rig.snapshot(number)
    rig.tracker.tamper(number, **change)
    report = rig.poll()
    assert [c["task"] for c in report.changed] == [task_id]
    task = rig.store.load_task(task_id)
    assert task.state is TaskState.NEEDS_HUMAN and f"approved Issue #{number} changed" in task.history[-1].reason
    assert task.acceptance == approved and "rewrite the backend" not in task.acceptance[0]  # never replaced
    record = load_intake(rig.store, feature_id_for(number))
    assert record is not None and record.spec_changed and record.body in approved[0]
    notices = [c for c in rig.own_comments(number) if "Approval withdrawn" in c]
    if "labels" in change:  # now a bug-workflow Issue: never written again, its card is left alone
        assert not notices and values(rig.coord, task_id)["Status"] == "Ready"
    else:
        assert len(notices) == 1 and values(rig.coord, task_id)["Status"] == "Blocked"
    rig.poll()
    rig.poll()
    assert len([c for c in rig.own_comments(number) if "Approval withdrawn" in c]) == len(notices)
    assert len(rig.events("intake-spec-changed")) == 1
    rig.tracker.tamper(number, **{k: original[k] for k in change})  # the approved text is restored
    assert rig.poll().restored == [feature_id_for(number)]
    assert rig.store.load_task(task_id).state is TaskState.NEEDS_HUMAN  # an operator decides, not the edit
    rig.coord.release_task(task_id, actor=OPERATOR)
    assert rig.store.load_task(task_id).state is TaskState.READY


def test_duplicate_approvals_and_repeated_polls_are_idempotent(rig: Rig) -> None:
    number = rig.issue()
    rig.approve(number)
    rig.approve(number)
    report = rig.poll()
    assert len(report.approved) == 1 and len(report.duplicates) == 1
    board = board_of(rig.coord)
    tracker_writes, board_writes = rig.tracker.writes, board.writes
    for _ in range(3):
        again = rig.poll()
        assert again.ok and not (again.approved or again.planned or again.activated or again.changed)
    assert (rig.tracker.writes, board.writes) == (tracker_writes, board_writes)
    rig.approve(number)  # yet another approval later: recorded as ignored, nothing else
    assert len(rig.poll().duplicates) == 1
    assert rig.tracker.writes == tracker_writes + 1  # only that comment itself
    assert len(rig.store.list_records("intake")) == 1 and [f.id for f in rig.store.list_features()] == \
        [feature_id_for(number)]
    assert len(rig.items(number)) == 1 and len(rig.own_comments(number)) == 1
    assert len(rig.events("intake-duplicate-ignored")) == 2


@pytest.mark.parametrize("fault", ["crash-after-approval:{number}", "crash-after-plan:issue-{number}",
                                   "crash-after-item-add:issue-{number}.cli"])
def test_a_restart_never_duplicates_the_task(repo: Path, state_dir: Path, fault: str) -> None:
    probe = Rig(repo, state_dir)
    number = probe.issue()
    probe.approve(number)
    name = fault.format(number=number)
    board_fault = name.startswith("crash-after-item-add")
    crashed = Rig(repo, state_dir, faults=(name,) if board_fault else ())
    with pytest.raises(SimulatedCrash):
        crashed.poll(faults=() if board_fault else (name,))
    restarted = Rig(repo, state_dir)  # a new coordinator over the same files
    report = restarted.poll()
    assert report.ok
    feature_id = feature_id_for(number)
    assert [f.id for f in restarted.store.list_features()] == [feature_id]
    assert restarted.store.load_feature(feature_id).task_ids == [f"{feature_id}.cli"]
    assert len(restarted.store.list_records("intake")) == 1 and len(restarted.events("intake-approved")) == 1
    assert len(restarted.items(number)) == 1 and len(restarted.own_comments(number)) == 1
    assert len(restarted.tracker.list_issues()) == 1
    writes = restarted.tracker.writes
    assert Rig(repo, state_dir).poll().ok and Rig(repo, state_dir).tracker.writes == writes


def test_moving_a_card_never_authorizes_execution(rig: Rig) -> None:
    board = board_of(rig.coord)
    waiting = rig.issue("Not approved yet")
    item = board.add_foreign_item(CFG.node_id, rig.remote(waiting))  # a person puts it on the board ...
    board.set_by_human(item, "Status", "Ready")                       # ... and moves it to Ready
    service = rig.service(until=lambda s: s.cycles >= 4)
    assert service.run() == 0
    assert not rig.store.list_features() and not rig.runners["cli"].requests  # nothing planned, no agent
    held = rig.issue()
    task_id = rig.script(held)
    rig.approve(held)
    rig.poll()
    rig.coord.hold_task(task_id, reason="not now", actor=OPERATOR)
    card = rig.coord.board.load_link(task_id).item_id  # type: ignore[union-attr]
    board.set_by_human(card, "Status", "In Progress")
    assert rig.service(until=lambda s: s.cycles >= 4).run() == 0
    assert rig.store.load_task(task_id).state is TaskState.NEEDS_HUMAN and not rig.runners["cli"].requests
    report = rig.coord.board.sync(feature_id_for(held), apply=True)  # type: ignore[union-attr]
    assert report.drift and values(rig.coord, task_id)["Status"] == "Blocked"  # the card is restored


def test_unrelated_issues_remain_untouched(rig: Rig) -> None:
    owned = rig.coord.plan(demo_spec())  # coordinator-owned Issues (ownership markers)
    bug = rig.issue("Crash in export")
    pull = rig.issue("A pull request")
    closed = rig.issue("Closed idea")
    for number in (bug, pull, closed, owned.issue_number):
        rig.approve(number)
    rig.tracker.tamper(bug, labels=["mx-auto", "severity::high"])  # the bug workflow's Issue
    rig.tracker.tamper(pull, is_pull_request=True)
    rig.tracker.tamper(closed, state="closed")
    before = {n: rig.snapshot(n) for n in (bug, pull, closed, owned.issue_number)}
    report = rig.poll()
    assert not report.approved and not report.rejected and not report.ignored
    assert set(report.examined).isdisjoint({bug, pull, closed, owned.issue_number})
    assert {n: rig.snapshot(n) for n in before} == before
    assert not rig.store.list_records("intake")
    assert [f.id for f in rig.store.list_features()] == [owned.id]


# -- the Worker service ---------------------------------------------------------------------------------------

def test_the_worker_service_receives_and_runs_an_approved_issue(rig: Rig) -> None:
    number = rig.issue()
    task_id = rig.script(number)
    rig.approve(number)
    service = rig.service(until=lambda s: rig.store.load_task(task_id).state is TaskState.VERIFIED
                          if rig.store.list_features() else False)
    assert service.run() == 0
    task = rig.store.load_task(task_id)
    assert task.state is TaskState.VERIFIED and len(task.commits) == 1
    assert service.launched == [(task_id, 1)] and len(rig.runners["cli"].requests) == 1
    prompt = rig.runners["cli"].requests[0].prompt
    assert f"GitHub Issue #{number}" in prompt and "it prints one CSV line per item" in prompt
    assert rig.gates.calls == ["cli-lint", "docs-check"]
    assert values(rig.coord, task_id)["Status"] == "Review"  # W4.3.3: Done only once its PR is merged
    kinds = [e["kind"] for e in rig.store.events()]
    assert kinds.index("intake-approved") < kinds.index("intake-activated") < kinds.index("worker-agent-started")


def test_the_worker_checks_the_issue_again_right_before_a_launch(rig: Rig) -> None:
    number = rig.issue()
    task_id = rig.script(number)
    rig.approve(number)
    rig.poll()
    rig.tracker.tamper(number, body="Something completely different")  # edited after the last poll
    no_polling = intake_config(enabled=False)  # only the pre-launch check can notice it
    service = rig.service(config=no_polling, until=lambda s: s.cycles >= 3)
    assert service.run() == 0
    assert not rig.runners["cli"].requests and service.launched == []
    assert rig.store.load_task(task_id).state is TaskState.NEEDS_HUMAN
    assert rig.events("worker-intake-held")


def test_github_failures_never_start_an_agent_or_stop_the_service(rig: Rig) -> None:
    number = rig.issue()
    task_id = rig.script(number)
    rig.approve(number)
    rig.poll()
    service = rig.service(config=intake_config(enabled=False), until=lambda s: s.cycles >= 2)
    service.coord.tracker.fail.append(("get_issue", GitHubUnavailableError("GET issue: HTTP 502")))
    assert service.run() == 0
    assert not rig.runners["cli"].requests
    assert rig.store.load_task(task_id).state is TaskState.READY  # not held: the text was not seen to change
    waits = [e for e in rig.events("worker-retry-scheduled") if e["task"] == task_id]
    assert waits and "could not be re-read" in waits[0]["reason"]
    other = rig.issue("Later")
    rig.approve(other)
    rig.tracker.fail.append(("list_issues", GitHubUnavailableError("GET issues: HTTP 503")))
    polling = rig.service(until=lambda s: s.cycles >= 1)
    assert polling.run() == 0  # the poll failed; the service went on
    assert any("deferred" in line for line in rig.out)


# -- scope, identity, configuration, commands ------------------------------------------------------------------

def test_only_the_approved_issue_widens_the_live_write_scope(rig: Rig) -> None:
    number = rig.issue()
    rig.approve(number)
    rig.poll()
    rig.tracker.live = True  # type: ignore[misc]  # as the real adapter: write_scope test-features-only applies
    board_of(rig.coord).live = True  # type: ignore[misc]
    assert rig.config.github.write_scope == "test-features-only"
    assert rig.coord.sync.sync(feature_id_for(number), apply=True).ok
    assert rig.coord.board is not None and rig.coord.board.sync(feature_id_for(number), apply=True).ok
    real = rig.coord.plan(demo_spec())  # neither a test feature nor an approved Issue
    with pytest.raises(SyncScopeError, match="not a test feature"):
        rig.coord.sync.sync(real.id, apply=True)
    refused = rig.coord.board.sync(real.id, apply=True)
    assert not refused.ok and "not a test feature" in refused.deferred[0]


def test_the_approved_issue_identity_is_verified(rig: Rig) -> None:
    number = rig.issue()
    rig.approve(number)
    rig.poll()
    record = load_intake(rig.store, feature_id_for(number))
    assert record is not None
    issue = rig.remote(number)
    verify_intake_issue(issue, record)
    for change in ({"id": issue.id + 1}, {"node_id": "I_other"}, {"is_pull_request": True},
                   {"labels": ("source::sentry",)}):
        with pytest.raises(OwnershipError):
            verify_intake_issue(dataclasses.replace(issue, **change), record)
    with pytest.raises(CoordinatorError, match="unsupported schema"):
        IntakeRecord.from_dict({**record.to_dict(), "schema_version": 2})
    with pytest.raises(CoordinatorError, match="unknown fields"):
        IntakeRecord.from_dict({**record.to_dict(), "command": "rm -rf"})
    assert rig.coord.sync.inspect(number)["verdict"].startswith("approved intake Issue")


@pytest.mark.parametrize("change, message", [
    ({"enabled": "yes"}, "true or false"),
    ({"poll_interval_seconds": 5}, "poll_interval_seconds"),
    ({"max_issues_per_poll": 0}, "max_issues_per_poll"),
    ({"max_spec_characters": 50}, "max_spec_characters"),
    ({"role_gates": {"wizard": ["docs-check"]}}, "unknown role"),
    ({"enabled": True, "role_gates": {"cli": ["no-such-gate"]}}, "unknown gates"),
    ({"role_gates": {"cli": []}}, "at least one gate"),
    ({"token": "x"}, "unknown github.intake keys"),
])
def test_the_intake_configuration_is_validated(change: dict, message: str) -> None:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["intake"].update(change)
    with pytest.raises(ConfigError, match=message):
        config_from_mapping(data)


def test_the_intake_is_off_by_default_and_documented() -> None:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert config_from_mapping(data).github.intake == default_config().github.intake == GitHubIntakeConfig()
    assert default_config().github.intake.enabled is False
    data["github"]["intake"]["enabled"] = True
    assert config_from_mapping(data).github.intake.enabled is True


def test_other_gate_sets_stay_valid_while_the_intake_is_off(config) -> None:
    only_docs = dataclasses.replace(config, gates={"docs-check": config.gates["docs-check"]}, lint_gates={})
    assert only_docs.github.intake.role_gates["backend"] == ("backend-tests", "backend-lint", "docs-check")
    with pytest.raises(ConfigError, match="unknown gates"):
        dataclasses.replace(only_docs, github=dataclasses.replace(only_docs.github, intake=dataclasses.replace(
            only_docs.github.intake, enabled=True)))


def test_a_poll_refuses_to_write_while_the_intake_is_disabled(repo: Path, state_dir: Path) -> None:
    off = Rig(repo, state_dir, config=dataclasses.replace(intake_config(), github=default_config().github))
    number = off.issue()
    off.approve(number)
    with pytest.raises(CoordinatorError, match="intake.enabled is false"):
        off.poll()
    preview = off.poll(apply=False)  # a dry run still shows what an operator would get
    assert [a["feature"] for a in preview.approved] == [feature_id_for(number)]
    assert not off.store.list_records("intake") and not off.store.list_features()


def test_the_intake_commands(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = ensure_state_dir(tmp_path / "cli-state")
    tracker = MockIssueTracker(state / "tracker.json")
    number = tracker.add_foreign_issue(TITLE, (), body=BODY, author="someone").number
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["intake"]["enabled"] = True
    enabled = tmp_path / "intake.yaml"
    enabled.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    base = ["--repo", str(repo), "--state-dir", str(state), "--tracker", "local"]
    assert cli_main([*base, "github", "intake", "show", str(number)]) == 0
    out = capsys.readouterr().out
    digest = spec_digest(TITLE, BODY)
    assert digest in out and f"/worker approve cli {digest}" in out
    tracker.add_comment_as(number, f"/worker approve cli {digest}", OPERATOR)
    assert cli_main([*base, "github", "intake", "poll"]) == 0  # dry run
    assert "would record feature" in capsys.readouterr().out and not (state / "intake").exists()
    assert cli_main([*base, "github", "intake", "poll", "--apply"]) == 1  # the built-in configuration: off
    assert "intake.enabled is false" in capsys.readouterr().err
    assert cli_main([*base, "--project-config", str(enabled), "github", "intake", "poll", "--apply"]) == 0
    assert f"activated {feature_id_for(number)}" in capsys.readouterr().out
    assert cli_main([*base, "github", "intake", "status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["issue"], r["status"], r["task_state"]) for r in rows] == [(number, "active", "ready")]


# -- final safety review: closed Issues, execution boundaries, the exact write scope --------------------------

class Behaving:  # pylint: disable=too-few-public-methods
    """A role's runner whose run first calls *before(request)*; a RunResult it returns replaces the run."""

    def __init__(self, runner: Any, before: Any) -> None:
        self.runner, self.before = runner, before
        self.name, self.kind = runner.name, runner.kind
        self.requests: list[Any] = []

    def run(self, request: Any) -> RunResult:
        self.requests.append(request)
        result = self.before(request)
        return result if isinstance(result, RunResult) else self.runner.run(request)


def wait_for(predicate: Any, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def approved(rig: Rig) -> tuple[int, str]:
    """An approved, activated Issue whose task (Ready) writes EDIT."""
    number = rig.issue()
    task_id = rig.script(number)
    rig.approve(number)
    rig.poll()
    assert rig.store.load_task(task_id).state is TaskState.READY
    return number, task_id


def outside(rig: Rig, number: int, **change: Any) -> None:
    """A person changes the Issue on GitHub (another tracker client over the same mock data)."""
    MockIssueTracker(rig.state / "tracker.json").tamper(number, **change)


def test_closing_an_approved_issue_holds_its_task_and_never_reopens_it(rig: Rig) -> None:
    number, task_id = approved(rig)
    rig.tracker.tamper(number, state="closed")
    report = rig.poll()
    assert [c["task"] for c in report.changed] == [task_id] and "was closed" in report.changed[0]["reason"]
    task = rig.store.load_task(task_id)
    assert task.state is TaskState.NEEDS_HUMAN and "was closed" in task.history[-1].reason
    notices = [c for c in rig.own_comments(number) if "Approval withdrawn" in c]
    assert len(notices) == 1 and "reopen the Issue" in notices[0]
    assert values(rig.coord, task_id)["Status"] == "Blocked"
    rig.coord.release_task(task_id, actor=OPERATOR)  # released while the Issue is still closed ...
    with pytest.raises(ClaimError, match="held for an operator"):
        rig.coord.run_task(task_id)                   # ... no agent starts: held again
    assert rig.store.load_task(task_id).state is TaskState.NEEDS_HUMAN and not rig.runners["cli"].requests
    assert rig.poll().ok and rig.store.load_task(task_id).state is TaskState.NEEDS_HUMAN
    assert rig.remote(number).state == "closed"      # never reopened by the coordinator
    rig.tracker.tamper(number, state="open")          # a person reopens it ...
    assert rig.poll().restored == [feature_id_for(number)]
    assert rig.store.load_task(task_id).state is TaskState.NEEDS_HUMAN  # ... the hold stays: an operator decides
    rig.coord.release_task(task_id, actor=OPERATOR)
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED


def test_closing_the_issue_stops_a_running_task_through_the_existing_cancellation(rig: Rig) -> None:
    number, task_id = approved(rig)

    def close_while_running(_request: Any) -> RunResult:
        outside(rig, number, state="closed")
        rig.clock.now += 3600  # the service's next look at the approved Issue is due
        assert wait_for(lambda: rig.store.get_flag(f"cancel-{task_id}") is not None)
        return RunResult("claude", "cancelled", error="stopped: cancel requested")  # what the supervisor does
    runner = rig.runners["cli"] = Behaving(rig.runners["cli"], close_while_running)
    service = rig.service(until=lambda s: rig.store.load_task(task_id).state is TaskState.NEEDS_HUMAN)
    assert service.run() == 0
    task = rig.store.load_task(task_id)
    assert task.state is TaskState.NEEDS_HUMAN and not task.commits and len(runner.requests) == 1
    assert service.launched == [(task_id, 1)]                       # never started again
    cancel = rig.events("cancel-requested")
    assert len(cancel) == 1 and "approval withdrawn" in cancel[0]["reason"]
    assert rig.events("run-cancelled") and rig.events("worker-intake-stopping")
    assert rig.remote(number).state == "closed"


def test_a_result_finished_after_the_approval_was_withdrawn_is_not_accepted(rig: Rig) -> None:
    number, task_id = approved(rig)

    def edit_while_running(_request: Any) -> None:
        outside(rig, number, body=BODY + "\n- [ ] and a second command\n")
        rig.clock.now += 3600
        assert wait_for(lambda: rig.store.get_flag(f"cancel-{task_id}") is not None)
        # the agent had already finished: its edit is written, committed and gated, but not accepted
    rig.runners["cli"] = Behaving(rig.runners["cli"], edit_while_running)
    service = rig.service(until=lambda s: rig.store.load_task(task_id).state is TaskState.NEEDS_HUMAN)
    assert service.run() == 0
    task = rig.store.load_task(task_id)
    assert task.state is TaskState.NEEDS_HUMAN and "result not accepted" in task.history[-1].reason
    assert "verified" not in [h.to_state for h in task.history] and task.evidence is None
    feature = rig.store.load_feature(feature_id_for(number))
    assert len(task.commits) == 1 and rig.coord.git.contains(feature.branch, task.commits[0])  # the work is kept
    assert values(rig.coord, task_id)["Status"] == "Blocked" and service.launched == [(task_id, 1)]


def test_an_edit_racing_the_acceptance_withdraws_the_verification(rig: Rig) -> None:
    number, task_id = approved(rig)
    rig.runners["cli"] = Behaving(rig.runners["cli"], lambda _r: outside(rig, number, body="Something else"))
    run = rig.coord.run_task(task_id)  # no poll, no service: only the run's own boundaries look
    task = rig.store.load_task(task_id)
    assert run.state is TaskState.NEEDS_HUMAN and task.state is TaskState.NEEDS_HUMAN
    assert [h.to_state for h in task.history][-3:] == ["verified", "ready", "needs-human"]
    assert "verification withdrawn" in task.history[-2].reason and task.evidence is None and len(task.commits) == 1
    assert values(rig.coord, task_id)["Status"] == "Blocked"  # Done was never shown


def test_an_unreadable_issue_never_starts_or_accepts_a_task(rig: Rig) -> None:
    number, task_id = approved(rig)
    rig.tracker.fail.append(("get_issue", GitHubUnavailableError("GET issue: HTTP 503")))
    with pytest.raises(ClaimError, match="could not be re-read"):
        rig.coord.run_task(task_id)
    assert rig.store.load_task(task_id).state is TaskState.READY and not rig.runners["cli"].requests
    assert rig.events("verification-deferred") and not rig.store.list_claims()

    def outage_after_the_run(_request: Any) -> None:
        rig.tracker.fail.append(("get_issue", GitHubUnavailableError("GET issue: HTTP 502")))
    rig.runners["cli"] = Behaving(rig.runners["cli"], outage_after_the_run)
    run = rig.coord.run_task(task_id)
    task = rig.store.load_task(task_id)
    assert run.state is TaskState.READY and task.state is TaskState.READY and task.evidence is None
    assert len(task.commits) == 1 and len(rig.events("verification-deferred")) == 2
    assert rig.remote(number).state == "open"


def test_integration_refuses_an_approved_issue_that_changed(rig: Rig) -> None:
    number, task_id = approved(rig)
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    feature_id = feature_id_for(number)
    rig.tracker.tamper(number, body="A different task")
    with pytest.raises(PullRequestPolicyError, match="not integrated"):
        rig.coord.integrate(feature_id, gates=())
    assert rig.store.load_feature(feature_id).state is FeatureState.ACTIVE
    rig.tracker.tamper(number, body=BODY)  # the approved text again
    assert rig.coord.integrate(feature_id, gates=()).state is FeatureState.REVIEW


def test_the_live_write_scope_is_exactly_the_approved_issue_and_its_card(rig: Rig) -> None:
    number, task_id = approved(rig)
    feature_id = feature_id_for(number)
    other = rig.issue("Unrelated idea")
    board = board_of(rig.coord)
    foreign = board.add_foreign_item(CFG.node_id, rig.remote(other))
    rig.tracker.live = True  # type: ignore[misc]
    board.live = True  # type: ignore[misc]
    issues = len(rig.tracker.list_issues())
    rig.coord.request_contract(task_id, key="api", title="API", contract="GET /x")  # a second task appears
    assert len(rig.tracker.list_issues()) == issues                                 # ... without an Issue
    other_issue = rig.remote(other)
    with rig.store.transaction():  # a forged mapping: the feature (it has no Issue) pointed at another Issue
        rig.store.save_record("github", IssueLink(id=feature_id, kind="feature", feature_id=feature_id,
                                                  key=f"{feature_id}/parent", issue_number=other,
                                                  issue_id=other_issue.id))
    before, card, protected = rig.snapshot(other), board.snapshot(CFG.node_id)["items"][foreign], \
        protected_snapshot(board)
    assert rig.coord.sync.sync(feature_id, apply=True).ok
    assert rig.coord.board is not None
    report = rig.coord.board.sync(feature_id, apply=True)
    assert feature_id in report.protected and f"{feature_id}.contract-api" in report.skipped
    with rig.store.transaction():  # the task's own mapping forged to another Issue
        link = rig.coord.sync.load_link(task_id)
        assert link is not None
        link.issue_number, link.issue_id = other, other_issue.id
        rig.store.save_record("github", link)
    assert task_id in rig.coord.sync.sync(feature_id, apply=True).protected
    assert task_id in rig.coord.board.sync(feature_id, apply=True).protected
    writes = ("create_issue", "update_issue", "add_labels", "remove_label", "create_comment")
    assert not [c for c in rig.tracker.calls if c.split(":")[0] in writes and c.endswith(f":{other}")]
    assert rig.snapshot(other) == before and board.snapshot(CFG.node_id)["items"][foreign] == card
    assert protected_snapshot(board) == protected and len(rig.tracker.list_issues()) == issues
    assert rig.config.github.write_scope == "test-features-only"


def test_only_the_task_may_show_the_approved_issue(rig: Rig) -> None:
    number, task_id = approved(rig)
    feature_id = feature_id_for(number)
    remote = rig.remote(number)
    card = values(rig.coord, task_id)
    with rig.store.transaction():  # a forged mapping of the feature record to the approved Issue itself
        rig.store.save_record("github", IssueLink(id=feature_id, kind="feature", feature_id=feature_id,
                                                  key=f"{feature_id}/parent", issue_number=number,
                                                  issue_id=remote.id))
    assert rig.coord.board is not None
    report = rig.coord.board.sync(feature_id, apply=True)
    assert "belongs to its task" in report.protected[feature_id]    # it never takes over the task's card
    assert rig.coord.board.load_link(feature_id) is None or not rig.coord.board.load_link(feature_id).item_id
    assert values(rig.coord, task_id) == card and len(rig.items(number)) == 1


def test_external_evidence_is_refused_once_the_approval_is_withdrawn(rig: Rig) -> None:
    number, task_id = approved(rig)
    rig.tracker.tamper(number, state="closed")
    rig.poll()  # records the withdrawal (and holds the task)
    with pytest.raises(EvidenceError, match="was closed"):
        rig.coord.verify_task(task_id, Evidence(head_sha="0" * 40, gates=()))
