"""A local fake model provider behind the broker (W1 tests; 127.0.0.1 only, no real model).

It answers the two wire formats the brokered runners use:
  * Anthropic Messages (`POST /v1/messages[?beta=true]`, streamed or not, plus count_tokens) —
    what Claude Code sends in gateway mode;
  * OpenAI Responses (`POST /v1/responses`, streamed) — what Codex sends to a custom provider.

Scripted agent behavior, so the REAL CLIs can be driven offline:
  * a user message containing `FAKE-TOOL:<command>` makes the Anthropic side answer with one
    Bash tool call; `FAKE-SHELL:<command>` makes the Responses side answer with one
    `exec_command` call; once the tool result comes back, the answer is `TOOLRESULT:<result>`;
  * otherwise the answer is the text `ok`.
Test knobs: `hang` (seconds before answering), `status` (error status), `body_bytes` (an
oversized body). Every request is recorded (method, path, query, lower-case headers, body).
"""

# pylint: disable=missing-function-docstring,missing-class-docstring

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

USAGE = {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 5,
         "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 15}


def sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def anthropic_events(blocks: list[dict[str, Any]], stop: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"type": "message_start", "message": {
        "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-fake", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}]
    for i, block in enumerate(blocks):
        if block["type"] == "text":
            events += [{"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
                       {"type": "content_block_delta", "index": i,
                        "delta": {"type": "text_delta", "text": block["text"]}}]
        else:
            events += [{"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}},
                       {"type": "content_block_delta", "index": i,
                        "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}}]
        events.append({"type": "content_block_stop", "index": i})
    return events + [{"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                      "usage": {"output_tokens": 5}}, {"type": "message_stop"}]


def _marker(text: str, marker: str) -> str:
    """The command after *marker* in a JSON-encoded transcript, up to the end of its line."""
    return text.split(marker, 1)[1].split("\\n", 1)[0].split('"', 1)[0]


class FakeProvider:
    """`with FakeProvider() as fake:` — then `fake.port`, `fake.requests`."""

    def __init__(self, *, hang: float = 0.0, status: int = 200, body_bytes: int = 0) -> None:
        self.hang, self.status, self.body_bytes = hang, status, body_bytes
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                del args

            def do_POST(self) -> None:  # pylint: disable=invalid-name
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                path, _, query = self.path.partition("?")
                with provider._lock:  # pylint: disable=protected-access
                    provider.requests.append({"method": self.command, "path": path, "query": query, "body": body,
                                              "headers": {k.lower(): v for k, v in self.headers.items()}})
                provider.reply(self, path, json.loads(body or b"{}"))

            do_GET = do_POST

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def __enter__(self) -> "FakeProvider":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # -- behavior ------------------------------------------------------------------

    def reply(self, handler: BaseHTTPRequestHandler, path: str, request: dict[str, Any]) -> None:
        if self.hang:
            time.sleep(self.hang)
        if self.status != 200:
            self.send(handler, self.status, b'{"type":"error","error":{"type":"fake","message":"scripted"}}')
        elif self.body_bytes:
            self.send(handler, 200, b"x" * self.body_bytes, "application/octet-stream")
        elif path == "/v1/messages/count_tokens":
            self.send(handler, 200, json.dumps({"input_tokens": 12}).encode())
        elif path == "/v1/messages":
            self.anthropic(handler, request)
        elif path == "/v1/responses":
            self.responses(handler, request)
        else:
            self.send(handler, 404, b"{}")

    @staticmethod
    def send(handler: BaseHTTPRequestHandler, status: int, body: bytes, ctype: str = "application/json") -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        try:
            handler.wfile.write(body)
        except OSError:
            pass

    def anthropic(self, handler: BaseHTTPRequestHandler, request: dict[str, Any]) -> None:
        results = [part.get("content") for message in request.get("messages", [])
                   if isinstance(message.get("content"), list)
                   for part in message["content"] if part.get("type") == "tool_result"]
        transcript = json.dumps(request.get("messages", []))
        if results:
            blocks, stop = [{"type": "text", "text": "TOOLRESULT:" + json.dumps(results)[:20000]}], "end_turn"
        elif "FAKE-TOOL:" in transcript:
            blocks = [{"type": "tool_use", "id": "toolu_fake1", "name": "Bash",
                       "input": {"command": _marker(transcript, "FAKE-TOOL:"), "description": "probe"}}]
            stop = "tool_use"
        else:
            blocks, stop = [{"type": "text", "text": "ok"}], "end_turn"
        if request.get("stream"):
            self.send(handler, 200, sse(anthropic_events(blocks, stop)), "text/event-stream")
            return
        content = [{"type": "text", "text": b["text"]} if b["type"] == "text" else
                   {"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]} for b in blocks]
        self.send(handler, 200, json.dumps({
            "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-fake", "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}}).encode())

    def responses(self, handler: BaseHTTPRequestHandler, request: dict[str, Any]) -> None:
        items = request.get("input", [])
        outputs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
        transcript = json.dumps(items)
        if outputs:
            out = [{"type": "message", "role": "assistant", "id": "msg_2",
                    "content": [{"type": "output_text", "text": "TOOLRESULT:" + json.dumps(outputs)[:20000]}]}]
        elif "FAKE-SHELL:" in transcript:
            out = [{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "exec_command",
                    "arguments": json.dumps({"cmd": _marker(transcript, "FAKE-SHELL:")})}]
        else:
            out = [{"type": "message", "role": "assistant", "id": "msg_1",
                    "content": [{"type": "output_text", "text": "ok"}]}]
        events = [{"type": "response.created", "response": {"id": "resp_1"}}]
        events += [{"type": "response.output_item.done", "item": item} for item in out]
        events.append({"type": "response.completed", "response": {"id": "resp_1", "usage": USAGE}})
        self.send(handler, 200, sse(events), "text/event-stream")
