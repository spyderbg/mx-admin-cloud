"""Capability validation for an OpenAI-compatible endpoint (architecture §11.11, V5-5).

`validate_capabilities()` probes what the operator's profile *declares* with synthetic
prompts only — no repository content is ever sent:

  1. `chat`          — a plain completion; the response must name the exact requested model;
  2. `tool_calling`  — the model must call a harmless probe tool with the requested JSON
                       arguments, and then answer after receiving the tool result
                       (a two-step loop);
  3. `streaming`     — a streamed completion must arrive in several chunks and assemble.

`thinking` is backend-specific and `vision` needs a multimodal test; both are reported as
not tested. The result is a report: it never changes the configuration. An operator copies
passed capabilities into `validated_capabilities` deliberately. In Phase 3 this runs only
against the local fake server (the HTTP transport refuses remote endpoints).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ProviderError
from .providers import OpenAICompatibleProfile, check_transport, resolve_endpoint
from .runners import ChatTransport

PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "record_value",
        "description": "Record one value. Used only to validate tool calling.",
        "parameters": {"type": "object", "required": ["value"], "additionalProperties": False,
                       "properties": {"value": {"type": "string"}}},
    },
}
PROBE_VALUE = "probe-42"


@dataclass(frozen=True)
class CapabilityCheck:
    """One validation step."""

    name: str
    passed: bool | None  # None: not tested
    detail: str


def _message(response: Mapping[str, Any]) -> Mapping[str, Any]:
    return response["choices"][0]["message"]


def validate_capabilities(profile: OpenAICompatibleProfile, env: Mapping[str, str], transport: ChatTransport,
                          *, max_tokens: int = 256) -> list[CapabilityCheck]:
    """Run the validation sequence. See the module docstring."""
    endpoint = resolve_endpoint(profile, env)
    check_transport(profile, endpoint, carries_code=False)
    timeouts = {"connect_timeout": profile.connect_timeout_seconds,
                "request_timeout": profile.request_timeout_seconds}
    base = {"model": endpoint.model, "temperature": profile.temperature, "max_tokens": max_tokens, "stream": False}
    checks: list[CapabilityCheck] = []

    def step(name: str, func: Any) -> None:
        try:
            ok, detail = func()
        except (ProviderError, KeyError, IndexError, TypeError, ValueError) as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        checks.append(CapabilityCheck(name, ok, detail))

    def chat() -> tuple[bool, str]:
        response = transport.send(endpoint, {**base, "messages": [
            {"role": "user", "content": "Reply with the single word: pong"}]}, **timeouts)
        text = str(_message(response).get("content") or "")
        if response.get("model") != endpoint.model:
            return False, f"model mismatch: requested {endpoint.model!r}, got {response.get('model')!r}"
        return bool(text.strip()), f"answered {text.strip()[:40]!r}"

    def tools() -> tuple[bool, str]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": f"Call record_value with value {PROBE_VALUE}."}]
        first = _message(transport.send(endpoint, {**base, "messages": messages, "tools": [PROBE_TOOL]}, **timeouts))
        calls = first.get("tool_calls") or []
        if not calls:
            return False, "no tool call was returned"
        function = calls[0]["function"]
        args = json.loads(function["arguments"])
        if function["name"] != "record_value" or args.get("value") != PROBE_VALUE:
            return False, f"unexpected call {function['name']}({args})"
        messages += [{"role": "assistant", "content": first.get("content"), "tool_calls": calls},
                     {"role": "tool", "tool_call_id": calls[0].get("id", ""), "content": "recorded"}]
        final = _message(transport.send(endpoint, {**base, "messages": messages, "tools": [PROBE_TOOL]}, **timeouts))
        if final.get("tool_calls") or not str(final.get("content") or "").strip():
            return False, "the model did not finish after the tool result"
        return True, "called record_value correctly and finished after the result"

    def streaming() -> tuple[bool, str]:
        response = transport.send(endpoint, {**base, "stream": True, "messages": [
            {"role": "user", "content": "Count from 1 to 5."}]}, **timeouts)
        chunks = int(response.get("x_stream_chunks", 0))
        text = str(_message(response).get("content") or "")
        return chunks >= 2 and bool(text.strip()), f"{chunks} chunk(s), {len(text)} characters"

    step("chat", chat)
    step("tool_calling", tools)
    step("streaming", streaming)
    checks.append(CapabilityCheck("thinking", None, "not tested: backend-specific request fields (§11.11 step 3)"))
    checks.append(CapabilityCheck("vision", None, "not tested: needs a multimodal smoke test (§11.11 step 4)"))
    return checks
