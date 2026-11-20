"""W4.7 (M5): GraphQL writes are an ALLOWLIST of the GitHub Projects sync's fixed documents.

The independent review of the final M5 corrections found the public `GitHubClient.graphql()` refusing only five mutation
NAMES (mergePullRequest, enablePullRequestAutoMerge, enqueuePullRequest, mergeBranch, createPullRequest): `updateRef`,
`updateRefs`, `createRef`, `deleteRef`, `createCommitOnBranch`, `revertPullRequest`, `updatePullRequest` — moving
`dev`, opening or retargeting a pull request outside the signed merge's write boundary — reached the transport through
any client. Now a document that contains `mutation` anywhere is sent only when it is, character for character, one of
github_projects.MUTATIONS; read-only documents pass.

Real GitHubClient, recording fake transport: no network, no real GitHub, no model."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator import github_api, github_projects
from feature_coordinator.config import GitHubConfig
from feature_coordinator.errors import IntegrationWriteRefused
from feature_coordinator.github_api import ApiResponse, GitHubClient
from feature_coordinator.integration_github import attribution_query

SHA = "a" * 40


class Recorder:  # pylint: disable=too-few-public-methods
    """A transport that records every request and answers any GraphQL document with `data`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:
        self.calls.append((method, path, body))
        return ApiResponse(200, {}, {"data": {"ok": True}})


def client() -> tuple[GitHubClient, Recorder]:
    recorder = Recorder()
    return GitHubClient(recorder, GitHubConfig(repository="o/r", min_write_interval_seconds=0.0, max_retries=0),
                        sleep=lambda _s: None), recorder


# == what stays available =============================================================================================

PROJECT_MUTATIONS = {"add-item": github_projects.ADD_ITEM, "set-value": github_projects.SET_VALUE,
                     "clear-value": github_projects.CLEAR_VALUE, "create-field": github_projects.CREATE_FIELD,
                     "update-field": github_projects.UPDATE_FIELD, "create-view": github_projects.CREATE_VIEW,
                     "update-view": github_projects.UPDATE_VIEW}


@pytest.mark.parametrize("name", sorted(PROJECT_MUTATIONS))
def test_the_projects_syncs_fixed_mutation_documents_are_sent(name: str):
    api, recorder = client()
    document = PROJECT_MUTATIONS[name]
    assert api.graphql(document, {"input": {"projectId": "PVT_x"}}, mutation=True) == {"ok": True}
    assert recorder.calls == [("POST", "graphql", {"query": document, "variables": {"input": {"projectId": "PVT_x"}}})]
    assert api.writes == 1


def test_the_allowlist_is_exactly_the_projects_syncs_mutation_documents():
    """Every mutation document the Projects module defines is allowed, and nothing else is: the client's pinned digests
    are exactly those documents' SHA-256."""
    defined = {value for name, value in vars(github_projects).items()
               if isinstance(value, str) and name.isupper() and "mutation" in value}
    assert github_projects.MUTATIONS == frozenset(PROJECT_MUTATIONS.values()) == frozenset(defined)
    assert isinstance(github_projects.MUTATIONS, frozenset) and len(github_projects.MUTATIONS) == 7
    assert github_api._ALLOWED_MUTATIONS == frozenset(  # pylint: disable=protected-access
        hashlib.sha256(document.encode("utf-8")).hexdigest() for document in github_projects.MUTATIONS)


READ_ONLY = {
    "project": github_projects.PROJECT_QUERY % "user",
    "issue-items": github_projects.ISSUE_ITEMS_QUERY,
    "item": github_projects.ITEM_QUERY,
    "items": github_projects.ITEMS_QUERY,
    "commit-attribution": attribution_query(3),
    "named-query": "query Viewer { viewer { login } }",
    "anonymous-shorthand": "{ viewer { login } }",
}


@pytest.mark.parametrize("name", sorted(READ_ONLY))
def test_read_only_graphql_queries_are_sent(name: str):
    api, recorder = client()
    assert api.graphql(READ_ONLY[name], {"owner": "o"}) == {"ok": True}
    assert recorder.calls == [("POST", "graphql", {"query": READ_ONLY[name], "variables": {"owner": "o"}})]
    assert api.writes == 0


# == what is refused ===================================================================================================

def _doc(field: str, arguments: str, *, alias: str = "") -> str:
    return f"mutation {{ {alias}{field}(input: {{{arguments}}}) {{ clientMutationId }} }}"


REFUSED = {
    "updateRef (moves dev)": _doc("updateRef", f'refId: "REF_dev", oid: "{SHA}", force: true'),
    "updateRefs": _doc("updateRefs", f'repositoryId: "R", refUpdates: [{{name: "refs/heads/dev", afterOid: "{SHA}"}}]'),
    "createRef": _doc("createRef", f'repositoryId: "R", name: "refs/heads/x", oid: "{SHA}"'),
    "deleteRef": _doc("deleteRef", 'refId: "REF_x"'),
    "createCommitOnBranch": _doc("createCommitOnBranch", 'branch: {repositoryNameWithOwner: "o/r", branchName: "dev"}, '
                                 f'expectedHeadOid: "{SHA}", message: {{headline: "x"}}, '
                                 'fileChanges: {additions: []}'),
    "revertPullRequest (opens a PR)": _doc("revertPullRequest", 'pullRequestId: "PR_x", draft: false'),
    "updatePullRequest (retargets)": _doc("updatePullRequest", 'pullRequestId: "PR_x", baseRefName: "release"'),
    "mergePullRequest": _doc("mergePullRequest", 'pullRequestId: "PR_x"'),
    "enablePullRequestAutoMerge": _doc("enablePullRequestAutoMerge", 'pullRequestId: "PR_x"'),
    "enqueuePullRequest": _doc("enqueuePullRequest", 'pullRequestId: "PR_x"'),
    "mergeBranch": _doc("mergeBranch", 'repositoryId: "R", base: "dev", head: "x"'),
    "createPullRequest": _doc("createPullRequest",
                              'repositoryId: "R", baseRefName: "dev", headRefName: "x", title: "t"'),
    "closePullRequest": _doc("closePullRequest", 'pullRequestId: "PR_x"'),
    "unknown mutation (addStar)": _doc("addStar", 'starrableId: "R"'),
    "unknown mutation (updateIssue)": _doc("updateIssue", 'id: "I_x", title: "t"'),
}

