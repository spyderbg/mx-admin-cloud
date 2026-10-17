"""A local fake OpenAI-compatible Chat Completions server for the Phase 3 tests (127.0.0.1 only).

Two ways to drive it:
  * `script`: a list of reply specs consumed one per request —
      {"json": {...}, "status": 200}, {"stream": [chunk, ...], "gap": 0.0},
      {"delay": seconds, ...}, {"raw": b"..."}, {"hang": seconds}  (accept, then never answer)
  * `mode` when the script is empty: "compliant" (answers chat, calls the probe tool, then
    finishes, streams on request), "no-tools" (ignores tools), "no-stream" (ignores stream).
Every request is recorded: path, headers (Authorization reduced to present/absent) and body.
"""

# pylint: disable=missing-function-docstring,missing-class-docstring

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "Qwen3.6-35B-A3B-Q4_K_M.gguf"


def completion(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None,
               model: str = MODEL) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"id": "chatcmpl-fake", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}]}


def tool_call(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def chunk(delta: dict[str, Any], finish: str | None = None, model: str = MODEL) -> dict[str, Any]:
    return {"object": "chat.completion.chunk", "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def text_stream(text: str, parts: int = 3) -> list[dict[str, Any]]:
    size = max(1, len(text) // parts)
    pieces = [text[i:i + size] for i in range(0, len(text), size)]
    return [chunk({"role": "assistant"})] + [chunk({"content": p}) for p in pieces] + [chunk({}, "stop")]


class FakeOpenAIServer:
    """Start with `with FakeOpenAIServer(...) as server:`; `server.base_url` ends in /v1."""

    def __init__(self, script: list[dict[str, Any]] | None = None, mode: str = "compliant",
                 model: str = MODEL) -> None:
        self.script = list(script or [])
        self.mode = mode
        self.model = model
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence
                del args

            def do_POST(self) -> None:  # pylint: disable=invalid-name
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                with server._lock:  # pylint: disable=protected-access
                    server.requests.append({"path": self.path, "body": body,
                                            "auth": "present" if self.headers.get("Authorization") else "absent",
                                            "auth_value": self.headers.get("Authorization", "")})
                    spec = server.script.pop(0) if server.script else server.answer(body)
                server.reply(self, spec)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def __enter__(self) -> "FakeOpenAIServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # -- behavior ------------------------------------------------------------------

    def answer(self, body: dict[str, Any]) -> dict[str, Any]:
        messages = body.get("messages", [])
        wants_stream = bool(body.get("stream")) and self.mode != "no-stream"
        if body.get("tools") and self.mode != "no-tools" and not any(m.get("role") == "tool" for m in messages):
            prompt = str(messages[-1].get("content", ""))
            value = prompt.rsplit("value ", maxsplit=1)[-1].rstrip(".") if "value " in prompt else "x"
            name = body["tools"][0]["function"]["name"]
            if wants_stream:
                args = json.dumps({"value": value})
                return {"stream": [chunk({"role": "assistant"}),
                                   chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                          "function": {"name": name, "arguments": args[:5]}}]}),
                                   chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[5:]}}]}),
                                   chunk({}, "tool_calls")]}
            return {"json": completion(tool_calls=[tool_call(name, {"value": value})], model=self.model)}
        text = "done" if any(m.get("role") == "tool" for m in messages) else "pong"
        if wants_stream:
            return {"stream": text_stream("1 2 3 4 5" if "Count" in str(messages[-1].get("content")) else text)}
        return {"json": completion(text, model=self.model)}

    @staticmethod
    def reply(handler: BaseHTTPRequestHandler, spec: dict[str, Any]) -> None:
        if spec.get("hang"):
            time.sleep(spec["hang"])
            return
        if spec.get("delay"):
            time.sleep(spec["delay"])
        status = int(spec.get("status", 200))
        if "stream" in spec:
            handler.send_response(status)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Connection", "close")
            handler.end_headers()
            try:
                for item in spec["stream"]:
                    handler.wfile.write(f"data: {json.dumps(item)}\n\n".encode())
                    handler.wfile.flush()
                    time.sleep(spec.get("gap", 0.0))
                handler.wfile.write(b"data: [DONE]\n\n")
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            handler.close_connection = True
            return
        payload = spec["raw"] if "raw" in spec else json.dumps(spec.get("json", {})).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
