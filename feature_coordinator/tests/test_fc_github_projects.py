"""The GitHub Projects GraphQL client without GitHub: scripted GraphQL responses through the real
GitHubClient (retries, rate limits, pacing, uncertain writes), response validation and the
write guard. Board synchronization is tested in test_fc_project_sync.py; the live check is
test_fc_projects_live.py."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

from typing import Any

import pytest

from feature_coordinator.config import GitHubConfig
from feature_coordinator.errors import (
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubResponseError,
    GitHubUncertainWriteError,
    GitHubValidationError,
    ProjectBoardError,
)
from feature_coordinator.github_api import ApiResponse, GitHubClient, TransportFailure
from feature_coordinator.github_projects import FieldOption, ProjectsClient, parse_item, parse_project

SETTINGS = GitHubConfig(repository="octo/repo", min_write_interval_seconds=0.5, max_retries=2,
                        backoff_base_seconds=1.0, max_rate_limit_wait_seconds=120.0)
PROJECT_ID, OTHER_ID = "PVT_project2", "PVT_project1"


def gql(data: Any = None, errors: list[dict[str, Any]] | None = None, status: int = 200,
        headers: dict[str, str] | None = None) -> ApiResponse:
    body: dict[str, Any] = {}
    if data is not None:
        body["data"] = data
    if errors is not None:
        body["errors"] = errors
    return ApiResponse(status, headers or {}, body)


def project_json(**over: Any) -> dict[str, Any]:
    data = {"id": PROJECT_ID, "number": 2, "title": "board", "closed": False, "url": "https://github.com/p/2",
            "updatedAt": "2026-10-17T00:00:00Z", "viewerCanUpdate": True, "owner": {"login": "octo"},
            "items": {"totalCount": 0},
            "fields": {"pageInfo": {"hasNextPage": False}, "nodes": [
                {"__typename": "ProjectV2Field", "id": "PVTF_title", "name": "Title", "dataType": "TITLE"},
                {"__typename": "ProjectV2SingleSelectField", "id": "PVTSSF_status", "name": "Status",
                 "dataType": "SINGLE_SELECT", "options": [{"id": "f75ad846", "name": "Todo", "color": "GREEN",
                                                           "description": "x"}]},
                {"__typename": "ProjectV2Field", "id": "PVTF_feature", "name": "Feature ID", "dataType": "TEXT"}]},
            "views": {"pageInfo": {"hasNextPage": False}, "nodes": [
                {"id": "PVTV_board", "number": 2, "name": "View 2", "layout": "BOARD_LAYOUT", "filter": None,
                 "verticalGroupByFields": {"nodes": [{"id": "PVTSSF_status"}]},
                 "fields": {"pageInfo": {"hasNextPage": False}, "nodes": [{"id": "PVTF_title"}]}}]},
            "workflows": {"pageInfo": {"hasNextPage": False},
                          "nodes": [{"number": 3, "name": "Auto-close issue", "enabled": True}]}}
    data.update(over)
    return data


def item_json(item_id: str = "PVTI_one", project: str = PROJECT_ID, **over: Any) -> dict[str, Any]:
    data = {"__typename": "ProjectV2Item", "id": item_id, "isArchived": False, "type": "ISSUE",
            "project": {"id": project, "number": 2},
            "content": {"__typename": "Issue", "id": "I_kwissue", "databaseId": 905, "number": 5,
                        "repository": {"nameWithOwner": "octo/repo"}},
            "fieldValues": {"pageInfo": {"hasNextPage": False}, "nodes": [
                {"__typename": "ProjectV2ItemFieldSingleSelectValue", "optionId": "f75ad846", "name": "Todo",
                 "field": {"id": "PVTSSF_status", "name": "Status"}},
                {"__typename": "ProjectV2ItemFieldTextValue", "text": "f1",
                 "field": {"id": "PVTF_feature", "name": "Feature ID"}},
                {"__typename": "ProjectV2ItemFieldTextValue", "text": "Issue title",
                 "field": {"id": "PVTF_title", "name": "Title"}}]}}
    data.update(over)
    return data


class Scripted:  # pylint: disable=too-few-public-methods
    """A transport replaying scripted responses (or raising) and recording requests."""

    def __init__(self, *responses: ApiResponse | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:
        self.requests.append((method, path, body))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Clock:  # pylint: disable=too-few-public-methods
    """A fake monotonic clock whose sleep advances it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(*responses: ApiResponse | Exception) -> tuple[ProjectsClient, Scripted, Clock]:
    transport, clock = Scripted(*responses), Clock()
    api = GitHubClient(transport, SETTINGS, sleep=clock.sleep, clock=lambda: clock.now, wall=lambda: 5000.0)
    return ProjectsClient(api), transport, clock


