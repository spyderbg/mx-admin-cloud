"""The host-side authentication broker (W1, D-20): authorization, approved operations, limits,
credential handling and redaction. Fake provider and fake credentials only; nothing leaves 127.0.0.1."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import http.client
import json
import os
import socket
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from feature_coordinator.broker import (
    ROUTES,
    AuthBroker,
    BrokerSession,
    StaticCredential,
    Upstream,
    configured_credential,
    read_api_key_file,
)
from feature_coordinator.config import BrokerConfig, ProjectConfig, config_from_mapping
from feature_coordinator.errors import BrokerError, BrokerUnavailableError, ConfigError
from feature_coordinator.providers import ProviderKind

from fc_fake_provider import FakeProvider
from fc_helpers import FAKE_UPSTREAM_KEY

CLAUDE, CODEX = ROUTES[ProviderKind.CLAUDE_CODE], ROUTES[ProviderKind.CODEX]
MESSAGE = json.dumps({"model": "claude-fake", "max_tokens": 16, "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]}).encode()


@pytest.fixture
def fake() -> Iterator[FakeProvider]:
    with FakeProvider() as server:
        yield server


class Clock:  # pylint: disable=too-few-public-methods
    """A settable monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_broker(port: int, route=CLAUDE, *, config: BrokerConfig | None = None, ttl: float = 60.0,  # pylint: disable=too-many-arguments
                clock=None, log_path: Path | None = None, credential=None) -> AuthBroker:
    return AuthBroker(route, credential or StaticCredential(*route.credential(FAKE_UPSTREAM_KEY)),
                      config or BrokerConfig(), ttl_seconds=ttl, upstream=Upstream("http", "127.0.0.1", port),
                      allow_loopback_upstream=True, log_path=log_path, **({"clock": clock} if clock else {}))


@contextmanager
def serving(broker: AuthBroker) -> Iterator[int]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    broker.serve(listener)
    try:
        yield port
    finally:
        broker.stop()


def call(port: int, method: str, target: str, *, token: str | None = None, body: bytes | None = MESSAGE,  # pylint: disable=too-many-arguments
         headers: dict | None = None, scheme: str = "Bearer") -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    sent = {"Content-Type": "application/json", "anthropic-version": "2023-06-01", **(headers or {})}
    if token is not None:
        sent["Authorization"] = f"{scheme} {token}"
    conn.request(method, target, body=body, headers=sent)
    response = conn.getresponse()
    try:
        return response.status, response.read()
    finally:
        conn.close()


def raw(port: int, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=20) as conn:
        conn.sendall(data)
        chunks = []
        while chunk := conn.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks)


# -- forwarding ------------------------------------------------------------------------------

def test_an_authorized_request_reaches_the_upstream_with_the_broker_credential_only(fake, tmp_path) -> None:
    broker = make_broker(fake.port, log_path=tmp_path / "broker.log")
    with serving(broker) as port:
        status, body = call(port, "POST", "/v1/messages?beta=true", token=broker.session.token,
                            headers={"x-api-key": "client-supplied", "Cookie": "c=1", "X-Forwarded-For": "1.2.3.4",
                                     "anthropic-beta": "claude-code-20250219", "X-Stainless-Lang": "js"})
    assert status == 200 and b"message_stop" in body  # streamed SSE relayed
    upstream = fake.requests[0]
    assert (upstream["method"], upstream["path"], upstream["query"]) == ("POST", "/v1/messages", "beta=true")
    assert upstream["headers"]["x-api-key"] == FAKE_UPSTREAM_KEY  # the broker's credential, not the client's
    assert "authorization" not in upstream["headers"] and "cookie" not in upstream["headers"]
    assert "x-forwarded-for" not in upstream["headers"]
    assert upstream["headers"]["anthropic-beta"] == "claude-code-20250219" and upstream["headers"]["x-stainless-lang"]
    assert broker.session.token not in json.dumps(upstream["headers"]) and upstream["body"] == MESSAGE
    assert broker.describe()["forwarded"] == 1 and broker.describe()["revoked"] is True


def test_codex_route_sends_the_upstream_key_as_a_bearer(fake) -> None:
    broker = make_broker(fake.port, CODEX)
    with serving(broker) as port:
        status, body = call(port, "POST", "/v1/responses", token=broker.session.token,
                            body=json.dumps({"model": "m", "input": [], "stream": True}).encode())
    assert status == 200 and b"response.completed" in body
    assert fake.requests[0]["headers"]["authorization"] == f"Bearer {FAKE_UPSTREAM_KEY}"


