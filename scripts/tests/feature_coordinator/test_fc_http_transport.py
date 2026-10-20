"""The OpenAI-compatible HTTP transport against a LOCAL fake server only (127.0.0.1)."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from feature_coordinator.capabilities import validate_capabilities
from feature_coordinator.errors import (
    ProviderAuthError,
    ProviderCancelledError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
    TransportPolicyError,
)
from feature_coordinator.http_transport import LIVE_REMOTE_ENABLED, HttpChatTransport, assemble_stream
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.providers import REMOTE_QWEN, Endpoint, resolve_endpoint
from feature_coordinator.runners import OpenAICompatibleRunner, RunContext, RunRequest

from fc_fake_openai import MODEL, FakeOpenAIServer, chunk, completion, text_stream, tool_call

TIMEOUTS = {"connect_timeout": 2.0, "request_timeout": 5.0}
VALIDATED = dataclasses.replace(REMOTE_QWEN, validated_capabilities=frozenset({"tool_calling", "streaming"}),
                                request_timeout_seconds=5.0)


def endpoint(server: FakeOpenAIServer, key: str | None = None) -> Endpoint:
    env = {"AGENT_QWEN_BASE_URL": server.base_url, **({"AGENT_QWEN_API_KEY": key} if key else {})}
    return resolve_endpoint(REMOTE_QWEN, env)


def payload(**extra) -> dict:
    return {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "temperature": 0.2,
            "max_tokens": 64, "stream": False, **extra}


def test_live_remote_execution_is_disabled() -> None:
    assert LIVE_REMOTE_ENABLED is False
    with pytest.raises(TransportPolicyError, match="Phase 3"):
        HttpChatTransport(allow_remote=True)
    remote = resolve_endpoint(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": "https://models.example.invalid/v1"})
    with pytest.raises(TransportPolicyError, match="loopback"):
        HttpChatTransport().send(remote, payload(), **TIMEOUTS)  # refused before any DNS lookup or socket


def test_request_path_headers_and_body() -> None:
    with FakeOpenAIServer() as server:
        response = HttpChatTransport().send(endpoint(server, "sk-local-test"), payload(), **TIMEOUTS)
        no_key = HttpChatTransport().send(endpoint(server), payload(), **TIMEOUTS)
    first, second = server.requests[0], server.requests[1]
    assert first["path"] == "/v1/chat/completions" and second["path"] == "/v1/chat/completions"
    assert first["auth_value"] == "Bearer sk-local-test" and second["auth"] == "absent"
    assert first["body"]["model"] == MODEL and first["body"]["temperature"] == 0.2
    assert response["choices"][0]["message"]["content"] == "pong" and no_key["model"] == MODEL


def test_streamed_text_and_fragmented_tool_calls_are_assembled() -> None:
    lines = [f"data: {json.dumps(c)}" for c in text_stream("hello world")] + ["data: [DONE]"]
    assert assemble_stream(lines)["choices"][0]["message"]["content"] == "hello world"
    args = json.dumps({"path": "scripts/e2e_check.py", "find": "'ok'", "replace": "'fine'"})
    parts = [chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                    "function": {"name": "find-", "arguments": args[:10]}}]}),
             chunk({"tool_calls": [{"index": 0, "function": {"name": "replace", "arguments": args[10:30]}}]}),
             chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[30:]}}]}),
             chunk({}, "tool_calls")]
    with FakeOpenAIServer([{"stream": parts, "gap": 0.01}]) as server:
        response = HttpChatTransport().send(endpoint(server), payload(stream=True), **TIMEOUTS)
    call = response["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "c1" and call["function"]["name"] == "find-replace"
    assert json.loads(call["function"]["arguments"])["replace"] == "'fine'"
    assert response["x_stream_chunks"] == 4 and response["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.parametrize("spec,error", [
    ({"status": 401, "json": {"error": "bad key"}}, ProviderAuthError),
    ({"status": 403, "json": {}}, ProviderAuthError),
    ({"status": 429, "json": {}}, ProviderRateLimitError),
    ({"status": 503, "json": {}}, ProviderUnavailableError),
    ({"status": 404, "json": {}}, ProviderResponseError),
    ({"raw": b"<html>not json</html>"}, ProviderResponseError),
    ({"stream": []}, ProviderResponseError),
])
def test_error_classification(spec: dict, error: type) -> None:
    with FakeOpenAIServer([spec]) as server:
        with pytest.raises(error):
            HttpChatTransport().send(endpoint(server, "sk-local-test"), payload(stream="stream" in spec), **TIMEOUTS)


def test_connection_refused_is_unavailable() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    closed = resolve_endpoint(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": f"http://127.0.0.1:{port}/v1"})
    with pytest.raises(ProviderUnavailableError, match="connect"):
        HttpChatTransport().send(closed, payload(), **TIMEOUTS)


def test_request_deadline() -> None:
    with FakeOpenAIServer([{"hang": 10}]) as server:
        start = time.monotonic()
        with pytest.raises(ProviderUnavailableError, match="exceeded"):
            HttpChatTransport().send(endpoint(server), payload(), connect_timeout=2, request_timeout=0.5)
        assert time.monotonic() - start < 4


def test_cancellation_aborts_a_stream() -> None:
    stop = threading.Event()
    with FakeOpenAIServer([{"stream": text_stream("x" * 40, parts=40), "gap": 0.1}]) as server:
        threading.Timer(0.3, stop.set).start()
        start = time.monotonic()
        with pytest.raises(ProviderCancelledError):
            HttpChatTransport().send(endpoint(server), payload(stream=True), **TIMEOUTS, cancelled=stop.is_set)
        assert time.monotonic() - start < 3


# -- the runner over HTTP ---------------------------------------------------------------------

def runner_for(server: FakeOpenAIServer, profile=VALIDATED, key: str | None = None) -> OpenAICompatibleRunner:
    env = {"AGENT_QWEN_BASE_URL": server.base_url, **({"AGENT_QWEN_API_KEY": key} if key else {})}
    return OpenAICompatibleRunner(profile, env, HttpChatTransport(), sleep=lambda _s: None)


def test_streamed_tool_loop_edits_locally_within_scope(repo: Path, config, tmp_path: Path) -> None:
    args = {"path": "scripts/e2e_check.py", "find": "'ok'", "replace": "'checked'"}
    with FakeOpenAIServer([
        {"stream": [chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                           "function": {"name": "find-replace", "arguments": json.dumps(args)[:12]}}]}),
                    chunk({"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(args)[12:]}}]}),
                    chunk({}, "tool_calls")]},
        {"stream": text_stream("edited the check")},
    ]) as server:
        workspace = ScopedWorkspace(PathPolicy(repo, config), "automation")
        request = RunRequest("t", "automation", "update the check", workspace, context=RunContext(run_dir=tmp_path))
        result = runner_for(server).run(request)
    assert result.status == "completed" and result.changed_paths == ["scripts/e2e_check.py"]
    assert (repo / "scripts/e2e_check.py").read_text(encoding="utf-8") == "print('checked')\n"
    assert all(r["body"]["stream"] is True for r in server.requests)
    assert server.requests[1]["body"]["messages"][-1]["role"] == "tool"


def test_runner_retries_rate_limits_then_succeeds_and_redacts_keys(repo: Path, config) -> None:
    with FakeOpenAIServer([{"status": 429, "json": {}}, {"json": completion("done")}]) as server:
        ok = runner_for(server, REMOTE_QWEN, key="sk-local-secret").run(
            RunRequest("t", "automation", "review", ScopedWorkspace(PathPolicy(repo, config), "automation")))
    assert ok.status == "advisory" and len(server.requests) == 2
    with FakeOpenAIServer([{"status": 401, "json": {}}] * 3) as server:
        denied = runner_for(server, REMOTE_QWEN, key="sk-local-secret").run(
            RunRequest("t", "automation", "review", ScopedWorkspace(PathPolicy(repo, config), "automation")))
    assert denied.status == "failed" and len(server.requests) == 1 and "sk-local-secret" not in denied.error


def test_runner_reports_cancellation(repo: Path, config, tmp_path: Path) -> None:
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    with FakeOpenAIServer([{"hang": 5}]) as server:
        result = runner_for(server, REMOTE_QWEN).run(RunRequest(
            "t", "automation", "review", ScopedWorkspace(PathPolicy(repo, config), "automation"),
            context=RunContext(run_dir=tmp_path, cancelled=stop.is_set)))
    assert result.status == "cancelled"


def test_model_mismatch_from_a_real_http_response(repo: Path, config) -> None:
    with FakeOpenAIServer([{"json": completion("hi", model="some-other-model")}]) as server:
        result = runner_for(server, REMOTE_QWEN).run(
            RunRequest("t", "automation", "review", ScopedWorkspace(PathPolicy(repo, config), "automation")))
    assert result.status == "failed" and "model mismatch" in result.error


# -- capability validation (never edits configuration) --------------------------------------------

@pytest.mark.parametrize("mode,expected", [
    ("compliant", {"chat": True, "tool_calling": True, "streaming": True}),
    ("no-tools", {"chat": True, "tool_calling": False, "streaming": True}),
    ("no-stream", {"chat": True, "tool_calling": True, "streaming": False}),
])
def test_capability_validation(mode: str, expected: dict) -> None:
    with FakeOpenAIServer(mode=mode) as server:
        checks = validate_capabilities(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": server.base_url}, HttpChatTransport())
    results = {c.name: c.passed for c in checks}
    assert {k: results[k] for k in expected} == expected
    assert results["thinking"] is None and results["vision"] is None
    assert REMOTE_QWEN.validated_capabilities == frozenset()  # a report, never a configuration change
    sent = json.dumps([r["body"] for r in server.requests])
    assert "def " not in sent and "import " not in sent  # synthetic prompts only, no repository content


def test_capability_validation_flags_a_model_mismatch() -> None:
    with FakeOpenAIServer(mode="compliant", model="wrong-model") as server:
        checks = validate_capabilities(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": server.base_url}, HttpChatTransport())
    assert checks[0].name == "chat" and checks[0].passed is False and "mismatch" in checks[0].detail


def test_plain_http_remote_is_blocked_for_validation_too() -> None:
    with pytest.raises(TransportPolicyError, match="D-15"):
        validate_capabilities(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": "http://10.1.2.3:8080/v1"}, HttpChatTransport())


def test_tool_call_helper_shape() -> None:
    assert json.loads(tool_call("x", {"a": 1})["function"]["arguments"]) == {"a": 1}
