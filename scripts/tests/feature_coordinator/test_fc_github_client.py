"""The real GitHub adapter without GitHub: `gh api --include` parsing through a fake `gh`
executable, and retry/rate-limit/validation logic through a scripted transport.

Failures that are unsafe to trigger against GitHub (rate limits, 5xx, timeouts, malformed
responses) are exercised here; the live acceptance tests are in test_fc_github_live.py."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.config import GitHubConfig
from feature_coordinator.errors import (
    GitHubAuthError,
    GitHubCliMissingError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubResponseError,
    GitHubUncertainWriteError,
    GitHubUnavailableError,
    GitHubValidationError,
    PullRequestPolicyError,
    TrackerError,
)
from feature_coordinator.github_api import ApiResponse, GhCliTransport, GitHubClient, TransportFailure, parse_include, \
    sanitize

FAKE_TOKEN = "ghp_" + "A" * 36
SETTINGS = GitHubConfig(repository="octo/repo", min_write_interval_seconds=0.0, max_retries=2,
                        backoff_base_seconds=1.0, max_rate_limit_wait_seconds=120.0)


def issue_json(number: int = 7, **over: Any) -> dict[str, Any]:
    data = {"number": number, "id": 1000 + number, "title": "T", "body": "B", "state": "open",
            "labels": [{"name": "feature:test"}], "user": {"login": "bot"},
            "html_url": f"https://github.com/octo/repo/issues/{number}", "updated_at": "2026-10-17T00:00:00Z",
            "comments": 0, "state_reason": None}
    data.update(over)
    return data


def http(status: int, body: Any = None, headers: dict[str, str] | None = None, reason: str = "X") -> str:
    head = [f"HTTP/2.0 {status} {reason}", "Content-Type: application/json; charset=utf-8",
            *(f"{k}: {v}" for k, v in (headers or {}).items())]
    return "\r\n".join(head) + "\r\n\r\n" + (json.dumps(body) if body is not None else "")


# -- fake gh executable -------------------------------------------------------------------

@pytest.fixture
def fake_gh(tmp_path: Path):
    target = tmp_path / "bin" / "gh"
    target.parent.mkdir()
    source = Path(__file__).with_name("fc_fake_gh.py").read_text(encoding="utf-8")
    target.write_text(source.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1), encoding="utf-8")
    target.chmod(0o755)
    script, log = tmp_path / "steps.json", tmp_path / "calls.jsonl"

    def make(steps: list[dict[str, Any]], timeout: float = 10.0) -> GhCliTransport:
        script.write_text(json.dumps(steps), encoding="utf-8")
        env = {**os.environ, "FAKE_GH_SCRIPT": str(script), "FAKE_GH_LOG": str(log)}
        return GhCliTransport(str(target), timeout=timeout, env=env)

    def calls() -> list[dict[str, Any]]:
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return make, calls


def test_transport_parses_status_headers_and_json_and_sends_bodies_on_stdin(fake_gh) -> None:
    make, calls = fake_gh
    transport = make([{"stdout": http(201, issue_json(), {"X-RateLimit-Remaining": "4999"})}])
    resp = transport.request("POST", "repos/octo/repo/issues", {"title": "T"})
    assert resp.status == 201 and resp.headers["x-ratelimit-remaining"] == "4999" and resp.data["number"] == 7
    call = calls()[0]
    assert call["argv"][:5] == ["api", "--include", "--method", "POST", "--hostname"]
    assert "Accept: application/vnd.github+json" in call["argv"] and "--input" in call["argv"]
    assert json.loads(call["stdin"]) == {"title": "T"}
    assert not any("token" in arg.lower() or arg.startswith("ghp_") for arg in call["argv"])


def test_http_errors_are_classified_and_messages_sanitized(fake_gh) -> None:
    make, _calls = fake_gh
    cases = [(401, GitHubAuthError), (403, GitHubPermissionError), (404, GitHubNotFoundError),
             (410, GitHubNotFoundError), (409, GitHubValidationError), (422, GitHubValidationError),
             (418, GitHubResponseError)]
    for status, error in cases:
        transport = make([{"stdout": http(status, {"message": f"bad \x1b[31m{FAKE_TOKEN}"}), "exit": 1,
                           "stderr": f"gh: HTTP {status}"}])
        with pytest.raises(error) as info:
            GitHubClient(transport, SETTINGS, sleep=lambda _s: None).get_issue(7)
        assert FAKE_TOKEN not in str(info.value) and "\x1b" not in str(info.value)
        assert "[redacted]" in str(info.value) and info.value.status == status


def test_no_http_response_is_retried_for_reads_and_uncertain_for_creates(fake_gh) -> None:
    make, calls = fake_gh
    down = {"exit": 1, "stderr": "error connecting to api.github.com\ncheck your internet connection"}
    transport = make([down, down, {"stdout": http(200, issue_json())}])
    client = GitHubClient(transport, SETTINGS, sleep=lambda _s: None)
    assert client.get_issue(7).number == 7 and client.retries == 2
    transport = make([down, {"stdout": http(201, issue_json())}])
    before = len(calls())
    with pytest.raises(GitHubUncertainWriteError):
        GitHubClient(transport, SETTINGS, sleep=lambda _s: None).create_issue("T", "B", ["feature:test"])
    assert len(calls()) == before + 1  # never blindly re-sent: the sync searches first


def test_timeout_kills_the_gh_process_group(fake_gh) -> None:
    make, _calls = fake_gh
    transport = make([{"sleep": 30, "stdout": http(200, issue_json())}], timeout=0.5)
    started = time.monotonic()
    with pytest.raises(TransportFailure) as info:
        transport.request("POST", "repos/octo/repo/issues/7/comments", {"body": "x"})
    assert info.value.maybe_applied and time.monotonic() - started < 10


def test_missing_and_unauthenticated_gh(fake_gh, tmp_path: Path) -> None:
    with pytest.raises(GitHubCliMissingError):
        GhCliTransport(str(tmp_path / "no-such-gh")).request("GET", "user")
    make, _calls = fake_gh
    transport = make([{"exit": 4, "stderr": "To get started with GitHub CLI, please run:  gh auth login"}])
    with pytest.raises(GitHubAuthError):
        GitHubClient(transport, SETTINGS).viewer_login()


def test_parse_include_handles_interim_responses_and_bodies() -> None:
    out = "HTTP/1.1 100 Continue\r\n\r\nHTTP/2.0 200 OK\r\nX-A: 1\r\n\r\n{\"a\": \"b\\r\\nc\"}"
    status, headers, body = parse_include(out)  # type: ignore[misc]
    assert status == 200 and headers == {"x-a": "1"} and json.loads(body) == {"a": "b\r\nc"}
    assert parse_include("not http") is None


def test_malformed_json_body_is_a_response_error(fake_gh) -> None:
    make, _calls = fake_gh
    with pytest.raises(GitHubResponseError):
        GitHubClient(make([{"stdout": "HTTP/2.0 200 OK\r\n\r\n<html>"}]), SETTINGS).get_issue(1)


# -- scripted transport: retries, rate limits, pacing, pagination, validation -------------------

class Scripted:  # pylint: disable=too-few-public-methods
    """A transport that returns scripted ApiResponses (or raises) and records requests."""

    def __init__(self, *responses: ApiResponse | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:
        self.requests.append((method, path, body))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(status, headers or {}, data)


class Clock:  # pylint: disable=too-few-public-methods
    """Fake monotonic/wall clock advanced by the client's sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def client_for(transport: Scripted, settings: GitHubConfig = SETTINGS) -> tuple[GitHubClient, Clock]:
    clock = Clock()
    return GitHubClient(transport, settings, sleep=clock.sleep, clock=lambda: clock.now,
                        wall=lambda: clock.now), clock


