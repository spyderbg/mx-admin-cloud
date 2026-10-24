"""GitHub Projects (v2) board synchronization (Phase 4 extension).

Direction of authority. The local coordinator state stays the only authority for task
states, claims, locks, workers and approvals; GitHub Issues are the tasks' public record
(issue_sync.py); the board shows where each Issue stands. A sync *derives* every board value
from the local state and converges the item to it. Nothing on the board is input: moving a
card, editing a field or removing an item never changes the local state, never claims or
starts a task and never approves anything — the next sync reports it as drift and restores
the derived value.

Status mapping (`board_status`), from the existing state machines (model.py):

  task proposed                       → Backlog  (created, feature not activated/approved)
  task ready                          → Ready
  task in-progress                    → In Progress
  task in-progress, run finished and
    committed, gates running          → Review   (the live claim's checkpoint says "verifying")
  task blocked                        → Blocked  (Blocked Reason: "waiting for <dependency>")
  task needs-human                    → Blocked  (Blocked Reason: "held for a human decision by
                                        <actor>: <reason>", or "needs a human: <reason>")
  task failed                         → Blocked  (Blocked Reason: "failed: <reason>")
  task verified                       → Done, only with evidence: every required gate passed at
                                        a commit that is on the feature branch; otherwise Review
                                        (W4.3.3: the task of an approved GitHub Issue stays Review
                                        until its feature is done with merge evidence: the merged PR)
  feature planned                     → Backlog
  feature active / integration        → In Progress
  feature review (one PR candidate)   → Review
  feature done                        → Done, only with merge evidence (`merged_sha`);
                                        verified component tasks alone never make it Done
  feature blocked / failed            → Blocked (with the reason)

Agent: the task's role ("Coordination" for the parent feature). Feature ID: the feature ID.
Blocked Reason: set while Blocked, cleared otherwise.

Views. "Kanban" shows every item by Status; "Active Tasks" (`active_filter`) shows only open
Issues whose Status is Ready, In Progress, Blocked or Review. A closed Issue is never active
work: the Issue sync closes a feature's Issues only when the feature is done (merged) or a
test cleanup finished it, and reopens an Issue a person closed. Finished test features, kept
for audit, therefore stay off that view.

Ownership. A record is shown only when (1) its feature is authorized for the board — features
planned while the integration is on are authorized by `plan`, older ones only by an explicit
`feature.py github project associate FEATURE --apply`; (2) its Issue passes the Phase 4
ownership proof (marker, author, number and database ID) — or, for the task of a feature made
from an approved GitHub Issue (W4.3.1, intake.py), it is exactly that Issue; (3) the project is
the configured one (owner, number and node ID) and not a protected project; and (4) the item's
content is exactly that Issue. Items of other Issues are never modified.

Idempotency and recovery. Before adding an item, `pending_add` is persisted. GitHub returns
the existing item when an Issue is added twice, and the next sync looks the Issue's items up
before adding, so a crash or a lost response never creates a second item. Field writes are
absolute values; after writing, the item is read back, so a GitHub project workflow that
changed a value meanwhile is detected and the value re-applied.

Locks. Like the Issue sync, a board sync holds only `github-sync.lock` while it talks to
GitHub; its local writes are short `StateStore.transaction()` sections on its own records.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .config import STATUS_KEYS, GitHubProjectConfig, ProjectConfig
from .errors import GitHubError, GitHubUncertainWriteError, OwnershipError, ProjectBoardError, StateDirError, \
    SyncScopeError
from .github_api import sanitize
from .github_projects import BoardClient, FieldOption, ProjectField, ProjectItem, ProjectView, RemoteProject
from .intake import load_intake
from .issue_sync import IssueSync, SimulatedCrash
from .issues import OwnershipMarker, RemoteIssue
from .locks import ProcessIdentity
from .model import Feature, FeatureState, Task, TaskState, utc_now
from .store import StateStore

LINK_SCHEMA = 1
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DEFAULT_VIEW_RE = re.compile(r"^View \d+$")
STATUS_STYLE = {
    "backlog": ("GRAY", "Planned; the feature is not activated (approved) yet"),
    "ready": ("BLUE", "Ready to be claimed by its role (local coordinator state)"),
    "in_progress": ("YELLOW", "An agent is executing it"),
    "blocked": ("RED", "Waiting for a dependency or a human; see Blocked Reason"),
    "review": ("PURPLE", "Implementation complete, awaiting verification (or the feature's PR review)"),
    "done": ("GREEN", "Verified with evidence on the feature branch (feature: PR merged)"),
}
AGENT_COLORS = {"coordination": "GRAY", "backend": "BLUE", "cli": "GREEN", "frontend": "ORANGE",
                "automation": "PINK", "full-project": "RED"}
# Built-in project workflows that change items on their own (they can be switched off only in
# the project's Workflows page; the API cannot change them).
INTERFERING_WORKFLOWS = {
    "Item added to project": "sets Status when an item is added; the coordinator re-applies its value",
    "Item closed": "sets Status to Done when an Issue is closed; the coordinator restores the derived value",
    "Auto-close issue": "closes the Issue when Status becomes Done; the Issue sync then reopens it (drift)",
    "Item reopened": "sets Status when an Issue is reopened; the coordinator restores the derived value",
    "Pull request linked to issue": "sets Status when a PR is linked; the coordinator restores the derived value",
    "Pull request merged": "sets Status when a PR merges; the coordinator restores the derived value",
    "Auto-add to project": "adds repository Issues automatically, including ones the coordinator does not own",
}


@dataclass
class ProjectLink:  # pylint: disable=too-many-instance-attributes
    """The board side of one local record: authorization, item identity, what was written."""

    id: str                        # the feature or task ID
    kind: str                      # "feature" | "task"
    feature_id: str
    project_id: str = ""
    project_number: int = 0
    item_id: str = ""
    issue_number: int | None = None
    issue_id: int | None = None
    authorized: bool = False       # feature records: the feature may be shown on the board
    authorized_by: str = ""
    authorized_at: str = ""
    pending_add: dict[str, str] | None = None
    fields_written: dict[str, str] = field(default_factory=dict)  # field name → value last written ("" = empty)
    last_synced_at: str = ""
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "schema_version": LINK_SCHEMA}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectLink":
        """Inverse of to_dict()."""
        if data.get("schema_version") != LINK_SCHEMA:
            raise StateDirError(f"unsupported project link schema {data.get('schema_version')!r}")
        values = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}  # pylint: disable=no-member
        return cls(**values)


@dataclass
class BoardReport:  # pylint: disable=too-many-instance-attributes
    """What a board sync did (apply) or would do (dry run)."""

    feature_id: str
    applied: bool
    project: dict[str, Any] = field(default_factory=dict)
    writes: list[str] = field(default_factory=list)
    added: dict[str, str] = field(default_factory=dict)
    adopted: dict[str, str] = field(default_factory=dict)
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    drift: list[str] = field(default_factory=list)
    protected: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    deferred: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing was deferred and no record was refused as not owned."""
        return not self.deferred and not self.protected

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