def bound(*responses: ApiResponse | Exception) -> tuple[ProjectsClient, Scripted, Clock]:
    client, transport, clock = make(gql({"owner": {"projectV2": project_json()}}), *responses)
    client.bind(client.project("octo", "user", 2), [OTHER_ID])  # type: ignore[arg-type]
    return client, transport, clock


# -- identification and validation ------------------------------------------------------------

def test_project_is_identified_with_fields_options_views_and_workflows() -> None:
    client, transport, _clock = make(gql({"owner": {"projectV2": project_json()}}))
    project = client.project("octo", "user", 2)
    assert project is not None and project.id == PROJECT_ID and project.owner == "octo" and project.viewer_can_update
    status = project.field("Status")
    assert status is not None and status.option("Todo") is not None and status.option("Todo").id == "f75ad846"
    view = project.view("View 2")
    assert view is not None and view.group_by == ("PVTSSF_status",) and view.layout == "BOARD_LAYOUT"
    assert [w.name for w in project.workflows] == ["Auto-close issue"]
    method, path, body = transport.requests[0]
    assert (method, path) == ("POST", "graphql") and body["variables"] == {"owner": "octo", "number": 2}
    assert "user(login: $owner)" in body["query"] and "query FcProject" in body["query"]
    with pytest.raises(ValueError):
        client.project("octo", "team", 2)


def test_missing_project_is_none_and_organization_queries_differ() -> None:
    client, transport, _clock = make(gql({"owner": None}, errors=[{"type": "NOT_FOUND", "message": "no"}]),
                                     gql({"owner": {"projectV2": None}}))
    assert client.project("octo", "user", 9) is None
    assert client.project("octo", "organization", 9) is None
    assert "organization(login: $owner)" in transport.requests[1][2]["query"]


@pytest.mark.parametrize("over", [{"id": "bad id!"}, {"fields": {"pageInfo": {"hasNextPage": True}, "nodes": []}},
                                  {"owner": {"login": ""}}, {"url": "http://insecure"}, {"number": -1}])
def test_untrusted_project_data_is_validated(over: dict[str, Any]) -> None:
    with pytest.raises(GitHubResponseError):
        parse_project(project_json(**over))


def test_item_values_are_read_by_field_name_and_validated() -> None:
    item = parse_item(item_json())
    assert item.values == {"Status": "Todo", "Feature ID": "f1", "Title": "Issue title"}
    assert item.option_ids == {"Status": "f75ad846"} and item.issue_db_id == 905 and item.repository == "octo/repo"
    draft = parse_item(item_json(content=None))
    assert draft.content_type == "DraftIssue" and draft.issue_number is None
    for bad in (item_json(id="x y"), item_json(content={"__typename": "Issue", "id": "I_x", "databaseId": 1,
                                                        "number": 1, "repository": {"nameWithOwner": "../etc"}})):
        with pytest.raises(GitHubResponseError):
            parse_item(bad)


# -- GraphQL errors, scopes, rate limits, retries --------------------------------------------------

def test_missing_project_scope_names_the_operator_action() -> None:
    client, _transport, _clock = make(gql(errors=[{"type": "INSUFFICIENT_SCOPES", "message": "needs read:project"}]))
    with pytest.raises(GitHubPermissionError, match="gh auth refresh -s project"):
        client.project("octo", "user", 2)


def test_graphql_rate_limit_waits_then_retries_and_is_bounded() -> None:
    limited = gql(errors=[{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}],
                  headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "5030"})
    client, _transport, clock = make(limited, gql({"owner": {"projectV2": project_json()}}))
    assert client.project("octo", "user", 2) is not None
    assert clock.sleeps == [31.0]
    client, _transport, clock = make(gql(errors=[{"type": "RATE_LIMITED", "message": "x"}],
                                         headers={"retry-after": "600"}))
    with pytest.raises(GitHubRateLimitError):
        client.project("octo", "user", 2)


def test_queries_retry_5xx_and_are_never_paced_or_counted_as_writes() -> None:
    client, _transport, clock = make(ApiResponse(502, {}, {"message": "bad gateway"}),
                                     TransportFailure("timeout", maybe_applied=True),
                                     gql({"owner": {"projectV2": project_json()}}))
    assert client.project("octo", "user", 2) is not None
    assert clock.sleeps == [1.0, 2.0] and client.writes == 0 and client.requests == 3


