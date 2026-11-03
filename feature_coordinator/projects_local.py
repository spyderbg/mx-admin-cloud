"""A local GitHub Projects board (Phase 4 extension): tests, the demo and `--tracker local`.

`LocalProjectBoard` implements the same BoardClient protocol and the same write guard as the
real GraphQL client (github_projects.py), backed by a dict that can be persisted to a JSON
file under an flock (so a restarted process sees the same board). It resolves Issues through
the local Issue tracker by their node ID, the way GitHub resolves `contentId`.

For tests it can also play GitHub's side:
  * `fail` — injected failures, consumed in order: (operation, exception) raises *before*
    the operation; (operation + "!", exception) applies it and *then* raises, like a write
    whose response was lost;
  * `hooks` — callables run after each mutation (e.g. a built-in project workflow that sets
    Status when an item is added);
  * human edits — `set_by_human`, `add_foreign_item`, `remove_item`, `archive_item`.

A protected project is a tripwire: any mutation that reaches it raises, even if a caller
bypassed the guard.
"""

from __future__ import annotations

import copy
import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .config import GitHubProjectConfig
from .errors import GitHubNotFoundError, GitHubValidationError, ProjectBoardError
from .github_projects import BoardGuard, FieldOption, ItemRef, ProjectField, ProjectItem, ProjectView, \
    ProjectWorkflow, RemoteProject
from .issues import RemoteIssue

DEFAULT_WORKFLOWS = ("Item closed", "Pull request merged", "Auto-close issue", "Auto-add sub-issues to project",
                     "Pull request linked to issue", "Item added to project")
_BUILTIN_FIELDS = (("Title", "TITLE"), ("Assignees", "ASSIGNEES"), ("Labels", "LABELS"),
                   ("Linked pull requests", "LINKED_PULL_REQUESTS"), ("Repository", "REPOSITORY"))


