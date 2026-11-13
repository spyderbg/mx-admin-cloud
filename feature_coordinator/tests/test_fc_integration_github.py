"""W4.7 (M2): complete PR discovery, the reuse rules and GitHub commit attribution (integration_github.py), through
the REAL GitHubClient over a scripted transport. No network, no GitHub."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

from typing import Any

import pytest

from feature_coordinator.errors import GitHubResponseError, GitHubUnavailableError
from feature_coordinator.github_api import ApiResponse, parse_pull
from feature_coordinator.integration_github import (
    SNAPSHOT_FIELDS,
    AttributionUnavailable,
    ConflictingObservation,
    IncompleteListing,
    assess_provenance,
    attribution_query,
    decide_reuse,
    discover_pull_requests,
    fetch_attribution,
    fetch_pull,
    match_candidates,
    parse_attribution,
    parse_link,
    pr_snapshot,
    recorded_pr_changes,
    strict_pages,
    strict_pull,
)
from feature_coordinator.integration_objects import CommitObject, Identity
from feature_coordinator.issues import MockIssueTracker

from fc_integration_rig import BRANCH, OPERATOR, REPOSITORY, FakeGitHub, commit_node, live_client, pr_json

SHA = "a" * 40
PATH = f"repos/{REPOSITORY}/pulls"
PARAMS = {"state": "all", "sort": "created", "direction": "asc"}
LAST_ONLY = f'<https://api.github.com/{PATH}?page=3>; rel="last"'


def listing(count: int, start: int = 1) -> list[dict[str, Any]]:
    return [pr_json(n, head_ref=f"feature/x-{n}", head_sha=f"{n:040x}") for n in range(start, start + count)]


def link(page: int, last: int | None = None, *, next_page: int | None = None) -> str:
    parts = []
    if next_page is not None or page < (last or 0):
        parts.append(f'<https://api.github.com/{PATH}?page={next_page or page + 1}>; rel="next"')
    if last is not None:
        parts.append(f'<https://api.github.com/{PATH}?page={last}>; rel="last"')
    return ", ".join(parts)


def pages(transport: FakeGitHub, mapping: dict[int, tuple[list[dict[str, Any]], str]]) -> None:
    transport.pages = mapping


# -- strict pagination --------------------------------------------------------------------------------------------

def test_parse_link_reads_every_relation():
    header = '<https://x/y?page=2>; rel="next", <https://x/y?page=5>; rel="last"'
    assert parse_link(header) == {"next": "https://x/y?page=2", "last": "https://x/y?page=5"}
    assert not parse_link("")


def test_a_single_short_page_is_complete():
    transport = FakeGitHub(listing(3))
    assert [p["number"] for p in strict_pages(live_client(transport), PATH, PARAMS, max_pages=5)] == [1, 2, 3]


def test_linked_pages_are_followed_to_the_end():
    transport = FakeGitHub(listing(250))
    items = strict_pages(live_client(transport), PATH, PARAMS, max_pages=5)
    assert [p["number"] for p in items] == list(range(1, 251))
    assert sum(1 for _m, p in transport.calls if p.startswith(PATH)) == 3


def test_a_full_page_without_a_next_link_is_confirmed_by_an_empty_next_page():
    transport = FakeGitHub(listing(100))
    assert len(strict_pages(live_client(transport), PATH, PARAMS, max_pages=5)) == 100
    assert any("page=2" in p for _m, p in transport.calls)


@pytest.mark.parametrize(("script", "match"), [
    ({1: (listing(100), ""), 2: (listing(1, 101), "")}, "is not empty"),
    ({1: (listing(40), link(1, 2))}, "is short"),
    ({1: (listing(100), link(1, 3, next_page=3))}, "does not point to page 2"),
    ({1: (listing(100), link(1, 3)), 2: (listing(30, 101), LAST_ONLY)}, "page 3 was announced"),
    ({1: ({"not": "a list"}, "")}, "not a list"),  # type: ignore[dict-item]
    ({1: (listing(101), "")}, "not a list of at most"),
    ({1: (listing(100), link(1, 3)), 2: (listing(100, 101), link(2, 4))}, "inconsistent last page"),
], ids=["full-page-then-more", "short-page-with-next", "next-skips", "ended-before-last", "not-a-list", "oversized",
        "last-changes"])
def test_any_inconsistent_pagination_fails_closed(script: dict[int, Any], match: str):
    transport = FakeGitHub(listing(500))
    pages(transport, script)
    with pytest.raises(IncompleteListing, match=match):
        strict_pages(live_client(transport), PATH, PARAMS, max_pages=5)


def test_more_pages_than_allowed_fail_closed():
    transport = FakeGitHub(listing(350))
    with pytest.raises(IncompleteListing, match="more than 3 pages"):
        strict_pages(live_client(transport), PATH, PARAMS, max_pages=3)


def test_an_api_failure_propagates_and_is_never_a_partial_result():
    transport = FakeGitHub(listing(150))
    transport.fail.append(("page=2", ApiResponse(502, {}, {"message": "bad gateway"})))
    with pytest.raises(GitHubUnavailableError):
        strict_pages(live_client(transport), PATH, PARAMS, max_pages=5)


# -- discovery ---------------------------------------------------------------------------------------------------

def test_discovery_lists_every_state_and_cross_checks_head_and_commit_lookups():
    prs = listing(120) + [pr_json(121, head_sha=SHA, state="closed"), pr_json(122, head_ref="other", head_sha=SHA,
                                                                              merged=True)]
    transport = FakeGitHub(prs)
    found = discover_pull_requests(live_client(transport), repository=REPOSITORY, source_repo=REPOSITORY,
                                   source_ref=BRANCH, source_sha=SHA, max_pages=5)
    assert found.method == "github" and found.listed == 122
    assert [pr.number for pr in found.candidates] == [121, 122]
    queries = [p for _m, p in transport.calls]
    assert any("state=all" in p and "sort=created" in p for p in queries)
    assert any("head=spyderbg%3Aintegration%2Fimprovements" in p for p in queries)
    assert any(f"/commits/{SHA}/pulls" in p for p in queries)
    assert transport.writes() == []


def test_a_pr_missing_from_the_full_listing_fails_closed():
    transport = FakeGitHub(listing(3))
    transport.extra_by_commit.append(pr_json(77, head_sha=SHA))
    with pytest.raises(IncompleteListing, match=r"misses PR\(s\) \[77\]"):
        discover_pull_requests(live_client(transport), repository=REPOSITORY, source_repo=REPOSITORY,
                               source_ref=BRANCH, source_sha=SHA, max_pages=5)


def test_a_pr_listed_twice_fails_closed():
    transport = FakeGitHub(listing(3) + listing(1, 2))
    with pytest.raises(IncompleteListing, match="more than once"):
        discover_pull_requests(live_client(transport), repository=REPOSITORY, source_repo=REPOSITORY,
                               source_ref=BRANCH, source_sha=SHA, max_pages=5)


def test_a_malformed_pr_is_an_error_not_a_skip():
    broken = pr_json(5, head_sha=SHA)
    broken["head"]["sha"] = "short"
    transport = FakeGitHub([broken])
    with pytest.raises(GitHubResponseError):
        discover_pull_requests(live_client(transport), repository=REPOSITORY, source_repo=REPOSITORY,
                               source_ref=BRANCH, source_sha=SHA, max_pages=5)


def test_the_local_tracker_lists_from_memory():
    tracker = MockIssueTracker(repository=REPOSITORY)
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha="f" * 40, state="closed")
    found = discover_pull_requests(tracker, repository=REPOSITORY, source_repo=REPOSITORY, source_ref=BRANCH,
                                   source_sha=SHA, max_pages=5)
    assert found.method == "local-tracker" and [pr.number for pr in found.candidates] == [1]


def pr(number: int, **fields: Any) -> Any:
    return parse_pull(pr_json(number, **{"head_sha": SHA, **fields}))


def test_candidates_are_the_union_of_branch_and_sha_matches_deduplicated():
    prs = [pr(1), pr(2, head_sha="b" * 40), pr(3, head_ref="other"), pr(4, head_ref="x", head_sha="c" * 40),
           pr(5, head_repo="SpyderBG/MX-Admin-Cloud", head_sha="d" * 40), pr(6, head_repo=None, head_ref="gone"),
           pr(7, head_repo="someone/fork", head_sha="e" * 40)]
    found = match_candidates(prs, source_repo=REPOSITORY, source_ref=BRANCH, source_sha=SHA)
    assert [c.number for c in found] == [1, 2, 3, 5, 6]  # 4: neither; 7: another repo's branch at another SHA


FACTS = {"repository": REPOSITORY, "source_repo": REPOSITORY, "source_ref": BRANCH, "source_sha": SHA,
         "base_ref": "dev"}


@pytest.mark.parametrize(("candidates", "recorded", "result", "match"), [
    ([], None, "none", ""),
    ([pr(1)], None, "reuse", ""),
    ([pr(1, draft=True)], 1, "reuse", ""),
    ([pr(1)], 2, "refused", "recorded at intake is not a candidate"),
    ([pr(1, state="closed")], None, "refused", "it is closed"),
    ([pr(1, merged=True)], None, "refused", "it is merged"),
    ([pr(1, base="main")], None, "refused", "targets"),
    ([pr(1, base_repo="someone/upstream")], None, "refused", "targets"),
    ([pr(1, head_sha="f" * 40)], None, "refused", "not source_sha"),
    ([pr(1, head_repo="someone/fork")], None, "refused", "head repository"),
    ([pr(1), pr(2, head_ref="copy")], None, "refused", "more than one candidate"),
    ([], 3, "refused", "no longer a candidate"),
], ids=["none", "reuse", "reuse-recorded-draft", "recorded-mismatch", "closed", "merged", "other-base",
        "other-base-repo", "moved-head", "fork", "two", "recorded-vanished"])
def test_the_reuse_rules_are_exactly_the_design_rules(candidates: list[Any], recorded: int | None, result: str,
                                                      match: str):
    decision = decide_reuse(candidates, recorded_number=recorded, **FACTS)
    assert decision.result == result
    assert decision.number == (candidates[0].number if result == "reuse" else None)
    assert decision.candidates == [c.number for c in candidates]
    if match:
        assert any(match in reason for reason in decision.reasons), decision.reasons


def test_changes_to_a_pinned_pr_are_listed():
    recorded = {"number": 1, "head_repo": REPOSITORY, "head_ref": BRANCH, "head_sha": SHA, "base_repo": REPOSITORY,
                "base_ref": "dev"}
    assert not recorded_pr_changes(recorded, pr(1))
    assert "head moved" in recorded_pr_changes(recorded, pr(1, head_sha="f" * 40))[0]
    assert "now targets" in recorded_pr_changes(recorded, pr(1, base="main"))[0]
    assert "head is now" in recorded_pr_changes(recorded, pr(1, head_repo=None))[0]


# -- attribution ---------------------------------------------------------------------------------------------------

def commit(sha: str, email: str = "dev@example.invalid") -> CommitObject:
    who = Identity("Dev", email, 1700000000)
    return CommitObject(sha=sha, tree="b" * 40, parents=(), author=who, committer=who)


def test_attribution_is_fetched_in_batches_with_one_query_shape():
    shas = [f"{n:040x}" for n in range(1, 121)]
    transport = FakeGitHub()
    found = fetch_attribution(live_client(transport), REPOSITORY, shas)
    assert set(found) == set(shas) and all(a.author_login == OPERATOR for a in found.values())
    assert sum(1 for _m, p in transport.calls if p == "graphql") == 3
    assert attribution_query(2).startswith("query IntegrationCommitAttribution($owner: String!, $name: String!, "
                                           "$o0: GitObjectID!, $o1: GitObjectID!)")
    assert fetch_attribution(MockIssueTracker(repository=REPOSITORY), REPOSITORY, shas) is None


@pytest.mark.parametrize(("node", "error", "match"), [
    (None, AttributionUnavailable, "does not know commit"),
    ({"oid": "b" * 40}, GitHubResponseError, "not that commit"),
    # each node is complete except for the one defect under test (an incomplete node fails earlier, see below)
    ({**commit_node(SHA), "author": {"user": {"login": "bad login!"}}}, GitHubResponseError, "login"),
    ({**commit_node(SHA), "signature": {"state": "VALID", "wasSignedByGitHub": False, "signer": None}},
     GitHubResponseError, r"signature lacks \['isValid'\]"),
    ({**commit_node(SHA), "signature": {"state": "valid; drop", "isValid": True, "wasSignedByGitHub": False,
                                        "signer": None}}, GitHubResponseError, "unexpected signature"),
], ids=["unknown", "other-oid", "bad-login", "signature-without-validity", "bad-state"])
def test_attribution_answers_are_validated(node: Any, error: type, match: str):
    with pytest.raises(error, match=match):
        parse_attribution(SHA, node)


def signed(signer: str | None, *, valid: bool = True, state: str = "VALID", by_github: bool = False) -> dict:
    return {"isValid": valid, "state": state, "wasSignedByGitHub": by_github,
            "signer": {"login": signer} if signer else None}


@pytest.mark.parametrize(("node", "level", "refused", "deferred"), [
    (commit_node(SHA), "unverified", "", ""),
    (commit_node(SHA, author=None), "unverified", "", ""),
    (commit_node(SHA, signature=signed(OPERATOR)), "verified", "", ""),
    (commit_node(SHA, signature=signed(OPERATOR, by_github=True)), "unverified", "", ""),
    (commit_node(SHA, signature=signed(None, by_github=True)), "unverified", "", ""),  # API-settable author
    (commit_node(SHA, author=None, signature=signed(None, by_github=True)), "unverified", "", ""),
    (commit_node(SHA, author="mallory"), "unverified", "account mallory", ""),
    (commit_node(SHA, signature=signed("mallory")), "unverified", "valid signature of mallory", ""),
    (commit_node(SHA, author="mallory", signature=signed(None, by_github=True)), "unverified", "account mallory", ""),
    (commit_node(SHA, signature=signed(OPERATOR, valid=False, state="UNKNOWN_KEY")), "unverified", "", ""),
    (commit_node(SHA, signature=signed(OPERATOR, valid=False, state="OCSP_PENDING")), "unverified", "", "OCSP_PENDING"),
], ids=["unsigned", "unattributed", "signed-trusted", "github-signed-trusted-signer", "github-signed-trusted-author",
        "github-signed-no-account",
        "untrusted-account", "untrusted-signer", "github-signed-untrusted-author", "unknown-key", "transient"])
# github-signed-trusted-author: a commit GitHub signed itself is never "verified" — its author can be set via the API
def test_provenance_levels_never_take_an_e_mail_for_identity(node: dict, level: str, refused: str, deferred: str):
    attributions = {SHA: parse_attribution(SHA, node)}
    assessment = assess_provenance([commit(SHA)], attributions, ["SpyderBG"])  # logins are case-insensitive
    assert assessment.levels == {SHA: level} and assessment.source == "github"
    assert (refused in " ".join(assessment.refusals)) if refused else not assessment.refusals
    assert (deferred in assessment.deferred) if deferred else not assessment.deferred
    assert assessment.needs_acceptance is (level != "verified")


def test_without_attribution_every_commit_is_unverifiable():
    assessment = assess_provenance([commit(SHA), commit("b" * 40)], None, [OPERATOR])
    assert assessment.source == "unavailable" and set(assessment.levels.values()) == {"unverifiable"}
    assert assessment.needs_acceptance and not assessment.refusals


# -- Codex review F2: every observation of one PR must agree --------------------------------------------------------

class Scripted(FakeGitHub):
    """The full listing from *prs*; the head query and the commit query answer what a test scripts for them."""

    def __init__(self, prs: list[dict[str, Any]], *, by_head: list[dict[str, Any]] | None = None,
                 by_commit: list[dict[str, Any]] | None = None) -> None:
        super().__init__(prs)
        self.by_head, self.by_commit = by_head, by_commit

    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:
        if "head=" in path and self.by_head is not None:
            self.calls.append((method, path))
            return ApiResponse(200, {}, self.by_head)
        if "/commits/" in path and self.by_commit is not None:
            self.calls.append((method, path))
            return ApiResponse(200, {}, self.by_commit)
        return super().request(method, path, body)


def discover(transport: FakeGitHub, **kwargs: Any) -> Any:
    return discover_pull_requests(live_client(transport), repository=REPOSITORY, source_repo=REPOSITORY,
                                  source_ref=BRANCH, source_sha=SHA, max_pages=20, **kwargs)


CONTRADICTIONS = {
    # Codex scenario A: the listing has the PR at another SHA/branch, the SHA lookup at the pinned SHA
    "codex-a-commit-query-at-pinned-sha": (pr_json(123, head_ref="another-branch", head_sha="b" * 40),
                                           {"by_commit": [pr_json(123, head_ref="another-branch", head_sha=SHA)]}),
    "codex-a-head-query-moved": (pr_json(123, head_ref="unrelated", head_sha="b" * 40),
                                 {"by_head": [pr_json(123, head_sha=SHA)]}),
    # Codex scenario B: the listing has a compatible open PR, the later lookups report it closed
    "codex-b-closed": (pr_json(123, head_sha=SHA), {"by_head": [pr_json(123, head_sha=SHA, state="closed")],
                                                    "by_commit": [pr_json(123, head_sha=SHA, state="closed")]}),
    "merged-in-commit-query": (pr_json(123, head_sha=SHA), {"by_commit": [pr_json(123, head_sha=SHA, merged=True)]}),
    "retargeted": (pr_json(123, head_sha=SHA), {"by_head": [pr_json(123, head_sha=SHA, base="main")]}),
    "other-base-repository": (pr_json(123, head_sha=SHA), {"by_commit": [pr_json(123, head_sha=SHA,
                                                                                 base_repo="someone/else")]}),
    "draft-flipped": (pr_json(123, head_sha=SHA), {"by_head": [pr_json(123, head_sha=SHA, draft=True)]}),
    "head-repository-differs": (pr_json(123, head_sha=SHA, head_repo="SpyderBG/fork"),
                                {"by_commit": [pr_json(123, head_sha=SHA, head_repo="spyderbg/other")]}),
}


@pytest.mark.parametrize("case", sorted(CONTRADICTIONS))
def test_contradictory_snapshots_of_one_pr_are_never_used(case: str):
    listed, scripted = CONTRADICTIONS[case]
    transport = Scripted([listed], **scripted)
    with pytest.raises(ConflictingObservation, match=r"PR #123: the listing and the (head|commit) query disagree"):
        discover(transport)
    assert transport.writes() == []


def test_consistent_snapshots_decide_as_before():
    listed = pr_json(123, head_sha=SHA)
    transport = Scripted([listed, pr_json(7, head_ref="feature/x", head_sha="c" * 40)], by_head=[listed],
                         by_commit=[listed])
    assert [pr.number for pr in discover(transport).candidates] == [123]
    unrelated = pr_json(123, head_ref="another-branch", head_sha="b" * 40)
    assert not discover(Scripted([unrelated], by_head=[], by_commit=[])).candidates


def test_a_head_query_answer_that_does_not_have_the_queried_head_is_not_used():
    other = pr_json(5, head_ref="feature/other", head_sha="e" * 40)
    with pytest.raises(ConflictingObservation, match="does not answer it"):
        discover(Scripted([other], by_head=[other], by_commit=[]))
    fork = pr_json(6, head_sha="e" * 40, head_repo="mallory/mx-admin-cloud")   # same branch name, other owner
    with pytest.raises(ConflictingObservation, match="does not answer it"):
        discover(Scripted([fork], by_head=[fork], by_commit=[]))


def test_a_pr_listed_twice_by_a_lookup_is_an_incomplete_answer():
    listed = pr_json(123, head_sha=SHA)
    with pytest.raises(IncompleteListing, match="the commit query lists a PR more than once"):
        discover(Scripted([listed], by_head=[listed], by_commit=[listed, listed]))


def test_further_observations_by_the_caller_are_cross_checked_too():
    listed = pr_json(1, head_sha=SHA)
    transport = Scripted([listed], by_head=[listed], by_commit=[listed])
    read_alone = parse_pull(pr_json(1, head_sha=SHA, state="closed"))
    with pytest.raises(ConflictingObservation, match="the read of the intake PR disagree on state 'open' vs 'closed'"):
        discover(transport, observed={"the read of the intake PR": [read_alone]})
    assert [pr.number for pr in discover(transport, observed={"x": [parse_pull(listed)]}).candidates] == [1]
    tracker = MockIssueTracker(repository=REPOSITORY)          # the local tracker is cross-checked as well
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=SHA)
    stale = parse_pull({**pr_json(1, head_sha="f" * 40)})
    with pytest.raises(ConflictingObservation, match="head_sha"):
        discover_pull_requests(tracker, repository=REPOSITORY, source_repo=REPOSITORY, source_ref=BRANCH,
                               source_sha=SHA, max_pages=20, observed={"the read of the intake PR": [stale]})


def test_the_snapshot_covers_every_security_relevant_field():
    pr = parse_pull(pr_json(1, head_sha=SHA))
    assert set(pr_snapshot(pr)) == set(SNAPSHOT_FIELDS) == {"state", "merged", "draft", "head_repo", "head",
                                                             "head_sha", "base_repo", "base"}
    assert pr_snapshot(parse_pull(pr_json(1, head_sha=SHA, head_repo="SpyderBG/MX-Admin-Cloud"))) == pr_snapshot(pr)


def _without(data: dict[str, Any], dotted: str) -> dict[str, Any]:
    copy = {**data, "head": dict(data["head"]), "base": dict(data["base"])}
    outer, _sep, inner = dotted.partition(".")
    if inner:
        del copy[outer][inner]
    else:
        del copy[outer]
    return copy


@pytest.mark.parametrize("field", ["state", "draft", "merged_at", "head", "base", "head.repo", "head.sha", "base.ref",
                                   "base.repo"])
def test_a_pr_object_missing_a_field_the_rules_read_is_malformed(field: str):
    data = pr_json(1, head_sha=SHA)
    with pytest.raises(GitHubResponseError):
        strict_pull(_without(data, field))
    transport = FakeGitHub([_without(data, field)])
    with pytest.raises(GitHubResponseError):
        discover(transport)


def test_a_single_pr_answer_must_say_merged_and_a_base_repository():
    data = pr_json(1, head_sha=SHA)
    assert strict_pull(data, single=True).number == 1
    with pytest.raises(GitHubResponseError, match=r"lacks \['merged'\]"):
        strict_pull({k: v for k, v in data.items() if k != "merged"}, single=True)
    assert strict_pull({k: v for k, v in data.items() if k != "merged"}).number == 1   # a listed PR has no `merged`
    with pytest.raises(GitHubResponseError, match="base repository is missing"):
        strict_pull({**data, "base": {**data["base"], "repo": None}})


def test_the_single_pr_read_must_answer_for_the_pr_asked_for():
    transport = FakeGitHub([pr_json(123, head_sha=SHA)])
    assert fetch_pull(live_client(transport), REPOSITORY, 123).number == 123
    transport.fail.append((f"GET repos/{REPOSITORY}/pulls/123", ApiResponse(200, {}, pr_json(124, head_sha=SHA))))
    with pytest.raises(ConflictingObservation, match="for PR #123 describes PR #124"):
        fetch_pull(live_client(transport), REPOSITORY, 123)
    tracker = MockIssueTracker(repository=REPOSITORY)
    tracker.add_pull_request_as(OPERATOR, BRANCH, "dev", head_sha=SHA)
    assert fetch_pull(tracker, REPOSITORY, 1).number == 1
    tracker.get_pull_request = lambda number: parse_pull(pr_json(2, head_sha=SHA))  # type: ignore[method-assign]
    with pytest.raises(ConflictingObservation, match="for PR #1 describes PR #2"):
        fetch_pull(tracker, REPOSITORY, 1)


# -- Codex review F5: incomplete GitHub attribution is never verified ---------------------------------------------

def _signature(**changes: Any) -> dict[str, Any]:
    data = {"isValid": True, "state": "VALID", "wasSignedByGitHub": False, "signer": {"login": OPERATOR}}
    data.update(changes)
    return {key: value for key, value in data.items() if value is not ...}


@pytest.mark.parametrize(("node", "match"), [
    # Codex's case: wasSignedByGitHub omitted from an otherwise "valid" signature
    ({**commit_node(SHA), "signature": _signature(wasSignedByGitHub=...)}, r"lacks \['wasSignedByGitHub'\]"),
    ({**commit_node(SHA), "signature": _signature(wasSignedByGitHub=None)}, "given explicitly"),
    ({**commit_node(SHA), "signature": _signature(wasSignedByGitHub="false")}, "given explicitly"),
    ({**commit_node(SHA), "signature": _signature(isValid=...)}, r"lacks \['isValid'\]"),
    ({**commit_node(SHA), "signature": _signature(isValid=None)}, "given explicitly"),
    ({**commit_node(SHA), "signature": _signature(state=...)}, r"lacks \['state'\]"),
    ({**commit_node(SHA), "signature": _signature(state=None)}, "given explicitly"),
    ({**commit_node(SHA), "signature": _signature(signer=...)}, r"lacks \['signer'\]"),
    ({**commit_node(SHA), "signature": _signature(signer={"name": "x"})}, "login"),
    ({**commit_node(SHA), "signature": _signature(isValid=False)}, "contradictory signature"),
    ({**commit_node(SHA), "signature": _signature(state="UNKNOWN_KEY")}, "contradictory signature"),
    ({**commit_node(SHA), "signature": ["VALID"]}, "unexpected signature"),
    ({k: v for k, v in commit_node(SHA, signature=_signature()).items() if k != "signature"},
     r"lacks \['signature'\]"),
    ({k: v for k, v in commit_node(SHA).items() if k != "author"}, r"lacks \['author'\]"),
    ({k: v for k, v in commit_node(SHA).items() if k != "committer"}, r"lacks \['committer'\]"),
    ({**commit_node(SHA), "author": "spyderbg"}, "user field is missing"),
    ({**commit_node(SHA), "author": {}}, "user field is missing"),
    ({**commit_node(SHA), "committer": {"user": "spyderbg"}}, "login"),
], ids=["no-github-flag", "null-github-flag", "string-github-flag", "no-isvalid", "null-isvalid", "no-state",
        "null-state", "no-signer", "signer-without-login", "valid-state-not-valid", "invalid-state-valid",
        "signature-not-object", "no-signature-field", "no-author-field", "no-committer-field", "author-not-object",
        "author-without-user", "user-not-object"])
def test_incomplete_or_contradictory_attribution_is_malformed_and_never_verified(node: dict[str, Any], match: str):
    with pytest.raises(GitHubResponseError, match=match):
        parse_attribution(SHA, node)


def test_complete_attribution_still_distinguishes_explicit_values():
    complete = parse_attribution(SHA, commit_node(SHA, signature=_signature()))
    assert complete.signed_by_github is False and complete.signature_valid is True
    levels = assess_provenance([commit(SHA)], {SHA: complete}, [OPERATOR]).levels
    assert levels == {SHA: "verified"}
    by_github = parse_attribution(SHA, commit_node(SHA, signature=_signature(wasSignedByGitHub=True)))
    assert assess_provenance([commit(SHA)], {SHA: by_github}, [OPERATOR]).levels == {SHA: "unverified"}
    unsigned = parse_attribution(SHA, commit_node(SHA, author=None))
    assert unsigned.signature_state == "UNSIGNED" and unsigned.author_login is None
    no_actor = parse_attribution(SHA, {**commit_node(SHA), "author": None, "committer": None})   # schema: nullable
    assert no_actor.author_login is None and no_actor.committer_login is None


def test_an_incomplete_attribution_answer_fails_the_whole_fetch():
    shas = ["a" * 40, "b" * 40]
    transport = FakeGitHub()
    transport.attribution[shas[1]] = {**commit_node(shas[1]), "signature": _signature(wasSignedByGitHub=...)}
    with pytest.raises(GitHubResponseError, match="wasSignedByGitHub"):
        fetch_attribution(live_client(transport), REPOSITORY, shas)
