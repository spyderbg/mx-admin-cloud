"""Host-side authentication broker for the real runners (W1: D-20 credential isolation, D-21 network).

Trust boundary. The worker process runs on the host and is trusted; it owns the upstream
provider credential. The model CLI it launches (Claude Code or Codex) runs inside a bubblewrap
sandbox with an empty network namespace and NO reusable credential. Its only route to a model
is this broker:

  * the broker accepts on a listener that lives INSIDE the sandbox's network namespace
    (handed over by netns_channel.py). Nothing listens on the host; there is no socket file;
  * every request must carry the run's session token — 32 random bytes made for this run,
    given to the CLI through the channel (never a file or a command line). The token expires
    with the run, and `stop()` revokes it the moment the run ends, whatever the outcome;
  * only the provider's approved operations are forwarded (ROUTES), always to that provider's
    one approved HTTPS upstream. The client cannot choose the host, path, query or headers
    that reach it; an absolute-form request target is refused;
  * the broker drops the client's Authorization header and adds the upstream credential
    itself. The credential never enters the sandbox, an environment, a command line, a file
    the sandbox can read, a log line or an error message;
  * request size, response size, idle and total request time, the number of requests per run
    and their concurrency are bounded. Each request is logged as one JSON line with method,
    path, status, reason, sizes and duration — never headers or bodies.

Fail closed: `broker.credential_source: none` (the default) means no broker can be created,
so a real runner is *unavailable*; there is no fallback to mounting the CLI's own login files
or forwarding API keys into the sandbox.

Credential sources (operator decision D-20; docs_agents/coordination/worker-security.md):
  * `none` — real runners stay unavailable;
  * `api-key-file` — a provider API key in a host file the operator provisions: an absolute
    path outside the checkout, a regular file owned by the user, mode 0600 or stricter. Such
    requests are billed per token to the key's account. A Claude or ChatGPT *subscription*
    login has no documented way to be brokered, so it is not supported.
"""

from __future__ import annotations

import hmac
import http.client
import ipaddress
import json
import os
import secrets
import socket
import ssl
import stat
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .config import BrokerConfig
from .errors import BrokerError, BrokerUnavailableError
from .providers import ProviderKind

CHUNK = 65536
CLIENT_IDLE_SECONDS = 120.0
MAX_CONNECTIONS = 32  # open client connections per run; more are closed at once (no thread is started)
# Response headers never passed back: hop-by-hop, the framing the broker replaces, and cookies.
DROPPED_RESPONSE_HEADERS = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
                                      "trailer", "transfer-encoding", "upgrade", "content-length", "set-cookie"})


