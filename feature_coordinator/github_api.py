"""The real GitHub Issues adapter (Phase 4): REST calls through the authenticated GitHub CLI.

Why `gh api`: it uses the operator's existing login (keyring or `GH_TOKEN`), so this code
never requests, reads, stores or prints a token, and it needs no new dependency. Each call is
one `gh api --include` subprocess; the status line and headers it prints give the HTTP status
and the rate-limit headers, so errors are classified exactly as with a plain REST client.

Layers:
  * GhCliTransport — one request: argv, JSON on stdin, deadline (the whole process group is
    killed on timeout), `--include` parsing. Raises only GitHub* errors; messages are
    sanitized (`sanitize`).
  * GitHubClient — the TrackerClient for one repository: retries with exponential backoff for
    idempotent requests, primary/secondary rate-limit handling (Retry-After, X-RateLimit-Reset,
    otherwise at least one minute) bounded by `max_rate_limit_wait_seconds`, pacing between
    writes, bounded pagination, and strict validation of every response (untrusted input).
    Non-idempotent writes (create Issue, create comment) are never blindly retried after an
    ambiguous failure: GitHubUncertainWriteError tells the synchronizer to search first.

Pull requests (W4.3.3, publish.py). Reading is allowed (`list_pull_requests`, `get_pull_request`: every
field an adoption or completion decision uses is validated). Creating is narrowly scoped: refused before
any request unless `github.publish.enabled` is true AND the coordinator authorized exactly this head and
base for one creation (`authorize_pull_request`, called by the publisher after its checks for an
approved GitHub Issue's feature); only Draft PRs from a `feature/` branch, never a merge (no merge
request exists in this client). Any other caller — including the operator-approved Phase 2
`open_pull_request` — is refused, as in Phase 4.

GraphQL (Phase 4 extension, GitHub Projects): `GitHubClient.graphql()` sends one query or
mutation through the same transport and the same retry, pacing and rate-limit rules. A
GraphQL response is HTTP 200 even when it failed, so its `errors` list is classified like an
HTTP status (NOT_FOUND, FORBIDDEN, INSUFFICIENT_SCOPES, RATE_LIMITED, …). Queries are never
paced and never counted as writes.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.parse import quote, urlencode

from .config import GitHubConfig
from .errors import (
    GitHubAuthError,
    GitHubCliMissingError,
    GitHubError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubResponseError,
    GitHubUncertainWriteError,
    GitHubUnavailableError,
    GitHubValidationError,
    PullRequestPolicyError,
)
from .issues import PullRequest, RemoteComment, RemoteIssue, RemoteIssueEvent, check_feature_labels, check_worker_labels

API_VERSION = "2022-11-28"
PER_PAGE = 100
_TOKEN_RE = re.compile(r"(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}"
                       r"|(?i:bearer|token)\s+[A-Za-z0-9_.\-]{12,})")
_CONTROL_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$")
_STATUS_RE = re.compile(r"^HTTP/[0-9.]+ (\d{3})")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_=-]{2,120}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_PR_HEAD_RE = re.compile(r"^feature/[a-z0-9][a-z0-9._-]{0,99}$")
GH_ENV = {"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1", "CLICOLOR": "0",
          "GH_PAGER": "cat", "PAGER": "cat", "GH_SPINNER_DISABLED": "1"}


# Secrets an agent CLI might print (W4): provider API keys and OAuth tokens, and NAME=value /
# "name": value assignments of keys, tokens, secrets, passwords.
_SECRET_RE = re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{16,}|\bAIza[A-Za-z0-9_\-]{30,}")
_ASSIGNMENT_RE = re.compile(r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTHORIZATION|CREDENTIALS?)"
                            r"[\"']?\s*[:=]\s*[\"']?)(?!\[redacted\])(?:(?:bearer|basic|token)\s+)?[^\s\"',;}]{4,}")


def redact_secrets(text: str) -> str:
    """Only the secrets above replaced by [redacted]; the text keeps its shape (W4: task reasons
    that quote an agent CLI's error output)."""
    return _ASSIGNMENT_RE.sub(r"\1[redacted]", _SECRET_RE.sub("[redacted]", text))


def sanitize(text: Any, limit: int = 300) -> str:
    """Make untrusted or diagnostic text safe to log or print: tokens redacted, terminal
    control sequences removed, whitespace collapsed, length bounded."""
    value = _TOKEN_RE.sub("[redacted]", str(text))
    value = _CONTROL_RE.sub("", value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


@dataclass(frozen=True)
class ApiResponse:
    """One HTTP response: status, lower-cased headers and the decoded JSON body (or None)."""

    status: int
    headers: Mapping[str, str]
    data: Any

    @property
    def message(self) -> str:
        """GitHub's error message, sanitized."""
        if isinstance(self.data, dict):
            return sanitize(self.data.get("message", ""))
        return ""


class Transport(Protocol):  # pylint: disable=too-few-public-methods
    """Sends one request; raises GitHubUnavailableError (with `uncertain`) when no response arrived."""

    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:
        """Send *method* *path* with an optional JSON *body*."""


class TransportFailure(GitHubUnavailableError):
    """No HTTP response (timeout, connection failure). `maybe_applied` is True when the request
    may have reached GitHub (a timeout after sending)."""

    def __init__(self, message: str, *, maybe_applied: bool) -> None:
        super().__init__(message)
        self.maybe_applied = maybe_applied


def parse_include(stdout: str) -> tuple[int, dict[str, str], str] | None:
    """(status, headers, body) from `gh api --include` output; None without a status line."""
    lines = stdout.split("\n")
    index = 0
    status: int | None = None
    headers: dict[str, str] = {}
    while index < len(lines) and lines[index].startswith("HTTP/"):
        match = _STATUS_RE.match(lines[index])
        if match is None:
            return None
        status, headers = int(match.group(1)), {}
        index += 1
        while index < len(lines) and lines[index].strip("\r") != "":
            name, _sep, value = lines[index].partition(":")
            headers[name.strip().lower()] = value.strip()
            index += 1
        index += 1
    if status is None:
        return None
    return status, headers, "\n".join(lines[index:])


class GhCliTransport:  # pylint: disable=too-few-public-methods
    """`gh api` as an HTTP transport. Uses the existing gh login; never sees the token."""

    def __init__(self, executable: str = "gh", *, hostname: str = "github.com", timeout: float = 30.0,
                 env: Mapping[str, str] | None = None) -> None:
        self.executable = executable
        self.hostname = hostname
        self.timeout = timeout
        self.env = dict(os.environ if env is None else env)
        self.env.update(GH_ENV)

    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:
        """One request. See the module docstring."""
        argv = [self.executable, "api", "--include", "--method", method, "--hostname", self.hostname,
                "-H", "Accept: application/vnd.github+json", "-H", f"X-GitHub-Api-Version: {API_VERSION}", path]
        payload = None
        if body is not None:
            argv += ["--input", "-"]
            payload = json.dumps(body)
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # pylint: disable=consider-using-with
                                    stderr=subprocess.PIPE, text=True, env=self.env, start_new_session=True)
        except FileNotFoundError as exc:
            raise GitHubCliMissingError(f"GitHub CLI {self.executable!r} is not installed") from exc
        except PermissionError as exc:
            raise GitHubCliMissingError(f"GitHub CLI {self.executable!r} is not executable") from exc
        try:
            stdout, stderr = proc.communicate(payload, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            raise TransportFailure(f"{method} {path}: no response within {self.timeout:.0f}s",
                                   maybe_applied=True) from None
        parsed = parse_include(stdout)
        if parsed is None:
            detail = sanitize(stderr or stdout)
            if proc.returncode != 0 and re.search(r"gh auth login|not logged in|authentication", detail, re.I):
                raise GitHubAuthError(f"gh is not authenticated: {detail}")
            # gh reports connection problems before any response; a write may still have been sent.
            raise TransportFailure(f"{method} {path}: gh exited {proc.returncode} without an HTTP response: "
                                   f"{detail}", maybe_applied=method != "GET")
        status, headers, text = parsed
        data = None
        if text.strip():
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise GitHubResponseError(f"{method} {path}: HTTP {status} body is not JSON", status=status) from exc
        return ApiResponse(status, headers, data)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.communicate()


# ---------------------------------------------------------------------------
# Response validation (GitHub data is untrusted input)
# ---------------------------------------------------------------------------

def _field(data: Mapping[str, Any], name: str, kind: type | tuple[type, ...], where: str) -> Any:
    value = data.get(name)
    if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
        raise GitHubResponseError(f"{where}: field {name!r} has an unexpected type")
    return value


def _login(data: Any, where: str) -> str:
    if not isinstance(data, dict):
        raise GitHubResponseError(f"{where}: missing user")
    login = _field(data, "login", str, where)
    if not _LOGIN_RE.fullmatch(login):
        raise GitHubResponseError(f"{where}: invalid login")
    return login


def parse_issue(data: Any) -> RemoteIssue:
    """A validated RemoteIssue from an Issues API object."""
    if not isinstance(data, dict):
        raise GitHubResponseError("issue: not an object")
    number = _field(data, "number", int, "issue")
    where = f"issue #{number}"
    if number <= 0:
        raise GitHubResponseError(f"{where}: invalid number")
    state = _field(data, "state", str, where)
    if state not in ("open", "closed"):
        raise GitHubResponseError(f"{where}: unexpected state")
    labels_raw = _field(data, "labels", list, where)
    labels = []
    for label in labels_raw:
        name = label.get("name") if isinstance(label, dict) else label
        if not isinstance(name, str) or not name or len(name) > 100:
            raise GitHubResponseError(f"{where}: invalid label")
        labels.append(name)
    body = data.get("body") or ""
    if not isinstance(body, str):
        raise GitHubResponseError(f"{where}: body is not text")
    url = data.get("html_url") or ""
    if not isinstance(url, str) or url and not url.startswith("https://"):
        raise GitHubResponseError(f"{where}: unexpected html_url")
    updated = data.get("updated_at") or ""
    if not isinstance(updated, str) or updated and not _ISO_RE.fullmatch(updated):
        raise GitHubResponseError(f"{where}: unexpected updated_at")
    reason = data.get("state_reason")
    node_id = data.get("node_id") or ""
    if not isinstance(node_id, str) or node_id and not _NODE_ID_RE.fullmatch(node_id):
        raise GitHubResponseError(f"{where}: unexpected node_id")
    return RemoteIssue(number=number, id=_field(data, "id", int, where), title=_field(data, "title", str, where),
                       body=body, state=state, labels=tuple(labels), author=_login(data.get("user"), where),
                       url=url, is_pull_request=data.get("pull_request") is not None, updated_at=updated,
                       comments=int(data.get("comments") or 0),
                       state_reason=reason if isinstance(reason, str) else None, node_id=node_id)


def parse_comment(data: Any) -> RemoteComment:
    """A validated RemoteComment."""
    if not isinstance(data, dict):
        raise GitHubResponseError("comment: not an object")
    comment_id = _field(data, "id", int, "comment")
    where = f"comment {comment_id}"
    created = _field(data, "created_at", str, where)
    if not _ISO_RE.fullmatch(created):
        raise GitHubResponseError(f"{where}: unexpected created_at")
    body = data.get("body") or ""
    if not isinstance(body, str):
        raise GitHubResponseError(f"{where}: body is not text")
    url = data.get("html_url") or ""
    updated = data.get("updated_at")
    return RemoteComment(id=comment_id, author=_login(data.get("user"), where), body=body, created_at=created,
                         url=url if isinstance(url, str) else "",
                         updated_at=updated if isinstance(updated, str) and _ISO_RE.fullmatch(updated) else "")


def parse_issue_event(data: Any) -> RemoteIssueEvent:
    """W4.5: a validated Issue event (`GET .../issues/{n}/events`). A deleted actor ("ghost") or a missing
    label stays "" — such an event never authorizes anything."""
    if not isinstance(data, dict):
        raise GitHubResponseError("issue event: not an object")
    event_id = _field(data, "id", int, "issue event")
    where = f"issue event {event_id}"
    kind = _field(data, "event", str, where)
    created = _field(data, "created_at", str, where)
    if not _ISO_RE.fullmatch(created):
        raise GitHubResponseError(f"{where}: unexpected created_at")
    actor = data.get("actor")
    login = actor.get("login") if isinstance(actor, dict) else None
    label = data.get("label")
    name = label.get("name") if isinstance(label, dict) else None
    return RemoteIssueEvent(id=event_id, event=kind[:60],
                            actor=login if isinstance(login, str) and _LOGIN_RE.fullmatch(login) else "",
                            created_at=created, label=name if isinstance(name, str) and len(name) <= 100 else "")


def parse_workflow_run(data: Any) -> dict[str, Any]:
    """W4.5: the fields of a GitHub Actions run that an approval receipt is checked against."""
    if not isinstance(data, dict):
        raise GitHubResponseError("workflow run: not an object")
    run_id = _field(data, "id", int, "workflow run")
    where = f"workflow run {run_id}"
    repo = data.get("repository") if isinstance(data.get("repository"), dict) else {}

    def text(name: str) -> str:
        value = data.get(name)
        return value[:300] if isinstance(value, str) else ""

    def login(name: str) -> str:
        user = data.get(name)
        value = user.get("login") if isinstance(user, dict) else None
        return value if isinstance(value, str) and _LOGIN_RE.fullmatch(value) else ""
    attempt = data.get("run_attempt")
    created = text("created_at")
    return {"id": run_id, "event": text("event"), "path": text("path"), "head_branch": text("head_branch"),
            "created_at": created if _ISO_RE.fullmatch(created) else "",
            "status": text("status"), "actor": login("actor"), "triggering_actor": login("triggering_actor"),
            "run_attempt": attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else 0,
            "repository": str(repo.get("full_name", ""))[:200], "where": where}


def _optional_text(data: Mapping[str, Any], name: str, pattern: re.Pattern[str], where: str) -> str:
    value = data.get(name)
    if value is None:
        return ""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise GitHubResponseError(f"{where}: unexpected {name}")
    return value


def _pull_end(data: Any, side: str, where: str) -> tuple[str, str, str]:
    """(ref, sha, repository full name) of a PR's head or base; the repository is "" when GitHub reports
    none (a deleted fork)."""
    if not isinstance(data, dict):
        raise GitHubResponseError(f"{where}: missing {side}")
    ref, sha = _field(data, "ref", str, where), _field(data, "sha", str, where)
    if not _REF_RE.fullmatch(ref) or not _SHA_RE.fullmatch(sha):
        raise GitHubResponseError(f"{where}: unexpected {side} ref or sha")
    repo = data.get("repo")
    name = repo.get("full_name") if isinstance(repo, dict) else ""
    if not isinstance(name, str) or name and not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}", name):
        raise GitHubResponseError(f"{where}: unexpected {side} repository")
    return ref, sha, name


def parse_pull(data: Any) -> PullRequest:
    """A validated PullRequest from a Pulls API object (list or single; only the single one says `merged`,
    so a listed PR counts as merged when it has `merged_at`)."""
    if not isinstance(data, dict):
        raise GitHubResponseError("pull request: not an object")
    number = _field(data, "number", int, "pull request")
    where = f"pull request #{number}"
    state = _field(data, "state", str, where)
    if number <= 0 or state not in ("open", "closed"):
        raise GitHubResponseError(f"{where}: invalid number or state")
    head, head_sha, head_repo = _pull_end(data.get("head"), "head", where)
    base, _base_sha, base_repo = _pull_end(data.get("base"), "base", where)
    merged_at = _optional_text(data, "merged_at", _ISO_RE, where)
    merged = data.get("merged", bool(merged_at))
    url, body = data.get("html_url") or "", data.get("body") or ""
    if not isinstance(url, str) or url and not url.startswith("https://"):
        raise GitHubResponseError(f"{where}: unexpected html_url")
    if not all(isinstance(value, kind) for value, kind in ((merged, bool), (body, str),
                                                            (data.get("draft", False), bool))):
        raise GitHubResponseError(f"{where}: unexpected merged, draft or body")
    merged_by = data.get("merged_by")
    return PullRequest(number=number, head=head, base=base, title=_field(data, "title", str, where), body=body,
                       state=state, head_sha=head_sha, head_repo=head_repo, base_repo=base_repo,
                       draft=bool(data.get("draft", False)), merged=merged, merged_at=merged_at,
                       merged_by=_login(merged_by, where) if merged_by else "",
                       merge_commit_sha=_optional_text(data, "merge_commit_sha", _SHA_RE, where),
                       author=_login(data.get("user"), where), url=url,
                       node_id=_optional_text(data, "node_id", _NODE_ID_RE, where), id=_field(data, "id", int, where))


def _operation_name(query: str) -> str:
    match = re.match(r"\s*(query|mutation)\s+([A-Za-z_][A-Za-z0-9_]*)", query)
    return match.group(2) if match else "anonymous"


def _graphql_error(errors: Any) -> tuple[str, str]:
    """(type, sanitized message) of the first GraphQL error."""
    first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    kind = first.get("type") or (first.get("extensions") or {}).get("code") or "UNKNOWN"
    return sanitize(kind, 40), sanitize(first.get("message", ""), 240)


def _graphql_exception(operation: str, kind: str, message: str) -> GitHubError:
    text = f"graphql {operation}: {kind}: {message or 'no message'}"
    if kind == "INSUFFICIENT_SCOPES":
        return GitHubPermissionError(f"{text} — the gh login lacks a scope (for projects: `project`); the "
                                     "operator must run `gh auth refresh -s project`", status=403)
    if kind == "FORBIDDEN":
        return GitHubPermissionError(f"{text} — the account lacks this capability", status=403)
    if kind == "NOT_FOUND":
        return GitHubNotFoundError(text, status=404)
    if kind in ("UNPROCESSABLE", "BAD_USER_INPUT", "UNPROCESSABLE_ENTITY", "argumentLiteralsIncompatible",
                "undefinedField", "variableMismatch", "missingRequiredArguments"):
        return GitHubValidationError(text, status=422)
    return GitHubResponseError(text)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GitHubClient:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """TrackerClient over the GitHub REST API for one repository."""

    live = True

    def __init__(self, transport: Transport, settings: GitHubConfig, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.transport = transport
        self.settings = settings
        self.repo_path = f"repos/{settings.repository}"
        self.sleep = sleep
        self.clock = clock
        self.wall = wall
        self.requests = 0
        self.writes = 0
        self.retries = 0
        self.log: list[str] = []          # "METHOD path -> status" (no bodies, no headers)
        self._last_write = float("-inf")
        self._viewer: str | None = None
        self._authorized: set[tuple[str, str]] = set()  # W4.3.3: (head, base) of the one PR the publisher may open

    @classmethod
    def from_config(cls, settings: GitHubConfig, **kwargs: Any) -> "GitHubClient":
        """A client using the configured `gh` executable."""
        transport = GhCliTransport(settings.gh_executable, hostname=settings.hostname,
                                   timeout=settings.request_timeout_seconds)
        return cls(transport, settings, **kwargs)

    # -- request core -----------------------------------------------------------

    def _pace(self) -> None:
        wait = self._last_write + self.settings.min_write_interval_seconds - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last_write = self.clock()

    def _backoff(self, attempt: int) -> float:
        return min(self.settings.backoff_base_seconds * (2 ** attempt), self.settings.max_backoff_seconds)

    def _rate_limit_wait(self, resp: ApiResponse, attempt: int) -> float | None:
        """Seconds to wait for a rate-limit response, or None if it is not one."""
        headers = resp.headers
        message = resp.message.lower()
        secondary = "secondary rate limit" in message or "abuse" in message
        primary = headers.get("x-ratelimit-remaining") == "0"
        if resp.status not in (403, 429) or not (resp.status == 429 or secondary or primary
                                                  or "retry-after" in headers):
            return None
        if "retry-after" in headers:
            try:
                return max(float(headers["retry-after"]), 1.0)
            except ValueError:
                pass
        if primary and headers.get("x-ratelimit-reset", "").isdigit():
            return max(int(headers["x-ratelimit-reset"]) - self.wall(), 0) + 1.0
        return 60.0 * (2 ** attempt)  # GitHub: otherwise wait at least one minute, then back off

    def _call(self, method: str, path: str, body: Any = None, *, idempotent: bool,  # pylint: disable=too-many-branches
              ok: Iterable[int] = (), write: bool | None = None) -> ApiResponse:
        """Send with bounded retries. *ok* lists extra statuses returned instead of raised.
        *write* defaults to "not a GET" (a GraphQL query is a POST that writes nothing)."""
        write = method != "GET" if write is None else write
        ok = set(ok)
        attempt = 0
        while True:
            if write:
                self._pace()
            self.requests += 1
            try:
                resp = self.transport.request(method, path, body)
            except TransportFailure as exc:
                self.log.append(f"{method} {path} -> no response")
                if write and not idempotent and exc.maybe_applied:
                    raise GitHubUncertainWriteError(f"{exc} (the write may have been applied)") from exc
                if attempt >= self.settings.max_retries:
                    raise
                self.retries += 1
                self.sleep(self._backoff(attempt))
                attempt += 1
                continue
            self.log.append(f"{method} {path} -> {resp.status}")
            if write:
                self.writes += 1
            if 200 <= resp.status < 300 or resp.status in ok:
                return resp
            wait = self._rate_limit_wait(resp, attempt)
            if wait is not None:
                # A rate-limited request was rejected, so even a non-idempotent write is safe to resend.
                if wait > self.settings.max_rate_limit_wait_seconds or attempt >= self.settings.max_retries:
                    raise GitHubRateLimitError(f"{method} {path}: rate limited (retry after {wait:.0f}s): "
                                               f"{resp.message}", status=resp.status)
                self.retries += 1
                self.sleep(wait)
                attempt += 1
                continue
            if resp.status >= 500:
                if write and not idempotent:
                    raise GitHubUncertainWriteError(f"{method} {path}: HTTP {resp.status} (the write may have been "
                                                    "applied)", status=resp.status)
                if attempt >= self.settings.max_retries:
                    raise GitHubUnavailableError(f"{method} {path}: HTTP {resp.status} after {attempt + 1} attempts",
                                                 status=resp.status)
                self.retries += 1
                self.sleep(self._backoff(attempt))
                attempt += 1
                continue
            raise self._error(method, path, resp)

    @staticmethod
    def _error(method: str, path: str, resp: ApiResponse) -> GitHubError:
        text = f"{method} {path}: HTTP {resp.status}: {resp.message or 'no message'}"
        if resp.status == 401:
            return GitHubAuthError(f"{text} — the gh login is missing or expired (run `gh auth login`)",
                                   status=401)
        if resp.status == 403:
            return GitHubPermissionError(f"{text} — the account lacks this capability", status=403)
        if resp.status in (404, 410):
            return GitHubNotFoundError(text, status=resp.status)
        if resp.status in (409, 422):
            return GitHubValidationError(text, status=resp.status)
        return GitHubResponseError(text, status=resp.status)

    def _pages(self, path: str, params: Mapping[str, str]) -> list[Any]:
        items: list[Any] = []
        for page in range(1, self.settings.max_pages + 1):
            query = urlencode({**params, "per_page": str(PER_PAGE), "page": str(page)})
            data = self._call("GET", f"{path}?{query}", idempotent=True).data
            if not isinstance(data, list):
                raise GitHubResponseError(f"GET {path}: expected a list")
            items.extend(data)
            if len(data) < PER_PAGE:
                return items
        raise GitHubResponseError(f"GET {path}: more than {self.settings.max_pages} pages; refusing a partial result")

    # -- GraphQL -------------------------------------------------------------------

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None, *, mutation: bool = False,
                idempotent: bool = True) -> dict[str, Any]:
        """The `data` of one GraphQL operation. `errors` raise like the matching HTTP status;
        RATE_LIMITED waits (bounded) and retries. A non-idempotent mutation that may have been
        applied raises GitHubUncertainWriteError, exactly like a REST create."""
        body = {"query": query, "variables": dict(variables or {})}
        operation = _operation_name(query)
        attempt = 0
        while True:
            resp = self._call("POST", "graphql", body, idempotent=idempotent or not mutation, write=mutation)
            data = resp.data
            if not isinstance(data, dict):
                raise GitHubResponseError(f"graphql {operation}: response is not an object")
            errors = data.get("errors")
            if errors:
                kind, message = _graphql_error(errors)
                if kind == "RATE_LIMITED":
                    wait = self._rate_limit_wait(ApiResponse(429, resp.headers, None), attempt) or 60.0
                    if wait > self.settings.max_rate_limit_wait_seconds or attempt >= self.settings.max_retries:
                        raise GitHubRateLimitError(f"graphql {operation}: rate limited (retry after {wait:.0f}s)",
                                                   status=429)
                    self.retries += 1
                    self.sleep(wait)
                    attempt += 1
                    continue
                raise _graphql_exception(operation, kind, message)
            payload = data.get("data")
            if not isinstance(payload, dict):
                raise GitHubResponseError(f"graphql {operation}: no data")
            return payload

    # -- identity and capabilities -------------------------------------------------

    def viewer_login(self) -> str:
        """The authenticated account (`GET /user`)."""
        if self._viewer is None:
            self._viewer = _login(self._call("GET", "user", idempotent=True).data, "user")
        return self._viewer

    def token_scopes(self) -> tuple[str, ...] | None:
        """The OAuth scopes GitHub reports for the gh login (the `X-OAuth-Scopes` response header;
        the token itself is never seen). None when GitHub reports none (e.g. a fine-grained token)."""
        header = self._call("GET", "user", idempotent=True).headers.get("x-oauth-scopes")
        if header is None:
            return None
        return tuple(sorted(s.strip() for s in header.split(",") if s.strip()))

    def repository_info(self) -> dict[str, Any]:
        """Validated repository facts needed for Issue coordination."""
        data = self._call("GET", self.repo_path, idempotent=True).data
        if not isinstance(data, dict):
            raise GitHubResponseError("repository: not an object")
        perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
        repo_id = data.get("id")
        return {"full_name": str(data.get("full_name", "")), "private": bool(data.get("private")),
                "id": repo_id if isinstance(repo_id, int) and not isinstance(repo_id, bool) else 0,
                "default_branch": str(data.get("default_branch") or "")[:200],
                "node_id": str(data.get("node_id") or "")[:120],
                "has_issues": bool(data.get("has_issues")), "archived": bool(data.get("archived")),
                "permissions": {k: bool(v) for k, v in perms.items()}}

    def rate_limit(self) -> dict[str, int]:
        """The core rate-limit budget (`GET /rate_limit` does not count against it)."""
        data = self._call("GET", "rate_limit", idempotent=True).data
        core = (data or {}).get("resources", {}).get("core", {}) if isinstance(data, dict) else {}
        return {k: int(core.get(k, 0)) for k in ("limit", "remaining", "reset", "used")}

    def author_permission(self, login: str) -> str:
        """Repository permission of *login*; 'none' when not a collaborator."""
        if not _LOGIN_RE.fullmatch(login):
            return "none"
        resp = self._call("GET", f"{self.repo_path}/collaborators/{quote(login)}/permission", idempotent=True,
                          ok=(404,))
        if resp.status == 404 or not isinstance(resp.data, dict):
            return "none"
        permission = resp.data.get("permission")
        return permission if permission in ("admin", "maintain", "write", "triage", "read", "none") else "none"

    # -- issues ------------------------------------------------------------------

    def get_issue(self, number: int) -> RemoteIssue:
        """`GET /repos/{repo}/issues/{number}`."""
        return parse_issue(self._call("GET", f"{self.repo_path}/issues/{int(number)}", idempotent=True).data)

    def list_issues(self, *, labels: Iterable[str] = (), state: str = "all",
                    creator: str | None = None) -> list[RemoteIssue]:
        """Issues (and PRs, flagged) with every label in *labels*; all pages or an error."""
        params = {"state": state, "sort": "created", "direction": "asc"}
        labels = list(labels)
        if labels:
            params["labels"] = ",".join(labels)
        if creator:
            params["creator"] = creator
        return [parse_issue(item) for item in self._pages(f"{self.repo_path}/issues", params)]

    def create_issue(self, title: str, body: str, labels: Iterable[str]) -> RemoteIssue:
        """`POST /repos/{repo}/issues` with `feature:*` labels only (not idempotent)."""
        payload = {"title": title, "body": body, "labels": check_feature_labels(labels)}
        return parse_issue(self._call("POST", f"{self.repo_path}/issues", payload, idempotent=False).data)

    def update_issue(self, number: int, *, body: str | None = None, state: str | None = None,
                     state_reason: str | None = None) -> RemoteIssue:
        """`PATCH` the body and/or state (absolute values, so idempotent)."""
        payload: dict[str, Any] = {}
        if body is not None:
            payload["body"] = body
        if state is not None:
            if state not in ("open", "closed"):
                raise ValueError(f"invalid issue state {state!r}")
            payload["state"] = state
            if state_reason:
                payload["state_reason"] = state_reason
        return parse_issue(self._call("PATCH", f"{self.repo_path}/issues/{int(number)}", payload,
                                      idempotent=True).data)

    def add_labels(self, number: int, labels: Iterable[str]) -> tuple[str, ...]:
        """`POST .../labels` (adding a present label is a no-op)."""
        payload = {"labels": check_feature_labels(labels)}
        data = self._call("POST", f"{self.repo_path}/issues/{int(number)}/labels", payload, idempotent=True).data
        if not isinstance(data, list):
            raise GitHubResponseError("labels: expected a list")
        return tuple(str(item.get("name", "")) for item in data if isinstance(item, dict))

    def remove_label(self, number: int, label: str) -> None:
        """`DELETE .../labels/{name}`; an absent label (404) counts as removed."""
        check_feature_labels([label])
        self._call("DELETE", f"{self.repo_path}/issues/{int(number)}/labels/{quote(label, safe='')}",
                   idempotent=True, ok=(404,))

    def list_comments(self, number: int) -> list[RemoteComment]:
        """All comments, oldest first."""
        return [parse_comment(item) for item in self._pages(f"{self.repo_path}/issues/{int(number)}/comments", {})]

    def create_comment(self, number: int, body: str) -> RemoteComment:
        """`POST .../comments` (not idempotent: callers embed an operation marker)."""
        return parse_comment(self._call("POST", f"{self.repo_path}/issues/{int(number)}/comments", {"body": body},
                                        idempotent=False).data)

    # -- W4.5: planning labels, Issue events, Actions runs ---------------------------------

    def list_issue_events(self, number: int) -> list[RemoteIssueEvent]:
        """`GET .../issues/{number}/events`, every page, oldest first."""
        return [parse_issue_event(item) for item in self._pages(f"{self.repo_path}/issues/{int(number)}/events", {})]

    def add_worker_labels(self, number: int, labels: Iterable[str]) -> tuple[str, ...]:
        """`POST .../labels` with the four `worker:*` planning labels only."""
        payload = {"labels": check_worker_labels(labels)}
        data = self._call("POST", f"{self.repo_path}/issues/{int(number)}/labels", payload, idempotent=True).data
        if not isinstance(data, list):
            raise GitHubResponseError("labels: expected a list")
        return tuple(str(item.get("name", "")) for item in data if isinstance(item, dict))

    def remove_worker_label(self, number: int, label: str) -> None:
        """`DELETE .../labels/{name}` for one `worker:*` label; absent (404) counts as removed."""
        check_worker_labels([label])
        self._call("DELETE", f"{self.repo_path}/issues/{int(number)}/labels/{quote(label, safe='')}",
                   idempotent=True, ok=(404,))

    def ensure_worker_label(self, name: str, color: str, description: str) -> str:
        """Create a `worker:*` planning label if it is missing. Existing labels are never edited."""
        check_worker_labels([name])
        resp = self._call("GET", f"{self.repo_path}/labels/{quote(name, safe='')}", idempotent=True, ok=(404,))
        if resp.status != 404:
            return "exists"
        resp = self._call("POST", f"{self.repo_path}/labels", {"name": name, "color": color,
                                                               "description": description},
                          idempotent=True, ok=(422,))
        return "exists" if resp.status == 422 else "created"

    def get_workflow_run(self, run_id: int) -> dict[str, Any]:
        """`GET .../actions/runs/{id}` (validated fields only)."""
        return parse_workflow_run(self._call("GET", f"{self.repo_path}/actions/runs/{int(run_id)}",
                                             idempotent=True).data)

    # -- labels --------------------------------------------------------------------

    def ensure_label(self, name: str, color: str, description: str) -> str:
        """Create a `feature:*` label if it is missing. Existing labels are never edited."""
        check_feature_labels([name])
        resp = self._call("GET", f"{self.repo_path}/labels/{quote(name, safe='')}", idempotent=True, ok=(404,))
        if resp.status != 404:
            return "exists"
        resp = self._call("POST", f"{self.repo_path}/labels", {"name": name, "color": color,
                                                               "description": description},
                          idempotent=True, ok=(422,))
        return "exists" if resp.status == 422 else "created"

    def list_labels(self) -> list[dict[str, str]]:
        """Every repository label (name, color, description)."""
        return [{"name": str(item.get("name", "")), "color": str(item.get("color", "")),
                 "description": str(item.get("description") or "")}
                for item in self._pages(f"{self.repo_path}/labels", {}) if isinstance(item, dict)]

    # -- pull requests (W4.3.3; see the module docstring) -----------------------------------

    def authorize_pull_request(self, head: str, base: str) -> None:
        """Allow exactly ONE Draft PR from *head* into *base* (the publisher, after its checks). Refused while
        `github.publish.enabled` is false, for a head outside `feature/…` and for a base that is a feature branch."""
        if not self.settings.publish.enabled:
            raise PullRequestPolicyError(f"creating a pull request for {head!r} is disabled: github.publish.enabled "
                                         "is false; the feature keeps its single local PR candidate")
        if not _PR_HEAD_RE.fullmatch(head) or not _REF_RE.fullmatch(base) or base.startswith("feature/"):
            raise PullRequestPolicyError(f"a pull request may only go from a feature branch into a base branch, not "
                                         f"{head!r} → {base!r}")
        self._authorized.add((head, base))

    def create_pull_request(self, head: str, base: str, title: str, body: str, *,  # pylint: disable=too-many-arguments
                            draft: bool = True) -> PullRequest:
        """`POST /repos/{repo}/pulls`: ONE Draft PR, only as authorized (not idempotent: callers list first)."""
        if not self.settings.publish.enabled or (head, base) not in self._authorized:
            raise PullRequestPolicyError(f"creating a pull request for {head!r} is not authorized (github.publish."
                                         f"enabled={self.settings.publish.enabled}); only the publisher of an approved "
                                         "GitHub Issue's feature may open one")
        if not draft:
            raise PullRequestPolicyError("the coordinator opens Draft pull requests only")
        self._authorized.discard((head, base))
        payload = {"title": title[:256], "body": body, "head": head, "base": base, "draft": True,
                   "maintainer_can_modify": False}
        return parse_pull(self._call("POST", f"{self.repo_path}/pulls", payload, idempotent=False).data)

    def get_pull_request(self, number: int) -> PullRequest:
        """`GET /repos/{repo}/pulls/{number}` (with `merged` and `merged_by`)."""
        return parse_pull(self._call("GET", f"{self.repo_path}/pulls/{int(number)}", idempotent=True).data)

    def list_pull_requests(self, head: str | None = None, state: str = "all") -> list[PullRequest]:
        """PRs (read-only), optionally only those from branch *head* of this repository."""
        params = {"state": state}
        if head:
            params["head"] = f"{self.settings.owner}:{head}"
        return [parse_pull(item) for item in self._pages(f"{self.repo_path}/pulls", params)]
