"""GitHub backend — issues via the REST API, status via a GitHub Project (GraphQL).

Status is deliberately NOT a label here. GitHub issues only have two native states
(open/closed), so the workflow status (open / in-progress / needs-human / done) lives in
a single-select field on a GitHub Project (Projects v2 — included in GitHub Free for both
personal accounts and organizations). Every tracked issue is added to that project as an
item, and its status is that item's field value:

    [issues.github] status_field (default "mx-status")   ->  single-select field
    [issues.github.status_options]                        ->  option name per status

`issues.py init` creates the field with all four options when it doesn't exist yet. It
never edits an existing field's options (replacing a single-select's option list via the
API can reset every item's value) — `doctor`/`init` report missing options instead, to
be added once in the project's UI. The default is a dedicated field rather than the
project's built-in "Status" so GitHub's own project workflows ("Item closed -> Done",
"Item reopened -> Todo"), which only ever touch "Status", can't race with this tool's
read-back assertion. Pointing status_field at "Status" works too, provided those
workflows are disabled or agree with status_options.

Like GitLab: "done" also closes the issue (state_reason=completed), any other status on a
closed issue reopens it, and every transition is re-read and asserted afterward.

Source/severity/ownership stay labels (`source::*`, `severity::*`, `mx-auto`) — they're
filters and provenance, not workflow state, and cost nothing on any plan.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from .. import fingerprint
from ..config import ConfigError, GitHubIssuesConfig, load_github_token
from ..tracker import (
    LABEL_COLORS,
    METADATA_LABELS,
    OWNERSHIP_LABEL,
    STATUSES,
    Comment,
    DoctorCheck,
    Issue,
    IssueNotFoundError,
    IssueTracker,
    TrackerError,
    TransitionError,
    TransitionResult,
    check_status,
)

GITHUB_API = "https://api.github.com"
PAGE_SIZE = 50

# Projects v2 single-select option colors (the API's enum values).
STATUS_OPTION_COLORS = {"open": "BLUE", "in-progress": "YELLOW", "needs-human": "RED", "done": "GREEN"}


class GitHubAPIError(Exception):
    """A GitHub REST or GraphQL call failed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# GraphQL documents
# ---------------------------------------------------------------------------

_ISSUE_FRAGMENT = """
fragment MxIssue on Issue {
  id number title body state url
  labels(first: 100) { nodes { name } }
  projectItems(first: 20) {
    nodes {
      id
      project { id }
      fieldValueByName(name: $statusField) {
        ... on ProjectV2ItemFieldSingleSelectValue { name }
      }
    }
  }
}
"""

_GET_ISSUE = """
query($owner: String!, $name: String!, $number: Int!, $statusField: String!) {
  repository(owner: $owner, name: $name) { issue(number: $number) { ...MxIssue } }
}
""" + _ISSUE_FRAGMENT

_LIST_ISSUES = """
query($owner: String!, $name: String!, $labels: [String!], $states: [IssueState!], $after: String, $statusField: String!) {
  repository(owner: $owner, name: $name) {
    issues(first: %d, after: $after, labels: $labels, states: $states, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { ...MxIssue }
    }
  }
}
""" % PAGE_SIZE + _ISSUE_FRAGMENT

_PROJECT_FRAGMENT = """
fragment MxProject on ProjectV2 {
  id title url closed
  field(name: $statusField) {
    ... on ProjectV2FieldCommon { id name dataType }
    ... on ProjectV2SingleSelectField { options { id name } }
  }
}
"""

_PROJECT_BY_OWNER = {
    kind: """
query($login: String!, $number: Int!, $statusField: String!) {
  %s(login: $login) { projectV2(number: $number) { ...MxProject } }
}
""" % kind + _PROJECT_FRAGMENT
    for kind in ("organization", "user")
}

_ADD_ITEM = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}
"""

_SET_SINGLE_SELECT = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}}) {
    projectV2Item { id }
  }
}
"""