class LocalProjectBoard(BoardGuard):  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """A JSON-file GitHub Projects board. See the module docstring."""

    live = False

    def __init__(self, issues: Callable[[], Iterable[RemoteIssue]], *, repository: str,
                 path: Path | None = None) -> None:
        super().__init__()
        self._issues = issues
        self.repository = repository
        self.path = path
        self.state: dict[str, Any] = {"projects": {}, "next": 1}
        self.calls: list[str] = []
        self.requests = 0
        self.writes = 0
        self.fail: list[tuple[str, BaseException]] = []
        self.hooks: list[Callable[["LocalProjectBoard", str, dict[str, Any]], None]] = []
        if path is not None and path.exists():
            self._load()

    # -- persistence ------------------------------------------------------------------

    def _load(self) -> None:
        assert self.path is not None
        self.state = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        if self.path is None:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    @contextmanager
    def _op(self, name: str, *, write: bool) -> Iterator[None]:
        self.calls.append(name)
        self.requests += 1
        late = None
        for index, (operation, exc) in enumerate(self.fail):
            if operation == name:
                del self.fail[index]
                raise exc
            if operation == name + "!":
                del self.fail[index]
                late = exc
                break
        handle = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path.with_suffix(".lock"), "a+", encoding="utf-8")  # pylint: disable=consider-using-with
            fcntl.flock(handle, fcntl.LOCK_EX)
            if self.path.exists():
                self._load()
        try:
            yield
            if write:
                self.writes += 1
                self._save()
        finally:
            if handle is not None:
                handle.close()
        if late is not None:
            raise late

    def _new_id(self, prefix: str) -> str:
        number = int(self.state["next"])
        self.state["next"] = number + 1
        return f"{prefix}_local{number:04d}"

    def _option_id(self) -> str:
        number = int(self.state["next"])
        self.state["next"] = number + 1
        return f"{number:08x}"

    # -- seeding -------------------------------------------------------------------------

    def add_project(self, *, node_id: str, number: int, title: str, owner: str,  # pylint: disable=too-many-arguments
                    status_options: Iterable[str] = ("Todo", "In Progress", "Done"),
                    with_board_view: bool = True) -> str:
        """Create a project the way GitHub creates a new one (default fields, Todo/In Progress/Done,
        a table view and optionally a board grouped by Status, the default workflows enabled)."""
        fields = [{"id": self._new_id("PVTF"), "name": name, "dataType": kind, "options": []}
                  for name, kind in _BUILTIN_FIELDS]
        status = {"id": self._new_id("PVTSSF"), "name": "Status", "dataType": "SINGLE_SELECT",
                  "options": [{"id": self._option_id(), "name": n, "color": "GRAY", "description": ""}
                              for n in status_options]}
        fields.insert(2, status)
        visible = [fields[0]["id"], fields[1]["id"], status["id"]]
        views = [{"id": self._new_id("PVTV"), "number": 1, "name": "View 1", "layout": "TABLE_LAYOUT", "filter": "",
                  "group_by": [], "visible": visible}]
        if with_board_view:
            views.append({"id": self._new_id("PVTV"), "number": 2, "name": "View 2", "layout": "BOARD_LAYOUT",
                          "filter": "", "group_by": [status["id"]], "visible": list(visible)})
        self.state["projects"][node_id] = {
            "id": node_id, "number": number, "title": title, "owner": owner, "closed": False, "fields": fields,
            "views": views, "items": {}, "updated": 0,
            "workflows": [{"number": i + 1, "name": n, "enabled": True} for i, n in enumerate(DEFAULT_WORKFLOWS)]}
        self._save()
        return node_id

    @classmethod
    def for_config(cls, project: GitHubProjectConfig, issues: Callable[[], Iterable[RemoteIssue]], *,  # pylint: disable=too-many-arguments
                   repository: str, path: Path | None = None, configured: bool = False) -> "LocalProjectBoard":
        """A board holding the configured project — as GitHub creates it, or *configured* as
        `python -m feature_coordinator github project setup --apply` leaves it — and every protected project (each
        with one foreign item), unless *path* already holds a board."""
        board = cls(issues, repository=repository, path=path)
        if not board.state["projects"]:
            board.add_project(node_id=project.node_id, number=project.number, title=project.title or "Board",
                              owner=project.owner)
            if configured:
                board.configure(project)
            for number in project.protected_numbers:
                pid = board.add_project(node_id=f"PVT_protected{number}", number=number,
                                        title=f"protected #{number}", owner=project.owner, with_board_view=False)
                proj = board.state["projects"][pid]
                item = board._new_id("PVTI")  # pylint: disable=protected-access
                proj["items"][item] = {"id": item, "content": None, "archived": False,
                                       "values": {"Status": proj["fields"][2]["options"][0]["id"]}}
            board._save()  # pylint: disable=protected-access
        return board

    def configure(self, project: GitHubProjectConfig) -> None:
        """Give the configured project the fields, options and views that `setup` creates (the
        reused "Todo" option keeps its ID, as on GitHub)."""
        from .project_sync import active_filter  # pylint: disable=import-outside-toplevel
        proj = self.state["projects"][project.node_id]
        status = next(f for f in proj["fields"] if f["name"] == project.status_field)
        reuse = {key: name for name, key in project.status_reuse.items()}
        options = []
        for key, name in project.status_names.items():
            old = next((o for o in status["options"] if o["name"] in (name, reuse.get(key))), None)
            options.append({"id": old["id"] if old else self._option_id(), "name": name, "color": "GRAY",
                            "description": ""})
        status["options"] = options
        added = [{"id": self._new_id("PVTSSF"), "name": project.agent_field, "dataType": "SINGLE_SELECT",
                  "options": [{"id": self._option_id(), "name": n, "color": "GRAY", "description": ""}
                              for n in project.agent_names.values()]},
                 {"id": self._new_id("PVTF"), "name": project.feature_id_field, "dataType": "TEXT", "options": []},
                 {"id": self._new_id("PVTF"), "name": project.blocked_reason_field, "dataType": "TEXT", "options": []}]
        proj["fields"] += added
        board = next(v for v in proj["views"] if v["layout"] == "BOARD_LAYOUT")
        board["name"] = project.board_view
        board["visible"] += [f["id"] for f in added]
        proj["views"].append({"id": self._new_id("PVTV"), "number": len(proj["views"]) + 1, "name": project.active_view,
                              "layout": "BOARD_LAYOUT", "group_by": [status["id"]], "visible": list(board["visible"]),
                              "filter": active_filter(project)})
        self._save()

    # -- lookups ---------------------------------------------------------------------

    def _project(self, project_id: str) -> dict[str, Any]:
        try:
            return self.state["projects"][project_id]
        except KeyError as exc:
            raise GitHubNotFoundError(f"project {project_id} not found", status=404) from exc

    def _tripwire(self, project: dict[str, Any]) -> None:
        if project["id"] in self.protected_ids:
            raise ProjectBoardError(f"TRIPWIRE: a mutation reached protected project #{project['number']}")
        project["updated"] = int(project.get("updated", 0)) + 1

    def _issue_by_node(self, node_id: str) -> RemoteIssue:
        for issue in self._issues():
            if issue.node_id == node_id:
                return issue
        raise GitHubNotFoundError(f"content {node_id} not found", status=404)

    def _issue_by_number(self, number: int) -> RemoteIssue | None:
        return next((i for i in self._issues() if i.number == number), None)

    def _find_item(self, item_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for project in self.state["projects"].values():
            if item_id in project["items"]:
                return project, project["items"][item_id]
        return None

    @staticmethod
    def _field(project: dict[str, Any], field_id: str) -> dict[str, Any]:
        for spec in project["fields"]:
            if spec["id"] == field_id:
                return spec
        raise GitHubNotFoundError(f"field {field_id} not found", status=404)

    @staticmethod
    def _as_field(spec: dict[str, Any]) -> ProjectField:
        return ProjectField(spec["id"], spec["name"], spec["dataType"],
                            tuple(FieldOption(o["id"], o["name"], o["color"], o["description"])
                                  for o in spec["options"]))

    @staticmethod
    def _as_view(spec: dict[str, Any]) -> ProjectView:
        return ProjectView(spec["id"], spec["number"], spec["name"], spec["layout"], spec["filter"],
                           tuple(spec["group_by"]), tuple(spec["visible"]))

    def _as_item(self, project: dict[str, Any], item: dict[str, Any]) -> ProjectItem:
        values, option_ids = {}, {}
        for spec in project["fields"]:
            raw = item["values"].get(spec["id"], item["values"].get(spec["name"]))
            if raw is None:
                continue
            if spec["dataType"] == "SINGLE_SELECT":
                option = next((o for o in spec["options"] if o["id"] == raw), None)
                if option is not None:
                    option_ids[spec["name"]], values[spec["name"]] = raw, option["name"]
            else:
                values[spec["name"]] = raw
        content: dict[str, Any] = {}
        if item.get("content"):
            issue = self._issue_by_node(item["content"])
            content = {"issue_node_id": issue.node_id, "issue_number": issue.number, "issue_db_id": issue.id,
                       "repository": self.repository}
        return ProjectItem(id=item["id"], project_id=project["id"], project_number=project["number"],
                           archived=item.get("archived", False),
                           content_type="Issue" if content else "DraftIssue", values=values, option_ids=option_ids,
                           **content)

    # -- BoardClient: reads ------------------------------------------------------------

    def project(self, owner: str, owner_type: str, number: int) -> RemoteProject | None:
        """The project (as configured on this local board)."""
        del owner_type
        with self._op(f"project:{number}", write=False):
            for proj in self.state["projects"].values():
                if proj["owner"] == owner and proj["number"] == number:
                    return RemoteProject(
                        id=proj["id"], number=proj["number"], title=proj["title"], owner=proj["owner"],
                        closed=proj["closed"], url=f"local://projects/{number}", viewer_can_update=True,
                        updated_at=str(proj.get("updated", 0)), items_total=len(proj["items"]),
                        fields=tuple(self._as_field(f) for f in proj["fields"]),
                        views=tuple(self._as_view(v) for v in proj["views"]),
                        workflows=tuple(ProjectWorkflow(w["number"], w["name"], w["enabled"])
                                        for w in proj["workflows"]))
            return None

    def issue_items(self, repository: str, number: int) -> list[ItemRef]:
        """Items of one Issue in every project."""
        del repository
        with self._op(f"issue_items:{number}", write=False):
            issue = self._issue_by_number(number)
            if issue is None:
                raise GitHubNotFoundError(f"issue #{number} not found", status=404)
            refs = [ItemRef(item["id"], proj["id"], proj["number"], item.get("archived", False))
                    for proj in self.state["projects"].values() for item in proj["items"].values()
                    if item.get("content") == issue.node_id]
        for ref in refs:
            self._remember(ref.project_id, ref.id, item=True)
        return refs

    def get_item(self, item_id: str) -> ProjectItem | None:
        """One item, or None."""
        with self._op(f"get_item:{item_id}", write=False):
            found = self._find_item(item_id)
            if found is None:
                return None
            item = self._as_item(*found)
        self._remember(item.project_id, item.id, item=True)
        return item

    def list_items(self, project_id: str) -> list[ProjectItem]:
        """Every item of one project."""
        with self._op(f"list_items:{project_id}", write=False):
            proj = self._project(project_id)
            items = [self._as_item(proj, item) for item in proj["items"].values()]
        for item in items:
            self._remember(item.project_id, item.id, item=True)
        return items

    # -- BoardClient: writes ----------------------------------------------------------

    def _after(self, op: str, **args: Any) -> None:
        for hook in list(self.hooks):
            hook(self, op, args)

    def add_item(self, project_id: str, content_id: str) -> str:
        """Add an Issue; an Issue already on the board returns its existing item (as GitHub does)."""
        self.check(project_id=project_id)
        with self._op("add_item", write=True):
            proj = self._project(project_id)
            self._tripwire(proj)
            self._issue_by_node(content_id)
            existing = next((i for i in proj["items"].values() if i.get("content") == content_id), None)
            if existing is None:
                existing = {"id": self._new_id("PVTI"), "content": content_id, "archived": False, "values": {}}
                proj["items"][existing["id"]] = existing
        self._remember(project_id, existing["id"], item=True)
        self._after("add_item", project_id=project_id, item_id=existing["id"])
        return existing["id"]

    def _write_value(self, op: str, project_id: str, item_id: str, field_id: str, value: str | None) -> None:
        self.check(project_id=project_id, node_id=field_id, item_id=item_id)
        with self._op(op, write=True):
            proj = self._project(project_id)
            self._tripwire(proj)
            spec = self._field(proj, field_id)
            if item_id not in proj["items"]:
                raise GitHubNotFoundError(f"item {item_id} not found", status=404)
            if value is not None and spec["dataType"] == "SINGLE_SELECT" \
                    and value not in {o["id"] for o in spec["options"]}:
                raise GitHubValidationError(f"option {value} is not an option of {spec['name']}", status=422)
            values = proj["items"][item_id]["values"]
            if value is None:
                values.pop(field_id, None)
            else:
                values[field_id] = value
        self._after(op, project_id=project_id, item_id=item_id, field_id=field_id, value=value)

    def set_text(self, project_id: str, item_id: str, field_id: str, text: str) -> None:
        """Set a text value."""
        self._write_value("set_text", project_id, item_id, field_id, text)

    def set_option(self, project_id: str, item_id: str, field_id: str, option_id: str) -> None:
        """Select an option."""
        self._write_value("set_option", project_id, item_id, field_id, option_id)

    def clear_value(self, project_id: str, item_id: str, field_id: str) -> None:
        """Clear a value."""
        self._write_value("clear_value", project_id, item_id, field_id, None)

    def create_field(self, project_id: str, name: str, data_type: str,
                     options: Iterable[FieldOption] = ()) -> ProjectField:
        """Create a custom field (a duplicate name is rejected, as on GitHub)."""
        self.check(project_id=project_id)
        with self._op("create_field", write=True):
            proj = self._project(project_id)
            self._tripwire(proj)
            if any(f["name"] == name for f in proj["fields"]):
                raise GitHubValidationError(f"Name has already been taken: {name}", status=422)
            spec = {"id": self._new_id("PVTSSF" if data_type == "SINGLE_SELECT" else "PVTF"), "name": name,
                    "dataType": data_type,
                    "options": [{"id": self._option_id(), "name": o.name, "color": o.color,
                                 "description": o.description} for o in options]}
            proj["fields"].append(spec)
        self._remember(project_id, spec["id"], item=False)
        return self._as_field(spec)

    def update_options(self, field_id: str, options: Iterable[FieldOption]) -> ProjectField:
        """Replace the options; an option without its ID gets a new identity and loses its item values."""
        self.check(node_id=field_id)
        with self._op("update_options", write=True):
            proj = next(p for p in self.state["projects"].values() if any(f["id"] == field_id for f in p["fields"]))
            self._tripwire(proj)
            spec = self._field(proj, field_id)
            known = {o["id"] for o in spec["options"]}
            spec["options"] = [{"id": o.id if o.id in known else self._option_id(), "name": o.name, "color": o.color,
                                "description": o.description} for o in options]
            kept = {o["id"] for o in spec["options"]}
            for item in proj["items"].values():
                if item["values"].get(field_id) not in kept:
                    item["values"].pop(field_id, None)
        return self._as_field(spec)

    def create_view(self, project_id: str, name: str, layout: str) -> ProjectView:
        """Create a view; a board groups by Status, as GitHub's default board does."""
        self.check(project_id=project_id)
        with self._op("create_view", write=True):
            proj = self._project(project_id)
            self._tripwire(proj)
            status = next(f for f in proj["fields"] if f["name"] == "Status")
            spec = {"id": self._new_id("PVTV"), "number": max([v["number"] for v in proj["views"]] or [0]) + 1,
                    "name": name, "layout": layout, "filter": "",
                    "group_by": [status["id"]] if layout == "BOARD_LAYOUT" else [],
                    "visible": [proj["fields"][0]["id"], status["id"]]}
            proj["views"].append(spec)
        self._remember(project_id, spec["id"], item=False)
        return self._as_view(spec)

    def update_view(self, view_id: str, *, name: str | None = None, filter_text: str | None = None,
                    visible_field_ids: Iterable[str] | None = None) -> ProjectView:
        """Rename, filter, choose visible fields."""
        self.check(node_id=view_id)
        ids = list(visible_field_ids) if visible_field_ids is not None else None
        for field_id in ids or ():
            self.check(node_id=field_id)
        with self._op("update_view", write=True):
            proj = next(p for p in self.state["projects"].values() if any(v["id"] == view_id for v in p["views"]))
            self._tripwire(proj)
            spec = next(v for v in proj["views"] if v["id"] == view_id)
            if name is not None:
                spec["name"] = name
            if filter_text is not None:
                spec["filter"] = filter_text
            if ids is not None:
                spec["visible"] = ids
        return self._as_view(spec)

    # -- simulated humans and GitHub (tests) ------------------------------------------------

    def set_by_human(self, item_id: str, field_name: str, value: str | None) -> None:
        """A person edits a field on the board (option NAME for single-select fields)."""
        with self._op("human_edit", write=True):
            found = self._find_item(item_id)
            assert found is not None, item_id
            proj, item = found
            spec = next(f for f in proj["fields"] if f["name"] == field_name)
            if value is None:
                item["values"].pop(spec["id"], None)
            elif spec["dataType"] == "SINGLE_SELECT":
                item["values"][spec["id"]] = next(o["id"] for o in spec["options"] if o["name"] == value)
            else:
                item["values"][spec["id"]] = value

    def add_foreign_item(self, project_id: str, issue: RemoteIssue | None = None) -> str:
        """A person adds an Issue (or a draft) the coordinator does not own."""
        with self._op("human_add", write=True):
            proj = self._project(project_id)
            item = {"id": self._new_id("PVTI"), "content": issue.node_id if issue else None, "archived": False,
                    "values": {}}
            proj["items"][item["id"]] = item
            return item["id"]

    def remove_item(self, item_id: str) -> None:
        """A person deletes an item from the board."""
        with self._op("human_remove", write=True):
            found = self._find_item(item_id)
            assert found is not None
            del found[0]["items"][item_id]

    def archive_item(self, item_id: str) -> None:
        """A person archives an item."""
        with self._op("human_archive", write=True):
            found = self._find_item(item_id)
            assert found is not None
            found[1]["archived"] = True

    def snapshot(self, project_id: str) -> dict[str, Any]:
        """A deep copy of one project (to prove it was not changed)."""
        with self._op(f"snapshot:{project_id}", write=False):
            return copy.deepcopy(self._project(project_id))