# -- authorization -----------------------------------------------------------------------------

@pytest.mark.parametrize("token,scheme", [(None, "Bearer"), ("wrong-token", "Bearer"), ("", "Bearer"),
                                          ("SESSION", "Basic")], ids=["missing", "wrong", "empty", "basic"])
def test_invalid_authorization_is_rejected_before_any_upstream_contact(fake, token, scheme) -> None:
    broker = make_broker(fake.port)
    with serving(broker) as port:
        presented = broker.session.token if token == "SESSION" else token
        status, body = call(port, "POST", "/v1/messages", token=presented, scheme=scheme)
    assert status == 401 and b"invalid-token" in body and not fake.requests


def test_expired_authorization_is_rejected(fake) -> None:
    clock = Clock()
    broker = make_broker(fake.port, ttl=30, clock=clock)
    with serving(broker) as port:
        assert call(port, "POST", "/v1/messages", token=broker.session.token)[0] == 200
        clock.now += 31
        status, body = call(port, "POST", "/v1/messages", token=broker.session.token)
    assert status == 401 and b"expired" in body and len(fake.requests) == 1


def test_stop_revokes_the_session_and_closes_the_endpoint(fake) -> None:
    broker = make_broker(fake.port)
    with serving(broker) as port:
        assert call(port, "POST", "/v1/messages", token=broker.session.token)[0] == 200
    assert broker.session.revoked and broker.session.admit(f"Bearer {broker.session.token}") == "revoked"
    with pytest.raises(OSError):
        call(port, "POST", "/v1/messages", token=broker.session.token)
    with pytest.raises(BrokerError):
        broker.serve(socket.socket())  # a stopped broker never serves again


def test_session_tokens_are_random_and_never_in_repr() -> None:
    first, second = BrokerSession(60, 10), BrokerSession(60, 10)
    assert first.token != second.token and len(first.token) >= 43
    assert first.token not in repr(first)
    assert FAKE_UPSTREAM_KEY not in repr(StaticCredential("x-api-key", FAKE_UPSTREAM_KEY))


# -- approved destinations and operations ----------------------------------------------------------

@pytest.mark.parametrize("method,target,expected", [
    ("GET", "/v1/messages", 403), ("POST", "/v1/models", 403), ("POST", "/v1/complete", 403),
    ("POST", "/v1/messages?beta=false", 403), ("POST", "/v1/messages/../responses", 403),
    ("POST", "/v1/responses", 403), ("DELETE", "/v1/messages", 403),
    ("POST", "http://evil.example/v1/messages", 400),
    ("POST", "//evil.example/v1/messages", 403)])  # the stdlib collapses a leading "//" to "/" (then not approved)
def test_unapproved_operations_are_rejected(fake, method, target, expected) -> None:
    broker = make_broker(fake.port)
    with serving(broker) as port:
        status, _ = call(port, method, target, token=broker.session.token)
    assert status == expected and not fake.requests


def test_the_client_cannot_choose_the_upstream_host(fake) -> None:
    broker = make_broker(fake.port)
    with serving(broker) as port:
        status, _ = call(port, "POST", "/v1/messages", token=broker.session.token, headers={"Host": "evil.example"})
    assert status == 200 and fake.requests[0]["headers"]["host"] == "127.0.0.1"


def test_only_the_approved_upstream_is_accepted() -> None:
    credential = StaticCredential("x-api-key", FAKE_UPSTREAM_KEY)
    for upstream in (Upstream("https", "evil.example", 443), Upstream("http", "api.anthropic.com", 80),
                     Upstream("https", "api.openai.com", 443)):
        with pytest.raises(BrokerError, match="not the approved destination"):
            AuthBroker(CLAUDE, credential, BrokerConfig(), ttl_seconds=60, upstream=upstream,
                       allow_loopback_upstream=True)
    with pytest.raises(BrokerError):  # a loopback fake needs the explicit test switch
        AuthBroker(CLAUDE, credential, BrokerConfig(), ttl_seconds=60, upstream=Upstream("http", "127.0.0.1", 9))
    assert AuthBroker(CLAUDE, credential, BrokerConfig(), ttl_seconds=60).upstream == \
        Upstream("https", "api.anthropic.com", 443)
    assert AuthBroker(CODEX, credential, BrokerConfig(), ttl_seconds=60).upstream.host == "api.openai.com"


