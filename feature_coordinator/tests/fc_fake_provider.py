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
Multi-step scripts (native hardening tests): every line `FAKE-STEP:<tool>:<argument>` of the
first user message is one tool call, issued in order, one per model turn; after the last tool
result the answer is `TOOLRESULT:` + the JSON list of all results. Anthropic tools: `Bash`,
`BashUnsandboxed` (Bash with `dangerouslyDisableSandbox`), `Read`, `Write`, `Glob`, `Grep`,
`Agent`/`Task` (a subagent), `WebFetch`, any other name (input `{"arg": ...}`). Responses tools:
`shell` (exec_command), `shell_escalated` (exec_command asking for escalated permissions),
`apply_patch` (adds the file named by the argument), `view_image` (the file named by the argument).
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


STEP = "FAKE-STEP:"


def _steps(first_user_text: str) -> list[tuple[str, str]]:
    """The scripted (tool, argument) steps of a multi-step prompt (see the module docstring)."""
    steps = []
    for line in first_user_text.splitlines():
        if line.startswith(STEP):
            tool, _, arg = line[len(STEP):].partition(":")
            steps.append((tool, arg))
    return steps


def _first_user_text(messages: list[dict[str, Any]]) -> str:
    """The text of the first user message that holds a scripted step (the task prompt; Codex
    may send its environment context as an earlier user message)."""
    for message in messages:
        content = message.get("content") if message.get("role") == "user" else None
        if isinstance(content, list):
            content = "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        if isinstance(content, str) and STEP in content:
            return content
    return ""


def anthropic_tool_input(tool: str, arg: str) -> tuple[str, dict[str, Any]]:
    """(tool name, input) for one scripted Anthropic step."""
    if tool in ("Bash", "BashUnsandboxed"):
        data: dict[str, Any] = {"command": arg, "description": "probe"}
        if tool == "BashUnsandboxed":
            data["dangerouslyDisableSandbox"] = True
        return "Bash", data
    inputs = {"Read": {"file_path": arg}, "Write": {"file_path": arg, "content": "FAKE-WRITTEN\n"},
              "Glob": {"pattern": arg}, "Grep": {"pattern": "FAKE", "path": arg, "output_mode": "content"},
              "WebFetch": {"url": arg, "prompt": "summarize"}}
    if tool in ("Agent", "Task"):
        return tool, {"description": "probe", "prompt": arg, "subagent_type": "general-purpose"}
    return tool, inputs.get(tool, {"arg": arg})


def responses_tool_call(tool: str, arg: str, index: int) -> dict[str, Any]:
    """One scripted Responses output item: `shell`/`shell_escalated` (exec_command), `apply_patch`
    (Codex's freeform tool, adding the file *arg*) or `view_image` (the local file *arg*)."""
    ids = {"id": f"fc_s{index}", "call_id": f"call_s{index}"}
    if tool == "apply_patch":
        patch = f"*** Begin Patch\n*** Add File: {arg}\n+FAKE-PATCHED\n*** End Patch\n"
        return {"type": "custom_tool_call", "name": "apply_patch", "input": patch, **ids}
    if tool == "view_image":
        return {"type": "function_call", "name": "view_image", "arguments": json.dumps({"path": arg}), **ids}
    args: dict[str, Any] = {"cmd": arg}
    if tool == "shell_escalated":
        args.update(sandbox_permissions="require_escalated", justification="probe needs the host")
    return {"type": "function_call", "name": "exec_command", "arguments": json.dumps(args), **ids}


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
        steps = _steps(_first_user_text(request.get("messages", [])))
        if steps and len(results) < len(steps):
            name, data = anthropic_tool_input(*steps[len(results)])
            blocks = [{"type": "tool_use", "id": f"toolu_step{len(results)}", "name": name, "input": data}]
            stop = "tool_use"
        elif results:
            blocks, stop = [{"type": "text", "text": "TOOLRESULT:" + json.dumps(results)[:60000]}], "end_turn"
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
        outputs = [i for i in items if isinstance(i, dict)
                   and i.get("type") in ("function_call_output", "custom_tool_call_output")]
        transcript = json.dumps(items)
        steps = _steps(_first_user_text([i for i in items if isinstance(i, dict)]))
        if steps and len(outputs) < len(steps):
            out = [responses_tool_call(*steps[len(outputs)], len(outputs))]
        elif outputs:
            out = [{"type": "message", "role": "assistant", "id": "msg_2",
                    "content": [{"type": "output_text", "text": "TOOLRESULT:" + json.dumps(outputs)[:60000]}]}]
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