class _Refusal(Exception):
    """A request the broker answers with an error status (internal)."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


# ---------------------------------------------------------------------------
# Approved provider routes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderRoute:
    """What the broker forwards for one runner kind, and where to."""

    kind: ProviderKind
    upstream_host: str
    operations: frozenset[tuple[str, str]]
    queries: frozenset[str] = frozenset()
    request_headers: frozenset[str] = frozenset()
    request_header_prefixes: tuple[str, ...] = ()
    credential_header: str = "authorization"

    def allows(self, method: str, path: str, query: str) -> bool:
        """True for an approved (method, path) with no query or an approved one."""
        return (method, path) in self.operations and (not query or query in self.queries)

    def forwards(self, header: str) -> bool:
        """True for a client request header that is passed upstream."""
        name = header.lower()
        return name in self.request_headers or name.startswith(self.request_header_prefixes)

    def credential(self, api_key: str) -> tuple[str, str]:
        """The upstream header that carries *api_key*."""
        if self.credential_header == "x-api-key":
            return "x-api-key", api_key
        return "Authorization", f"Bearer {api_key}"


ROUTES: dict[ProviderKind, ProviderRoute] = {
    # Observed with Claude Code 2.1.303 in gateway mode (fake provider): POST /v1/messages?beta=true.
    ProviderKind.CLAUDE_CODE: ProviderRoute(
        kind=ProviderKind.CLAUDE_CODE, upstream_host="api.anthropic.com",
        operations=frozenset({("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens")}),
        queries=frozenset({"beta=true"}),
        request_headers=frozenset({"content-type", "accept", "anthropic-version", "anthropic-beta", "user-agent",
                                   "x-app", "x-claude-code-session-id"}),
        request_header_prefixes=("x-stainless-",), credential_header="x-api-key"),
    # Observed with Codex 0.163.0 and a custom `responses` provider (fake provider): POST /v1/responses.
    ProviderKind.CODEX: ProviderRoute(
        kind=ProviderKind.CODEX, upstream_host="api.openai.com",
        operations=frozenset({("POST", "/v1/responses")}),
        request_headers=frozenset({"content-type", "accept", "user-agent", "originator", "session-id", "thread-id",
                                   "x-client-request-id", "openai-beta", "x-codex-beta-features",
                                   "x-codex-turn-metadata", "x-codex-window-id"}),
        credential_header="authorization"),
}


@dataclass(frozen=True)
class Upstream:
    """Where forwarded requests go: the provider's approved HTTPS host, or a loopback fake in tests."""

    scheme: str
    host: str
    port: int

    @classmethod
    def approved(cls, route: ProviderRoute) -> "Upstream":
        """The only production destination for *route*."""
        return cls("https", route.upstream_host, 443)

    @property
    def is_loopback(self) -> bool:
        """True for a 127.0.0.0/8 or ::1 address."""
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False

    def connection(self, timeout: float) -> http.client.HTTPConnection:
        """A new connection; HTTPS verifies the certificate and the host name."""
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)


# ---------------------------------------------------------------------------
# Upstream credentials (host side only)
# ---------------------------------------------------------------------------

class CredentialSource(Protocol):  # pylint: disable=too-few-public-methods
    """Produces the upstream credential header. Lives only in the worker (host) process."""

    def header(self) -> tuple[str, str]:
        """(name, value); raises BrokerUnavailableError when no credential can be produced."""


class StaticCredential:  # pylint: disable=too-few-public-methods
    """A fixed header. For fake providers in tests — never constructed from a real key."""

    def __init__(self, name: str, value: str) -> None:
        self._header = (name, value)

    def __repr__(self) -> str:
        return "StaticCredential(<redacted>)"

    def header(self) -> tuple[str, str]:
        """The configured header."""
        return self._header


class ApiKeyCredential:  # pylint: disable=too-few-public-methods
    """A provider API key read once from an operator-provisioned host file (`api-key-file`)."""

    def __init__(self, route: ProviderRoute, api_key: str) -> None:
        self._header = route.credential(api_key)

    def __repr__(self) -> str:
        return "ApiKeyCredential(<redacted>)"

    def header(self) -> tuple[str, str]:
        """The provider's API-key header."""
        return self._header


def read_api_key_file(path: Path, *, forbidden_roots: Sequence[Path] = ()) -> str:
    """The key in *path*, after checking that only this user can read it. The value never
    appears in an exception message."""
    if not path.is_absolute():
        raise BrokerUnavailableError("the broker API key file must be an absolute path")
    for root in forbidden_roots:
        root = Path(root).resolve()
        if path.resolve() == root or root in path.resolve().parents:
            raise BrokerUnavailableError(f"the broker API key file must not be inside {root}")
    try:
        info = path.lstat()
    except OSError as exc:
        raise BrokerUnavailableError(f"the broker API key file {path} is missing ({exc.strerror})") from exc
    if not stat.S_ISREG(info.st_mode):
        raise BrokerUnavailableError(f"the broker API key file {path} must be a regular file (not a link)")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BrokerUnavailableError(f"the broker API key file {path} must be owned by this user with mode 0600")
    try:
        key = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise BrokerUnavailableError(f"the broker API key file {path} is unreadable") from exc
    if not key or any(c.isspace() or not c.isprintable() for c in key) or len(key) > 512:
        raise BrokerUnavailableError(f"the broker API key file {path} must hold exactly one key")
    return key