_CREATE_SINGLE_SELECT_FIELD = """
mutation($project: ID!, $name: String!, $options: [ProjectV2SingleSelectFieldOptionInput!]) {
  createProjectV2Field(input: {projectId: $project, dataType: SINGLE_SELECT, name: $name, singleSelectOptions: $options}) {
    projectV2Field { ... on ProjectV2SingleSelectField { id name dataType options { id name } } }
  }
}
"""


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class _Client:
    def __init__(self, token: str) -> None:
        self._http = httpx.Client(
            base_url=GITHUB_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def rest(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise GitHubAPIError(
                f"GitHub {method} {path} failed ({response.status_code}): {response.text[:500]}",
                status_code=response.status_code,
            )
        return response

    def rest_paginated(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        response = self.rest("GET", path, params={"per_page": 100, **(params or {})})
        while True:
            items.extend(response.json())
            next_url = response.links.get("next", {}).get("url")
            if not next_url:
                return items
            response = self.rest("GET", next_url)

    def graphql(self, query: str, variables: dict[str, Any], *, tolerate_not_found: bool = False) -> dict[str, Any]:
        """Run *query*. GraphQL reports most failures as HTTP 200 + an `errors` array; any
        error raises, except NOT_FOUND ones when *tolerate_not_found* (the caller then sees
        the corresponding field as null).
        """
        response = self.rest("POST", "/graphql", json={"query": query, "variables": variables})
        payload = response.json()
        errors = payload.get("errors") or []
        if errors and not (tolerate_not_found and all(e.get("type") == "NOT_FOUND" for e in errors)):
            detail = "; ".join(f"{e.get('type', 'ERROR')}: {e.get('message', e)}" for e in errors)
            raise GitHubAPIError(f"GitHub GraphQL error: {detail}")
        return payload.get("data") or {}


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

class GitHubTracker(IssueTracker):
    name = "github"
    display_name = "GitHub"

    def __init__(self, config: GitHubIssuesConfig, *, token: str, repo: str) -> None:
        self.config = config
        self.repo = repo
        self.owner, self.repo_name = repo.split("/", 1)
        self.project_owner = config.project_owner or self.owner
        self._client = _Client(token)
        self._status_by_option = {name: status for status, name in config.status_options.items()}
        self._login = ""
        self._scopes: list[str] | None = None  # None = fine-grained token (no X-OAuth-Scopes header)
        self._repo_info: dict[str, Any] = {}
        self._project: dict[str, Any] | None = None
        self._fp_index: dict[str, int] | None = None

    @classmethod
    def from_config(cls, config: GitHubIssuesConfig) -> GitHubTracker:
        try:
            token = load_github_token()
        except ConfigError as exc:
            raise TrackerError(str(exc)) from exc
        repo = config.repo
        if not repo:
            # Lazy: mxfix.git_target owns origin-remote parsing (it opens PRs against it).
            from mxfix.git_target import GitTargetError, github_owner_repo  # noqa: PLC0415
            from testenvlib.paths import REPO_ROOT  # noqa: PLC0415
            try:
                repo = github_owner_repo(cwd=REPO_ROOT)
            except GitTargetError as exc:
                raise TrackerError(f"[issues.github] repo is empty and it couldn't be derived from origin: {exc}") from exc
        return cls(config, token=token, repo=repo)

    # -- setup / diagnostics ---------------------------------------------------

    def describe(self) -> list[tuple[str, str]]:
        number = self.config.project_number
        return [
            ("repo", self.repo),
            ("project", f"{self.project_owner}/projects/{number}" if number else "(not configured — [issues.github] project_number)"),
            ("status field", f"{self.config.status_field} ({', '.join(self.config.status_options[s] for s in STATUSES)})"),
        ]

    def connect(self) -> None:
        try:
            user = self._client.rest("GET", "/user")
        except GitHubAPIError as exc:
            if exc.status_code == 401:
                raise TrackerError("GitHub authentication failed — GITHUB_PATH is invalid or expired.") from exc
            raise TrackerError(f"Could not reach GitHub: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TrackerError(f"Could not reach GitHub: {exc}") from exc
        self._login = user.json().get("login", "?")
        scopes_header = user.headers.get("X-OAuth-Scopes")
        self._scopes = [s.strip() for s in scopes_header.split(",") if s.strip()] if scopes_header is not None else None

        try:
            self._repo_info = self._client.rest("GET", f"/repos/{self.repo}").json()
        except GitHubAPIError as exc:
            raise TrackerError(f"GitHub repository {self.repo!r} not reachable with this token: {exc}") from exc
        if not self._repo_info.get("has_issues", False):
            raise TrackerError(f"Issues are disabled on {self.repo} — enable them under the repository's Settings -> Features.")

        if not self.config.project_number:
            raise TrackerError(
                "[issues.github] project_number is not set. Create a GitHub Project (free on every plan) "
                f"owned by {self.project_owner} — or set project_owner — and put the number from its URL "
                "(…/projects/<N>) in testenv.toml or testenv.local.toml. Status is tracked in that project, not in labels.",
            )
        self._project = self._load_project()

    def _load_project(self) -> dict[str, Any]:
        variables = {"login": self.project_owner, "number": self.config.project_number, "statusField": self.config.status_field}
        try:
            for kind in ("organization", "user"):
                data = self._client.graphql(_PROJECT_BY_OWNER[kind], variables, tolerate_not_found=True)
                owner = data.get(kind)
                if owner is None:
                    continue  # the login isn't this kind of account — try the other
                if owner.get("projectV2") is None:
                    break
                return owner["projectV2"]
        except GitHubAPIError as exc:
            raise TrackerError(
                f"Could not read GitHub Project {self.project_owner}/{self.config.project_number}: {exc}\n"
                "A classic PAT needs the `project` scope (plus `repo`); a fine-grained PAT needs Projects "
                "read/write access for the project's owner.",
            ) from exc
        raise TrackerError(
            f"GitHub Project #{self.config.project_number} not found for {self.project_owner!r} "
            "(or this token can't see it) — check [issues.github] project_owner/project_number.",
        )

    def doctor(self) -> list[DoctorCheck]:
        project = self._require_project()
        checks = [DoctorCheck("ok", f"token valid (authenticated as {self._login})")]

        if self._scopes is None:
            checks.append(DoctorCheck("info", "fine-grained token — scopes aren't reported; it needs Issues read/write on the repo and Projects read/write for the project owner"))
        else:
            missing_scopes = [s for s in ("repo", "project") if s not in self._scopes and not (s == "repo" and "public_repo" in self._scopes)]
            if missing_scopes:
                checks.append(DoctorCheck("warn", f"classic token is missing scope(s): {', '.join(missing_scopes)} (has: {', '.join(self._scopes) or 'none'})"))
            else:
                checks.append(DoctorCheck("ok", f"classic token scopes: {', '.join(self._scopes)}"))

        permissions = self._repo_info.get("permissions") or {}
        if permissions.get("push") or permissions.get("triage"):
            checks.append(DoctorCheck("ok", f"repository {self.repo} reachable, issues enabled"))
        else:
            checks.append(DoctorCheck(
                "warn", f"repository {self.repo} reachable, but this token lacks triage/write access",
                ("GitHub silently drops labels on issues created without it — mx-auto/source/severity would be lost.",),
            ))

        checks.append(DoctorCheck("warn" if project.get("closed") else "ok", f"project \"{project['title']}\" {project['url']}" + (" is CLOSED" if project.get("closed") else "")))
        checks.extend(self._field_checks(project))

        try:
            existing = {label["name"] for label in self._client.rest_paginated(f"/repos/{self.repo}/labels")}
        except GitHubAPIError as exc:
            checks.append(DoctorCheck("fail", f"could not list labels: {exc}"))
            return checks
        missing = [name for name in METADATA_LABELS if name not in existing]
        if missing:
            checks.append(DoctorCheck("warn", f"{len(missing)} label(s) not yet created", tuple(missing), fixed_by_init=True))
        else:
            checks.append(DoctorCheck("ok", f"all {len(METADATA_LABELS)} labels present (status is the project field, not a label)"))
        return checks

    def _field_checks(self, project: dict[str, Any]) -> list[DoctorCheck]:
        name = self.config.status_field
        status_field = project.get("field")
        if not status_field:
            return [DoctorCheck("warn", f"project has no {name!r} field yet", fixed_by_init=True)]
        if status_field.get("dataType") != "SINGLE_SELECT":
            return [DoctorCheck("fail", f"project field {name!r} exists but is {status_field.get('dataType')}, not single select — rename it or set [issues.github] status_field")]
        missing = self._missing_options(status_field)
        if missing:
            return [DoctorCheck(
                "fail", f"project field {name!r} is missing option(s): {', '.join(missing)}",
                ("Add them in the project's UI (field settings) or adjust [issues.github.status_options] — "
                 "init never edits an existing field's options.",),
            )]
        return [DoctorCheck("ok", f"project field {name!r} has all {len(STATUSES)} status options")]

    def _missing_options(self, status_field: dict[str, Any]) -> list[str]:
        present = {option["name"] for option in status_field.get("options") or []}
        return [self.config.status_options[s] for s in STATUSES if self.config.status_options[s] not in present]

    def init(self) -> tuple[list[str], list[str]]:
        project = self._require_project()
        created: list[str] = []
        problems: list[str] = []

        existing = {label["name"] for label in self._client.rest_paginated(f"/repos/{self.repo}/labels")}
        for name in METADATA_LABELS:
            if name in existing:
                continue
            self._client.rest("POST", f"/repos/{self.repo}/labels", json={"name": name, "color": LABEL_COLORS.get(name, "999999")})
            created.append(f"label {name}")

        status_field = project.get("field")
        if not status_field:
            options = [
                {"name": self.config.status_options[s], "color": STATUS_OPTION_COLORS[s], "description": f"mx-auto status: {s}"}
                for s in STATUSES
            ]
            data = self._client.graphql(
                _CREATE_SINGLE_SELECT_FIELD, {"project": project["id"], "name": self.config.status_field, "options": options},
            )
            project["field"] = data["createProjectV2Field"]["projectV2Field"]
            created.append(f"project field {self.config.status_field} ({', '.join(o['name'] for o in options)})")
        else:
            problems.extend(check.message for check in self._field_checks(project) if check.level == "fail")
        return created, problems

    # -- internals -------------------------------------------------------------

    def _require_project(self) -> dict[str, Any]:
        if self._project is None:
            raise RuntimeError("GitHubTracker.connect() was not called")
        return self._project

    def _status_field_ids(self, status: str) -> tuple[str, str]:
        """(field id, option id) for *status* — raises TrackerError, before anything is
        written, if the field or option doesn't exist yet.
        """
        project = self._require_project()
        status_field = project.get("field")
        if not status_field or status_field.get("dataType") != "SINGLE_SELECT":
            raise TrackerError(f"GitHub Project has no single-select {self.config.status_field!r} field — run `issues.py init`.")
        option_name = self.config.status_options[status]
        option = next((o for o in status_field.get("options") or [] if o["name"] == option_name), None)
        if option is None:
            raise TrackerError(f"Project field {self.config.status_field!r} has no {option_name!r} option — see `issues.py doctor`.")
        return status_field["id"], option["id"]

    def _to_issue(self, node: dict[str, Any]) -> Issue:
        project_id = self._require_project()["id"]
        status = None
        for item in (node.get("projectItems") or {}).get("nodes") or []:
            if item and (item.get("project") or {}).get("id") == project_id:
                value = item.get("fieldValueByName") or {}
                status = self._status_by_option.get(value.get("name", ""))
                break
        return Issue(
            number=node["number"],
            title=node["title"],
            description=node.get("body") or "",
            labels=tuple(label["name"] for label in (node.get("labels") or {}).get("nodes") or []),
            state="closed" if node["state"] == "CLOSED" else "open",
            status=status,
            web_url=node["url"],
            raw=node,
        )

    def _iter_issue_nodes(self, *, labels: list[str] | None, states: list[str] | None):
        after = None
        while True:
            data = self._client.graphql(_LIST_ISSUES, {
                "owner": self.owner, "name": self.repo_name, "labels": labels, "states": states,
                "after": after, "statusField": self.config.status_field,
            })
            connection = data["repository"]["issues"]
            yield from connection["nodes"]
            if not connection["pageInfo"]["hasNextPage"]:
                return
            after = connection["pageInfo"]["endCursor"]

    def _get_node(self, number: int) -> dict[str, Any]:
        data = self._client.graphql(
            _GET_ISSUE, {"owner": self.owner, "name": self.repo_name, "number": number, "statusField": self.config.status_field},
            tolerate_not_found=True,
        )
        node = (data.get("repository") or {}).get("issue")
        if node is None:
            raise IssueNotFoundError(f"No issue #{number} in {self.repo}.")
        return node

    def _apply_status_field(self, content_id: str, status: str) -> None:
        field_id, option_id = self._status_field_ids(status)
        project_id = self._require_project()["id"]
        # Idempotent — returns the existing item when the issue is already in the project.
        data = self._client.graphql(_ADD_ITEM, {"project": project_id, "content": content_id})
        item_id = data["addProjectV2ItemById"]["item"]["id"]
        self._client.graphql(_SET_SINGLE_SELECT, {"project": project_id, "item": item_id, "field": field_id, "option": option_id})

    # -- reads -----------------------------------------------------------------

    def get(self, number: int) -> Issue:
        return self._to_issue(self._get_node(number))

    def list_issues(
            self, *, status: str | None = None, labels: tuple[str, ...] = (),
            owned_only: bool = True, state: Literal["open", "closed", "all"] = "all",
    ) -> list[Issue]:
        if status is not None:
            check_status(status)
        wanted = set(labels) | ({OWNERSHIP_LABEL} if owned_only else set())
        # GraphQL's labels filter is OR, not AND — push at most one label server-side,
        # then require all of them client-side.
        server_label = [OWNERSHIP_LABEL] if owned_only else (sorted(wanted)[:1] or None)
        states = None if state == "all" else ["OPEN" if state == "open" else "CLOSED"]
        issues = (self._to_issue(node) for node in self._iter_issue_nodes(labels=server_label, states=states))
        return [
            issue for issue in issues
            if wanted.issubset(issue.labels) and (status is None or issue.status == status)
        ]

    def find_by_fingerprint(self, fp: str) -> Issue | None:
        # One paginated listing of every issue (any state, owned or not) per process,
        # instead of a search call per finding: GitHub's search index lags writes by
        # seconds to minutes and search is rate-limited to 30 requests/minute, either of
        # which could turn a sync into duplicate issues or a 403 halfway through.
        if self._fp_index is None:
            self._fp_index = {}
            for node in self._iter_issue_nodes(labels=None, states=None):
                meta = fingerprint.parse_meta_block(node.get("body") or "")
                if meta and meta.get("fp"):
                    self._fp_index.setdefault(str(meta["fp"]), node["number"])
        number = self._fp_index.get(fp)
        if number is None:
            return None
        issue = self.get(number)  # fresh, not the index-time snapshot
        meta = fingerprint.parse_meta_block(issue.description)
        return issue if meta and meta.get("fp") == fp else None

    def comments(self, number: int) -> list[Comment]:
        try:
            raw = self._client.rest_paginated(f"/repos/{self.repo}/issues/{number}/comments")
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                raise IssueNotFoundError(f"No issue #{number} in {self.repo}.") from exc
            raise
        return sorted((Comment(body=c.get("body") or "", created_at=c["created_at"]) for c in raw), key=lambda c: c.created_at)

    # -- writes ----------------------------------------------------------------

    def create(self, *, title: str, description: str, labels: list[str], status: str = "open") -> Issue:
        check_status(status)
        self._status_field_ids(status)  # fail before filing an issue we couldn't give a status
        data = self._client.rest("POST", f"/repos/{self.repo}/issues", json={"title": title, "body": description, "labels": labels}).json()
        self._apply_status_field(data["node_id"], status)
        meta = fingerprint.parse_meta_block(description)
        if self._fp_index is not None and meta and meta.get("fp"):
            self._fp_index[str(meta["fp"])] = data["number"]
        return self.get(data["number"])

    def update_description(self, number: int, description: str) -> None:
        self._client.rest("PATCH", f"/repos/{self.repo}/issues/{number}", json={"body": description})

    def add_comment(self, number: int, body: str) -> None:
        self._client.rest("POST", f"/repos/{self.repo}/issues/{number}/comments", json={"body": body})

    def set_status(self, number: int, target: str) -> TransitionResult:
        check_status(target)
        before = self.get(number)

        self._apply_status_field(before.raw["id"], target)

        closed = False
        reopened = False
        if target == "done":
            if before.state != "closed":
                self._client.rest("PATCH", f"/repos/{self.repo}/issues/{number}", json={"state": "closed", "state_reason": "completed"})
                closed = True
        elif before.state == "closed":
            # e.g. a fix commit's `Closes owner/repo#N` closed it on reaching the default
            # branch, then a regression recurred; or a human is reopening by hand.
            self._client.rest("PATCH", f"/repos/{self.repo}/issues/{number}", json={"state": "open"})
            reopened = True

        after = self.get(number)
        target_name = self.config.status_options[target]
        if after.status != target:
            raise TransitionError(
                f"Issue #{number}: expected project field {self.config.status_field!r} = {target_name!r} after "
                f"transition, got {after.status!r}. Check the issue in the project UI.",
            )
        if target == "done" and after.state != "closed":
            raise TransitionError(f"Issue #{number}: status {target_name!r} set but the issue is still open.")
        if target != "done" and after.state == "closed":
            raise TransitionError(f"Issue #{number}: status {target_name!r} set but the issue is still closed.")

        return TransitionResult(number=number, from_status=before.status, to_status=target, closed=closed, reopened=reopened)

    # -- cross-references ------------------------------------------------------

    def issue_url(self, number: int) -> str:
        return f"https://github.com/{self.repo}/issues/{number}"

    def closing_reference(self, number: int) -> str:
        """`owner/repo#N` — unambiguous whether or not the issues live in the same
        repository the fix PR is opened against. GitHub closes the issue once the commit
        reaches the repository's default branch (not on merge into [git] base_branch
        when that's a different branch), mirroring GitLab's close-on-default-branch.
        """
        return f"{self.repo}#{number}"