@dataclass
class SetupReport:
    """What `setup` changed (or would change) on the project's configuration."""

    applied: bool
    project: dict[str, Any] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)   # steps the API cannot do
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the board has every required field, option and view."""
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


@dataclass(frozen=True)
class BoardLayout:
    """The configured project's fields and options, resolved to IDs."""

    status: ProjectField
    agent: ProjectField
    feature_id: ProjectField
    blocked_reason: ProjectField


def status_filter(names: Iterable[str], field_name: str = "Status") -> str:
    """A view filter showing only items whose Status is one of *names*."""
    key = field_name.lower().replace(" ", "-")
    return f"{key}:" + ",".join(f'"{n}"' if re.search(r"[\s,\"]", n) else n for n in names)


ACTIVE_STATUS_KEYS = ("ready", "in_progress", "blocked", "review")


def active_filter(project: GitHubProjectConfig) -> str:
    """The Active Tasks view filter: open Issues whose Status is Ready, In Progress, Blocked or
    Review (closed Issues, such as finished test features kept for audit, are not shown)."""
    return "is:open " + status_filter([project.status(k) for k in ACTIVE_STATUS_KEYS], project.status_field)


class ProjectSync:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Synchronizes one state store with one GitHub project board. See the module docstring."""

    def __init__(self, store: StateStore, board: BoardClient, issues: IssueSync,  # pylint: disable=too-many-arguments
                 config: ProjectConfig, *, sleep: Callable[[float], None] = time.sleep, faults: Iterable[str] = (),
                 revision_check: Callable[[Feature, str], bool] | None = None) -> None:
        self.store = store
        self.board = board
        self.issues = issues
        self.config = config
        self.cfg: GitHubProjectConfig = config.github.project
        self.sleep = sleep
        self.faults = set(faults)
        self.revision_check = revision_check
        self._protected: dict[int, str] | None = None

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    # -- local records ---------------------------------------------------------------

    def load_link(self, record_id: str) -> ProjectLink | None:
        """The board link of a feature or task, if any."""
        data = self.store.load_record("project", record_id)
        return ProjectLink.from_dict(data) if data else None

    def links(self, feature_id: str | None = None) -> list[ProjectLink]:
        """All board links (of one feature)."""
        links = [ProjectLink.from_dict(d) for d in self.store.list_records("project")]
        return [link for link in links if feature_id is None or link.feature_id == feature_id]

    def _update_link(self, feature: Feature, task: Task | None, mutate: Callable[[ProjectLink], None]) -> ProjectLink:
        with self.store.transaction():
            record_id = task.id if task else feature.id
            link = self.load_link(record_id) or ProjectLink(id=record_id, kind="task" if task else "feature",
                                                            feature_id=feature.id)
            mutate(link)
            self.store.save_record("project", link)
        return link

    def authorized(self, feature_id: str) -> bool:
        """True when the feature may be shown on the board."""
        link = self.load_link(feature_id)
        return bool(link and link.authorized)

    def authorize(self, feature_id: str, actor: str) -> ProjectLink:
        """Allow the board to show this feature (plan does this for new features; older features
        need an explicit `feature.py github project associate`). Local only; idempotent."""
        feature = self.store.load_feature(feature_id)
        with self.store.transaction():
            link = self.load_link(feature.id) or ProjectLink(id=feature.id, kind="feature", feature_id=feature.id)
            if not link.authorized:
                link.authorized, link.authorized_by, link.authorized_at = True, sanitize(actor, 60), utc_now()
                self.store.save_record("project", link)
                self.store.append_event("github-project-authorized", feature=feature.id, actor=link.authorized_by)
        return link

    # -- the project's identity and layout ---------------------------------------------------

    def protected_ids(self) -> dict[int, str]:
        """Node IDs of the protected projects (read-only lookups; cached)."""
        if self._protected is None:
            found = {}
            for number in self.cfg.protected_numbers:
                project = self.board.project(self.cfg.owner, self.cfg.owner_type, number)
                if project is not None:
                    found[number] = project.id
            self._protected = found
        return self._protected

    def resolve(self, warnings: list[str] | None = None) -> RemoteProject:
        """The configured project, after its identity was proven; binds the board's write guard."""
        cfg = self.cfg
        if not cfg.enabled:
            raise ProjectBoardError("github.project is disabled in the configuration")
        project = self.board.project(cfg.owner, cfg.owner_type, cfg.number)
        where = f"project #{cfg.number} of {cfg.owner}"
        if project is None:
            raise ProjectBoardError(f"{where} not found or not visible (the gh login needs the `project` scope)")
        if project.id != cfg.node_id or project.number != cfg.number or project.owner.lower() != cfg.owner.lower():
            raise ProjectBoardError(f"{where} is not the configured project (node ID {sanitize(project.id, 40)}, "
                                    f"expected {cfg.node_id}); nothing is written")
        protected = self.protected_ids()
        if project.id in protected.values():
            raise ProjectBoardError(f"{where} is a protected project; it is never written")
        if project.closed:
            raise ProjectBoardError(f"{where} is closed")
        if not project.viewer_can_update:
            raise ProjectBoardError(f"the gh account cannot update {where}")
        if cfg.title and project.title != cfg.title and warnings is not None:
            warnings.append(f"{where} is titled {sanitize(project.title, 80)!r}, configured "
                            f"{cfg.title!r} (identity is the node ID; update the configuration)")
        self.board.bind(project, protected.values())
        return project

    def layout(self, project: RemoteProject) -> BoardLayout:
        """The required fields with every required option, or ProjectBoardError naming what is missing."""
        cfg = self.cfg
        missing = []
        fields = {name: project.field(name) for name in (cfg.status_field, cfg.agent_field, cfg.feature_id_field,
                                                         cfg.blocked_reason_field)}
        for name, kind in ((cfg.status_field, "SINGLE_SELECT"), (cfg.agent_field, "SINGLE_SELECT"),
                           (cfg.feature_id_field, "TEXT"), (cfg.blocked_reason_field, "TEXT")):
            found = fields[name]
            if found is None or found.data_type != kind:
                missing.append(f"{kind} field {name!r}")
        status, agent = fields[cfg.status_field], fields[cfg.agent_field]
        if status is not None:
            missing += [f"Status option {n!r}" for n in cfg.status_names.values() if status.option(n) is None]
        if agent is not None:
            missing += [f"Agent option {n!r}" for n in cfg.agent_names.values() if agent.option(n) is None]
        if missing:
            raise ProjectBoardError(f"project #{project.number} is not set up ({', '.join(missing)}); run "
                                    "`feature.py github project setup --apply`")
        return BoardLayout(status, agent, fields[cfg.feature_id_field],  # type: ignore[arg-type]
                           fields[cfg.blocked_reason_field])  # type: ignore[arg-type]

    def interfering_workflows(self, project: RemoteProject) -> list[str]:
        """Enabled built-in workflows that change items behind the coordinator's back."""
        return [f"workflow {w.name!r} is enabled: {INTERFERING_WORKFLOWS[w.name]}"
                for w in project.workflows if w.enabled and w.name in INTERFERING_WORKFLOWS]

    # -- setup (one-time project configuration) ---------------------------------------------------

    def setup(self, *, apply: bool) -> SetupReport:
        """Create or reuse the Status options, the Agent / Feature ID / Blocked Reason fields and the
        Kanban and Active Tasks views. Never deletes a field, an option or a view; options are
        renamed only through `status_reuse`, keeping their IDs (and item values)."""
        report = SetupReport(apply)
        lock = self.store.locks.github_sync().acquire(timeout=self.config.github.sync_lock_timeout_seconds)
        try:
            project = self.resolve(report.manual)
            report.project = {"number": project.number, "id": project.id, "title": project.title, "url": project.url,
                              "items": project.items_total}
            self._setup_status(project, report, apply)
            for name, kind, options in ((self.cfg.agent_field, "SINGLE_SELECT", self._agent_options()),
                                        (self.cfg.feature_id_field, "TEXT", ()),
                                        (self.cfg.blocked_reason_field, "TEXT", ())):
                self._setup_field(project, name, kind, options, report, apply)
            project = self.resolve() if apply else project
            self._setup_views(project, report, apply)
            if apply:
                project = self.resolve()
                try:
                    self.layout(project)
                except ProjectBoardError as exc:
                    report.problems.append(str(exc))
            report.manual += self.interfering_workflows(project)
        except GitHubError as exc:
            report.problems.append(sanitize(exc))
        finally:
            lock.release()
        if apply:
            with self.store.transaction():
                self.store.append_event("github-project-setup", changes=report.changes, problems=report.problems)
        return report

    def _agent_options(self) -> list[FieldOption]:
        return [FieldOption("", name, AGENT_COLORS[role], f"Tasks of the {role} role" if role != "coordination"
                            else "Parent features and coordination tasks")
                for role, name in self.cfg.agent_names.items()]

    def _item_counts(self, project: RemoteProject, field_name: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.board.list_items(project.id):
            value = item.option_ids.get(field_name)
            if value:
                counts[value] = counts.get(value, 0) + 1
        return counts

    def _setup_status(self, project: RemoteProject, report: SetupReport, apply: bool) -> None:
        cfg = self.cfg
        status = project.field(cfg.status_field)
        if status is None or status.data_type != "SINGLE_SELECT":
            report.problems.append(f"the project has no single-select {cfg.status_field!r} field; it is not created "
                                   "or replaced automatically")
            return
        counts = self._item_counts(project, cfg.status_field) if project.items_total else {}
        desired, used = [], set()
        for key in STATUS_KEYS:
            name = cfg.status(key)
            color, description = STATUS_STYLE[key]
            existing = status.option(name)
            if existing is None:
                existing = next((o for o in status.options if cfg.status_reuse.get(o.name) == key
                                 and o.id not in used and o.name not in cfg.status_names.values()), None)
                if existing is not None:
                    report.changes.append(f"rename Status option {existing.name!r} → {name!r} (option ID "
                                          f"{existing.id} kept; {counts.get(existing.id, 0)} item(s) keep it)")
                    desired.append(FieldOption(existing.id, name, color, description))
                    used.add(existing.id)
                    continue
                report.changes.append(f"add Status option {name!r}")
                desired.append(FieldOption("", name, color, description))
                continue
            report.reused.append(f"Status option {name!r} ({existing.id}, {counts.get(existing.id, 0)} item(s))")
            desired.append(existing)
            used.add(existing.id)
        extras = [o for o in status.options if o.id not in used]
        if extras:
            report.reused.append("other Status options kept unchanged: "
                                 + ", ".join(f"{o.name!r} ({counts.get(o.id, 0)} item(s))" for o in extras))
        desired += extras
        if [astuple_option(o) for o in desired] == [astuple_option(o) for o in status.options]:
            return
        report.changes.append("Status options in board order: " + ", ".join(o.name for o in desired))
        if apply:
            self.board.update_options(status.id, desired)

    def _setup_field(self, project: RemoteProject, name: str, kind: str,  # pylint: disable=too-many-arguments
                     options: list[FieldOption] | tuple[()], report: SetupReport, apply: bool) -> None:
        existing = project.field(name)
        if existing is not None and existing.data_type != kind:
            report.problems.append(f"field {name!r} exists as {existing.data_type}, not {kind}; it is never "
                                   "changed or deleted automatically")
            return
        if existing is None:
            report.changes.append(f"create {kind} field {name!r}"
                                  + (f" with options {[o.name for o in options]}" if options else ""))
            if apply:
                try:
                    self.board.create_field(project.id, name, kind, options)
                except GitHubUncertainWriteError:
                    if self.resolve().field(name) is None:
                        raise
            return
        report.reused.append(f"field {name!r} ({existing.id})")
        missing = [o for o in options if existing.option(o.name) is None]
        if missing:
            report.changes.append(f"add {name} options {[o.name for o in missing]} (existing options kept)")
            if apply:
                self.board.update_options(existing.id, [*existing.options, *missing])

    def _setup_views(self, project: RemoteProject, report: SetupReport, apply: bool) -> None:
        cfg = self.cfg
        status = project.field(cfg.status_field)
        wanted = [f.id for f in (project.field(cfg.agent_field), project.field(cfg.feature_id_field),
                                 project.field(cfg.blocked_reason_field)) if f is not None]
        board = project.view(cfg.board_view)
        if board is None:
            reusable = next((v for v in project.views if v.layout == "BOARD_LAYOUT" and status is not None
                             and v.group_by == (status.id,) and _DEFAULT_VIEW_RE.fullmatch(v.name)), None)
            if reusable is not None:
                report.changes.append(f"rename board view {reusable.name!r} (grouped by Status) → {cfg.board_view!r}")
                board = self.board.update_view(reusable.id, name=cfg.board_view) if apply else reusable
            else:
                report.changes.append(f"create board view {cfg.board_view!r}")
                board = self._create_view(project, cfg.board_view, report, apply)
        else:
            report.reused.append(f"view {cfg.board_view!r}")
        active = project.view(cfg.active_view)
        if active is None:
            report.changes.append(f"create board view {cfg.active_view!r}")
            active = self._create_view(project, cfg.active_view, report, apply)
        else:
            report.reused.append(f"view {cfg.active_view!r}")
        for view, filter_text in ((board, None), (active, active_filter(cfg))):
            if view is None:
                continue
            self._configure_view(view, status, wanted, filter_text, report, apply)

    def _create_view(self, project: RemoteProject, name: str, report: SetupReport, apply: bool) -> ProjectView | None:
        if not apply:
            return None
        try:
            return self.board.create_view(project.id, name, "BOARD_LAYOUT")
        except GitHubUncertainWriteError:
            view = self.resolve().view(name)
            if view is None:
                raise
            report.changes.append(f"(the view {name!r} was found after an uncertain create)")
            return view

    def _configure_view(self, view: ProjectView, status: ProjectField | None,  # pylint: disable=too-many-arguments
                        wanted: list[str], filter_text: str | None, report: SetupReport, apply: bool) -> None:
        visible = list(view.visible) + [f for f in wanted if f not in view.visible]
        name_filter = filter_text if filter_text is not None and view.filter != filter_text else None
        if visible != list(view.visible) or name_filter is not None:
            report.changes.append(f"view {view.name!r}: " + "; ".join(
                part for part in (f"show the {len(visible) - len(view.visible)} coordinator field(s)"
                                  if visible != list(view.visible) else "",
                                  f"filter {filter_text!r}" if name_filter is not None else "") if part))
            if apply:
                view = self.board.update_view(view.id, filter_text=name_filter,
                                              visible_field_ids=visible if visible != list(view.visible) else None)
        if view.layout != "BOARD_LAYOUT":
            report.manual.append(f"view {view.name!r} uses {view.layout}; switch it to Board in the UI if wanted")
        elif status is not None and view.group_by and view.group_by != (status.id,):
            report.manual.append(f"view {view.name!r}: set 'Column by' to {status.name!r} in the view's menu "
                                 "(the API cannot set the board column field)")
        elif status is not None and not view.group_by and apply:
            report.manual.append(f"view {view.name!r}: GitHub reports no column field yet; check 'Column by: "
                                 f"{status.name}' in the UI")

    # -- desired values ----------------------------------------------------------------------

    def _reason(self, record: Feature | Task, prefix: str = "") -> str:
        text = record.blocked_reason if isinstance(record, Task) and record.state is TaskState.BLOCKED else (
            record.history[-1].reason if record.history else "")
        return sanitize(prefix + sanitize(text or "no reason recorded", 180), 200)

    def evidence_problem(self, feature: Feature, task: Task) -> str | None:
        """Why the task's evidence does not justify Done (None when it does)."""
        evidence = task.evidence
        if evidence is None:
            return "no verification evidence"
        if not _SHA_RE.fullmatch(evidence.head_sha):
            return "the evidence is not bound to a commit SHA"
        passed = {g.gate for g in evidence.gates if g.passed}
        failing = [g for g in task.required_gates if g not in passed]
        if failing:
            return f"required gate(s) {failing} did not pass in the evidence"
        if self.revision_check is not None and not self.revision_check(feature, evidence.head_sha):
            return f"the verified commit {evidence.head_sha[:12]} is not on {feature.branch}"
        return None

    def _dependency_state(self, task_id: str) -> TaskState:
        try:
            return self.store.load_task(task_id).state
        except KeyError:
            return TaskState.BLOCKED

    def _verifying(self, task: Task) -> bool:
        claim = self.store.load_claim(task.id)
        if not claim or (claim.get("checkpoint") or {}).get("phase") != "verifying":
            return False
        try:
            return ProcessIdentity.from_dict(claim["identity"]).is_alive()
        except (KeyError, TypeError, ValueError):
            return False

    def board_status(self, feature: Feature, task: Task | None, notes: list[str] | None = None) -> tuple[str, str]:
        """(status key, blocked reason) for a record. See the module docstring."""
        notes = notes if notes is not None else []
        if task is None:
            return self._feature_status(feature, notes)
        state = task.state
        if state is TaskState.VERIFIED:
            return self._verified_status(feature, task, notes), ""
        if state is TaskState.IN_PROGRESS:
            return ("review" if self._verifying(task) else "in_progress"), ""
        if state in (TaskState.PROPOSED, TaskState.READY):
            return ("backlog" if state is TaskState.PROPOSED else "ready"), ""
        return "blocked", self._blocked_reason(task)

    def _verified_status(self, feature: Feature, task: Task, notes: list[str]) -> str:
        problem = self.evidence_problem(feature, task)
        if problem:
            notes.append(f"{task.id} is verified locally but not shown as Done: {problem}")
            return "review"
        if load_intake(self.store, feature.id) is not None and not (feature.state is FeatureState.DONE
                                                                     and feature.merged_sha):
            return "review"  # W4.3.3: an approved Issue's task is Done only once its PR is merged
        return "done"

    def _blocked_reason(self, task: Task) -> str:
        """Why a blocked, held or failed task cannot proceed."""
        if task.state is TaskState.BLOCKED:
            states = {d: self._dependency_state(d) for d in task.depends_on}
            unmet = [d for d, dep in states.items() if dep is not TaskState.VERIFIED]
            if unmet:
                return sanitize("waiting for " + ", ".join(f"{d} ({states[d].value})" for d in unmet), 200)
            return self._reason(task)
        if task.state is TaskState.NEEDS_HUMAN:
            held = task.history[-1].reason if task.history else ""
            return self._reason(task, "" if held.startswith("held for a human") else "needs a human: ")
        return self._reason(task, "failed: ")

    def _feature_status(self, feature: Feature, notes: list[str]) -> tuple[str, str]:
        state = feature.state
        if state is FeatureState.DONE:
            if feature.merged_sha:
                return "done", ""
            notes.append(f"{feature.id} is done locally without merge evidence; not shown as Done")
            return "review", ""
        if state in (FeatureState.BLOCKED, FeatureState.FAILED):
            return "blocked", self._reason(feature, "failed: " if state is FeatureState.FAILED else "")
        return {FeatureState.PLANNED: "backlog", FeatureState.ACTIVE: "in_progress",
                FeatureState.INTEGRATION: "in_progress", FeatureState.REVIEW: "review"}[state], ""

    def desired_values(self, feature: Feature, task: Task | None, notes: list[str] | None = None) -> dict[str, str]:
        """Field name → value for the record's item ("" = empty)."""
        cfg = self.cfg
        key, reason = self.board_status(feature, task, notes)
        role = task.role if task else "coordination"
        return {cfg.status_field: cfg.status(key), cfg.agent_field: cfg.agent_names[role],
                cfg.feature_id_field: feature.id, cfg.blocked_reason_field: reason if key == "blocked" else ""}

    # -- sync ------------------------------------------------------------------------------

    def _check_scope(self, feature: Feature) -> None:
        if getattr(self.board, "live", False) and self.config.github.write_scope == "test-features-only" \
                and not feature.test and load_intake(self.store, feature.id) is None:
            raise SyncScopeError(f"feature {feature.id!r} is not a test feature or an approved GitHub Issue; the "
                                 "GitHub write scope is 'test-features-only'. Widening it needs operator approval")

    def sync(self, feature_id: str, *, apply: bool = True) -> BoardReport:
        """Converge the feature's board items to the local state. Holds only github-sync.lock."""
        report = BoardReport(feature_id, apply)
        lock = self.store.locks.github_sync().acquire(timeout=self.config.github.sync_lock_timeout_seconds)
        try:
            self._sync(feature_id, report, apply)
        except (GitHubError, ProjectBoardError, SyncScopeError) as exc:
            report.deferred.append(sanitize(str(exc)))
        finally:
            lock.release()
        if apply:
            with self.store.transaction():
                self.store.append_event("github-project-sync", feature=feature_id, writes=len(report.writes),
                                        added=report.added, adopted=report.adopted, drift=report.drift,
                                        deferred=report.deferred, protected=report.protected,
                                        skipped=report.skipped)
        return report

    def _sync(self, feature_id: str, report: BoardReport, apply: bool) -> None:
        feature = self.store.load_feature(feature_id)
        if not self.authorized(feature_id):
            report.skipped[feature_id] = ("the feature is not authorized for the board; run `feature.py github "
                                          f"project associate {feature_id} --apply`")
            return
        if apply:
            self._check_scope(feature)
        project = self.resolve(report.warnings)
        layout = self.layout(project)
        report.project = {"number": project.number, "id": project.id, "title": project.title, "url": project.url}
        tasks = self.store.load_tasks(feature)
        for task in (None, *tasks.values()):
            self._sync_record(feature, task, project, layout, report, apply)

    def _owned_issue(self, feature: Feature, task: Task | None, report: BoardReport) -> RemoteIssue | None:
        """The record's Issue after the Phase 4 ownership proof (None: skipped or protected)."""
        record_id = task.id if task else feature.id
        ilink = self.issues.load_link(record_id)
        if ilink is None or ilink.issue_number is None or ilink.issue_id is None:
            report.skipped[record_id] = ("from an approved Issue: that Issue is shown as its task's card"
                                         if task is None and load_intake(self.store, feature.id) is not None
                                         else "no Issue yet (the Issue sync creates it first)")
            return None
        issue = self.issues.client.get_issue(ilink.issue_number)
        try:
            self.issues.verify_issue(feature.id, task.id if task else None, issue, ilink)
            if not issue.node_id:
                raise OwnershipError(f"#{issue.number} has no node ID")
        except OwnershipError as exc:
            report.protected[record_id] = str(exc)
            return None
        return issue

    def _sync_record(self, feature: Feature, task: Task | None, project: RemoteProject,  # pylint: disable=too-many-arguments
                     layout: BoardLayout, report: BoardReport, apply: bool) -> None:
        issue = self._owned_issue(feature, task, report)
        if issue is None:
            return
        record_id = task.id if task else feature.id
        try:
            item = self._ensure_item(feature, task, project, issue, report, apply)
        except OwnershipError as exc:
            report.protected[record_id] = str(exc)
            return
        if item is None:
            return
        self._reconcile_values(feature, task, project, layout, issue, item, report, apply)

    def _verify_item(self, item: ProjectItem, project: RemoteProject, issue: RemoteIssue,
                     known_item: str | None) -> None:
        if item.project_id != project.id or item.project_number != project.number:
            raise OwnershipError(f"item {item.id} belongs to project #{item.project_number}, not #{project.number}")
        if item.content_type != "Issue" or item.issue_db_id != issue.id or item.issue_number != issue.number \
                or item.repository != self.config.github.repository or item.issue_node_id != issue.node_id:
            raise OwnershipError(f"item {item.id} does not show Issue #{issue.number} of "
                                 f"{self.config.github.repository}")
        if known_item and item.id != known_item:
            raise OwnershipError(f"#{issue.number} is recorded as item {known_item}, found {item.id}")

    def _record_item(self, feature: Feature, task: Task | None, project: RemoteProject, issue: RemoteIssue,
                     item_id: str) -> None:
        def mapped(link: ProjectLink) -> None:
            if link.item_id and link.item_id != item_id and link.project_id == project.id:
                raise OwnershipError(f"{link.id} is already mapped to item {link.item_id}, not {item_id}")
            link.project_id, link.project_number, link.item_id = project.id, project.number, item_id
            link.issue_number, link.issue_id, link.pending_add = issue.number, issue.id, None
        with self.store.transaction():
            self._update_link(feature, task, mapped)
            self.store.append_event("github-project-item-linked", record=task.id if task else feature.id,
                                    issue=issue.number, item=item_id)

    def _ensure_item(self, feature: Feature, task: Task | None, project: RemoteProject,  # pylint: disable=too-many-arguments
                     issue: RemoteIssue, report: BoardReport, apply: bool) -> ProjectItem | None:
        """The record's item: the recorded one (verified), the Issue's existing item on this board
        (adopted), or a new one (added after a persisted pending-add record)."""
        record_id = task.id if task else feature.id
        link = self.load_link(record_id)
        if link and link.item_id and link.project_id == project.id:
            item = self.board.get_item(link.item_id)
            if item is not None:
                self._verify_item(item, project, issue, link.item_id)
                return item
            report.drift.append(f"#{issue.number}: its board item was removed from project #{project.number}; "
                                "re-added")
            if apply:
                self._update_link(feature, task, lambda l: setattr(l, "item_id", ""))
        refs = self.board.issue_items(self.config.github.repository, issue.number)
        others = sorted({r.project_number for r in refs if r.project_id != project.id})
        if others:
            report.warnings.append(f"#{issue.number} is also on project(s) {others} (never modified)")
        mine = [r for r in refs if r.project_id == project.id]
        if mine:
            item = self.board.get_item(mine[0].id)
            if item is None:
                raise OwnershipError(f"#{issue.number}: item {mine[0].id} disappeared while reading it")
            self._verify_item(item, project, issue, None)
            if not apply:
                report.writes.append(f"would record the existing item {item.id} of #{issue.number}")
                return item
            self._record_item(feature, task, project, issue, item.id)
            report.adopted[record_id] = item.id
            report.writes.append(f"recorded the existing item {item.id} of #{issue.number} (no duplicate added)")
            return item
        if not apply:
            report.writes.append(f"would add #{issue.number} to project #{project.number}")
            return None
        self._update_link(feature, task, lambda l: setattr(l, "pending_add", {"issue": str(issue.number),
                                                                              "started_at": utc_now()}))
        item_id = self.board.add_item(project.id, issue.node_id)
        self._fault(f"crash-after-item-add:{record_id}")
        self._record_item(feature, task, project, issue, item_id)
        report.added[record_id] = item_id
        report.writes.append(f"added #{issue.number} to project #{project.number} as {item_id}")
        # Project workflows (e.g. "Item added to project") act right after an add: let them finish first.
        self.sleep(self.cfg.item_settle_seconds)
        item = self.board.get_item(item_id)
        if item is None:
            raise OwnershipError(f"#{issue.number}: the new item {item_id} is not readable")
        self._verify_item(item, project, issue, item_id)
        return item

    def _write_value(self, project: RemoteProject, layout: BoardLayout, item: ProjectItem,  # pylint: disable=too-many-arguments
                     name: str, value: str) -> None:
        cfg = self.cfg
        target = {cfg.status_field: layout.status, cfg.agent_field: layout.agent,
                  cfg.feature_id_field: layout.feature_id, cfg.blocked_reason_field: layout.blocked_reason}[name]
        if not value:
            self.board.clear_value(project.id, item.id, target.id)
        elif target.data_type == "SINGLE_SELECT":
            option = target.option(value)
            if option is None:
                raise ProjectBoardError(f"option {value!r} of {name!r} is missing; run `project setup --apply`")
            self.board.set_option(project.id, item.id, target.id, option.id)
        else:
            self.board.set_text(project.id, item.id, target.id, value)

    def _reconcile_values(self, feature: Feature, task: Task | None,  # pylint: disable=too-many-arguments
                          project: RemoteProject, layout: BoardLayout, issue: RemoteIssue, item: ProjectItem,
                          report: BoardReport, apply: bool) -> None:
        record_id = task.id if task else feature.id
        desired = self.desired_values(feature, task, report.warnings)
        link = self.load_link(record_id) or ProjectLink(id=record_id, kind="task" if task else "feature",
                                                        feature_id=feature.id)
        current = _values_of(item, desired)
        if link.fields_written:
            changed = {n: v for n, v in current.items() if v != link.fields_written.get(n, "")}
            if changed:
                report.drift.append(f"#{issue.number}: {sorted(changed)} changed on the board ({changed}); the local "
                                    "state is authoritative and the derived values are restored")
        if not apply:
            report.writes += [f"would set {n} = {v or '(empty)'!r} on #{issue.number}"
                              for n, v in desired.items() if current[n] != v]
        else:
            item, current = self._converge((project, layout), issue, item, desired, report)

            def written(l: ProjectLink) -> None:
                l.fields_written, l.last_synced_at = dict(desired), utc_now()
            self._update_link(feature, task, written)
        report.items[record_id] = {"issue": issue.number, "item": item.id, **current, "desired": desired}

    def _converge(self, target: tuple[RemoteProject, BoardLayout], issue: RemoteIssue,  # pylint: disable=too-many-arguments
                  item: ProjectItem, desired: Mapping[str, str],
                  report: BoardReport) -> tuple[ProjectItem, dict[str, str]]:
        """Write the differing values, read them back, and re-apply what a project workflow changed
        meanwhile (bounded by max_field_attempts)."""
        project, layout = target
        current = _values_of(item, desired)
        for attempt in range(self.cfg.max_field_attempts):
            diff = {n: v for n, v in desired.items() if current[n] != v}
            if not diff:
                return item, current
            for name, value in diff.items():
                report.writes.append(f"set {name} = {value or '(empty)'!r} on #{issue.number}")
                self._write_value(project, layout, item, name, value)
            self._fault(f"crash-after-values:{item.id}")
            fresh = self.board.get_item(item.id)
            if fresh is None:
                raise OwnershipError(f"#{issue.number}: item {item.id} disappeared while it was updated")
            item, current = fresh, _values_of(fresh, desired)
            if current != dict(desired):
                report.warnings.append(f"#{issue.number}: a read-back differs from what was written (a project "
                                       f"workflow?); attempt {attempt + 1} of {self.cfg.max_field_attempts}")
                if attempt + 1 < self.cfg.max_field_attempts:
                    self.sleep(self.cfg.item_settle_seconds)
        unstable = sorted(n for n, v in desired.items() if current[n] != v)
        if unstable:
            report.deferred.append(f"#{issue.number}: the board kept changing {unstable} after it was written; "
                                   "the next sync retries")
        return item, current

    # -- reconcile, inspection --------------------------------------------------------------

    def reconcile(self, feature_ids: Iterable[str], *, apply: bool) -> dict[str, Any]:
        """After a restart: sync every listed feature, then classify every item of the board —
        mapped (owned), orphan (an owned Issue with no local record) or foreign. Orphans and
        foreign items are reported, never modified."""
        reports = {fid: self.sync(fid, apply=apply) for fid in feature_ids}
        result: dict[str, Any] = {"features": {fid: r.to_dict() for fid, r in reports.items()}, "orphans": [],
                                  "foreign": [], "duplicates": {}, "mapped": 0}
        try:
            project = self.resolve()
            mapped = {link.item_id for link in self.links() if link.item_id}
            by_issue: dict[int, list[str]] = {}
            for item in self.board.list_items(project.id):
                if item.issue_number is not None:
                    by_issue.setdefault(item.issue_number, []).append(item.id)
                if item.id in mapped:
                    result["mapped"] += 1
                    continue
                result[self._classify(item)].append({"item": item.id, "issue": item.issue_number,
                                                     "status": item.values.get(self.cfg.status_field, "")})
            result["duplicates"] = {n: ids for n, ids in by_issue.items() if len(ids) > 1}
        except (GitHubError, ProjectBoardError) as exc:
            result["error"] = sanitize(exc)
        result["ok"] = (all(r.ok for r in reports.values()) and not result["duplicates"]
                        and "error" not in result)
        return result

    def _classify(self, item: ProjectItem) -> str:
        if item.content_type != "Issue" or item.repository != self.config.github.repository \
                or item.issue_number is None:
            return "foreign"
        try:
            issue = self.issues.client.get_issue(item.issue_number)
        except GitHubError:
            return "foreign"
        parsed = OwnershipMarker.parse(issue.body)
        if parsed is not None and parsed.coordinator == self.issues.coordinator_id \
                and issue.author == self.issues.viewer():
            return "orphans"
        return "foreign"

    def item_status(self, record_id: str) -> dict[str, Any]:
        """One record on the board (read-only): local state, derived values and the item's values."""
        link = self.load_link(record_id)
        try:
            task: Task | None = self.store.load_task(record_id)
            feature = self.store.load_feature(task.feature_id)  # type: ignore[union-attr]
        except KeyError:
            task, feature = None, self.store.load_feature(record_id)
        notes: list[str] = []
        desired = self.desired_values(feature, task, notes)
        data: dict[str, Any] = {"record": record_id, "local_state": (task or feature).state.value,
                                "issue": (task or feature).issue_number, "authorized": self.authorized(feature.id),
                                "desired": desired, "notes": notes, "item": link.item_id if link else "",
                                "board": None}
        if link and link.item_id:
            item = self.board.get_item(link.item_id)
            data["board"] = None if item is None else {k: item.values.get(k, "") for k in desired}
            data["in_sync"] = data["board"] == desired
        return data


def _values_of(item: ProjectItem, names: Iterable[str]) -> dict[str, str]:
    return {name: item.values.get(name, "") for name in names}


def astuple_option(option: FieldOption) -> tuple[str, str, str, str]:
    """Comparable form of an option."""
    return option.id, option.name, option.color, option.description