def configured_credential(config: BrokerConfig, provider_name: str, route: ProviderRoute, *,
                          forbidden_roots: Sequence[Path] = ()) -> CredentialSource:
    """The upstream credential the operator configured for *provider_name* (fail closed)."""
    if config.credential_source == "none":
        raise BrokerUnavailableError(
            "no broker credential is configured (broker.credential_source: none): real runners stay unavailable "
            "until the operator chooses one (D-20); runner login files are never mounted into a sandbox")
    if config.credential_source != "api-key-file":
        raise BrokerUnavailableError(f"unsupported broker credential source {config.credential_source!r}")
    configured = config.api_key_files.get(provider_name)
    if not configured:
        raise BrokerUnavailableError(f"broker.api_key_files has no entry for provider {provider_name!r}")
    return ApiKeyCredential(route, read_api_key_file(Path(configured), forbidden_roots=forbidden_roots))


# ---------------------------------------------------------------------------
# Per-run authorization
# ---------------------------------------------------------------------------

class BrokerSession:
    """One run's authorization: a random token, an expiry, a request budget and revocation."""

    def __init__(self, ttl_seconds: float, max_requests: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._token = secrets.token_urlsafe(32)
        self._clock = clock
        self._lock = threading.Lock()
        self.expires_at = clock() + ttl_seconds
        self.max_requests = max_requests
        self.requests = 0
        self.revoked = False

    def __repr__(self) -> str:
        return f"BrokerSession(requests={self.requests}, revoked={self.revoked})"

    @property
    def token(self) -> str:
        """The bearer token the runner must present (handed over through the channel only)."""
        return self._token

    def admit(self, authorization: str | None) -> str:
        """'' when a request with this Authorization header may proceed, else the refusal reason."""
        presented = authorization[7:].strip() if authorization and authorization[:7].lower() == "bearer " else ""
        with self._lock:
            if not hmac.compare_digest(presented.encode(), self._token.encode()):
                return "invalid-token"
            if self.revoked:
                return "revoked"
            if self._clock() >= self.expires_at:
                return "expired"
            if self.requests >= self.max_requests:
                return "request-budget-exhausted"
            self.requests += 1
            return ""

    def revoke(self) -> None:
        """Refuse every further request, permanently."""
        with self._lock:
            self.revoked = True


# ---------------------------------------------------------------------------
# The broker
# ---------------------------------------------------------------------------

@dataclass
class BrokerStats:
    """Counters for the run record (no request content)."""

    requests: int = 0
    forwarded: int = 0
    refused: dict[str, int] = field(default_factory=dict)
    upstream_errors: int = 0
    request_bytes: int = 0
    response_bytes: int = 0


class _BrokerServer(ThreadingHTTPServer):
    """An HTTP server on a listener handed over by the sandbox (never bound by the broker)."""

    daemon_threads = True

    def __init__(self, listener: socket.socket, handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(("127.0.0.1", 0), handler, bind_and_activate=False)
        self.socket.close()
        self.socket = listener
        self.server_address = listener.getsockname()
        self._connections = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request: Any, client_address: Any) -> None:
        """One thread per connection, at most MAX_CONNECTIONS at a time."""
        if not self._connections.acquire(blocking=False):  # pylint: disable=consider-using-with
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connections.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        """No traceback on stderr: a failed exchange is already counted and logged."""


class AuthBroker:  # pylint: disable=too-many-instance-attributes
    """Serves one run. See the module docstring."""

    def __init__(self, route: ProviderRoute, credential: CredentialSource, config: BrokerConfig, *,  # pylint: disable=too-many-arguments
                 ttl_seconds: float, upstream: Upstream | None = None, allow_loopback_upstream: bool = False,
                 log_path: Path | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        upstream = upstream or Upstream.approved(route)
        if upstream != Upstream.approved(route) and not (allow_loopback_upstream and upstream.scheme == "http"
                                                         and upstream.is_loopback):
            raise BrokerError(f"upstream {upstream.scheme}://{upstream.host}:{upstream.port} is not the approved "
                              f"destination for {route.kind.value} (https://{route.upstream_host})")
        self.route = route
        self.upstream = upstream
        self.config = config
        self.session = BrokerSession(ttl_seconds, config.max_requests_per_run, clock=clock)
        self.stats = BrokerStats()
        self._credential = credential
        self._slots = threading.BoundedSemaphore(config.max_concurrent_requests)
        self._lock = threading.Lock()
        self._log_path = log_path
        self._server: _BrokerServer | None = None
        self._thread: threading.Thread | None = None

    def __repr__(self) -> str:
        return f"AuthBroker({self.route.kind.value} → {self.upstream.host}, {self.session!r})"

    # -- lifecycle -----------------------------------------------------------------

    def serve(self, listener: socket.socket) -> None:
        """Start answering on *listener* (in a daemon thread)."""
        if self._server is not None or self.session.revoked:
            listener.close()
            raise BrokerError("this broker is already serving or was stopped")
        self._server = _BrokerServer(listener, self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
                                        name="fc-broker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Revoke the session and close the endpoint (idempotent; safe if never served)."""
        self.session.revoke()
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def describe(self) -> dict[str, Any]:
        """The run record's view: destination, counters and revocation — never the token."""
        with self._lock:
            return {"kind": self.route.kind.value, "upstream": self.upstream.host, "requests": self.stats.requests,
                    "forwarded": self.stats.forwarded, "refused": dict(self.stats.refused),
                    "upstream_errors": self.stats.upstream_errors, "request_bytes": self.stats.request_bytes,
                    "response_bytes": self.stats.response_bytes, "revoked": self.session.revoked}

    # -- request handling ----------------------------------------------------------------

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        broker = self

        class Handler(BaseHTTPRequestHandler):
            """Every method goes through AuthBroker.handle()."""

            protocol_version = "HTTP/1.1"
            server_version = "fc-broker"
            sys_version = ""
            timeout = CLIENT_IDLE_SECONDS

            def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
                """The broker writes its own redacted log."""

            def do_POST(self) -> None:  # pylint: disable=invalid-name
                """Any method."""
                broker.handle(self)

            do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_POST

        return Handler

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        """Authorize, check and forward one request; answer refusals with a JSON error."""
        started = time.monotonic()
        method, target = handler.command, handler.path
        path, _, query = target.partition("?")
        status, reason, sizes = 0, "forwarded", (0, 0)
        handler.close_connection = True
        try:
            if not target.startswith("/") or target.startswith("//") or "#" in target:
                raise _Refusal(400, "bad-request-target")
            refusal = self.session.admit(handler.headers.get("Authorization"))
            if refusal:
                raise _Refusal(429 if refusal == "request-budget-exhausted" else 401, refusal)
            if not self.route.allows(method, path, query):
                raise _Refusal(403, "operation-not-allowed")
            body = self._read_body(handler)
            if not self._slots.acquire(timeout=30):  # pylint: disable=consider-using-with
                raise _Refusal(503, "too-many-concurrent-requests")
            try:
                status, sizes = self._forward(handler, method, target, body, started)
            finally:
                self._slots.release()
        except _Refusal as refused:
            status, reason = refused.status, refused.reason
            self._answer(handler, status, reason)
        except OSError:
            reason = "client-disconnected"
        # Only an approved path is logged verbatim; a refused target is never echoed into the log.
        self._record(method, path if self.route.allows(method, path, "") else "-", status, reason, sizes, started)

    def _read_body(self, handler: BaseHTTPRequestHandler) -> bytes | None:
        if handler.headers.get("Transfer-Encoding"):
            raise _Refusal(411, "chunked-request-body")
        raw = handler.headers.get("Content-Length")
        if raw is None:
            if handler.command == "POST":
                raise _Refusal(411, "length-required")
            return None
        try:
            length = int(raw)
        except ValueError as exc:
            raise _Refusal(400, "bad-content-length") from exc
        if length < 0 or length > self.config.max_request_bytes:
            raise _Refusal(413, "request-too-large")
        try:
            body = handler.rfile.read(length)
        except OSError as exc:
            raise _Refusal(400, "request-body-unreadable") from exc
        if len(body) != length:
            raise _Refusal(400, "request-body-truncated")
        return body

    def _upstream_headers(self, handler: BaseHTTPRequestHandler, body: bytes | None) -> dict[str, str]:
        try:
            name, value = self._credential.header()
        except BrokerUnavailableError as exc:
            raise _Refusal(503, "upstream-credential-unavailable") from exc
        headers = {key: val for key, val in handler.headers.items() if self.route.forwards(key)}
        headers.update({"Host": self.upstream.host, "Accept-Encoding": "identity", name: value})
        if body is not None:
            headers["Content-Length"] = str(len(body))
        return headers

    def _forward(self, handler: BaseHTTPRequestHandler, method: str, target: str,  # pylint: disable=too-many-arguments
                 body: bytes | None, started: float) -> tuple[int, tuple[int, int]]:
        headers = self._upstream_headers(handler, body)
        connection = self.upstream.connection(self.config.upstream_idle_timeout_seconds)
        try:
            try:
                connection.request(method, target, body=body, headers=headers)
                response = connection.getresponse()
            except socket.timeout as exc:
                self._count_upstream_error()
                raise _Refusal(504, "upstream-timeout") from exc
            except (OSError, http.client.HTTPException) as exc:
                self._count_upstream_error()
                raise _Refusal(502, "upstream-unreachable") from exc
            handler.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in DROPPED_RESPONSE_HEADERS:
                    handler.send_header(key, value)
            handler.send_header("Transfer-Encoding", "chunked")
            handler.send_header("Connection", "close")
            handler.end_headers()
            return response.status, (len(body or b""), self._relay(handler, response, started))
        finally:
            connection.close()

    def _relay(self, handler: BaseHTTPRequestHandler, response: http.client.HTTPResponse, started: float) -> int:
        """Stream the upstream body to the client re-chunked; abort (truncate) past a limit."""
        total = 0
        deadline = started + self.config.max_request_seconds
        try:
            while True:
                chunk = response.read1(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.config.max_response_bytes or time.monotonic() > deadline:
                    self._count_upstream_error()
                    return total  # no terminating chunk: the client sees a truncated, failed response
                handler.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                handler.wfile.flush()
            handler.wfile.write(b"0\r\n\r\n")
        except (OSError, http.client.HTTPException):
            self._count_upstream_error()
        return total

    @staticmethod
    def _answer(handler: BaseHTTPRequestHandler, status: int, reason: str) -> None:
        body = json.dumps({"type": "error", "error": {"type": "broker_refused", "message": reason}}).encode()
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(body)
        except OSError:
            pass

    def _count_upstream_error(self) -> None:
        with self._lock:
            self.stats.upstream_errors += 1

    def _record(self, method: str, path: str, status: int,  # pylint: disable=too-many-arguments
                reason: str, sizes: tuple[int, int], started: float) -> None:
        with self._lock:
            self.stats.requests += 1
            if reason == "forwarded":
                self.stats.forwarded += 1
                self.stats.request_bytes += sizes[0]
                self.stats.response_bytes += sizes[1]
            else:
                self.stats.refused[reason] = self.stats.refused.get(reason, 0) + 1
            if self._log_path is None:
                return
            line = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "method": method[:16],
                    "path": path[:200], "status": status, "reason": reason, "request_bytes": sizes[0],
                    "response_bytes": sizes[1], "ms": round((time.monotonic() - started) * 1000)}
            try:
                with open(self._log_path, "a", encoding="utf-8") as log:
                    log.write(json.dumps(line) + "\n")
            except OSError:
                pass