# -- limits ------------------------------------------------------------------------------------------

def test_request_size_and_framing_limits(fake) -> None:
    broker = make_broker(fake.port, config=BrokerConfig(max_request_bytes=1024))
    with serving(broker) as port:
        token = broker.session.token
        assert call(port, "POST", "/v1/messages", token=token, body=b"x" * 2048)[0] == 413
        head = f"POST /v1/messages HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\n".encode()
        assert raw(port, head + b"\r\n").startswith(b"HTTP/1.1 411")
        assert raw(port, head + b"Transfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n").startswith(b"HTTP/1.1 411")
    assert not fake.requests and broker.describe()["refused"] == {"request-too-large": 1, "length-required": 1,
                                                                  "chunked-request-body": 1}


def test_request_budget_per_run(fake) -> None:
    broker = make_broker(fake.port, config=BrokerConfig(max_requests_per_run=2))
    with serving(broker) as port:
        codes = [call(port, "POST", "/v1/messages", token=broker.session.token)[0] for _ in range(3)]
    assert codes == [200, 200, 429] and len(fake.requests) == 2


def test_oversized_responses_are_cut_off() -> None:
    with FakeProvider(body_bytes=300_000) as big:
        broker = make_broker(big.port, config=BrokerConfig(max_response_bytes=100_000))
        with serving(broker) as port:
            with pytest.raises(http.client.IncompleteRead):
                call(port, "POST", "/v1/messages", token=broker.session.token)
    assert broker.describe()["upstream_errors"] == 1


def test_upstream_timeout_and_unreachable_upstream() -> None:
    with FakeProvider(hang=3) as slow:
        broker = make_broker(slow.port, config=BrokerConfig(upstream_idle_timeout_seconds=0.5))
        with serving(broker) as port:
            assert call(port, "POST", "/v1/messages", token=broker.session.token)[0] == 504
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    dead_port = closed.getsockname()[1]
    closed.close()
    broker = make_broker(dead_port)
    with serving(broker) as port:
        assert call(port, "POST", "/v1/messages", token=broker.session.token)[0] == 502


def test_upstream_errors_pass_through_without_retries_or_fallback() -> None:
    with FakeProvider(status=529) as overloaded:
        broker = make_broker(overloaded.port)
        with serving(broker) as port:
            status, body = call(port, "POST", "/v1/messages", token=broker.session.token)
    assert status == 529 and b"scripted" in body and len(overloaded.requests) == 1


def test_a_failing_credential_source_fails_closed(fake) -> None:
    class Broken:  # pylint: disable=too-few-public-methods
        """A credential source that fails mid-run."""

        def header(self):
            raise BrokerUnavailableError("key file removed during the run")

    broker = make_broker(fake.port, credential=Broken())
    with serving(broker) as port:
        status, body = call(port, "POST", "/v1/messages", token=broker.session.token)
    assert status == 503 and b"upstream-credential-unavailable" in body and not fake.requests


# -- redaction -----------------------------------------------------------------------------------------

def test_the_log_holds_no_credential_token_header_or_body(fake, tmp_path: Path) -> None:
    log = tmp_path / "broker.log"
    broker = make_broker(fake.port, log_path=log)
    with serving(broker) as port:
        call(port, "POST", "/v1/messages?beta=true", token=broker.session.token)
        call(port, "POST", "/v1/" + broker.session.token, token=broker.session.token)  # token in a refused path
        call(port, "POST", "/v1/messages", token="guess-" + FAKE_UPSTREAM_KEY)
    text = log.read_text(encoding="utf-8")
    lines = [json.loads(line) for line in text.splitlines()]
    assert [(e["path"], e["status"], e["reason"]) for e in lines] == [
        ("/v1/messages", 200, "forwarded"), ("-", 403, "operation-not-allowed"), ("/v1/messages", 401, "invalid-token")]
    for secret in (FAKE_UPSTREAM_KEY, broker.session.token, "claude-fake", "hi"):
        assert secret not in text
    assert set(lines[0]) == {"time", "method", "path", "status", "reason", "request_bytes", "response_bytes", "ms"}


# -- credential sources (D-20) --------------------------------------------------------------------------

