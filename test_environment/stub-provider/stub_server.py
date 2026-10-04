#!/usr/bin/env python3
"""Deterministic stub for every external provider the test backend can be pointed at.

Stdlib only (ThreadingHTTPServer) — this file is bind-mounted read-only into a plain
python:3.11-slim container in Docker mode (no Dockerfile, no image build) and run
directly as a subprocess in native mode, so it must not depend on anything outside the
standard library.

Routes, one per redirected provider (see docs/test_environment/implementation_plan.md §4):

    POST /v1/chat/completions           openai_translate            -> "{word}-{lang}"
                                        qwen_translate (same shape) -> "{word}-{lang}"
                                                 (calls are recorded alike — /__calls
                                                 cannot tell the two providers apart)
    POST /v1/images/generations         openai_http (image)         -> b64 PNG
    POST /tts                           speaktor (response_mode=raw)-> raw MP3 bytes
    POST /v1beta/models/{model}:predict         genai_http (imagen) -> b64 PNG
    POST /v1beta/models/{model}:generateContent genai_http (gemini) -> inline b64 PNG
                                                 (unreachable via the current config,
                                                 which pins api=imagen; kept so flipping
                                                 that one config value needs no stub change)
    POST /prompt, GET /history/{id}, GET /view  comfyui              -> submit/poll/fetch
    POST /api/events/raw, GET /api              logging (Seq sink)   -> 201 / 200
    POST /stt                                   speaktor evaluate    -> best-effort only;
                                                 evaluate.enabled is false in the test
                                                 config, so nothing exercises this route
                                                 today — kept for the "every external
                                                 service" inventory, contract unverified.

Plus test-only introspection:

    GET  /__health   -> 200 once the server is accepting connections
    GET  /__calls    -> {"calls": [...]}  every call recorded so far, in order
    POST /__reset    -> clears the call log and any in-flight ComfyUI jobs

Usage:
    python stub_server.py [--host 127.0.0.1] [--port 48002] [--verbose]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

sys.stdout.reconfigure(line_buffering=True)  # see scripts/testenv.py for why

# ---------------------------------------------------------------------------
# Language names -> codes. Mirrors backend/src/core/languages.py LANG_MAP.
# Duplicated deliberately: the stub must never import backend/src (it runs in a bare
# python:3.11-slim container with no application code mounted in Docker mode).
# ---------------------------------------------------------------------------

LANG_MAP: dict[str, str] = {
    "English": "en",
    "German": "de",
    "French": "fr",
    "Italian": "it",
    "Spanish": "es",
    "Polish": "pl",
    "Ukrainian": "uk",
    "Romanian": "ro",
    "Dutch": "nl",
    "Bulgarian": "bg",
}

# ---------------------------------------------------------------------------
# Deterministic fixture generators (stdlib only — no Pillow, no audio libs)
# ---------------------------------------------------------------------------


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def tiny_png(seed_text: str) -> bytes:
    """An 8x8 solid-RGB PNG, colour derived from sha1(seed_text). ~90-150 bytes."""
    digest = hashlib.sha1(seed_text.encode("utf-8")).digest()
    r, g, b = digest[0], digest[1], digest[2]

    width = height = 8
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # filter type 0 (None) for this scanline
        raw.extend((r, g, b) * width)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit, color type 2 = RGB
    idat = zlib.compress(bytes(raw), level=9)
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def tiny_mp3() -> bytes:
    """~0.5s of a valid MPEG-1 Layer III silent frame, so <audio> can decode it.

    32kbps/44.1kHz mono: frame size = 144 * 32000 // 44100 = 104 bytes/frame.
    Header 0xFF 0xFB 0x10 0xC4 + 100 zero bytes, repeated 20x.
    """
    frame = b"\xff\xfb\x10\xc4" + b"\x00" * 100
    return frame * 20


# ---------------------------------------------------------------------------
# Call log + ComfyUI job store (thread-safe: ThreadingHTTPServer = one thread/request)
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_CALLS: list[dict] = []
_COMFY_JOBS: dict[str, dict] = {}
_COMFY_COUNTER = 0


def _record(entry: dict) -> dict:
    full = {"ts": time.time(), **entry}
    with _LOCK:
        _CALLS.append(full)
    return full


def _reset_state() -> None:
    global _COMFY_COUNTER  # noqa: PLW0603
    with _LOCK:
        _CALLS.clear()
        _COMFY_JOBS.clear()
        _COMFY_COUNTER = 0


def _next_comfy_id() -> str:
    global _COMFY_COUNTER  # noqa: PLW0603
    with _LOCK:
        _COMFY_COUNTER += 1
        return f"stub-job-{_COMFY_COUNTER}"


# ---------------------------------------------------------------------------
# Request shape helpers
# ---------------------------------------------------------------------------


def _detect_target_language(system_content: str) -> tuple[str, str]:
    """Return (name, code) for the first non-English LANG_MAP name found in *system_content*."""
    for name, code in LANG_MAP.items():
        if name == "English":
            continue
        if name in system_content:
            return name, code
    return "Unknown", "xx"


_COMFY_PROMPT_RE = re.compile(r'"CLIPTextEncode"[^{}]*"inputs"\s*:\s*{[^{}]*"text"\s*:\s*"([^"]*)"')


def _extract_comfy_prompt(graph: dict) -> str:
    """Best-effort: find the first CLIPTextEncode node's rendered prompt text.

    Falls back to a fixed string for a differently-shaped workflow rather than
    raising — the submit step must never fail just because the stub can't find a
    prompt to colour the placeholder image with.
    """
    try:
        for node in graph.values():
            if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
                text = node.get("inputs", {}).get("text")
                if isinstance(text, str) and text:
                    return text
    except AttributeError:
        pass
    return "comfyui-stub-prompt"


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

VERBOSE = False


class StubHandler(BaseHTTPRequestHandler):
    server_version = "MxTestStub/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format_: str, *args: object) -> None:  # noqa: A002
        if VERBOSE:
            sys.stderr.write(f"[stub] {self.address_string()} {format_ % args}\n")

    # -- small response helpers -------------------------------------------------

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # -- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        query = parse_qs(urlsplit(self.path).query)

        if path == "/__health":
            self._send_json(200, {"status": "ok"})
            return
        if path == "/__calls":
            with _LOCK:
                calls = list(_CALLS)
            self._send_json(200, {"calls": calls})
            return
        if path == "/api":
            # seqlog's SeqLogHandler reachability probe (GET {server_url}/api).
            self._send_json(200, {"Title": "mx-test-stub", "Version": "1.0"})
            return

        history_match = re.match(r"^/history/(?P<job_id>[^/]+)$", path)
        if history_match:
            self._handle_comfy_history(history_match.group("job_id"))
            return

        if path == "/view":
            filename = (query.get("filename") or ["stub.png"])[0]
            data = tiny_png(f"comfyui-view-{filename}")
            _record({"kind": "image-view", "provider": "comfyui", "path": path, "filename": filename})
            self._send_bytes(200, data, "image/png")
            return

        self._send_json(404, {"error": f"no such route: GET {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path

        if path == "/__reset":
            _reset_state()
            self._send_json(200, {"reset": True})
            return
        if path == "/v1/chat/completions":
            self._handle_chat_completions()
            return
        if path == "/v1/images/generations":
            self._handle_openai_image()
            return
        if path == "/tts":
            self._handle_speaktor_tts()
            return
        if path == "/stt":
            self._handle_speaktor_stt()
            return
        if path == "/prompt":
            self._handle_comfy_submit()
            return
        if path == "/api/events/raw":
            self._handle_seq_ingest()
            return

        predict_match = re.match(r"^/v1beta/models/(?P<model>[^/:]+):predict$", path)
        if predict_match:
            self._handle_genai_predict(predict_match.group("model"))
            return
        generate_match = re.match(r"^/v1beta/models/(?P<model>[^/:]+):generateContent$", path)
        if generate_match:
            self._handle_genai_generate_content(generate_match.group("model"))
            return

        self._send_json(404, {"error": f"no such route: POST {path}"})

    # -- translation ----------------------------------------------------------

    def _handle_chat_completions(self) -> None:
        body = self._read_json_body()
        messages = body.get("messages", [])
        system_content = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        user_content = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "",
        )
        lang_name, lang_code = _detect_target_language(system_content)
        word = str(user_content).strip()
        translated = f"{word}-{lang_code}"

        _record({
            "kind": "translate", "path": "/v1/chat/completions",
            "word": word, "target_language": lang_name, "target_lang_code": lang_code,
        })

        now = int(time.time())
        self._send_json(200, {
            "id": f"chatcmpl-stub-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": now,
            "model": body.get("model", "gpt-4o"),
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": translated},
                "logprobs": None,
            }],
            "usage": {
                "prompt_tokens": len(system_content.split()) + len(word.split()),
                "completion_tokens": len(translated.split()),
                "total_tokens": len(system_content.split()) + len(word.split()) + len(translated.split()),
            },
        })

    # -- images -----------------------------------------------------------------

    def _handle_openai_image(self) -> None:
        body = self._read_json_body()
        prompt = str(body.get("prompt", "openai-http-stub"))
        _record({"kind": "image", "provider": "openai_http", "path": "/v1/images/generations", "prompt": prompt})
        png_b64 = _b64(tiny_png(prompt))
        self._send_json(200, {"created": int(time.time()), "data": [{"b64_json": png_b64}]})

    def _handle_genai_predict(self, model: str) -> None:
        body = self._read_json_body()
        instances = body.get("instances") or [{}]
        prompt = str(instances[0].get("prompt", "genai-imagen-stub"))
        _record({
            "kind": "image", "provider": "genai_http", "path": f"/v1beta/models/{model}:predict", "prompt": prompt,
        })
        png_b64 = _b64(tiny_png(prompt))
        self._send_json(200, {"predictions": [{"bytesBase64Encoded": png_b64, "mimeType": "image/png"}]})

    def _handle_genai_generate_content(self, model: str) -> None:
        body = self._read_json_body()
        contents = body.get("contents") or [{}]
        parts = (contents[0] or {}).get("parts") or [{}]
        prompt = str(parts[0].get("text", "genai-gemini-stub"))
        _record({
            "kind": "image", "provider": "genai_http_gemini",
            "path": f"/v1beta/models/{model}:generateContent", "prompt": prompt,
        })
        png_b64 = _b64(tiny_png(prompt))
        self._send_json(200, {
            "candidates": [{"content": {"parts": [{"inlineData": {"data": png_b64, "mimeType": "image/png"}}]}}],
        })

    def _handle_comfy_submit(self) -> None:
        body = self._read_json_body()
        graph = body.get("prompt") or {}
        prompt_text = _extract_comfy_prompt(graph)
        job_id = _next_comfy_id()
        with _LOCK:
            _COMFY_JOBS[job_id] = {"prompt": prompt_text, "filename": f"{job_id}.png"}
        _record({"kind": "image", "provider": "comfyui", "path": "/prompt", "prompt": prompt_text, "job_id": job_id})
        self._send_json(200, {"prompt_id": job_id, "number": 1, "node_errors": {}})

    def _handle_comfy_history(self, job_id: str) -> None:
        with _LOCK:
            job = _COMFY_JOBS.get(job_id)
        if job is None:
            self._send_json(200, {})  # ComfyUI provider treats a missing entry as "still queued"
            return
        # Completed on the first poll — nothing under test needs simulated render latency.
        self._send_json(200, {
            job_id: {
                "status": {"status_str": "success", "completed": True, "messages": []},
                "outputs": {
                    "9": {"images": [{"filename": job["filename"], "subfolder": "", "type": "output"}]},
                },
            },
        })

    # -- speech -------------------------------------------------------------------

    def _handle_speaktor_tts(self) -> None:
        body = self._read_json_body()
        _record({
            "kind": "tts", "path": "/tts",
            "text": body.get("text", ""), "language": body.get("language", ""),
            "voice_name": body.get("voice_name", ""),
        })
        self._send_bytes(200, tiny_mp3(), "audio/mpeg")

    def _handle_speaktor_stt(self) -> None:
        # Best-effort only — see module docstring: evaluate.enabled=false in the test
        # config, so this route is never exercised by the current suite.
        body = self._read_json_body()
        _record({"kind": "stt", "path": "/stt"})
        self._send_json(200, {"text": body.get("expected_text", ""), "confidence": 1.0})

    # -- logging --------------------------------------------------------------------

    def _handle_seq_ingest(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)  # drain the body; CLEF payload shape is not validated
        _record({"kind": "log", "path": "/api/events/raw"})
        self._send_json(201, {})


def _b64(data: bytes) -> str:
    import base64  # pylint: disable=import-outside-toplevel

    return base64.b64encode(data).decode("ascii")


def main() -> int:
    global VERBOSE  # noqa: PLW0603
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=48002)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    server = ThreadingHTTPServer((args.host, args.port), StubHandler)
    print(f"[stub] listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