def test_an_uncertain_add_is_never_blindly_resent() -> None:
    client, transport, _clock = bound(ApiResponse(502, {}, {"message": "bad gateway"}))
    with pytest.raises(GitHubUncertainWriteError):
        client.add_item(PROJECT_ID, "I_kwissue")
    assert len(transport.requests) == 2


def test_field_value_writes_are_idempotent_retried_and_paced() -> None:
    client, transport, clock = bound(gql({"node": item_json()}), ApiResponse(503, {}, {}),
                                     gql({"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "PVTI_one"}}}),
                                     gql({"clearProjectV2ItemFieldValue": {"projectV2Item": {"id": "PVTI_one"}}}))
    assert client.get_item("PVTI_one") is not None
    client.set_option(PROJECT_ID, "PVTI_one", "PVTSSF_status", "f75ad846")
    client.clear_value(PROJECT_ID, "PVTI_one", "PVTF_feature")
    body = transport.requests[2][2]
    assert body["variables"]["input"] == {"projectId": PROJECT_ID, "itemId": "PVTI_one", "fieldId": "PVTSSF_status",
                                          "value": {"singleSelectOptionId": "f75ad846"}}
    assert client.writes == 3 and 1.0 in clock.sleeps


def test_add_item_rejects_an_item_of_another_project() -> None:
    client, _transport, _clock = bound(gql({"addProjectV2ItemById": {"item": {"id": "PVTI_x",
                                                                               "project": {"id": OTHER_ID}}}}))
    with pytest.raises(GitHubResponseError):
        client.add_item(PROJECT_ID, "I_kwissue")


def test_validation_errors_are_not_retried() -> None:
    client, transport, _clock = bound(gql(errors=[{"type": "UNPROCESSABLE", "message": "Name has already been taken"}]))
    with pytest.raises(GitHubValidationError):
        client.create_field(PROJECT_ID, "Agent", "SINGLE_SELECT", [FieldOption("", "Backend", "BLUE", "")])
    assert len(transport.requests) == 2


# -- the write guard --------------------------------------------------------------------------------

def test_nothing_is_written_before_the_project_is_bound() -> None:
    client, transport, _clock = make()
    with pytest.raises(ProjectBoardError, match="no project is bound"):
        client.add_item(PROJECT_ID, "I_kwissue")
    assert not transport.requests


def test_the_guard_refuses_other_and_protected_projects_and_foreign_nodes() -> None:
    client, transport, _clock = bound()
    sent = len(transport.requests)
    with pytest.raises(ProjectBoardError, match="only the configured project"):
        client.add_item(OTHER_ID, "I_kwissue")
    with pytest.raises(ProjectBoardError, match="not seen in the configured project"):
        client.set_text(PROJECT_ID, "PVTI_from_project1", "PVTF_feature", "x")
    with pytest.raises(ProjectBoardError, match="not a field or view"):
        client.update_options("PVTSSF_of_project1", [])
    with pytest.raises(ProjectBoardError, match="not a field or view"):
        client.update_view("PVTV_board", visible_field_ids=["PVTF_of_project1"])
    assert len(transport.requests) == sent  # every refusal happened before a request
    with pytest.raises(ProjectBoardError, match="protected"):
        client.bind(parse_project(project_json()), [PROJECT_ID])


def test_items_read_from_a_protected_project_are_never_writable() -> None:
    client, _transport, _clock = bound(gql({"node": item_json("PVTI_p1", project=OTHER_ID)}))
    assert client.get_item("PVTI_p1") is not None
    with pytest.raises(ProjectBoardError):
        client.set_text(PROJECT_ID, "PVTI_p1", "PVTF_feature", "x")


def test_update_view_sends_only_the_given_settings() -> None:
    view = {"id": "PVTV_board", "number": 2, "name": "Kanban", "layout": "BOARD_LAYOUT", "filter": "",
            "verticalGroupByFields": {"nodes": []}, "fields": {"pageInfo": {"hasNextPage": False}, "nodes": []}}
    client, transport, _clock = bound(gql({"updateProjectV2View": {"projectV2View": view}}))
    assert client.update_view("PVTV_board", name="Kanban").name == "Kanban"
    assert transport.requests[-1][2]["variables"]["input"] == {"viewId": "PVTV_board", "name": "Kanban"}


def test_token_scopes_come_from_the_response_header_only() -> None:
    transport = Scripted(ApiResponse(200, {"x-oauth-scopes": "repo, project, read:org"}, {"login": "octo"}),
                         ApiResponse(200, {}, {"login": "octo"}))
    api = GitHubClient(transport, SETTINGS, sleep=lambda _s: None)
    assert api.token_scopes() == ("project", "read:org", "repo")
    assert api.token_scopes() is None