UPDATE_REF = REFUSED["updateRef (moves dev)"]
TRICKS = {
    # an allowed document's operation name, alias or text around another mutation
    "the-allowed-operation-name": github_projects.SET_VALUE.replace(
        "updateProjectV2ItemFieldValue(input: $input) { projectV2Item { id } }",
        "updateRef(input: $input) { clientMutationId }"),
    "aliased-as-the-allowed-field": github_projects.ADD_ITEM.replace(
        "addProjectV2ItemById(input: $input) { item { id project { id number } } }",
        "addProjectV2ItemById: updateRef(input: $input) { clientMutationId }"),
    "an-allowed-document-plus-a-second-field": github_projects.CLEAR_VALUE.replace(
        "clearProjectV2ItemFieldValue(input: $input) { projectV2Item { id } }",
        "clearProjectV2ItemFieldValue(input: $input) { projectV2Item { id } } "
        'x: deleteRef(input: {refId: "REF_dev"}) { clientMutationId }'),
    "an-allowed-document-plus-a-second-operation": github_projects.ADD_ITEM + "\n" + UPDATE_REF,
    "a-query-then-a-mutation": "query A { viewer { login } }\n" + UPDATE_REF,
    "comma-separated-operations": "query A { viewer { login } }," + UPDATE_REF,
    "a-mutation-after-a-comment": "# just a query\n{ viewer { login } }\n" + UPDATE_REF,
    "a-fragment-around-a-mutation": UPDATE_REF + "\nfragment F on Ref { name }",
    "an-allowed-document-reformatted": " ".join(github_projects.SET_VALUE.split()),
    "an-allowed-document-with-trailing-space": github_projects.SET_VALUE + " ",
    "upper-case-keyword": UPDATE_REF.replace("mutation", "MUTATION"),
}


@pytest.mark.parametrize("name", sorted(REFUSED))
@pytest.mark.parametrize("flag", [True, False])
def test_repository_changing_and_unknown_mutations_never_reach_the_transport(name: str, flag: bool):
    """Refs, branches, commits, pull requests (create, merge, auto-merge, merge queue, revert, retarget, close) and
    any mutation not in the allowlist — whatever the caller's `mutation` flag says."""
    api, recorder = client()
    with pytest.raises(IntegrationWriteRefused, match="no GraphQL merge, pull-request, ref, branch or commit mutation"):
        api.graphql(REFUSED[name], mutation=flag)
    assert not recorder.calls and api.writes == 0


@pytest.mark.parametrize("name", sorted(TRICKS))
def test_aliases_operation_names_extra_operations_and_reformatting_cannot_pass_the_allowlist(name: str):
    """The comparison is the whole document, character for character: an allowed operation NAME around another field,
    an alias named like the allowed field, an extra field or operation, a query before a mutation (commas are
    whitespace in GraphQL), a comment, a fragment, or a reformatted copy of an allowed document — all refused."""
    api, recorder = client()
    with pytest.raises(IntegrationWriteRefused):
        api.graphql(TRICKS[name], {"input": {}}, mutation=True)
    assert not recorder.calls


def test_a_document_that_is_not_a_plain_string_is_refused():
    """A str subclass could answer the allowlist's `in` / `==` differently from the text that is sent."""
    class Lying(str):
        """Claims to contain nothing and to equal everything."""

        def __contains__(self, item: object) -> bool:
            return False

        def lower(self) -> str:  # type: ignore[override]
            return ""

        def __eq__(self, other: object) -> bool:
            return True

        __hash__ = str.__hash__

    api, recorder = client()
    with pytest.raises(IntegrationWriteRefused, match="plain str"):
        api.graphql(Lying(UPDATE_REF), mutation=True)
    with pytest.raises(IntegrationWriteRefused, match="plain str"):
        api.graphql(Lying(github_projects.SET_VALUE), mutation=True)
    assert not recorder.calls


def test_the_allowlist_holds_for_a_client_with_integration_anchors_too(tmp_path: Path):
    """Binding the integration trust anchors grants no GraphQL write: the two integration writes are REST only."""
    from test_fc_integration_merge import bind_any  # pylint: disable=import-outside-toplevel
    api, recorder = client()
    bind_any(api, tmp_path)
    with pytest.raises(IntegrationWriteRefused):
        api.graphql(UPDATE_REF, mutation=True)
    assert api.graphql(github_projects.SET_VALUE, {"input": {}}, mutation=True) == {"ok": True}
    assert [body["query"] for _m, _p, body in recorder.calls] == [github_projects.SET_VALUE]
