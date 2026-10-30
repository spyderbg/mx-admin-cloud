"""GitHub Projects board synchronization (Phase 4 extension) against the local board: project
identity and protection, setup, the status mapping, Agent / Feature ID / Blocked Reason, item
ownership, idempotency, recovery, concurrency and the rule that nothing on the board is input.
The client's GraphQL handling is in test_fc_github_projects.py; the live check is
test_fc_projects_live.py."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access

from __future__ import annotations

import dataclasses
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator import CLI, CODE_ROOT
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import GitHubProjectConfig, config_from_mapping, default_config
from feature_coordinator.demo import DEMO_SCRIPTS, build_coordinator, demo_spec
from feature_coordinator.errors import (
    ClaimError,
    ConfigError,
    GitHubUncertainWriteError,
    GitHubUnavailableError,
    ProjectBoardError,
)
from feature_coordinator.github_projects import FieldOption
from feature_coordinator.issue_sync import IssueSync, SimulatedCrash
from feature_coordinator.issues import MockIssueTracker
from feature_coordinator.locks import ProcessIdentity
from feature_coordinator.model import Evidence, Feature, FeatureState, GateResult, TaskState, utc_now
from feature_coordinator.operations import DetachedPublisher
from feature_coordinator.project_sync import ProjectSync, active_filter, status_filter
from feature_coordinator.projects_local import LocalProjectBoard
from feature_coordinator.workflow import FeatureCoordinator

from fc_helpers import child_env

CFG = default_config().github.project
FID = "items-export"


def attach(coord: FeatureCoordinator, *, configured: bool = True, path: Path | None = None,
           faults: tuple[str, ...] = ()) -> LocalProjectBoard:
    """A local board (Project #2 plus the protected Project #1) wired into *coord*."""
    board = LocalProjectBoard.for_config(CFG, lambda: coord.tracker.list_issues(state="all"),
                                         repository=coord.config.github.repository, path=path, configured=configured)
    coord.board = ProjectSync(coord.store, board, coord.sync, coord.config, sleep=lambda _s: None, faults=faults,
                              revision_check=lambda feature, sha: coord.git.contains(feature.branch, sha))
    return board


@pytest.fixture
def bcoord(coord: FeatureCoordinator) -> FeatureCoordinator:
    attach(coord)
    return coord


def board_of(coord: FeatureCoordinator) -> LocalProjectBoard:
    assert coord.board is not None
    return coord.board.board  # type: ignore[return-value]


def values(coord: FeatureCoordinator, record_id: str) -> dict[str, str]:
    assert coord.board is not None
    link = coord.board.load_link(record_id)
    assert link is not None and link.item_id, record_id
    item = board_of(coord).get_item(link.item_id)
    assert item is not None
    return {k: item.values.get(k, "") for k in ("Status", "Agent", "Feature ID", "Blocked Reason")}


def status(coord: FeatureCoordinator, record_id: str) -> str:
    return values(coord, record_id)["Status"]


def item_id(coord: FeatureCoordinator, record_id: str) -> str:
    assert coord.board is not None
    link = coord.board.load_link(record_id)
    assert link is not None
    return link.item_id


def protected_snapshot(board: LocalProjectBoard) -> dict[str, Any]:
    return board.snapshot("PVT_protected1")


# -- identity, protection, configuration ---------------------------------------------------------------

def test_project_identity_is_proven_before_any_write(bcoord: FeatureCoordinator) -> None:
    board = board_of(bcoord)
    wrong = dataclasses.replace(bcoord.config, github=dataclasses.replace(
        bcoord.config.github, project=dataclasses.replace(CFG, node_id="PVT_someoneelse")))
    bcoord.board = ProjectSync(bcoord.store, board, bcoord.sync, wrong, sleep=lambda _s: None)
    bcoord.plan(demo_spec())
    report = bcoord.last_board_sync
    assert report is not None and not report.ok and "not the configured project" in report.deferred[0]
    assert board.writes == 0 and not board.list_items(CFG.node_id)
    renamed = dataclasses.replace(bcoord.config, github=dataclasses.replace(
        bcoord.config.github, project=dataclasses.replace(CFG, title="old title")))
    warnings: list[str] = []
    ProjectSync(bcoord.store, board, bcoord.sync, renamed).resolve(warnings)
    assert "identity is the node ID" in warnings[0]  # a renamed project is reported, not refused
    board.state["projects"][CFG.node_id]["closed"] = True
    with pytest.raises(ProjectBoardError, match="closed"):
        ProjectSync(bcoord.store, board, bcoord.sync, bcoord.config).resolve()


def test_protected_projects_are_never_bound_or_written(bcoord: FeatureCoordinator) -> None:
    board = board_of(bcoord)
    before = protected_snapshot(board)
    feature_id = bcoord.activate(bcoord.plan(demo_spec()).id).id
    bcoord.run_task("items-export.view")
    bcoord.hold_task("items-export.cmd", reason="later")
    assert bcoord.board is not None and bcoord.board.reconcile([feature_id], apply=True)["ok"]
    assert protected_snapshot(board) == before
    assert {r.project_number for n in (1, 2, 3, 4) for r in board.issue_items("x/y", n)} == {2}
    project1 = board.project(CFG.owner, CFG.owner_type, 1)
    assert project1 is not None
    with pytest.raises(ProjectBoardError, match="protected"):
        board.bind(project1, ["PVT_protected1"])
    with pytest.raises(ProjectBoardError, match="only the configured project"):
        board.add_item("PVT_protected1", board._issue_by_number(1).node_id)  # type: ignore[union-attr]
    p1_item = next(iter(board.state["projects"]["PVT_protected1"]["items"]))
    with pytest.raises(ProjectBoardError):
        board.set_text(CFG.node_id, p1_item, "PVTF_any", "x")
    assert protected_snapshot(board) == before


@pytest.mark.parametrize("change, message", [
    ({"node_id": "not-a-node"}, "node_id"),
    ({"number": 1}, "protected_numbers"),
    ({"owner_type": "team"}, "owner_type"),
    ({"status_names": {"backlog": "A", "ready": "A", "in_progress": "B", "blocked": "C", "review": "D",
                       "done": "E"}}, "unique"),
    ({"status_names": {"todo": "Todo"}}, "status_names"),
    ({"agent_field": "Labels"}, "built-in"),
    ({"token": "x"}, "unknown github.project keys"),
    ({"enabled": "yes"}, "true or false"),
])
def test_project_configuration_is_validated(change: dict[str, Any], message: str) -> None:
    import yaml  # pylint: disable=import-outside-toplevel
    path = Path(__file__).resolve().parents[2] / "docs_agents" / "coordination" / "project-config.example.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert config_from_mapping(data) == default_config()
    data["github"]["project"].update(change)
    with pytest.raises(ConfigError, match=message):
        config_from_mapping(data)


def test_the_generic_default_is_disabled_and_mx_admin_cloud_uses_project_2() -> None:
    assert not GitHubProjectConfig().enabled and GitHubProjectConfig().number == 0
    assert CFG.enabled and CFG.number == 2 and CFG.node_id == "PVT_kwHOACEIPc4BndH-" and CFG.protected_numbers == (1,)
    assert status_filter(["Ready", "In Progress", "Blocked", "Review"]) == 'status:Ready,"In Progress",Blocked,Review'
    assert active_filter(CFG) == 'is:open status:Ready,"In Progress",Blocked,Review'


# -- setup: field and option discovery -----------------------------------------------------------------

def test_setup_discovers_and_reuses_fields_options_and_views(coord: FeatureCoordinator) -> None:
    board = attach(coord, configured=False)
    project = board.state["projects"][CFG.node_id]
    status_field = next(f for f in project["fields"] if f["name"] == "Status")
    todo = status_field["options"][0]["id"]
    status_field["options"].append({"id": "1cebox00", "name": "Icebox", "color": "GRAY", "description": ""})
    foreign = coord.tracker.add_foreign_issue("someone's issue", ["enhancement"])  # type: ignore[attr-defined]
    held = board.add_foreign_item(CFG.node_id, coord.tracker.get_issue(foreign.number))
    other = board.add_foreign_item(CFG.node_id)
    board.state["projects"][CFG.node_id]["items"][held]["values"][status_field["id"]] = todo
    board.state["projects"][CFG.node_id]["items"][other]["values"][status_field["id"]] = "1cebox00"
    assert coord.board is not None
    dry = coord.board.setup(apply=False)
    assert dry.ok and board.writes == 2  # the two human-added items only: a dry run writes nothing
    assert any("rename Status option 'Todo' → 'Backlog' (option ID" in c and "1 item(s)" in c for c in dry.changes)
    report = coord.board.setup(apply=True)
    assert report.ok, report.problems
    after = board.project(CFG.owner, CFG.owner_type, CFG.number)
    assert after is not None
    names = [o.name for o in after.field("Status").options]  # type: ignore[union-attr]
    assert names == ["Backlog", "Ready", "In Progress", "Blocked", "Review", "Done", "Icebox"]
    assert after.field("Status").option("Backlog").id == todo  # type: ignore[union-attr]
    # Item assignments survive: the Todo item now shows Backlog, the Icebox item keeps Icebox.
    held_item, other_item = board.get_item(held), board.get_item(other)
    assert held_item is not None and held_item.values["Status"] == "Backlog"
    assert other_item is not None and other_item.values["Status"] == "Icebox"
    assert {f.name for f in after.fields} >= {"Agent", "Feature ID", "Blocked Reason"}
    kanban, active = after.view("Kanban"), after.view("Active Tasks")
    assert kanban is not None and kanban.number == 2 and active is not None
    assert active.filter == active_filter(CFG)
    assert coord.board.setup(apply=True).changes == []  # idempotent


def test_setup_adds_missing_options_and_never_changes_a_conflicting_field(coord: FeatureCoordinator) -> None:
    board = attach(coord, configured=False)
    board.bind(board.project(CFG.owner, CFG.owner_type, CFG.number), ["PVT_protected1"])  # type: ignore[arg-type]
    agent = board.create_field(CFG.node_id, "Agent", "SINGLE_SELECT", [FieldOption("", "Backend", "BLUE", "mine")])
    board.create_field(CFG.node_id, "Feature ID", "SINGLE_SELECT", [FieldOption("", "x", "GRAY", "")])
    assert coord.board is not None
    report = coord.board.setup(apply=True)
    assert not report.ok and any("'Feature ID' exists as SINGLE_SELECT" in p for p in report.problems)
    after = board.project(CFG.owner, CFG.owner_type, CFG.number)
    kept = after.field("Agent").option("Backend")  # type: ignore[union-attr]
    assert kept is not None and kept.id == agent.options[0].id and kept.description == "mine"
    agent_field, conflicting = after.field("Agent"), after.field("Feature ID")
    assert agent_field is not None and len(agent_field.options) == 6
    assert conflicting is not None and conflicting.data_type == "SINGLE_SELECT"
    coord.plan(demo_spec())
    assert coord.last_board_sync is not None and "not set up" in coord.last_board_sync.deferred[0]


def test_setup_limits_the_active_tasks_view_to_open_issues(bcoord: FeatureCoordinator) -> None:
    # A board set up before D-29 filtered on Status only, so closed test Issues kept for audit
    # (e.g. a parent left in Review) stayed on Active Tasks. Setup adds `is:open` and changes nothing else.
    board = board_of(bcoord)
    project = board.state["projects"][CFG.node_id]
    view = next(v for v in project["views"] if v["name"] == CFG.active_view)
    view["filter"] = 'status:Ready,"In Progress",Blocked,Review'
    board._save()  # pylint: disable=protected-access
    assert bcoord.board is not None
    dry = bcoord.board.setup(apply=False)
    assert dry.ok and dry.changes == [f"view 'Active Tasks': filter {active_filter(CFG)!r}"]
    assert board.writes == 0 and view["filter"] == 'status:Ready,"In Progress",Blocked,Review'
    report = bcoord.board.setup(apply=True)
    assert report.ok and report.changes == dry.changes and board.writes == 1
    after = board.project(CFG.owner, CFG.owner_type, CFG.number)
    assert after is not None and after.view(CFG.active_view).filter == active_filter(CFG)  # type: ignore[union-attr]
    assert after.view(CFG.board_view).filter == ""  # type: ignore[union-attr]
    assert bcoord.board.setup(apply=True).changes == []  # idempotent


# -- item creation, authorization, duplicates --------------------------------------------------------------

def test_plan_adds_new_feature_issues_automatically_with_derived_values(bcoord: FeatureCoordinator) -> None:
    feature = bcoord.plan(demo_spec())
    report = bcoord.last_board_sync
    assert report is not None and report.ok and len(report.added) == 4
    assert values(bcoord, feature.id) == {"Status": "Backlog", "Agent": "Coordination", "Feature ID": FID,
                                          "Blocked Reason": ""}
    assert values(bcoord, "items-export.view")["Agent"] == "Frontend"
    assert values(bcoord, "items-export.cmd")["Agent"] == "CLI"
    assert values(bcoord, "items-export.e2e")["Agent"] == "Automation"
    writes = board_of(bcoord).writes
    assert bcoord.board is not None and not bcoord.board.sync(FID).writes and board_of(bcoord).writes == writes


def test_existing_features_are_associated_only_when_authorized(coord: FeatureCoordinator) -> None:
    feature = coord.plan(demo_spec())  # planned before the board existed: its Issues are not on a board
    board = attach(coord)
    assert coord.board is not None
    report = coord.board.sync(feature.id)
    assert feature.id in report.skipped and board.writes == 0 and not board.list_items(CFG.node_id)
    coord.board.authorize(feature.id, actor="operator")
    report = coord.board.sync(feature.id)
    assert report.ok and len(report.added) == 4 and len(board.list_items(CFG.node_id)) == 4
    assert any(e["kind"] == "github-project-authorized" and e["actor"] == "operator" for e in coord.store.events())


def test_a_crash_after_adding_an_item_never_duplicates_it(coord: FeatureCoordinator) -> None:
    board = attach(coord, faults=("crash-after-item-add:items-export.cmd",))
    coord.auto_sync = False
    coord.plan(demo_spec())
    assert coord.board is not None
    coord.board.authorize(FID, actor="test")
    coord.auto_sync = True
    with pytest.raises(SimulatedCrash):
        coord.sync_issues(FID)
    link = coord.board.load_link("items-export.cmd")
    assert link is not None and link.pending_add and not link.item_id
    restarted = ProjectSync(coord.store, board, coord.sync, coord.config, sleep=lambda _s: None)
    report = restarted.sync(FID)
    assert report.ok and report.adopted == {"items-export.cmd": item_id(coord, "items-export.cmd")}
    number = coord.store.load_task("items-export.cmd").issue_number
    assert number is not None and len(board.issue_items("x/y", number)) == 1


def test_a_lost_add_response_is_recovered_without_a_duplicate(bcoord: FeatureCoordinator) -> None:
    board = board_of(bcoord)
    board.fail.append(("add_item!", GitHubUncertainWriteError("timeout after sending")))
    bcoord.plan(demo_spec())
    assert bcoord.last_board_sync is not None and bcoord.last_board_sync.deferred
    assert bcoord.board is not None
    report = bcoord.board.sync(FID)
    assert report.ok and len(report.adopted) == 1 and len(report.added) == 3
    assert len(board.list_items(CFG.node_id)) == 4


def test_concurrent_board_syncs_never_create_duplicate_items(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)
    coord.tracker = MockIssueTracker(state_dir / "tracker.json")
    coord.sync = IssueSync(coord.store, coord.tracker, coord.config)
    coord.auto_sync = False
    coord.plan(demo_spec())
    coord.sync.sync(FID)
    attach(coord, path=state_dir / "project-board.json")
    assert coord.board is not None
    coord.board.authorize(FID, actor="test")
    code = textwrap.dedent("""
        import sys
        from pathlib import Path
        from feature_coordinator.config import default_config
        from feature_coordinator.issue_sync import IssueSync
        from feature_coordinator.issues import MockIssueTracker
        from feature_coordinator.operations import make_board
        from feature_coordinator.project_sync import ProjectSync
        from feature_coordinator.store import StateStore
        state, config = Path(sys.argv[1]), default_config()
        tracker = MockIssueTracker(state / "tracker.json")
        store = StateStore(state)
        report = ProjectSync(store, make_board("local", config, state, tracker), IssueSync(store, tracker, config),
                             config, sleep=lambda _s: None).sync("items-export")
        assert report.ok, report
    """)
    procs = [subprocess.Popen([sys.executable, "-c", code, str(state_dir)], stdout=subprocess.PIPE,  # pylint: disable=consider-using-with
                              stderr=subprocess.PIPE, text=True, env=child_env()) for _ in range(3)]
    for proc in procs:
        _out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err
    board = LocalProjectBoard(coord.tracker.list_issues, repository="x/y", path=state_dir / "project-board.json")
    contents = [i.issue_number for i in board.list_items(CFG.node_id)]
    assert len(contents) == 4 and len(set(contents)) == 4


# -- status mapping ---------------------------------------------------------------------------------------

def _evidence(coord: FeatureCoordinator, task_id: str, *, on_branch: bool = True) -> Evidence:
    feature = coord.store.load_feature(FID)
    head = coord.git.rev_parse(f"refs/heads/{feature.branch}") if on_branch else "1" * 40
    task = coord.store.load_task(task_id)
    return Evidence(head_sha=head, gates=tuple(GateResult(g, 0) for g in task.required_gates))


def test_task_status_mapping_follows_the_state_machine(bcoord: FeatureCoordinator) -> None:
    feature = bcoord.activate(bcoord.plan(demo_spec()).id)
    sync = bcoord.board
    assert sync is not None
    task = bcoord.store.load_task("items-export.view")
    cases = {TaskState.PROPOSED: ("backlog", ""), TaskState.READY: ("ready", ""),
             TaskState.IN_PROGRESS: ("in_progress", "")}
    for state, expected in cases.items():
        assert sync.board_status(feature, dataclasses.replace(task, state=state)) == expected
    blocked = sync.board_status(feature, bcoord.store.load_task("items-export.e2e"))
    assert blocked == ("blocked", "waiting for items-export.view (ready), items-export.cmd (ready)")
    failed = dataclasses.replace(task, state=TaskState.FAILED, history=[*task.history])
    failed.history.append(dataclasses.replace(task.history[-1], reason="gates failed: ['frontend-tests']"))
    assert sync.board_status(feature, failed) == ("blocked", "failed: gates failed: ['frontend-tests']")
    # Implementation complete, gates running: the live claim's checkpoint says "verifying".
    with bcoord.store.transaction():
        bcoord.store.save_claim(task.id, {"task_id": task.id, "identity": ProcessIdentity.current().to_dict(),
                                          "checkpoint": {"phase": "verifying"}, "heartbeat_at": utc_now()})
    assert sync.board_status(feature, dataclasses.replace(task, state=TaskState.IN_PROGRESS))[0] == "review"
    with bcoord.store.transaction():
        bcoord.store.delete_claim(task.id)
    verified = dataclasses.replace(task, state=TaskState.VERIFIED, evidence=_evidence(bcoord, task.id))
    assert sync.board_status(feature, verified) == ("done", "")
    notes: list[str] = []
    off_branch = dataclasses.replace(verified, evidence=_evidence(bcoord, task.id, on_branch=False))
    assert sync.board_status(feature, off_branch, notes)[0] == "review" and "is not on feature/" in notes[0]
    assert verified.evidence is not None
    no_gates = dataclasses.replace(verified, evidence=Evidence(head_sha=verified.evidence.head_sha, gates=()))
    assert sync.board_status(feature, no_gates)[0] == "review"


@pytest.mark.parametrize("state, merged, expected", [
    (FeatureState.PLANNED, None, "backlog"), (FeatureState.ACTIVE, None, "in_progress"),
    (FeatureState.INTEGRATION, None, "in_progress"), (FeatureState.REVIEW, None, "review"),
    (FeatureState.DONE, None, "review"), (FeatureState.DONE, "a" * 40, "done"), (FeatureState.BLOCKED, None, "blocked"),
    (FeatureState.FAILED, None, "blocked")])
def test_feature_status_is_done_only_with_merge_evidence(bcoord: FeatureCoordinator, state: FeatureState,
                                                        merged: str | None, expected: str) -> None:
    feature = bcoord.plan(demo_spec())
    assert bcoord.board is not None
    record = Feature.from_dict({**feature.to_dict(), "state": state.value, "merged_sha": merged})
    assert bcoord.board.board_status(record, None)[0] == expected


def test_the_parent_is_never_done_because_its_tasks_are_verified(bcoord: FeatureCoordinator) -> None:
    feature_id = bcoord.activate(bcoord.plan(demo_spec()).id).id
    for task_id in ("items-export.view", "items-export.cmd", "items-export.e2e"):
        assert bcoord.run_task(task_id).state is TaskState.VERIFIED
        assert status(bcoord, task_id) == "Done"
    assert status(bcoord, feature_id) == "In Progress"
    bcoord.integrate(feature_id)
    assert status(bcoord, feature_id) == "Review"
    bcoord.open_pull_request(feature_id, approved=True)  # the mock tracker's PR (no real PR anywhere)
    assert status(bcoord, feature_id) == "Review"
    bcoord.complete(feature_id, merged_sha="b" * 40)
    assert status(bcoord, feature_id) == "Done"


# -- Agent, Feature ID, Blocked Reason; holds; dependency invalidation -----------------------------------------

def test_blocked_reason_follows_holds_and_releases(bcoord: FeatureCoordinator) -> None:
    bcoord.activate(bcoord.plan(demo_spec()).id)
    bcoord.hold_task("items-export.cmd", reason="needs a product decision", actor="operator")
    assert values(bcoord, "items-export.cmd") == {
        "Status": "Blocked", "Agent": "CLI", "Feature ID": FID,
        "Blocked Reason": "held for a human decision by operator: needs a product decision"}
    bcoord.release_task("items-export.cmd", actor="operator")
    assert values(bcoord, "items-export.cmd")["Status"] == "Ready"
    assert values(bcoord, "items-export.cmd")["Blocked Reason"] == ""


def test_a_card_moved_on_the_board_never_releases_claims_or_completes(bcoord: FeatureCoordinator) -> None:
    feature_id = bcoord.activate(bcoord.plan(demo_spec()).id).id
    bcoord.hold_task("items-export.cmd", reason="wait", actor="operator")
    board = board_of(bcoord)
    board.set_by_human(item_id(bcoord, "items-export.cmd"), "Status", "Ready")
    board.set_by_human(item_id(bcoord, "items-export.e2e"), "Status", "In Progress")
    board.set_by_human(item_id(bcoord, "items-export.view"), "Status", "Done")
    board.set_by_human(item_id(bcoord, feature_id), "Status", "Done")
    board.set_by_human(item_id(bcoord, "items-export.view"), "Agent", "Backend")
    for task_id in ("items-export.cmd", "items-export.e2e"):
        with pytest.raises(ClaimError):
            bcoord.run_task(task_id)
    assert bcoord.store.load_task("items-export.view").state is TaskState.READY
    assert bcoord.store.load_feature(feature_id).state is FeatureState.ACTIVE
    assert bcoord.board is not None
    report = bcoord.board.sync(feature_id)
    assert len(report.drift) == 4 and report.ok
    assert [status(bcoord, r) for r in (feature_id, "items-export.view", "items-export.cmd", "items-export.e2e")] == \
        ["In Progress", "Ready", "Blocked", "Blocked"]
    assert values(bcoord, "items-export.view")["Agent"] == "Frontend"
    assert not bcoord.board.sync(feature_id).drift


def test_dependency_invalidation_reblocks_dependents_on_the_board(bcoord: FeatureCoordinator) -> None:
    bcoord.activate(bcoord.plan(demo_spec()).id)
    bcoord.run_task("items-export.view")
    bcoord.run_task("items-export.cmd")
    assert status(bcoord, "items-export.e2e") == "Ready"
    assert bcoord.reopen_task("items-export.view", "the export format changed") == ["items-export.e2e"]
    assert status(bcoord, "items-export.view") == "Ready"
    assert values(bcoord, "items-export.e2e")["Status"] == "Blocked"
    assert values(bcoord, "items-export.e2e")["Blocked Reason"] == "waiting for items-export.view (ready)"
    bcoord.run_task("items-export.view")
    assert status(bcoord, "items-export.view") == "Done" and status(bcoord, "items-export.e2e") == "Ready"


# -- ownership of items, failures, recovery ---------------------------------------------------------------

def test_items_that_are_not_provably_ours_are_never_modified(bcoord: FeatureCoordinator) -> None:
    feature_id = bcoord.plan(demo_spec()).id
    board = board_of(bcoord)
    foreign = bcoord.tracker.add_foreign_issue("bug", ["mx-auto", "source::pytest"])  # type: ignore[attr-defined]
    foreign_item = board.add_foreign_item(CFG.node_id, bcoord.tracker.get_issue(foreign.number))
    draft = board.add_foreign_item(CFG.node_id)
    board.set_by_human(foreign_item, "Status", "Done")
    before = {i: board.get_item(i) for i in (foreign_item, draft)}
    assert bcoord.board is not None
    result = bcoord.board.reconcile([feature_id], apply=True)
    assert {f["item"] for f in result["foreign"]} == {foreign_item, draft} and result["mapped"] == 4
    assert {i: board.get_item(i) for i in (foreign_item, draft)} == before
    # A recorded item that now shows another Issue is refused, not "fixed".
    bcoord.board._update_link(bcoord.store.load_feature(feature_id), bcoord.store.load_task("items-export.cmd"),
                              lambda link: setattr(link, "item_id", foreign_item))
    # An Issue that lost its ownership marker is refused too.
    view_issue = bcoord.store.load_task("items-export.view").issue_number
    bcoord.tracker.tamper(view_issue, body="no marker any more")  # type: ignore[attr-defined]
    report = bcoord.board.sync(feature_id)
    assert set(report.protected) == {"items-export.cmd", "items-export.view"} and not report.ok
    assert "does not show Issue" in report.protected["items-export.cmd"]
    assert {i: board.get_item(i) for i in (foreign_item, draft)} == before


def test_network_failures_never_corrupt_local_state_and_recover(bcoord: FeatureCoordinator) -> None:
    feature_id = bcoord.activate(bcoord.plan(demo_spec()).id).id
    board = board_of(bcoord)
    tasks_before = {t: bcoord.store.load_task(t).to_dict() for t in bcoord.store.load_feature(feature_id).task_ids}
    board.fail.append(("set_option", GitHubUnavailableError("502 after retries")))
    bcoord.hold_task("items-export.cmd", reason="x", actor="operator")
    assert bcoord.last_board_sync is not None and bcoord.last_board_sync.deferred
    assert any(e["kind"] == "tracker-sync-deferred" and e.get("board") for e in bcoord.store.events())
    tasks_after = {t: bcoord.store.load_task(t).to_dict() for t in tasks_before}
    assert {t for t in tasks_before if tasks_before[t] != tasks_after[t]} == {"items-export.cmd"}  # only the hold
    assert bcoord.board is not None
    report = bcoord.board.sync(feature_id)
    assert report.ok and status(bcoord, "items-export.cmd") == "Blocked"


def test_a_restarted_coordinator_reconciles_without_writes(bcoord: FeatureCoordinator, state_dir: Path) -> None:
    feature_id = bcoord.activate(bcoord.plan(demo_spec()).id).id
    board = board_of(bcoord)
    assert bcoord.board is not None
    ids = {link.id: link.item_id for link in bcoord.board.links(feature_id)}
    restarted = ProjectSync(bcoord.store, board, IssueSync(bcoord.store, bcoord.tracker, bcoord.config),
                            bcoord.config, sleep=lambda _s: None)
    writes = board.writes
    result = restarted.reconcile([feature_id], apply=True)
    assert result["ok"] and result["mapped"] == 4 and not result["orphans"] and not result["foreign"]
    assert board.writes == writes and {l.id: l.item_id for l in restarted.links(feature_id)} == ids
    # A lost local record turns its item into an orphan: reported, never modified.
    (state_dir / "project" / "items-export.cmd.json").unlink()
    orphan = restarted.reconcile([], apply=True)
    assert [o["item"] for o in orphan["orphans"]] == [ids["items-export.cmd"]] and board.writes == writes


def test_a_project_workflow_overwrite_is_detected_and_reapplied(bcoord: FeatureCoordinator) -> None:
    board = board_of(bcoord)
    reverted: list[str] = []

    def workflow(b: LocalProjectBoard, op: str, args: dict[str, Any]) -> None:  # GitHub's "Item added" workflow
        if op == "set_option" and args["item_id"] not in reverted and len(reverted) < 1:
            reverted.append(args["item_id"])
            b.set_by_human(args["item_id"], "Status", "Backlog")
    board.hooks.append(workflow)
    bcoord.activate(bcoord.plan(demo_spec()).id)
    assert all(status(bcoord, t) == "Ready" for t in ("items-export.view", "items-export.cmd"))
    assert bcoord.board is not None
    board.hooks = [lambda b, op, args: b.set_by_human(args["item_id"], "Status", "Backlog")
                   if op == "set_option" and args["field_id"] == _status_field_id(b) else None]
    bcoord.hold_task("items-export.cmd", reason="x", actor="operator")
    report = bcoord.last_board_sync
    assert report is not None and any("kept changing" in d for d in report.deferred)


def _status_field_id(board: LocalProjectBoard) -> str:
    return next(f["id"] for f in board.state["projects"][CFG.node_id]["fields"] if f["name"] == "Status")


def test_no_coordinator_lock_is_held_during_board_requests(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)
    board = attach(coord)
    original = board._op

    def probing(name: str, *, write: bool):  # type: ignore[no-untyped-def]
        locks = coord.store.locks
        for lock in (locks.coordinator(), locks.project(shared=False), *(locks.role(r) for r in coord.config.roles)):
            lock.acquire()  # raises LockBusyError if anyone holds it
            lock.release()
        return original(name, write=write)
    board._op = probing  # type: ignore[method-assign]
    feature_id = coord.activate(coord.plan(demo_spec()).id).id
    coord.run_task("items-export.view")
    coord.hold_task("items-export.cmd", reason="x")
    assert coord.board is not None and coord.board.sync(feature_id).ok and len(board.calls) > 30


def test_write_scope_limits_live_boards_to_test_features(bcoord: FeatureCoordinator) -> None:
    board = board_of(bcoord)
    board.live = True
    bcoord.plan(demo_spec())
    report = bcoord.last_board_sync
    assert report is not None and "not a test feature" in report.deferred[0] and board.writes == 0


# -- the separate-process publisher ------------------------------------------------------------------------

def test_the_publisher_starts_an_independent_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class Fake:  # pylint: disable=too-few-public-methods
        """Records the Popen arguments."""

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            seen.update(argv=argv, **kwargs)

        def poll(self) -> int:
            return 0
    monkeypatch.setattr(subprocess, "Popen", Fake)
    publisher = DetachedPublisher.for_cli(tmp_path / "repo", tmp_path / "state", "github", "cfg.yaml")
    publisher("f1")
    assert seen["argv"][-4:] == ["github", "sync", "f1", "--apply"]
    assert seen["close_fds"] is True and seen["start_new_session"] is True and seen["stdin"] == subprocess.DEVNULL
    assert seen["argv"][:3] == [*CLI] and seen["cwd"] == CODE_ROOT  # importable from any working directory
    assert ["--tracker", "github"] == seen["argv"][7:9] and "--project-config" in seen["argv"]


def test_run_task_publishes_from_another_process_without_waiting(bcoord: FeatureCoordinator) -> None:
    published: list[str] = []
    bcoord.publisher = published.append
    bcoord.activate(bcoord.plan(demo_spec()).id)
    run = bcoord.run_task("items-export.view")
    assert run.state is TaskState.VERIFIED and published == [FID, FID]  # started, verifying


def test_projects_rehearsal_with_the_local_tracker(tmp_path: Path, needs_bwrap: None) -> None:
    from feature_coordinator.live_projects import run_projects_test  # pylint: disable=import-outside-toplevel
    del needs_bwrap
    report = run_projects_test(default_config(), tmp_path / "work", tracker_name="local", feature_id="fc-p4x-rehearsal",
                               log=lambda _l: None)
    failed = [c for c in report["checks"] if not c["passed"]]
    assert report["passed"] and not failed, failed
    assert len(report["checks"]) >= 30 and report["cleanup"]["ok"] and report["cleanup"]["repeat_writes"] == []
    assert report["observations"]["running"]["status"] == "In Progress"
    assert report["observations"]["verifying"]["status"] == "Review"


def test_cli_project_commands_with_the_local_board(tmp_path: Path, repo: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    state = tmp_path / "cli-state"
    base = ["--repo", str(repo), "--state-dir", str(state), "--tracker", "local"]
    spec = tmp_path / "spec.json"
    spec.write_text('{"slug": "c1", "title": "C", "test": true, "tasks": [{"key": "a", "role": "backend", '
                    '"title": "A"}, {"key": "b", "role": "cli", "title": "B", "depends_on": ["a"]}]}',
                    encoding="utf-8")
    assert cli_main([*base, "github", "project", "validate"]) == 0
    assert cli_main([*base, "github", "project", "inspect"]) == 0
    assert cli_main([*base, "plan", str(spec), "--apply"]) == 0
    assert "project board: 3 item(s) added; ok" in capsys.readouterr().out
    assert cli_main([*base, "github", "project", "sync", "c1", "--apply"]) == 0
    assert cli_main([*base, "hold", "c1.a", "--reason", "wait", "--apply"]) == 0
    assert cli_main([*base, "github", "project", "active"]) == 0
    out = capsys.readouterr().out
    assert "c1.a" in out and "board=Blocked" in out and "reason=held for a human decision" in out
    assert cli_main([*base, "github", "project", "task", "c1.b"]) == 0
    assert cli_main([*base, "github", "project", "reconcile", "--apply"]) == 0
    assert cli_main([*base, "github", "project", "setup"]) == 0  # nothing to change
    assert "none (already configured)" in capsys.readouterr().out
    assert cli_main([*base, "github", "sync", "c1", "--apply"]) == 0  # Issues and board
    assert cli_main([*base, "github", "project", "associate", "c1"]) == 0  # dry run
    assert cli_main([*base, "github", "project", "test"]) == 0  # describes the live test only
