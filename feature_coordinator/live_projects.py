"""Phase 4 extension acceptance test: the coordinator on a real GitHub Projects (v2) board.

`run_projects_test()` drives the real FeatureCoordinator, IssueSync, ProjectSync, Git
coordinator and the bubblewrap-sandboxed gates through the board lifecycle, in a DISPOSABLE
Git repository and state directory under *workdir*. Only the agent is scripted. It creates
exactly TWO Issues — the parent of one test feature and its single Backend task — both
labeled `feature:test`, and reads every board value back through the Projects API.

Steps (the report names each check):
  B0  preflight: authentication, the `project` scope, the project's identity, the protected
      projects, the board's fields/options/views; snapshots of the protected projects and of
      every Issue and PR
  B1  the feature is planned by a coordinator WITHOUT the board and interrupted right after
      its parent Issue was created and recorded (an existing Issue, not on any board)
  B2  a board-enabled sync does not touch the unauthorized feature
  B3  the operator associates the feature (`github project associate --apply`, a separate
      process): the existing parent Issue is added; Status/Agent/Feature ID set
  B4  `activate` creates the task Issue and adds it automatically; a crash is injected right
      after GitHub added its item, before the item ID was recorded
  B5  a restarted coordinator finds the item (no duplicate) and sets its values
  B6  cards moved by a person (task → In Progress, parent → Done) start or complete nothing;
      the next sync reports the drift and restores the derived values
  B7  a human hold shows Blocked with its reason; moving the card to Ready cannot authorize a
      claim; B8 the release shows Ready again
  B9  the agent run: In Progress while it runs and Review while its commit is verified (both
      published by a separate process, since the worker holds its role slot), Done afterwards
      with evidence on the feature branch
  B10 verification invalidated (re-opened) → Ready; re-verified → Done
  B11 repeated synchronization writes nothing; one item per Issue
  B12 integration: the parent shows Review, never Done without a merged PR; no PR exists
  B13 restart: `github project reconcile --apply` in a new process changes nothing
  B14 the protected projects, every unrelated Issue and PR, and the labels are unchanged
Cleanup (only if every check passed): a final comment and close for both Issues (parent
last); their board values are restored afterwards (GitHub's "Item closed" workflow may set
Done), and the items stay on the board as audit records.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import CLI, CODE_ROOT
from .config import ProjectConfig
from .demo import DEMO_AUTHOR, init_demo_repo
from .errors import ClaimError, CoordinatorError, IllegalTransitionError, PullRequestPolicyError
from .gates import SandboxedGateRunner
from .github_api import GitHubClient, sanitize
from .github_projects import BoardClient
from .issue_sync import IssueSync, SimulatedCrash
from .issues import MockIssueTracker, TEST_LABEL, TrackerClient
from .live_test import INTEGRATION_GATES, REPO_ROOT, SEED_FILES, Checks, backend_edits, snapshot, test_config
from .model import FeatureState, TaskState
from .operations import DetachedPublisher, make_board
from .project_sync import ProjectSync
from .runners import MockClaudeCodeRunner, RunRequest, RunResult
from .statedir import ensure_state_dir
from .store import StateStore
from .workflow import FeatureCoordinator, FeatureSpec, TaskSpec

WORKFLOW_SETTLE_SECONDS = 25.0   # how long to watch for GitHub's own project workflows after a change


def new_feature_id() -> str:
    """A unique test feature ID."""
    return "fc-p4x-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def board_spec(feature_id: str) -> FeatureSpec:
    """The test feature: one Backend task (two Issues in total)."""
    return FeatureSpec(
        slug=feature_id, test=True, title="Board sync (Phase 4 Projects extension test)",
        acceptance=("Every coordinator state change is shown on the project board",
                    "Nothing on the board changes the local coordinator state"),
        tasks=(TaskSpec("contract", "backend", "Board sync contract", provides_contract=True,
                        required_gates=("contract-check", "backend-tests"),
                        acceptance=("GET /api/v1/items/export with a 200 response and security",)),))


def project_snapshot(board: BoardClient, config: ProjectConfig, number: int) -> dict[str, Any]:
    """A protected project's configuration and items (to prove it was not changed)."""
    cfg = config.github.project
    project = board.project(cfg.owner, cfg.owner_type, number)
    if project is None:
        return {"missing": True}
    return {"id": project.id, "title": project.title, "updated_at": project.updated_at, "closed": project.closed,
            "fields": [(f.id, f.name, f.data_type, [(o.id, o.name, o.color, o.description) for o in f.options])
                       for f in project.fields],
            "views": [(v.id, v.name, v.layout, v.filter, list(v.group_by), list(v.visible)) for v in project.views],
            "workflows": [(w.name, w.enabled) for w in project.workflows],
            "items": sorted((i.id, i.issue_number, i.archived, sorted(i.values.items()))
                            for i in board.list_items(project.id))}