def test_5xx_is_retried_with_exponential_backoff_then_bounded() -> None:
    client, clock = client_for(Scripted(ok({}, 502), ok({}, 503), ok(issue_json())))
    assert client.get_issue(7).number == 7 and clock.sleeps == [1.0, 2.0]
    client, clock = client_for(Scripted(ok({}, 500), ok({}, 500), ok({}, 500)))
    with pytest.raises(GitHubUnavailableError, match="after 3 attempts"):
        client.get_issue(7)


def test_5xx_on_a_non_idempotent_write_is_uncertain_and_not_resent() -> None:
    transport = Scripted(ok({}, 502), ok(issue_json(), 201))
    client, _clock = client_for(transport)
    with pytest.raises(GitHubUncertainWriteError):
        client.create_comment(7, "hello")
    assert len(transport.requests) == 1


def test_429_and_secondary_rate_limits_wait_as_github_requires() -> None:
    transport = Scripted(ok({"message": "slow down"}, 429, {"retry-after": "7"}), ok(issue_json(), 201))
    client, clock = client_for(transport)
    assert client.create_issue("T", "B", ["feature:test"]).number == 7  # a rejected request is safe to resend
    assert clock.sleeps == [7.0] and len(transport.requests) == 2
    transport = Scripted(ok({"message": "You have exceeded a secondary rate limit."}, 403), ok(issue_json()))
    client, clock = client_for(transport)
    client.get_issue(7)
    assert clock.sleeps == [60.0]  # no header: at least one minute