def test_no_configured_credential_means_no_broker() -> None:
    with pytest.raises(BrokerUnavailableError, match="D-20"):
        configured_credential(BrokerConfig(), "claude", CLAUDE)


def key_file(tmp_path: Path, text: str = FAKE_UPSTREAM_KEY + "\n", mode: int = 0o600) -> Path:
    path = tmp_path / "keys" / "claude.key"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="ascii")
    path.chmod(mode)
    return path


def test_api_key_file_source(tmp_path: Path) -> None:
    path = key_file(tmp_path)
    config = BrokerConfig(credential_source="api-key-file", api_key_files={"claude": str(path)})
    assert configured_credential(config, "claude", CLAUDE).header() == ("x-api-key", FAKE_UPSTREAM_KEY)
    assert configured_credential(dataclasses.replace(config, api_key_files={"codex": str(path)}), "codex",
                                 CODEX).header() == ("Authorization", f"Bearer {FAKE_UPSTREAM_KEY}")
    with pytest.raises(BrokerUnavailableError, match="no entry"):
        configured_credential(config, "codex", CODEX)


@pytest.mark.parametrize("problem", ["missing", "mode", "symlink", "empty", "two-lines", "relative", "in-repo"])
def test_api_key_file_is_refused_unless_private_and_outside_the_checkout(tmp_path: Path, problem: str) -> None:
    path = key_file(tmp_path)
    roots: tuple[Path, ...] = ()
    if problem == "missing":
        path.unlink()
    elif problem == "mode":
        path.chmod(0o640)
    elif problem == "symlink":
        link = tmp_path / "keys" / "link.key"
        link.symlink_to(path)
        path = link
    elif problem == "empty":
        path.write_text("\n", encoding="ascii")
    elif problem == "two-lines":
        path.write_text(f"{FAKE_UPSTREAM_KEY}\nsecond\n", encoding="ascii")
    elif problem == "relative":
        path = Path("keys/claude.key")
    else:
        roots = (tmp_path,)
    with pytest.raises(BrokerUnavailableError) as raised:
        read_api_key_file(path, forbidden_roots=roots)
    assert FAKE_UPSTREAM_KEY not in str(raised.value)


def test_broker_configuration_is_validated(config: ProjectConfig) -> None:
    for change, match in (({"credential_source": "oauth"}, "credential_source"),
                          ({"credential_source": "api-key-file", "api_key_files": {"claude": "keys/c"}}, "absolute"),
                          ({"api_key_files": {"claude": "/k"}}, "only used"),
                          ({"credential_source": "api-key-file", "api_key_files": {"remote_qwen": "/k"}}, "not a"),
                          ({"max_request_bytes": 0}, "positive")):
        with pytest.raises(ConfigError, match=match):
            dataclasses.replace(config, broker=dataclasses.replace(config.broker, **change))


def test_yaml_broker_section_rejects_inline_secrets(config: ProjectConfig) -> None:
    import yaml  # pylint: disable=import-outside-toplevel
    from feature_coordinator.cli import EXAMPLE_CONFIG  # pylint: disable=import-outside-toplevel
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["broker"]["api_key"] = "inline"
    with pytest.raises(ConfigError, match="unknown broker keys"):
        config_from_mapping(data)
    del data["broker"]["api_key"]
    data["providers"]["claude"]["credential_files"] = [".claude/.credentials.json"]
    with pytest.raises(ConfigError, match="no longer supported"):
        config_from_mapping(data)
    assert os.environ.get("ANTHROPIC_API_KEY") != FAKE_UPSTREAM_KEY and config.broker.credential_source == "none"


def test_open_connections_are_capped(fake) -> None:
    from feature_coordinator.broker import MAX_CONNECTIONS  # pylint: disable=import-outside-toplevel
    broker = make_broker(fake.port)
    with serving(broker) as port:
        idle = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(MAX_CONNECTIONS)]
        try:
            extra = socket.create_connection(("127.0.0.1", port), timeout=5)
            assert extra.recv(1) == b""                                     # closed at once, no thread
            extra.close()
        finally:
            for conn in idle:
                conn.close()
        assert _eventually(lambda: call(port, "POST", "/v1/messages", token=broker.session.token)[0] == 200)


def _eventually(predicate, timeout: float = 10.0) -> bool:
    import time  # pylint: disable=import-outside-toplevel
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except OSError:
            pass
        time.sleep(0.1)
    return False