class ObservingRunner:  # pylint: disable=too-few-public-methods
    """The scripted agent, which first lets the test observe the board while it 'works'."""

    def __init__(self, inner: MockClaudeCodeRunner, before: Callable[[RunRequest], None]) -> None:
        self.inner, self.before = inner, before
        self.name, self.kind = inner.name, inner.kind

    def run(self, request: RunRequest) -> RunResult:
        """Observe, then apply the scripted edits."""
        self.before(request)
        return self.inner.run(request)


class ObservingGates:  # pylint: disable=too-few-public-methods
    """The real sandboxed gate runner; the first gate of a run lets the test observe the board."""

    def __init__(self, inner: SandboxedGateRunner, before: Callable[[], None]) -> None:
        self.inner, self.before = inner, before
        self.armed = False

    def __call__(self, name: str, context: Any) -> Any:
        if self.armed:
            self.armed = False
            self.before()
        return self.inner(name, context)


class ProjectsLifecycle:  # pylint: disable=too-many-instance-attributes
    """One run of the scenario. See the module docstring."""

    def __init__(self, config: ProjectConfig, client: TrackerClient, workdir: Path, *, tracker_name: str,
                 feature_id: str | None = None, log: Callable[[str], None] = print) -> None:
        self.config = test_config(config)
        self.cfg = self.config.github.project
        self.client = client
        self.fid = feature_id or new_feature_id()
        self.task_id = f"{self.fid}.contract"
        self.workdir = workdir / self.fid
        self.repo, self.state = self.workdir / "repo", self.workdir / "state"
        ensure_state_dir(self.state)
        self.tracker_name = tracker_name
        self.board: Any = make_board(tracker_name, self.config, self.state, client)
        self.check = Checks(log)
        self.log = log
        self.report: dict[str, Any] = {"feature": self.fid, "tracker": tracker_name, "workdir": str(self.workdir),
                                       "observations": {}, "drift": []}
        self.coord: FeatureCoordinator | None = None
        self.publisher = DetachedPublisher.for_cli(self.repo, self.state, tracker_name)
        self.gates: ObservingGates | None = None
        self.before: dict[str, Any] = {}

    # -- plumbing ----------------------------------------------------------------------

    def _coordinator(self, *, board: bool = True, issue_faults: tuple[str, ...] = (),
                     board_faults: tuple[str, ...] = ()) -> FeatureCoordinator:
        store = StateStore(self.state)
        claude = self.config.providers["claude"]
        scripted = MockClaudeCodeRunner(claude, {self.task_id: backend_edits(1)})  # type: ignore[arg-type]
        runner = ObservingRunner(scripted, self._while_running)
        self.gates = ObservingGates(SandboxedGateRunner(self.config, self.state / "logs"), self._while_verifying)
        sync = IssueSync(store, self.client, self.config, faults=issue_faults)
        project = ProjectSync(store, self.board, sync, self.config, faults=board_faults) if board else None
        return FeatureCoordinator(self.config, self.repo, store, tracker=self.client, runners={"backend": runner},
                                  gate_runner=self.gates, author=DEMO_AUTHOR, sync=sync, board=project,
                                  publisher=self.publisher if board else None)

    @property
    def project_sync(self) -> ProjectSync:
        """The coordinator's board synchronizer."""
        assert self.coord is not None and self.coord.board is not None
        return self.coord.board

    def number(self, record_id: str) -> int:
        """The Issue number of a local record."""
        assert self.coord is not None
        record = self.coord.store.load_feature(record_id) if record_id == self.fid else self.coord.store.load_task(
            record_id)
        assert record.issue_number is not None, f"{record_id} has no Issue"
        return record.issue_number

    def item(self, record_id: str) -> str:
        """The recorded board item of a local record."""
        link = self.project_sync.load_link(record_id)
        assert link is not None and link.item_id, f"{record_id} has no board item"
        return link.item_id

    def values(self, record_id: str) -> dict[str, str]:
        """The record's board values, read through the Projects API."""
        item = self.board.get_item(self.item(record_id))
        cfg = self.cfg
        return {} if item is None else {k: item.values.get(k, "") for k in (cfg.status_field, cfg.agent_field,
                                                                              cfg.feature_id_field,
                                                                              cfg.blocked_reason_field)}

    def status(self, record_id: str) -> str:
        """The record's board Status."""
        return self.values(record_id).get(self.cfg.status_field, "")

    def items_on_board(self, number: int) -> list[str]:
        """Item IDs of Issue #number on the configured project."""
        return [r.id for r in self.board.issue_items(self.config.github.repository, number)
                if r.project_number == self.cfg.number]

    def human_set_status(self, record_id: str, name: str) -> None:
        """A person moves a card (only after the record's ownership was verified)."""
        verdict = self.project_sync.item_status(record_id)
        if not verdict.get("item"):
            raise CoordinatorError(f"{record_id} has no recorded item; nothing simulated")
        item = self.item(record_id)
        if hasattr(self.board, "set_by_human"):
            self.board.set_by_human(item, self.cfg.status_field, name)
            return
        project = self.project_sync.resolve()
        status = project.field(self.cfg.status_field)
        option = status.option(name) if status else None
        assert status is not None and option is not None
        self.board.set_option(project.id, item, status.id, option.id)

    def settle(self, record_id: str, reason: str) -> dict[str, Any]:
        """Watch GitHub's own project workflows act after a change (live board only)."""
        if not self.board.live:
            return {}
        number = self.number(record_id)
        start = self.client.get_issue(number).state
        deadline = time.monotonic() + WORKFLOW_SETTLE_SECONDS
        state = start
        while time.monotonic() < deadline:
            time.sleep(5)
            state = self.client.get_issue(number).state
            if state != start:
                break
        observed = {"issue": number, "before": start, "after": state, "reason": reason}
        self.report["observations"].setdefault("workflows", []).append(observed)
        return observed

    def _while_running(self, request: RunRequest) -> None:
        del request
        codes = self.publisher.wait(timeout=300)
        self.report["observations"]["running"] = {"status": self.status(self.task_id), "publisher_exit": codes}
        if self.gates is not None:
            self.gates.armed = True

    def _while_verifying(self) -> None:
        codes = self.publisher.wait(timeout=300)
        self.report["observations"]["verifying"] = {"status": self.status(self.task_id), "publisher_exit": codes}

    def converge(self, rounds: int = 3) -> list[dict[str, Any]]:
        """Sync until a sync writes nothing (GitHub workflows act asynchronously)."""
        assert self.coord is not None
        history = []
        for index in range(rounds):
            issues = self.coord.sync.sync(self.fid)
            board = self.project_sync.sync(self.fid)
            history.append({"issue_writes": issues.writes, "issue_drift": issues.drift, "board_writes": board.writes,
                            "board_drift": board.drift, "ok": issues.ok and board.ok})
            self.report["drift"] += issues.drift + board.drift
            if not issues.writes and not board.writes:
                break
            if index + 1 < rounds and self.board.live:
                time.sleep(10)
        return history

    # -- the scenario ------------------------------------------------------------------

    def run(self, *, cleanup: bool) -> dict[str, Any]:
        """Run every step; never raises for a failed check."""
        try:
            self._preflight()
            self._existing_issue_association()
            self._automatic_add_and_recovery()
            self._manual_moves_and_holds()
            self._agent_run()
            self._invalidation()
            self._idempotent_and_integration()
            self._restart_and_reconcile()
            self._untouched()
        except Exception as exc:  # pylint: disable=broad-except
            self.check("scenario completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
        self._finish(cleanup)
        return self.report

    def _preflight(self) -> None:
        check, client = self.check, self.client
        viewer = client.viewer_login()
        check("B0 tracker authentication", bool(viewer), f"acting as {viewer}")
        if isinstance(client, GitHubClient):
            scopes = client.token_scopes()
            check("B0 the gh login has the `project` scope", scopes is None or "project" in scopes, scopes)
        init_demo_repo(self.repo, SEED_FILES)
        check("B0 disposable repository (not the live checkout)", self.repo.resolve() != REPO_ROOT.resolve(),
              str(self.repo))
        self.coord = self._coordinator(board=True)
        warnings: list[str] = []
        project = self.project_sync.resolve(warnings)
        check("B0 the configured project's identity is proven (owner, number, node ID; not protected)",
              project.id == self.cfg.node_id and project.number == self.cfg.number,
              f"#{project.number} {project.title!r} {project.id}")
        if not self.board.live:
            self.project_sync.setup(apply=True)  # a local rehearsal configures its own board
            project = self.project_sync.resolve()
        layout_ok = True
        try:
            self.project_sync.layout(project)
        except CoordinatorError as exc:
            layout_ok = False
            warnings.append(str(exc))
        check("B0 the board has the Status options and the Agent / Feature ID / Blocked Reason fields", layout_ok,
              warnings)
        check("B0 Kanban and Active Tasks views exist",
              project.view(self.cfg.board_view) is not None and project.view(self.cfg.active_view) is not None)
        protected = self.project_sync.protected_ids()
        check("B0 protected projects resolved (never written)",
              sorted(protected) == sorted(self.cfg.protected_numbers), protected)
        self.report["project"] = {"number": project.number, "id": project.id, "url": project.url}
        self.report["interfering_workflows"] = self.project_sync.interfering_workflows(project)
        self.before = {"protected": {n: project_snapshot(self.board, self.config, n) for n in protected},
                       "issues": snapshot(client),
                       "labels": {l["name"]: l for l in client.list_labels()}  # type: ignore[attr-defined]
                       if hasattr(client, "list_labels") else {},
                       "pulls": {(p.number, p.state) for p in client.list_pull_requests()}}

    def _existing_issue_association(self) -> None:
        check = self.check
        # B1: a coordinator without the board plans the feature and stops right after the parent Issue.
        self.coord = self._coordinator(board=False, issue_faults=(f"crash-after-link:{self.fid}",))
        try:
            self.coord.plan(board_spec(self.fid))
            crashed = False
        except SimulatedCrash:
            crashed = True
        parent = self.number(self.fid)
        self.coord = self._coordinator(board=True)
        check("B1 an existing coordinator-owned parent Issue, on no project board",
              crashed and self.coord.store.load_task(self.task_id).issue_number is None
              and not self.board.issue_items(self.config.github.repository, parent), f"#{parent}")
        report = self.project_sync.sync(self.fid)
        check("B2 a board sync does not touch a feature that was never authorized for the board",
              self.fid in report.skipped and not report.writes
              and not self.board.issue_items(self.config.github.repository, parent), report.skipped)
        argv = [*CLI, "--repo", str(self.repo), "--state-dir", str(self.state), "--tracker", self.tracker_name,
                "github", "project", "associate", self.fid, "--actor", "phase4x-live-test", "--apply", "--json"]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False, cwd=CODE_ROOT)
        try:
            result = json.loads(proc.stdout)
        except ValueError:
            result = {}
        values = self.values(self.fid) if self.project_sync.load_link(self.fid) else {}
        check("B3 the operator associated the existing Issue (separate process): it is on the board once",
              proc.returncode == 0 and self.fid in result.get("added", {}) and len(self.items_on_board(parent)) == 1,
              sanitize(proc.stderr or result.get("writes"), 300))
        check("B3 Status, Agent and Feature ID derived from the local state",
              values == {"Status": "Backlog", "Agent": "Coordination", "Feature ID": self.fid, "Blocked Reason": ""},
              values)
        self.report["issues"] = {self.fid: {"number": parent, "url": self.client.get_issue(parent).url,
                                            "item": self.item(self.fid)}}

    def _automatic_add_and_recovery(self) -> None:
        check = self.check
        self.coord = self._coordinator(board=True, board_faults=(f"crash-after-item-add:{self.task_id}",))
        try:
            self.coord.activate(self.fid)
            crashed = False
        except SimulatedCrash:
            crashed = True
        task_issue = self.number(self.task_id)
        link = self.project_sync.load_link(self.task_id)
        check("B4 activate created the task Issue and added it to the board automatically (crash injected "
              "right after GitHub added the item)",
              crashed and link is not None and link.pending_add is not None and not link.item_id
              and len(self.items_on_board(task_issue)) == 1, f"#{task_issue}")
        self.coord = self._coordinator(board=True)
        report = self.project_sync.sync(self.fid)
        check("B5 the restarted coordinator found the item instead of adding a second one",
              self.task_id in report.adopted and not report.added and len(self.items_on_board(task_issue)) == 1,
              report.adopted)
        check("B5 task values: Ready / Backend / Feature ID; parent In Progress",
              self.values(self.task_id) == {"Status": "Ready", "Agent": "Backend", "Feature ID": self.fid,
                                            "Blocked Reason": ""} and self.status(self.fid) == "In Progress",
              {"task": self.values(self.task_id), "parent": self.status(self.fid)})
        self.report["issues"][self.task_id] = {"number": task_issue, "url": self.client.get_issue(task_issue).url,
                                               "item": self.item(self.task_id)}

    def _manual_moves_and_holds(self) -> None:
        check, coord = self.check, self.coord
        assert coord is not None
        self.human_set_status(self.task_id, "In Progress")
        self.human_set_status(self.fid, "Done")
        observed = self.settle(self.fid, "parent card moved to Done by a person")
        check("B6 moved cards start and complete nothing locally",
              coord.store.load_task(self.task_id).state is TaskState.READY
              and coord.store.load_feature(self.fid).state is FeatureState.ACTIVE
              and coord.store.load_claim(self.task_id) is None, observed)
        history = self.converge()
        drift = [d for h in history for d in h["board_drift"]]
        check("B6 the next sync reports the drift and restores the derived values",
              len(drift) >= 2 and self.status(self.task_id) == "Ready" and self.status(self.fid) == "In Progress"
              and self.client.get_issue(self.number(self.fid)).state == "open", drift)
        coord.hold_task(self.task_id, reason="waiting for a UX review", actor="phase4x-live-test")
        held = self.values(self.task_id)
        check("B7 a human hold shows Blocked with its reason",
              held.get("Status") == "Blocked" and held.get("Blocked Reason")
              == "held for a human decision by phase4x-live-test: waiting for a UX review",
              held)
        self.human_set_status(self.task_id, "Ready")
        try:
            coord.run_task(self.task_id)
            claimed = "CLAIMED"
        except (ClaimError, IllegalTransitionError) as exc:
            claimed = f"refused: {exc}"
        restored = self.project_sync.sync(self.fid)
        check("B7 a card moved to Ready cannot authorize a claim; the sync restores Blocked",
              claimed.startswith("refused") and coord.store.load_task(self.task_id).state is TaskState.NEEDS_HUMAN
              and self.status(self.task_id) == "Blocked" and bool(restored.drift), f"claim {claimed}")
        coord.release_task(self.task_id, actor="phase4x-live-test")
        released = self.values(self.task_id)
        check("B8 the release shows Ready and clears the reason",
              released.get("Status") == "Ready" and released.get("Blocked Reason") == "", released)

    def _agent_run(self) -> None:
        check, coord = self.check, self.coord
        assert coord is not None
        run = coord.run_task(self.task_id)
        self.publisher.wait(timeout=300)
        seen = self.report["observations"]
        check("B9 In Progress while the agent ran (published by a separate process)",
              seen.get("running", {}).get("status") == "In Progress", seen.get("running"))
        check("B9 Review while the commit was verified (published by a separate process)",
              seen.get("verifying", {}).get("status") == "Review", seen.get("verifying"))
        task = coord.store.load_task(self.task_id)
        branch = coord.store.load_feature(self.fid).branch
        head = task.evidence.head_sha if task.evidence else ""
        on_branch = bool(head) and coord.git.contains(branch, head)
        check("B9 Done only after verification with evidence on the feature branch",
              run.state is TaskState.VERIFIED and on_branch and self.status(self.task_id) == "Done"
              and self.values(self.task_id).get("Agent") == "Backend",
              f"commit {str(run.commit)[:12]}, evidence at {task.evidence.head_sha[:12] if task.evidence else '-'}")
        self.report["observations"]["after_done"] = self.settle(self.task_id, "task Status set to Done")

    def _invalidation(self) -> None:
        check, coord = self.check, self.coord
        assert coord is not None
        coord.reopen_task(self.task_id, "contract v2: the response gains a description")
        check("B10 invalidated verification: the board shows Ready, not Done",
              coord.store.load_task(self.task_id).state is TaskState.READY and self.status(self.task_id) == "Ready"
              and self.client.get_issue(self.number(self.task_id)).state == "open")
        coord.runners["backend"].inner.script[self.task_id] = backend_edits(2)  # type: ignore[attr-defined]
        run = coord.run_task(self.task_id)
        self.publisher.wait(timeout=300)
        check("B10 re-verified at the new HEAD → Done again",
              run.state is TaskState.VERIFIED and bool(run.commit) and self.status(self.task_id) == "Done",
              str(run.commit)[:12])
        self.settle(self.task_id, "task Status set to Done again")

    def _idempotent_and_integration(self) -> None:
        check, coord = self.check, self.coord
        assert coord is not None
        history = self.converge()
        writes_before = getattr(self.client, "writes", 0)
        again = self.project_sync.sync(self.fid)
        issues_again = coord.sync.sync(self.fid)
        check("B11 repeated synchronization writes nothing (Issues and board)",
              not again.writes and not issues_again.writes and getattr(self.client, "writes", 0) == writes_before,
              {"converged_after": len(history), "board": again.writes, "issues": issues_again.writes})
        check("B11 exactly one item per Issue on the board",
              all(len(self.items_on_board(self.number(r))) == 1 for r in (self.fid, self.task_id)))
        check("B12 a verified task alone does not make the parent Done",
              self.status(self.fid) == "In Progress" and self.status(self.task_id) == "Done")
        feature = coord.integrate(self.fid, gates=INTEGRATION_GATES)
        self.converge()
        refused = "not attempted (local tracker)"
        if getattr(self.client, "live", False):
            try:
                coord.open_pull_request(self.fid, approved=True)
                refused = "CREATED"
            except PullRequestPolicyError as exc:
                refused = f"refused: {exc}"
        remote = self.client.list_pull_requests(feature.branch)
        check("B12 integration: the parent shows Review (one PR candidate, no merge), never Done; no PR exists",
              feature.state is FeatureState.REVIEW and self.status(self.fid) == "Review" and not remote
              and refused != "CREATED" and feature.merged_sha is None, refused)

    def _restart_and_reconcile(self) -> None:
        check = self.check
        before = {link.id: link.item_id for link in self.project_sync.links(self.fid)}
        argv = [*CLI, "--repo", str(self.repo), "--state-dir", str(self.state), "--tracker", self.tracker_name,
                "github", "project", "reconcile", self.fid, "--apply", "--json"]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False, cwd=CODE_ROOT)
        try:
            result = json.loads(proc.stdout)
        except ValueError:
            result = {}
        board = result.get("features", {}).get(self.fid, {})
        check("B13 a new process reconciled the board: no drift, no write, no duplicate",
              proc.returncode == 0 and result.get("ok") is True and not board.get("writes")
              and not board.get("drift") and not result.get("duplicates") and not result.get("orphans"),
              sanitize(proc.stderr or board.get("writes"), 300))
        self.coord = self._coordinator(board=True)
        after = {link.id: link.item_id for link in self.project_sync.links(self.fid)}
        check("B13 item IDs stable across the restart", before == after and len(after) == 2, after)

    def _untouched(self) -> None:
        check, client = self.check, self.client
        protected_after = {n: project_snapshot(self.board, self.config, n) for n in self.before["protected"]}
        check("B14 the protected projects are unchanged (fields, options, views, workflows, items)",
              protected_after == self.before["protected"], list(protected_after))
        ours = {info["number"] for info in self.report.get("issues", {}).values()}
        check("B14 the test Issues are on no protected project",
              all(r.project_number not in self.cfg.protected_numbers
                  for n in ours for r in self.board.issue_items(self.config.github.repository, n)))
        after = snapshot(client)
        changed = sorted(n for n, fields in self.before["issues"].items() if n not in ours and after.get(n) != fields)
        check("B14 no unrelated Issue or PR was modified", not changed and len(ours) == 2,
              f"{len(self.before['issues'])} pre-existing compared; changed: {changed}; created: {sorted(ours)}")
        if self.before["labels"]:
            labels_after = {l["name"]: l for l in client.list_labels()}  # type: ignore[attr-defined]
            check("B14 labels unchanged", labels_after == self.before["labels"])
        pulls = {(p.number, p.state) for p in client.list_pull_requests()}
        check("B14 no pull request created or changed", pulls == self.before["pulls"])

    # -- end -----------------------------------------------------------------------------

    def _finish(self, cleanup: bool) -> None:
        self.report["checks"] = self.check.items
        self.report["passed"] = self.check.passed
        self.report["requests"] = getattr(self.client, "requests", None)
        self.report["writes"] = getattr(self.client, "writes", None)
        coord = self.coord
        if coord is None or len(self.report.get("issues", {})) != 2:
            return
        failed = [c["check"] for c in self.check.items if not c["passed"]]
        summary = "\n".join([
            "Phase 4 extension (GitHub Projects board) — "
            f"**{'all checks passed' if not failed else 'FAILED'}** "
            f"({sum(c['passed'] for c in self.check.items)}/{len(self.check.items)} checks).",
            f"Feature `{self.fid}`, task `{self.task_id}`, project #{self.cfg.number}.", "",
            *[f"- {'PASS' if c['passed'] else 'FAIL'}: {c['check']}" for c in self.check.items]])
        if failed or not cleanup:
            self.report["cleanup"] = "skipped: " + ("checks failed" if failed else "not requested")
            return
        result = coord.sync.cleanup_test_feature(self.fid, summary, apply=True)
        for record_id in (self.task_id, self.fid):
            self.settle(record_id, "Issue closed by the cleanup")
        board = self.project_sync.sync(self.fid)
        states = {rid: self.client.get_issue(info["number"]).state for rid, info in self.report["issues"].items()}
        final = {rid: self.values(rid) for rid in self.report["issues"]}
        self.report["cleanup"] = {
            "ok": result.ok and board.ok and all(s == "closed" for s in states.values())
            and final[self.fid].get("Status") == "Review" and final[self.task_id].get("Status") == "Done",
            "states": states, "board_after_close": final, "writes": result.writes, "board_writes": board.writes,
            "board_drift": board.drift}
        again = coord.sync.cleanup_test_feature(self.fid, summary, apply=True)
        self.report["cleanup"]["repeat_writes"] = again.writes
        protected_after = {n: project_snapshot(self.board, self.config, n) for n in self.before["protected"]}
        self.report["cleanup"]["protected_unchanged"] = protected_after == self.before["protected"]


def run_projects_test(config: ProjectConfig, workdir: Path, *, tracker_name: str, cleanup: bool = True,
                      feature_id: str | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Run the scenario against the real GitHub (`github`) or a local rehearsal (`local`)."""
    feature_id = feature_id or new_feature_id()
    if tracker_name == "github":
        client: Any = GitHubClient.from_config(config.github)
    else:
        operator = config.github.command_authors[0]
        state_dir = ensure_state_dir(workdir / feature_id / "state")
        client = MockIssueTracker(state_dir / "tracker.json", permissions={"feature-coordinator": "admin",
                                                                           operator: "admin"})
    lifecycle = ProjectsLifecycle(config, client, workdir, tracker_name=tracker_name, feature_id=feature_id, log=log)
    report = lifecycle.run(cleanup=cleanup)
    report["test_label"] = TEST_LABEL
    path = lifecycle.workdir / "report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report["report_path"] = str(path)
    return report
