"""GitHub Projects (v2) board client (Phase 4 extension): GraphQL through the authenticated `gh`.

The board is a *view* of the coordinator's Issues. This module only reads and writes the
board; it decides nothing (project_sync.py does). Two implementations share one protocol:

  * ProjectsClient   — GraphQL over GitHubClient.graphql(): the same `gh api` transport,
                       retries, pacing and rate-limit rules as the Issue adapter.
  * LocalProjectBoard — a JSON-file board for tests, the demo and `--tracker local`
                       (projects_local.py).

Write guard (both implementations, `BoardGuard`): nothing is written before `bind()` was
called with the configured project, after its identity (owner, number, node ID) was
verified. Afterwards a mutation is refused unless its project ID is that project, its field
or view ID was read from that project, and its item ID was seen in that project. Protected
projects (for mx-admin-cloud: Project #1) are refused by node ID even if a caller passed
one. Every response is validated: GitHub data is untrusted input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol

from .errors import GitHubNotFoundError, GitHubResponseError, ProjectBoardError
from .github_api import GitHubClient, sanitize

_NODE_RE = re.compile(r"^[A-Za-z0-9_=-]{2,120}$")
_OPTION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
LAYOUTS = ("BOARD_LAYOUT", "TABLE_LAYOUT", "ROADMAP_LAYOUT")
COLORS = ("GRAY", "BLUE", "GREEN", "YELLOW", "ORANGE", "RED", "PINK", "PURPLE")
MAX_FIELDS = 50


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldOption:
    """One single-select option. `id` is empty for an option that does not exist yet."""

    id: str
    name: str
    color: str = "GRAY"
    description: str = ""

    def as_input(self) -> dict[str, str]:
        """GraphQL ProjectV2SingleSelectFieldOptionInput (the ID keeps the option's identity)."""
        data = {"name": self.name, "color": self.color, "description": self.description}
        return {"id": self.id, **data} if self.id else data


@dataclass(frozen=True)
class ProjectField:
    """A project field (custom or built-in)."""

    id: str
    name: str
    data_type: str
    options: tuple[FieldOption, ...] = ()

    def option(self, name: str) -> FieldOption | None:
        """The option called *name*."""
        return next((o for o in self.options if o.name == name), None)


@dataclass(frozen=True)
class ProjectView:  # pylint: disable=too-many-instance-attributes
    """A project view: layout, filter, the field that forms the board columns, visible fields."""

    id: str
    number: int
    name: str
    layout: str
    filter: str = ""
    group_by: tuple[str, ...] = ()    # field IDs (vertical grouping = board columns)
    visible: tuple[str, ...] = ()     # visible field IDs, in order


@dataclass(frozen=True)
class ProjectWorkflow:
    """A built-in project workflow (read-only through the API)."""

    number: int
    name: str
    enabled: bool


@dataclass(frozen=True)
class RemoteProject:  # pylint: disable=too-many-instance-attributes
    """A project and its configuration."""

    id: str
    number: int
    title: str
    owner: str
    closed: bool = False
    url: str = ""
    viewer_can_update: bool = False
    updated_at: str = ""
    items_total: int = 0
    fields: tuple[ProjectField, ...] = ()
    views: tuple[ProjectView, ...] = ()
    workflows: tuple[ProjectWorkflow, ...] = ()

    def field(self, name: str) -> ProjectField | None:
        """The field called *name*."""
        return next((f for f in self.fields if f.name == name), None)

    def view(self, name: str) -> ProjectView | None:
        """The first view called *name*."""
        return next((v for v in self.views if v.name == name), None)


@dataclass(frozen=True)
class ItemRef:
    """An Issue's membership in one project."""

    id: str
    project_id: str
    project_number: int
    archived: bool = False


@dataclass(frozen=True)
class ProjectItem:  # pylint: disable=too-many-instance-attributes
    """A project item with its content identity and its field values (by field name)."""

    id: str
    project_id: str
    project_number: int
    archived: bool
    content_type: str                  # Issue | PullRequest | DraftIssue
    issue_node_id: str = ""
    issue_number: int | None = None
    issue_db_id: int | None = None
    repository: str = ""
    values: Mapping[str, str] = field(default_factory=dict)      # text, or the option name
    option_ids: Mapping[str, str] = field(default_factory=dict)  # single-select field → option ID


class BoardClient(Protocol):
    """What project_sync.py needs from a board (ProjectsClient or LocalProjectBoard)."""

    live: bool

    def project(self, owner: str, owner_type: str, number: int) -> RemoteProject | None:
        """The project (with fields, views and workflows), or None if it does not exist."""

    def bind(self, project: RemoteProject, protected_ids: Iterable[str] = ()) -> None:
        """Allow writes to *project* only (after its identity was verified)."""

    def issue_items(self, repository: str, number: int) -> list[ItemRef]:
        """The project items of one Issue, in every project (including archived ones)."""

    def get_item(self, item_id: str) -> ProjectItem | None:
        """One item, or None if it no longer exists."""

    def list_items(self, project_id: str) -> list[ProjectItem]:
        """Every item of a project (all pages or an error)."""

    def add_item(self, project_id: str, content_id: str) -> str:
        """Add an Issue (by node ID) to the bound project; returns the item ID."""

    def set_text(self, project_id: str, item_id: str, field_id: str, text: str) -> None:
        """Set a text field value."""

    def set_option(self, project_id: str, item_id: str, field_id: str, option_id: str) -> None:
        """Set a single-select field value."""

    def clear_value(self, project_id: str, item_id: str, field_id: str) -> None:
        """Clear a field value."""

    def create_field(self, project_id: str, name: str, data_type: str,
                     options: Iterable[FieldOption] = ()) -> ProjectField:
        """Create a custom field (TEXT or SINGLE_SELECT)."""

    def update_options(self, field_id: str, options: Iterable[FieldOption]) -> ProjectField:
        """Replace a single-select field's options; options with an ID keep their identity."""

    def create_view(self, project_id: str, name: str, layout: str) -> ProjectView:
        """Create a view."""

    def update_view(self, view_id: str, *, name: str | None = None, filter_text: str | None = None,
                    visible_field_ids: Iterable[str] | None = None) -> ProjectView:
        """Rename a view, set its filter and/or its visible fields."""


class BoardGuard:
    """The write guard shared by both board implementations (see the module docstring)."""

    def __init__(self) -> None:
        self.bound_project: str | None = None
        self.protected_ids: set[str] = set()
        self._nodes: set[str] = set()   # fields and views of the bound project
        self._items: set[str] = set()   # items seen in the bound project

    def bind(self, project: RemoteProject, protected_ids: Iterable[str] = ()) -> None:
        """Allow writes to *project* only."""
        self.protected_ids = set(protected_ids)
        if project.id in self.protected_ids:
            raise ProjectBoardError(f"project #{project.number} is protected; it is never written")
        self.bound_project = project.id
        self._nodes = {f.id for f in project.fields} | {v.id for v in project.views}

    def _remember(self, project_id: str, node_id: str, *, item: bool) -> None:
        if project_id == self.bound_project and project_id not in self.protected_ids:
            (self._items if item else self._nodes).add(node_id)

    def check(self, *, project_id: str | None = None, node_id: str | None = None,
              item_id: str | None = None) -> None:
        """Raise ProjectBoardError unless the write stays inside the bound project."""
        if self.bound_project is None:
            raise ProjectBoardError("no project is bound: its identity was not verified, nothing is written")
        if project_id is not None and (project_id != self.bound_project or project_id in self.protected_ids):
            raise ProjectBoardError(f"refusing to write project {sanitize(project_id, 60)}: only the configured "
                                    "project may be written")
        if node_id is not None and node_id not in self._nodes:
            raise ProjectBoardError(f"refusing to change {sanitize(node_id, 60)}: not a field or view read from "
                                    "the configured project")
        if item_id is not None and item_id not in self._items:
            raise ProjectBoardError(f"refusing to change item {sanitize(item_id, 60)}: not seen in the configured "
                                    "project")


# ---------------------------------------------------------------------------
# Validation of GraphQL data (untrusted)
# ---------------------------------------------------------------------------

def _obj(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GitHubResponseError(f"{where}: not an object")
    return value


def _node(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _NODE_RE.fullmatch(value):
        raise GitHubResponseError(f"{where}: invalid node ID")
    return value


def _text(value: Any, where: str, limit: int = 1024, *, empty: bool = True) -> str:
    if value is None and empty:
        return ""
    if not isinstance(value, str) or len(value) > limit or not empty and not value:
        raise GitHubResponseError(f"{where}: invalid text")
    return value


def _int(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GitHubResponseError(f"{where}: invalid number")
    return value


def _nodes(connection: Any, where: str, *, complete: bool = True) -> list[Any]:
    conn = _obj(connection, where)
    if complete and (conn.get("pageInfo") or {}).get("hasNextPage"):
        raise GitHubResponseError(f"{where}: more than one page; refusing a partial result")
    nodes = conn.get("nodes")
    if not isinstance(nodes, list):
        raise GitHubResponseError(f"{where}: nodes missing")
    return [n for n in nodes if n is not None]


def parse_field(data: Any) -> ProjectField:
    """A validated ProjectField."""
    data = _obj(data, "field")
    field_id = _node(data.get("id"), "field")
    where = f"field {field_id}"
    options = []
    for raw in data.get("options") or ():
        raw = _obj(raw, where)
        option_id = raw.get("id")
        if not isinstance(option_id, str) or not _OPTION_RE.fullmatch(option_id):
            raise GitHubResponseError(f"{where}: invalid option ID")
        color = raw.get("color") or "GRAY"
        options.append(FieldOption(option_id, _text(raw.get("name"), where, 100, empty=False),
                                   color if color in COLORS else "GRAY", _text(raw.get("description"), where)))
    return ProjectField(field_id, _text(data.get("name"), where, 100, empty=False),
                        _text(data.get("dataType"), where, 40, empty=False), tuple(options))


def parse_view(data: Any) -> ProjectView:
    """A validated ProjectView."""
    data = _obj(data, "view")
    view_id = _node(data.get("id"), "view")
    where = f"view {view_id}"
    layout = data.get("layout")
    if layout not in LAYOUTS:
        raise GitHubResponseError(f"{where}: unexpected layout")
    groups = [_node(n.get("id"), where) for n in _nodes(data.get("verticalGroupByFields") or {"nodes": []}, where)
              if isinstance(n, dict) and n.get("id")]
    visible = [_node(n.get("id"), where) for n in _nodes(data.get("fields") or {"nodes": []}, where)
               if isinstance(n, dict) and n.get("id")]
    return ProjectView(view_id, _int(data.get("number"), where), _text(data.get("name"), where, 100, empty=False),
                       layout, _text(data.get("filter"), where), tuple(groups), tuple(visible))


def parse_project(data: Any) -> RemoteProject:
    """A validated RemoteProject (fields, views and workflows must each fit in one page)."""
    data = _obj(data, "project")
    project_id = _node(data.get("id"), "project")
    where = f"project {project_id}"
    owner = _obj(data.get("owner"), where).get("login")
    url = _text(data.get("url"), where, 300)
    if url and not url.startswith("https://"):
        raise GitHubResponseError(f"{where}: unexpected url")
    workflows = [ProjectWorkflow(_int(w.get("number"), where), _text(w.get("name"), where, 100),
                                 bool(w.get("enabled"))) for w in _nodes(data.get("workflows"), where)]
    return RemoteProject(
        id=project_id, number=_int(data.get("number"), where), title=_text(data.get("title"), where, 300),
        owner=_text(owner, where, 39, empty=False), closed=bool(data.get("closed")), url=url,
        viewer_can_update=data.get("viewerCanUpdate") is True, updated_at=_text(data.get("updatedAt"), where, 40),
        items_total=_int(_obj(data.get("items"), where).get("totalCount"), where),
        fields=tuple(parse_field(f) for f in _nodes(data.get("fields"), where) if isinstance(f, dict) and f.get("id")),
        views=tuple(parse_view(v) for v in _nodes(data.get("views"), where)),
        workflows=tuple(workflows))


def parse_item(data: Any) -> ProjectItem:
    """A validated ProjectItem."""
    data = _obj(data, "item")
    item_id = _node(data.get("id"), "item")
    where = f"item {item_id}"
    project = _obj(data.get("project"), where)
    content = data.get("content") or {}
    kind = content.get("__typename", "DraftIssue") if isinstance(content, dict) else "DraftIssue"
    issue: dict[str, Any] = {}
    if kind in ("Issue", "PullRequest"):
        repo = _text(_obj(content.get("repository"), where).get("nameWithOwner"), where, 141, empty=False)
        if not _REPO_RE.fullmatch(repo):
            raise GitHubResponseError(f"{where}: invalid repository")
        issue = {"issue_node_id": _node(content.get("id"), where), "issue_number": _int(content.get("number"), where),
                 "issue_db_id": _int(content.get("databaseId"), where), "repository": repo}
    values: dict[str, str] = {}
    option_ids: dict[str, str] = {}
    for value in _nodes(data.get("fieldValues"), where):
        if not isinstance(value, dict) or not isinstance(value.get("field"), dict):
            continue
        name = value["field"].get("name")
        if not isinstance(name, str):
            continue
        if value.get("__typename") == "ProjectV2ItemFieldSingleSelectValue":
            option_id = value.get("optionId")
            if not isinstance(option_id, str) or not _OPTION_RE.fullmatch(option_id):
                raise GitHubResponseError(f"{where}: invalid option ID")
            option_ids[name] = option_id
            values[name] = _text(value.get("name"), where, 100)
        elif value.get("__typename") == "ProjectV2ItemFieldTextValue":
            values[name] = _text(value.get("text"), where, 4096)
    return ProjectItem(id=item_id, project_id=_node(project.get("id"), where),
                       project_number=_int(project.get("number"), where), archived=bool(data.get("isArchived")),
                       content_type=kind, values=values, option_ids=option_ids, **issue)


# ---------------------------------------------------------------------------
# GraphQL documents (each operation has its own name; tests dispatch on it)
# ---------------------------------------------------------------------------

_FIELD_PARTS = """
fragment FieldParts on ProjectV2FieldConfiguration {
  __typename
  ... on ProjectV2FieldCommon { id name dataType }
  ... on ProjectV2SingleSelectField { options { id name color description } }
}"""
_VIEW_PARTS = """
fragment ViewParts on ProjectV2View {
  id number name layout filter
  verticalGroupByFields(first: 5) { nodes { ... on ProjectV2FieldCommon { id } } }
  fields(first: 50) { pageInfo { hasNextPage } nodes { ... on ProjectV2FieldCommon { id } } }
}"""
_ITEM_PARTS = """
fragment ItemParts on ProjectV2Item {
  id isArchived type
  project { id number }
  content {
    __typename
    ... on Issue { id databaseId number repository { nameWithOwner } }
    ... on PullRequest { id databaseId number repository { nameWithOwner } }
  }
  fieldValues(first: 50) {
    pageInfo { hasNextPage }
    nodes {
      __typename
      ... on ProjectV2ItemFieldSingleSelectValue { optionId name field { ... on ProjectV2FieldCommon { id name } } }
      ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { id name } } }
    }
  }
}"""
PROJECT_QUERY = """
query FcProject($owner: String!, $number: Int!) {
  owner: %s(login: $owner) {
    projectV2(number: $number) {
      id number title closed url updatedAt viewerCanUpdate
      owner { ... on User { login } ... on Organization { login } }
      items(first: 1) { totalCount }
      fields(first: 50) { pageInfo { hasNextPage } nodes { ...FieldParts } }
      views(first: 50) { pageInfo { hasNextPage } nodes { ...ViewParts } }
      workflows(first: 50) { pageInfo { hasNextPage } nodes { number name enabled } }
    }
  }
}""" + _FIELD_PARTS + _VIEW_PARTS
ISSUE_ITEMS_QUERY = """
query FcIssueItems($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      id number
      projectItems(first: 50, includeArchived: true) {
        pageInfo { hasNextPage }
        nodes { id isArchived project { id number } }
      }
    }
  }
}"""
ITEM_QUERY = """
query FcItem($id: ID!) {
  node(id: $id) { __typename ...ItemParts }
}""" + _ITEM_PARTS
ITEMS_QUERY = """
query FcItems($id: ID!, $after: String) {
  node(id: $id) {
    ... on ProjectV2 { items(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes { ...ItemParts } } }
  }
}""" + _ITEM_PARTS
ADD_ITEM = """
mutation FcAddItem($input: AddProjectV2ItemByIdInput!) {
  addProjectV2ItemById(input: $input) { item { id project { id number } } }
}"""
SET_VALUE = """
mutation FcSetValue($input: UpdateProjectV2ItemFieldValueInput!) {
  updateProjectV2ItemFieldValue(input: $input) { projectV2Item { id } }
}"""
CLEAR_VALUE = """
mutation FcClearValue($input: ClearProjectV2ItemFieldValueInput!) {
  clearProjectV2ItemFieldValue(input: $input) { projectV2Item { id } }
}"""
CREATE_FIELD = """
mutation FcCreateField($input: CreateProjectV2FieldInput!) {
  createProjectV2Field(input: $input) { projectV2Field { ...FieldParts } }
}""" + _FIELD_PARTS
UPDATE_FIELD = """
mutation FcUpdateField($input: UpdateProjectV2FieldInput!) {
  updateProjectV2Field(input: $input) { projectV2Field { ...FieldParts } }
}""" + _FIELD_PARTS
CREATE_VIEW = """
mutation FcCreateView($input: CreateProjectV2ViewInput!) {
  createProjectV2View(input: $input) { projectV2View { ...ViewParts } }
}""" + _VIEW_PARTS
UPDATE_VIEW = """
mutation FcUpdateView($input: UpdateProjectV2ViewInput!) {
  updateProjectV2View(input: $input) { projectV2View { ...ViewParts } }
}""" + _VIEW_PARTS


def _payload(data: Mapping[str, Any], *path: str) -> Any:
    value: Any = data
    for key in path:
        value = _obj(value, ".".join(path)).get(key)
    if value is None:
        raise GitHubResponseError(f"graphql: {'.'.join(path)} missing")
    return value


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ProjectsClient(BoardGuard):
    """BoardClient over the GitHub GraphQL API (the authenticated `gh`; needs the `project` scope)."""

    live = True

    def __init__(self, api: GitHubClient) -> None:
        super().__init__()
        self.api = api

    @property
    def requests(self) -> int:
        """Requests sent by the shared GitHub client."""
        return self.api.requests

    @property
    def writes(self) -> int:
        """Writes sent by the shared GitHub client (REST and GraphQL mutations)."""
        return self.api.writes

    # -- reads --------------------------------------------------------------------

    def project(self, owner: str, owner_type: str, number: int) -> RemoteProject | None:
        """The project with its fields, views and workflows (None if it does not exist)."""
        if owner_type not in ("user", "organization"):
            raise ValueError(f"invalid owner type {owner_type!r}")
        try:
            data = self.api.graphql(PROJECT_QUERY % owner_type, {"owner": owner, "number": int(number)})
        except GitHubNotFoundError:
            return None
        raw = _obj(data.get("owner"), "owner").get("projectV2")
        return parse_project(raw) if raw is not None else None

    def issue_items(self, repository: str, number: int) -> list[ItemRef]:
        """Every project item of one Issue (all projects, archived included)."""
        owner, name = repository.split("/", 1)
        data = self.api.graphql(ISSUE_ITEMS_QUERY, {"owner": owner, "repo": name, "number": int(number)})
        issue = _obj(_payload(data, "repository", "issue"), "issue")
        refs = []
        for node in _nodes(issue.get("projectItems"), f"issue #{number} project items"):
            node = _obj(node, "project item")
            project = _obj(node.get("project"), "project item")
            ref = ItemRef(_node(node.get("id"), "project item"), _node(project.get("id"), "project item"),
                          _int(project.get("number"), "project item"), bool(node.get("isArchived")))
            self._remember(ref.project_id, ref.id, item=True)
            refs.append(ref)
        return refs

    def get_item(self, item_id: str) -> ProjectItem | None:
        """One item (None when it no longer exists)."""
        try:
            data = self.api.graphql(ITEM_QUERY, {"id": item_id})
        except GitHubNotFoundError:
            return None
        node = data.get("node")
        if node is None:
            return None
        if _obj(node, "item").get("__typename") != "ProjectV2Item":
            raise GitHubResponseError(f"node {sanitize(item_id, 60)} is not a project item")
        item = parse_item(node)
        self._remember(item.project_id, item.id, item=True)
        return item

    def list_items(self, project_id: str) -> list[ProjectItem]:
        """Every item of *project_id* (bounded by github.max_pages)."""
        items: list[ProjectItem] = []
        after = None
        for _page in range(self.api.settings.max_pages):
            data = self.api.graphql(ITEMS_QUERY, {"id": project_id, "after": after})
            conn = _obj(_payload(data, "node", "items"), "items")
            items += [parse_item(n) for n in _nodes(conn, "items", complete=False)]
            info = _obj(conn.get("pageInfo"), "items")
            if not info.get("hasNextPage"):
                for item in items:
                    self._remember(item.project_id, item.id, item=True)
                return items
            after = _text(info.get("endCursor"), "items", 200, empty=False)
        raise GitHubResponseError(f"project items: more than {self.api.settings.max_pages} pages; "
                                  "refusing a partial result")

    # -- item writes ----------------------------------------------------------------

    def add_item(self, project_id: str, content_id: str) -> str:
        """Add an Issue. GitHub returns the existing item when the Issue is already on the board."""
        self.check(project_id=project_id)
        data = self.api.graphql(ADD_ITEM, {"input": {"projectId": project_id, "contentId": content_id}},
                                mutation=True, idempotent=False)
        item = _obj(_payload(data, "addProjectV2ItemById", "item"), "item")
        item_id = _node(item.get("id"), "item")
        if _obj(item.get("project"), "item").get("id") != project_id:
            raise GitHubResponseError("added item belongs to another project")
        self._remember(project_id, item_id, item=True)
        return item_id

    def set_text(self, project_id: str, item_id: str, field_id: str, text: str) -> None:
        """Set a text value (absolute, so idempotent)."""
        self._set(project_id, item_id, field_id, {"text": text})

    def set_option(self, project_id: str, item_id: str, field_id: str, option_id: str) -> None:
        """Select an option (absolute, so idempotent)."""
        self._set(project_id, item_id, field_id, {"singleSelectOptionId": option_id})

    def _set(self, project_id: str, item_id: str, field_id: str, value: dict[str, str]) -> None:
        self.check(project_id=project_id, node_id=field_id, item_id=item_id)
        self.api.graphql(SET_VALUE, {"input": {"projectId": project_id, "itemId": item_id, "fieldId": field_id,
                                               "value": value}}, mutation=True)

    def clear_value(self, project_id: str, item_id: str, field_id: str) -> None:
        """Clear a value (idempotent)."""
        self.check(project_id=project_id, node_id=field_id, item_id=item_id)
        self.api.graphql(CLEAR_VALUE, {"input": {"projectId": project_id, "itemId": item_id, "fieldId": field_id}},
                         mutation=True)

    # -- configuration writes ----------------------------------------------------------

    def create_field(self, project_id: str, name: str, data_type: str,
                     options: Iterable[FieldOption] = ()) -> ProjectField:
        """Create a TEXT or SINGLE_SELECT field (not idempotent: callers re-read first)."""
        self.check(project_id=project_id)
        if data_type not in ("TEXT", "SINGLE_SELECT"):
            raise ValueError(f"unsupported field type {data_type!r}")
        payload: dict[str, Any] = {"projectId": project_id, "name": name, "dataType": data_type}
        if data_type == "SINGLE_SELECT":
            payload["singleSelectOptions"] = [o.as_input() for o in options]
        data = self.api.graphql(CREATE_FIELD, {"input": payload}, mutation=True, idempotent=False)
        created = parse_field(_payload(data, "createProjectV2Field", "projectV2Field"))
        self._remember(project_id, created.id, item=False)
        return created

    def update_options(self, field_id: str, options: Iterable[FieldOption]) -> ProjectField:
        """Replace the options (absolute; options with an ID keep their identity and item values)."""
        self.check(node_id=field_id)
        data = self.api.graphql(UPDATE_FIELD, {"input": {"fieldId": field_id,
                                                         "singleSelectOptions": [o.as_input() for o in options]}},
                                mutation=True)
        return parse_field(_payload(data, "updateProjectV2Field", "projectV2Field"))

    def create_view(self, project_id: str, name: str, layout: str) -> ProjectView:
        """Create a view (not idempotent: callers re-read first)."""
        self.check(project_id=project_id)
        if layout not in LAYOUTS:
            raise ValueError(f"invalid layout {layout!r}")
        data = self.api.graphql(CREATE_VIEW, {"input": {"projectId": project_id, "name": name, "layout": layout}},
                                mutation=True, idempotent=False)
        view = parse_view(_payload(data, "createProjectV2View", "projectV2View"))
        self._remember(project_id, view.id, item=False)
        return view

    def update_view(self, view_id: str, *, name: str | None = None, filter_text: str | None = None,
                    visible_field_ids: Iterable[str] | None = None) -> ProjectView:
        """Rename / filter / choose visible fields (absolute values, so idempotent)."""
        self.check(node_id=view_id)
        payload: dict[str, Any] = {"viewId": view_id}
        if name is not None:
            payload["name"] = name
        if filter_text is not None:
            payload["filter"] = filter_text
        if visible_field_ids is not None:
            ids = list(visible_field_ids)
            for field_id in ids:
                self.check(node_id=field_id)
            payload["configuration"] = {"visibleFieldIds": ids}
        data = self.api.graphql(UPDATE_VIEW, {"input": payload}, mutation=True)
        return parse_view(_payload(data, "updateProjectV2View", "projectV2View"))