def test_primary_rate_limit_waits_until_reset_or_defers() -> None:
    transport = Scripted(ok({"message": "API rate limit exceeded"}, 403,
                            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}), ok(issue_json()))
    client, clock = client_for(transport)
    client.get_issue(7)
    assert clock.sleeps == [31.0]
    far = Scripted(ok({"message": "API rate limit exceeded"}, 403,
                      {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "999999"}))
    with pytest.raises(GitHubRateLimitError, match="retry after"):
        client_for(far)[0].get_issue(7)


def test_writes_are_paced_and_counted() -> None:
    transport = Scripted(*(ok([{"name": "feature:test"}]) for _ in range(3)))
    client, clock = client_for(transport, dataclasses.replace(SETTINGS, min_write_interval_seconds=1.5))
    for _ in range(3):
        client.add_labels(7, ["feature:test"])
    assert clock.sleeps == [1.5, 1.5] and client.writes == 3


def test_pagination_reads_every_page_and_refuses_a_partial_result() -> None:
    page = [issue_json(n) for n in range(1, 101)]
    transport = Scripted(ok(page), ok(page), ok([issue_json(500)]))
    client, _clock = client_for(transport)
    assert len(client.list_issues(labels=["feature:parent"], creator="bot")) == 201
    assert "labels=feature%3Aparent" in transport.requests[0][1] and "page=3" in transport.requests[2][1]
    capped = dataclasses.replace(SETTINGS, max_pages=2)
    with pytest.raises(GitHubResponseError, match="refusing a partial result"):
        client_for(Scripted(ok(page), ok(page)), capped)[0].list_issues()


@pytest.mark.parametrize("change", [{"number": "7"}, {"number": 0}, {"labels": "feature:test"}, {"state": "merged"},
                                    {"user": {"login": "bad login"}}, {"html_url": "http://evil"},
                                    {"id": True}, {"body": 5}, {"updated_at": "yesterday"}])
def test_unexpected_response_formats_are_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(GitHubResponseError):
        client_for(Scripted(ok({**issue_json(), **change})))[0].get_issue(7)


def test_pull_requests_are_flagged_and_never_created() -> None:
    client, _clock = client_for(Scripted(ok(issue_json(pull_request={"url": "x"}))))
    assert client.get_issue(7).is_pull_request
    transport = Scripted()
    with pytest.raises(PullRequestPolicyError, match="disabled in Phase 4"):
        client_for(transport)[0].create_pull_request("feature/x", "dev", "T", "B")
    assert not transport.requests
    transport = Scripted(ok([]))
    client_for(transport)[0].list_pull_requests("feature/x")
    assert "head=octo%3Afeature%2Fx" in transport.requests[0][1]


def test_labels_are_feature_namespace_only_and_never_edited() -> None:
    transport = Scripted(ok({"name": "feature:test"}))
    client, _clock = client_for(transport)
    assert client.ensure_label("feature:test", "ffffff", "d") == "exists"
    assert [r[0] for r in transport.requests] == ["GET"]  # an existing label is never PATCHed
    transport = Scripted(ok({}, 404), ok({}, 422))
    assert client_for(transport)[0].ensure_label("feature:x", "ffffff", "d") == "exists"
    with pytest.raises(TrackerError):
        client.add_labels(7, ["mx-auto"])
    transport = Scripted(ok({"message": "Label does not exist"}, 404))
    client_for(transport)[0].remove_label(7, "feature:blocked")  # absent label: idempotent
    assert transport.requests[0][1].endswith("labels/feature%3Ablocked")


def test_author_permission_and_viewer() -> None:
    transport = Scripted(ok({"login": "spyderbg"}), ok({"permission": "admin"}), ok({}, 404))
    client, _clock = client_for(transport)
    assert client.viewer_login() == "spyderbg" and client.viewer_login() == "spyderbg"  # cached
    assert client.author_permission("spyderbg") == "admin" and client.author_permission("stranger") == "none"
    assert client.author_permission("../evil") == "none" and len(transport.requests) == 3


def test_sanitize_redacts_tokens_and_strips_terminal_escapes() -> None:
    text = f"token {FAKE_TOKEN} github_pat_{'b' * 30} Bearer abcdefghijklmnop \x1b]0;title\x07\x1b[2J ok"
    clean = sanitize(text)
    assert FAKE_TOKEN not in clean and "github_pat_" not in clean and "abcdefghijklmnop" not in clean
    assert "\x1b" not in clean and "\x07" not in clean and clean.endswith("ok")
    assert len(sanitize("x" * 1000)) == 300


def test_gh_is_installed_for_the_live_adapter() -> None:
    if not shutil.which("gh"):
        pytest.skip("gh is not installed on this host")
    assert GitHubClient.from_config(GitHubConfig()).transport.executable == "gh"  # type: ignore[attr-defined]
