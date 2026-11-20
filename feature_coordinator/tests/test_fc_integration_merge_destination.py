"""W4.7 (M5), the routing re-review of Codex F1: the DESTINATION of an integration write is its authorized identity.

The write boundary authorized the approved repository and PR, but the client sent the request to `{repo_path}/…` — a
public, mutable field. `client.repo_path = "repos/unapproved/target"` sent the PR creation and the merge to another
repository; `client.repo_path = "repos/<approved>/pulls/555/merge?unused="` merged PR #555 into `release` although the
signed approval authorized PR #101 into `dev`. Now github_api.integration_route builds the one method and path of a
write from exact values (the configured repository and, for a merge, the PR number) — `repo_path` plays no part —, the
boundary compares it with the route it derives from the anchored configuration and the recorded intent before it
records anything, and the request core sends exactly the authorized request and nothing else.

Every test checks the arguments that actually reach the transport. Disposable checkout/origin, throw-away operator keys
and `FakeMergeGitHub` behind the real GitHubClient: no network, no real GitHub, no model."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument
# pylint: disable=too-many-arguments,too-many-locals

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.errors import IntegrationWriteRefused, PullRequestPolicyError
from feature_coordinator.github_api import GitHubClient, integration_route
from feature_coordinator.integration_authority import IntegrationWriteAuthority
from feature_coordinator.issue_sync import SimulatedCrash

from fc_integration_merge_rig import FakeMergeGitHub, anchors_for, bare, merge
from fc_integration_rig import REPOSITORY, Rig, github_origin, origin  # pylint: disable=unused-import
from test_fc_integration_merge import Recorder, client_with
from test_fc_integration_merge_boundary import at_boundary, dispatches, prepared
from test_fc_integration_review import launchers, of_kind, rrig  # pylint: disable=unused-import
from test_fc_integration_validate import key, rig, seeded, tracker  # pylint: disable=unused-import

ACTIONS = ["create-pr", "merge"]
CREATE = ("POST", f"repos/{REPOSITORY}/pulls")
MERGE = ("PUT", f"repos/{REPOSITORY}/pulls/101/merge")


def intent_open(rig: Rig, launchers: Any, origin_path: Path, action: str) -> tuple[Any, FakeMergeGitHub, dict]:
    """A signed job whose write-ahead intent for *action* is recorded and open — the run crashed just before the
    request (a PR #101 exists for `merge`): the attempt a caller could try to send somewhere else."""
    job, fake = prepared(rig, launchers, origin_path, action)
    with pytest.raises(SimulatedCrash):
        merge(rig, job.job_id, faults=("before-merge-request",) if action == "merge" else ("after-pr-intent",))
    return rig.jobs.load(job.job_id), fake, of_kind(rig, job.job_id, "merge-attempt")[-1]


def outgoing(fake: FakeMergeGitHub) -> list[tuple[str, str, Any]]:
    """Every non-GET request exactly as the transport receives it (method, path, body)."""
    sent: list[tuple[str, str, Any]] = []
    real = fake.request

    def request(method: str, path: str, body: Any = None) -> Any:
        if method != "GET":
            sent.append((method, path, body))
        return real(method, path, body)
    fake.request = request  # type: ignore[method-assign]
    return sent


def send(rig: Rig, job: Any, intent: dict, action: str) -> Any:
    if action == "merge":
        return rig.client.merge_integration_pull_request(101, job.source["sha"], job_id=job.job_id,
                                                         attempt=intent["id"])
    return rig.client.create_integration_pull_request(job.source["ref"], job.base["ref"], "t", "b", job_id=job.job_id,
                                                      attempt=intent["id"])


def expected(job: Any, action: str) -> tuple[str, str, Any]:
    if action == "merge":
        return (*MERGE, {"merge_method": "merge", "sha": job.source["sha"]})
    return (*CREATE, {"title": "t", "body": "b", "head": job.source["ref"], "base": job.base["ref"], "draft": False,
                      "maintainer_can_modify": False})


# == the original reproductions ======================================================================================

REDIRECTS = {
    "another-repository": "repos/unapproved/target",
    "query-suffix-to-another-pr": f"repos/{REPOSITORY}/pulls/555/merge?unused=",
    "fragment-suffix": f"repos/{REPOSITORY}/pulls/555/merge#",
    "dot-segments": f"repos/{REPOSITORY}/../../unapproved/target",
    "trailing-slash": f"repos/{REPOSITORY}/",
    "letter-case": f"repos/{REPOSITORY.upper()}",
    "absolute-url": "https://api.github.com/repos/unapproved/target",
}


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("redirect", sorted(REDIRECTS))
def test_f1_a_mutated_repo_path_does_not_change_where_an_integration_write_goes(rrig: Rig, launchers: Any,
                                                                               github_origin: Path, action: str,
                                                                               redirect: str):
    """Defect A (and its prefix/suffix/query/normalization variants): the client is bound to an approved job and
    `repo_path` is changed before an otherwise legitimate write. Before: the request went to the changed destination.
    Now: exactly the authorized request reaches the transport, and the legitimate write succeeds."""
    job, fake, intent = intent_open(rrig, launchers, github_origin, action)
    before = bare(github_origin, "rev-parse", "dev").strip()
    sent = outgoing(fake)
    rrig.client.repo_path = REDIRECTS[redirect]
    result = send(rrig, job, intent, action)
    assert sent == [expected(job, action)]
    assert [entry["attempt"] for entry in dispatches(rrig, job.job_id)] == [intent["id"]]
    if action == "merge":
        assert result.merged and fake.merge_requests == [{"number": 101, "merge_method": "merge",
                                                          "sha": job.source["sha"]}]
        assert bare(github_origin, "rev-parse", "dev").strip() != before          # the approved PR, into dev
    else:
        assert result.number == 101 and len(fake.created) == 1 and not result.draft
        assert bare(github_origin, "rev-parse", "dev").strip() == before


def test_f1_a_route_suffix_cannot_merge_another_pr_into_another_branch(rrig: Rig, launchers: Any,
                                                                       github_origin: Path):
    """Defect B, exactly: PR #555 targets `release`; `repo_path` ends in `/pulls/555/merge?unused=`; the approved PR is
    #101 into `dev`. Before: #555 was merged into release. Now: the one PUT goes to #101, #555 and release stay as they
    were."""
    job, fake, intent = intent_open(rrig, launchers, github_origin, "merge")
    bare(github_origin, "branch", "release", "dev")
    other = copy.deepcopy(fake.prs[0])
    other["number"], other["base"]["ref"] = 555, "release"
    fake.prs.append(other)
    dev, release = (bare(github_origin, "rev-parse", ref).strip() for ref in ("dev", "release"))
    sent = outgoing(fake)
    rrig.client.repo_path = f"repos/{REPOSITORY}/pulls/555/merge?unused="
    assert send(rrig, job, intent, "merge").merged
    assert sent == [(*MERGE, {"merge_method": "merge", "sha": job.source["sha"]})]
    assert fake.merge_requests == [{"number": 101, "merge_method": "merge", "sha": job.source["sha"]}]
    assert fake.prs[0]["merged"] and not other["merged"]
    assert bare(github_origin, "rev-parse", "release").strip() == release
    assert bare(github_origin, "rev-parse", "dev").strip() != dev


def test_f1_a_whole_merge_run_with_a_mutated_repo_path_writes_only_to_the_approved_destination(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch):
    """The workflow end to end (creation, binding, readiness, merge, verification) with `repo_path` pointing elsewhere
    from the first write on: every write goes to the authorized route, and the job completes."""
    job, fake = prepared(rrig, launchers, github_origin, "create-pr")
    sent = outgoing(fake)
    done = at_boundary(monkeypatch, "create-pr", lambda: setattr(rrig.client, "repo_path", "repos/unapproved/target"))
    report = merge(rrig, job.job_id)
    assert done and rrig.client.repo_path == "repos/unapproved/target"
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert [(method, path) for method, path, _body in sent] == [CREATE, MERGE]


# == the repository or the route changed later ========================================================================

@pytest.mark.parametrize("action", ACTIONS)
def test_f1_a_repository_changed_after_the_client_was_bound_is_refused(rrig: Rig, launchers: Any,
                                                                       github_origin: Path, action: str):
    """The client's settings name another repository after the trust anchors were bound: the boundary compares the
    repository with the anchored configuration's, refuses, records nothing — nothing reaches the transport."""
    job, fake, intent = intent_open(rrig, launchers, github_origin, action)
    sent = outgoing(fake)
    rrig.client.settings = dataclasses.replace(rrig.client.settings, repository="unapproved/target")
    with pytest.raises(IntegrationWriteRefused, match="the configured repository is"):
        send(rrig, job, intent, action)
    assert not sent and not dispatches(rrig, job.job_id)


@pytest.mark.parametrize("when", ["during-the-checks", "inside-the-dispatch-transaction", "after-the-authorization"])
@pytest.mark.parametrize("action", ACTIONS)
def test_f1_the_repository_and_pr_routing_changed_after_the_checks_does_not_move_the_write(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, action: str, when: str):
    """`repo_path` and the client's settings change while the boundary is checking, inside its final transaction, or
    right after it authorized: the route was fixed (from exact values) before the authorization and is the one sent."""
    job, fake, intent = intent_open(rrig, launchers, github_origin, action)
    sent = outgoing(fake)
    client = rrig.client

    def redirect() -> None:
        client.repo_path = f"repos/{REPOSITORY}/pulls/555/merge?unused="
        client.settings = dataclasses.replace(client.settings, repository="unapproved/target")

    owner, name = {"during-the-checks": (IntegrationWriteAuthority, "_path_problems"),
                   "inside-the-dispatch-transaction": (IntegrationWriteAuthority, "_unchanged"),
                   "after-the-authorization": (GitHubClient, "_authorize_integration")}[when]
    real = getattr(owner, name)

    def hooked(self: Any, *args: Any, **kwargs: Any) -> Any:
        if when == "after-the-authorization":
            result = real(self, *args, **kwargs)
            redirect()
            return result
        redirect()
        return real(self, *args, **kwargs)
    monkeypatch.setattr(owner, name, hooked)
    send(rrig, job, intent, action)
    assert sent == [expected(job, action)] and len(dispatches(rrig, job.job_id)) == 1


ROUTES = {
    "another-pr": ("PUT", f"repos/{REPOSITORY}/pulls/555/merge"),
    "another-repository": ("PUT", "repos/unapproved/target/pulls/101/merge"),
    "query-string": ("PUT", f"repos/{REPOSITORY}/pulls/101/merge?merge_method=squash"),
    "trailing-slash": ("PUT", f"repos/{REPOSITORY}/pulls/101/merge/"),
    "dot-segments": ("PUT", f"repos/{REPOSITORY}/pulls/555/../101/merge"),
    "letter-case": ("PUT", f"repos/{REPOSITORY.upper()}/pulls/101/merge"),
    "another-method": ("POST", f"repos/{REPOSITORY}/pulls/101/merge"),
    "the-creation-route": CREATE,
}


def test_f1_the_boundary_refuses_any_route_but_the_authorized_one_before_recording_anything(
        rrig: Rig, launchers: Any, github_origin: Path):
    """The boundary itself: a request whose route is not exactly the authorized one — another PR (the merge target
    would change: another base branch), another repository, a query, a trailing slash, dot segments, another letter
    case, another method — is refused BEFORE the dispatch is recorded; the exact route is authorized once."""
    job, _fake, intent = intent_open(rrig, launchers, github_origin, "merge")
    authority = IntegrationWriteAuthority(anchors_for(rrig), rrig.client)
    facts = {"job_id": job.job_id, "attempt": intent["id"], "repository": REPOSITORY,
             "request": {"number": 101, "merge_method": "merge", "sha": job.source["sha"]}}
    for name, route in ROUTES.items():
        with pytest.raises(IntegrationWriteRefused, match="the request would go to") as exc:
            authority.authorize("merge", route=route, **facts)
        assert f"goes to PUT repos/{REPOSITORY}/pulls/101/merge" in " ".join(exc.value.reasons), name
        assert not dispatches(rrig, job.job_id), name
    authority.authorize("merge", route=MERGE, **facts)
    assert [entry["attempt"] for entry in dispatches(rrig, job.job_id)] == [intent["id"]]


def test_f1_the_creation_route_names_no_pr_and_the_boundary_checks_it_too(rrig: Rig, launchers: Any,
                                                                          github_origin: Path):
    job, _fake, intent = intent_open(rrig, launchers, github_origin, "create-pr")
    authority = IntegrationWriteAuthority(anchors_for(rrig), rrig.client)
    facts = {"job_id": job.job_id, "attempt": intent["id"], "repository": REPOSITORY,
             "request": {"head": job.source["ref"], "base": job.base["ref"], "draft": False}}
    for route in (("POST", "repos/unapproved/target/pulls"), ("POST", f"repos/{REPOSITORY}/pulls?draft=true"),
                  ("POST", f"repos/{REPOSITORY}/pulls/"), MERGE):
        with pytest.raises(IntegrationWriteRefused, match="the request would go to"):
            authority.authorize("create-pr", route=route, **facts)
    assert not dispatches(rrig, job.job_id)
    authority.authorize("create-pr", route=CREATE, **facts)
    assert len(dispatches(rrig, job.job_id)) == 1


# == the route builder and the request core ===========================================================================

def test_f1_integration_route_is_built_from_exact_values_only():
    assert integration_route("create-pr", REPOSITORY) == CREATE
    assert integration_route("merge", REPOSITORY, 101) == MERGE
    for repository in ("unapproved/target/pulls/555/merge?x=", "o/r/", "o/r?x", "o/../r", "/o/r", "o", "o/r.git",
                       "o/r#x", "o/r/x"):
        with pytest.raises(IntegrationWriteRefused):
            integration_route("merge", repository, 101)
    for number in (0, -1, True, "101", 101.0, None):
        with pytest.raises(IntegrationWriteRefused):
            integration_route("merge", REPOSITORY, number)  # type: ignore[arg-type]
    with pytest.raises(IntegrationWriteRefused):
        integration_route("create-pr", REPOSITORY, 101)
    with pytest.raises(IntegrationWriteRefused):
        integration_route("delete", REPOSITORY)


DISGUISED = ["repos/o/r/pulls?", "repos/o/r/pulls#", "REPOS/o/r/PULLS?", "repos//o/r//pulls?", "repos/o%2Fr/pulls?",
             "repos/o/r/x/../pulls?", "repos/o/r/pulls/?", "repos/o/r/%70ulls?",
             "https://api.github.com/repos/o/r/pulls?"]


@pytest.mark.parametrize("prefix", DISGUISED)
def test_f1_no_disguised_pr_creation_reaches_the_transport_through_another_method(prefix: str):
    """Defence in depth on the request core: a PR-creation POST disguised by a query, fragment, letter case, repeated
    slashes, percent-encoding or dot segments in a mutated `repo_path` (here through create_issue's
    `{repo_path}/issues`) is recognized on the normalized route and refused."""
    recorder = Recorder()
    client = client_with(recorder)
    client.repo_path = prefix
    with pytest.raises(IntegrationWriteRefused, match="a pull request is created only through"):
        client.create_issue("t", "b", [])
    assert not recorder.calls


def test_f1_an_authorized_write_lets_exactly_one_request_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The request core's grant is the one authorized (method, path), for ONE request: a second request inside the
    same window — whatever it is — is refused, and so is a request to any other route."""
    from test_fc_integration_merge import SHA, JOB, bind_any  # pylint: disable=import-outside-toplevel
    monkeypatch.setattr(IntegrationWriteAuthority, "authorize", lambda self, operation, **facts: None)
    recorder = Recorder()
    client = bind_any(client_with(recorder), tmp_path)
    with client._sending(MERGE):
        with pytest.raises(IntegrationWriteRefused, match="nothing else is sent in its place"):
            client._call("PUT", f"repos/{REPOSITORY}/pulls/555/merge", {}, idempotent=False, retry=False)
    with client._sending(MERGE):
        client._call(*MERGE, {"merge_method": "merge", "sha": SHA}, idempotent=False, retry=False)
        with pytest.raises(IntegrationWriteRefused, match="merged only through"):
            client._call(*MERGE, {"merge_method": "merge", "sha": SHA}, idempotent=False, retry=False)
    assert recorder.calls == [(*MERGE, {"merge_method": "merge", "sha": SHA})]
    assert client.merge_integration_pull_request(7, SHA, job_id=JOB, attempt="mat-0001").merged
    assert recorder.calls[-1][:2] == ("PUT", f"repos/{REPOSITORY}/pulls/7/merge")


# == the Feature Publisher ============================================================================================

def test_f1_the_feature_publishers_draft_pr_is_unchanged():
    """The publisher's Draft PR (W4.3.3) still goes through create_pull_request: authorized once per (head, base),
    Draft only, `github.publish.enabled` — and no integration grant is involved."""
    recorder = Recorder()
    client = client_with(recorder, publish=True)
    with pytest.raises(PullRequestPolicyError):
        client.create_pull_request("feature/x", "dev", "t", "b")
    client.authorize_pull_request("feature/x", "dev")
    assert client.create_pull_request("feature/x", "dev", "t", "b").number == 7
    assert recorder.calls == [("POST", f"repos/{REPOSITORY}/pulls",
                               {"title": "t", "body": "b", "head": "feature/x", "base": "dev", "draft": True,
                                "maintainer_can_modify": False})]
    with pytest.raises(PullRequestPolicyError):
        client.create_pull_request("feature/x", "dev", "t", "b")        # authorized once
    assert client._integration_write is None and not client._pr_creation
