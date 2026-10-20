"""A minimal native OpenAI-compatible Chat Completions transport (D-17), loopback-only in Phase 3.

This is deliberately small: one POST to `<base>/chat/completions` per call, JSON in and out,
optional Server-Sent Events streaming. It is not an agent and not a general client; the tool
loop lives in runners.OpenAICompatibleRunner and executes edits locally.

Phase 3 safety: LIVE_REMOTE_ENABLED is False, so `send()` refuses every endpoint that is not
a loopback address before opening a socket — the only reachable server is a local fake used
by the tests. Enabling remote execution needs D-15 (transport), D-16 (disclosure), D-18
(authentication), validated capabilities and explicit operator approval, and a code change
that sets this constant; the provider-level gates in providers.check_transport still apply.

Behavior:
  * connect timeout and an overall request deadline; the blocking exchange runs in a helper
    thread so cancellation (`cancelled()`) and the deadline abort it by closing the socket;
  * HTTP 401/403 → ProviderAuthError (not retried); 429 → ProviderRateLimitError; 5xx,
    connection and timeout errors → ProviderUnavailableError (both retried by the runner);
    other non-2xx or invalid JSON → ProviderResponseError;
  * streaming responses are assembled into the non-streaming shape, including tool calls
    whose id, name and arguments arrive in fragments (`delta.tool_calls[i]`);
  * the API key is sent only as `Authorization: Bearer …` and never logged or echoed.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .errors import (
    ProviderAuthError,
    ProviderCancelledError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
    TransportPolicyError,
)
from .providers import Endpoint

LIVE_REMOTE_ENABLED = False
USER_AGENT = "mx-admin-feature-coordinator/0.3"
_ABORT_JOIN_SECONDS = 5.0


def assemble_stream(lines: Any) -> dict[str, Any]:
    """Turn SSE `data:` lines of chat.completion.chunk objects into one chat.completion."""
    content: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    model = None
    finish = None
    chunks = 0
    for raw in lines:
        line = (raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)).strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError as exc:
            raise ProviderResponseError("a streamed chunk is not valid JSON") from exc
        chunks += 1
        model = chunk.get("model", model)
        for choice in chunk.get("choices") or ():
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(str(delta["content"]))
            for part in delta.get("tool_calls") or ():
                slot = calls.setdefault(int(part.get("index", 0)),
                                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if part.get("id"):
                    slot["id"] = part["id"]
                function = part.get("function") or {}
                slot["function"]["name"] += function.get("name") or ""
                slot["function"]["arguments"] += function.get("arguments") or ""
            finish = choice.get("finish_reason") or finish
    if chunks == 0:
        raise ProviderResponseError("the stream contained no chunks")
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content) or None}
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    return {"object": "chat.completion", "model": model, "x_stream_chunks": chunks,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}]}


class HttpChatTransport:  # pylint: disable=too-few-public-methods
    """See the module docstring."""

    def __init__(self, *, allow_remote: bool = False) -> None:
        if allow_remote and not LIVE_REMOTE_ENABLED:
            raise TransportPolicyError("remote model execution is disabled in Phase 3 (D-15/D-16/D-18)")
        self.allow_remote = allow_remote

    def send(self, endpoint: Endpoint, payload: Mapping[str, Any], *, connect_timeout: float,
             request_timeout: float, cancelled: Callable[[], bool] | None = None) -> Mapping[str, Any]:
        """POST one Chat Completions request. See the module docstring for the error classes."""
        if not endpoint.is_loopback and not self.allow_remote:
            raise TransportPolicyError("live remote model execution is disabled in Phase 3; only loopback "
                                       "test servers are reachable (D-15/D-16/D-18 and approval required)")
        parts = urlsplit(endpoint.url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if parts.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                parts.hostname, port, timeout=connect_timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(parts.hostname, port, timeout=connect_timeout)
        try:
            conn.connect()
        except (OSError, socket.timeout) as exc:
            raise ProviderUnavailableError(f"cannot connect to the model endpoint: {type(exc).__name__}") from exc
        raw_socket = conn.sock  # kept: a `Connection: close` response detaches it from conn
        raw_socket.settimeout(request_timeout)
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
                   "Accept": "text/event-stream" if payload.get("stream") else "application/json"}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"
        outcome: dict[str, Any] = {}

        def exchange() -> None:
            try:
                conn.request("POST", parts.path, body=body, headers=headers)
                outcome["value"] = self._read(conn.getresponse(), bool(payload.get("stream")))
            except BaseException as exc:  # pylint: disable=broad-except
                outcome["error"] = exc

        worker = threading.Thread(target=exchange, name="chat-completions", daemon=True)
        deadline = time.monotonic() + request_timeout
        worker.start()
        try:
            while worker.is_alive():
                worker.join(0.05)
                if cancelled and cancelled():
                    self._abort(conn, raw_socket, worker)
                    raise ProviderCancelledError("the model request was cancelled")
                if time.monotonic() > deadline:
                    self._abort(conn, raw_socket, worker)
                    raise ProviderUnavailableError(f"the model request exceeded {request_timeout:g}s")
        finally:
            conn.close()
        error = outcome.get("error")
        if isinstance(error, (ProviderAuthError, ProviderResponseError, ProviderUnavailableError)):
            raise error
        if isinstance(error, (OSError, socket.timeout, http.client.HTTPException)):
            raise ProviderUnavailableError(f"model endpoint connection failed: {type(error).__name__}") from error
        if error is not None:
            raise ProviderResponseError(f"unexpected transport failure: {type(error).__name__}") from error
        return outcome["value"]

    @staticmethod
    def _abort(conn: http.client.HTTPConnection, raw_socket: socket.socket, worker: threading.Thread) -> None:
        try:
            raw_socket.shutdown(socket.SHUT_RDWR)  # unblocks the reader, even after the response took the socket
        except OSError:
            pass
        conn.close()
        worker.join(_ABORT_JOIN_SECONDS)

    @staticmethod
    def _read(response: http.client.HTTPResponse, stream: bool) -> dict[str, Any]:
        status = response.status
        if status in (401, 403):
            raise ProviderAuthError(f"the model endpoint rejected the credentials (HTTP {status})")
        if status == 429:
            raise ProviderRateLimitError("the model endpoint is rate limiting (HTTP 429)")
        if status >= 500:
            raise ProviderUnavailableError(f"the model endpoint failed (HTTP {status})")
        if status < 200 or status >= 300:
            raise ProviderResponseError(f"unexpected HTTP {status} from the model endpoint")
        if stream and "text/event-stream" in (response.getheader("Content-Type") or ""):
            return assemble_stream(response)
        try:
            data = json.loads(response.read().decode("utf-8"))
        except ValueError as exc:
            raise ProviderResponseError("the model endpoint did not return JSON") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError("the model response is not a JSON object")
        return data
