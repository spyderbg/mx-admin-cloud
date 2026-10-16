"""Agent runners: mock Claude Code, mock Codex, and the OpenAI-compatible (remote Qwen) runner.

Every runner writes only through a ScopedWorkspace, so the role's write scope is
enforced at write time whatever the model asks for.

  * MockClaudeCodeRunner / MockCodexRunner replay scripted file edits per task. They
    never start the real CLIs; `planned_invocation()` shows the command line Phase 3
    would launch (flags confirmed to exist by the read-only `--help` probe; their
    containment behavior is a Phase 3 validation item).
  * OpenAICompatibleRunner is the real control loop for a Chat Completions endpoint: it
    sends requests through a pluggable transport, enforces the context budget, the
    transport/disclosure gates, timeouts and bounded retries, and executes tool calls
    LOCALLY with the client-side edit tools. Tools are only offered once the
    `tool_calling` capability is validated; until then the model is advisory/read-only.
    `apply-patch` is declared by the operator profile but not implemented yet, so it is
    refused.
  * The default transport is DisabledLiveTransport: Phase 2 cannot reach the remote
    server even by accident. Tests use MockChatTransport.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from .errors import (
    ContextBudgetError,
    ProviderAuthError,
    ProviderError,
    ProviderResponseError,
    ProviderUnavailableError,
    ScopeViolationError,
    TransportPolicyError,
)
from .paths import ScopedWorkspace
from .providers import (
    CliRunnerProfile,
    Endpoint,
    OpenAICompatibleProfile,
    ProviderKind,
    check_budget,
    check_transport,
    redact,
    resolve_endpoint,
)

IMPLEMENTED_EDIT_TOOLS = ("find-replace", "multi-find-replace")


@dataclass(frozen=True)
class FileEdit:
    """One scripted edit: new content for *path*, or None to delete it."""

    path: str
    content: str | None


@dataclass
class RunRequest:
    """What a runner is asked to do for one task."""

    task_id: str
    role: str
    prompt: str
    workspace: ScopedWorkspace
    carries_code: bool = True
    max_turns: int = 8


@dataclass
class RunResult:
    """What a runner did."""

    runner: str
    status: str  # completed | advisory | rejected | unavailable | failed
    changed_paths: list[str] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    rejected_tool_calls: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the runner finished its work (with or without edits)."""
        return self.status in ("completed", "advisory")


class Runner(Protocol):  # pylint: disable=too-few-public-methods
    """A role's agent runner."""

    name: str
    kind: ProviderKind

    def run(self, request: RunRequest) -> RunResult:
        """Do the work for one task."""


# ---------------------------------------------------------------------------
# Mock CLI runners
# ---------------------------------------------------------------------------

class MockCliRunner:
    """Replays scripted edits per task ID. Base class of the Claude Code and Codex mocks."""

    def __init__(self, profile: CliRunnerProfile, script: Mapping[str, Sequence[FileEdit]] | None = None,
                 *, failing_tasks: Sequence[str] = ()) -> None:
        self.profile = profile
        self.name = profile.name
        self.kind = profile.kind
        self.script = dict(script or {})
        self.failing_tasks = set(failing_tasks)
        self.requests: list[RunRequest] = []

    def run(self, request: RunRequest) -> RunResult:
        """Apply this task's scripted edits through the scoped workspace."""
        self.requests.append(request)
        if request.task_id in self.failing_tasks:
            return RunResult(self.name, "failed", error="scripted failure")
        try:
            for edit in self.script.get(request.task_id, ()):
                if edit.content is None:
                    request.workspace.delete(edit.path)
                else:
                    request.workspace.write_text(edit.path, edit.content)
        except ScopeViolationError as exc:
            return RunResult(self.name, "rejected", list(request.workspace.changed), error=str(exc))
        return RunResult(self.name, "completed", list(request.workspace.changed),
                         transcript=[f"mock {self.kind.value}: {len(request.workspace.changed)} file(s) written"])

    def planned_invocation(self, request: RunRequest) -> list[str]:
        """The command line Phase 3 would launch (never executed in Phase 2)."""
        if self.kind is ProviderKind.CLAUDE_CODE:
            # --setting-sources user,local skips the repository's project settings, whose
            # default is bypassPermissions; the launcher supplies its own --settings instead.
            return [self.profile.executable, "-p", "--output-format", "json", "--setting-sources", "user,local",
                    "--permission-mode", "acceptEdits", "--no-session-persistence", request.prompt]
        return [self.profile.executable, "exec", "--json", "--sandbox", "workspace-write", "--ephemeral",
                "-C", str(request.workspace.policy.root), request.prompt]


class MockClaudeCodeRunner(MockCliRunner):
    """Stand-in for Claude Code (Backend, CLI and Coordination defaults)."""


class MockCodexRunner(MockCliRunner):
    """Stand-in for Codex (Frontend default)."""


# ---------------------------------------------------------------------------
# OpenAI-compatible Chat Completions
# ---------------------------------------------------------------------------

class ChatTransport(Protocol):  # pylint: disable=too-few-public-methods
    """Sends one Chat Completions request and returns the decoded JSON response."""

    def send(self, endpoint: Endpoint, payload: Mapping[str, Any], *, connect_timeout: float,
             request_timeout: float) -> Mapping[str, Any]:
        """Raise ProviderUnavailableError (retryable), ProviderAuthError or ProviderResponseError."""


class DisabledLiveTransport:  # pylint: disable=too-few-public-methods
    """The default transport: live connections do not exist in Phase 2."""

    def send(self, endpoint: Endpoint, payload: Mapping[str, Any], *, connect_timeout: float,
             request_timeout: float) -> Mapping[str, Any]:
        """Always refuse."""
        del endpoint, payload, connect_timeout, request_timeout
        raise TransportPolicyError("live model connections are disabled in Phase 2; use a mock transport")


@dataclass
class RecordedRequest:
    """A request seen by MockChatTransport. The API key itself is never recorded."""

    url: str
    payload: dict[str, Any]
    has_api_key: bool
    connect_timeout: float
    request_timeout: float


class MockChatTransport:  # pylint: disable=too-few-public-methods
    """Scripted responses (dicts) or exceptions, consumed in order."""

    def __init__(self, script: Sequence[Mapping[str, Any] | Exception]) -> None:
        self.script = list(script)
        self.requests: list[RecordedRequest] = []

    def send(self, endpoint: Endpoint, payload: Mapping[str, Any], *, connect_timeout: float,
             request_timeout: float) -> Mapping[str, Any]:
        """Record the request and return (or raise) the next scripted item."""
        self.requests.append(RecordedRequest(endpoint.url, json.loads(json.dumps(payload)),
                                             endpoint.api_key is not None,
                                             connect_timeout, request_timeout))
        if not self.script:
            raise ProviderUnavailableError("mock transport has no scripted response left")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def chat_response(content: str | None = None, tool_calls: Sequence[Mapping[str, Any]] = (),
                  model: str = "Qwen3.6-35B-A3B-Q4_K_M.gguf") -> dict[str, Any]:
    """Build a Chat Completions response (for mocks and tests)."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"id": call.get("id", f"call_{i}"), "type": "function",
             "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])}}
            for i, call in enumerate(tool_calls)
        ]
    return {"id": "chatcmpl-mock", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}]}


TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "find-replace": {
        "type": "function",
        "function": {
            "name": "find-replace",
            "description": "Replace exactly one occurrence of `find` with `replace` in one file.",
            "parameters": {"type": "object", "additionalProperties": False, "required": ["path", "find", "replace"],
                           "properties": {"path": {"type": "string"}, "find": {"type": "string"},
                                          "replace": {"type": "string"}}},
        },
    },
    "multi-find-replace": {
        "type": "function",
        "function": {
            "name": "multi-find-replace",
            "description": "Apply several exact single-occurrence replacements to one file, all or nothing.",
            "parameters": {"type": "object", "additionalProperties": False, "required": ["path", "edits"],
                           "properties": {"path": {"type": "string"}, "edits": {
                               "type": "array", "minItems": 1, "items": {
                                   "type": "object", "required": ["find", "replace"],
                                   "properties": {"find": {"type": "string"}, "replace": {"type": "string"}}}}}},
        },
    },
}


def _replace_once(text: str, find: str, replace: str) -> str:
    if not find:
        raise ValueError("`find` must not be empty")
    count = text.count(find)
    if count != 1:
        raise ValueError(f"`find` must occur exactly once, found {count} occurrence(s)")
    return text.replace(find, replace, 1)


class OpenAICompatibleRunner:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Local agent loop driving a remote OpenAI-compatible Chat Completions endpoint."""

    def __init__(self, profile: OpenAICompatibleProfile, env: Mapping[str, str],
                 transport: ChatTransport | None = None, *, max_tokens: int = 4096,
                 sleep: Callable[[float], None] = time.sleep, backoff_seconds: float = 0.5) -> None:
        self.profile = profile
        self.name = profile.name
        self.kind = profile.kind
        self.env = env
        self.transport = transport or DisabledLiveTransport()
        self.max_tokens = max_tokens
        self.sleep = sleep
        self.backoff_seconds = backoff_seconds

    def _secrets(self) -> list[str | None]:
        return [self.env.get(self.profile.api_key_env)]

    def _send(self, endpoint: Endpoint, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        attempts = 1 + self.profile.max_retries
        for attempt in range(attempts):
            try:
                return self.transport.send(endpoint, payload, connect_timeout=self.profile.connect_timeout_seconds,
                                           request_timeout=self.profile.request_timeout_seconds)
            except ProviderUnavailableError:
                if attempt == attempts - 1:
                    raise
                self.sleep(self.backoff_seconds * (2 ** attempt))
        raise ProviderUnavailableError("unreachable")  # pragma: no cover

    @staticmethod
    def _message(response: Mapping[str, Any], model: str) -> Mapping[str, Any]:
        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError("response has no choices[0].message") from exc
        if not isinstance(message, Mapping):
            raise ProviderResponseError("choices[0].message is not an object")
        if response.get("model") not in (None, model):
            raise ProviderResponseError(
                f"model mismatch: requested {model!r}, server answered {response.get('model')!r}")
        return message

    def _execute(self, call: Mapping[str, Any], workspace: ScopedWorkspace) -> str:
        """Run one tool call locally. Returns the tool result text; raises ValueError to refuse."""
        function = call.get("function") or {}
        name = function.get("name")
        if name not in self.profile.client_edit_tools:
            raise ValueError(f"tool {name!r} is not in the provider's client_edit_tools")
        if name not in IMPLEMENTED_EDIT_TOOLS:
            raise ValueError(f"tool {name!r} is declared but not implemented or validated")
        try:
            args = json.loads(function.get("arguments") or "")
        except ValueError as exc:
            raise ValueError("tool arguments are not valid JSON") from exc
        if not isinstance(args, dict) or not isinstance(args.get("path"), str):
            raise ValueError("tool arguments need a string `path`")
        text = workspace.read_text(args["path"])
        if name == "find-replace":
            updated = _replace_once(text, str(args.get("find", "")), str(args.get("replace", "")))
        else:
            edits = args.get("edits")
            if not isinstance(edits, list) or not edits:
                raise ValueError("`edits` must be a non-empty list")
            updated = text
            for edit in edits:
                updated = _replace_once(updated, str(edit.get("find", "")), str(edit.get("replace", "")))
        rel = workspace.write_text(args["path"], updated)
        return f"ok: {name} applied to {rel}"

    def _request(self, endpoint: Endpoint, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 result: RunResult) -> dict[str, Any] | None:
        """One chat completion. On failure, record the outcome in *result* and return None."""
        payload: dict[str, Any] = {"model": endpoint.model, "messages": messages, "stream": False,
                                   "temperature": self.profile.temperature, "max_tokens": self.max_tokens}
        if tools:
            payload["tools"] = tools
        try:
            check_budget(self.profile, messages, tools, self.max_tokens)
            return self._message(self._send(endpoint, payload), endpoint.model)
        except ContextBudgetError as exc:
            result.error = str(exc)
        except TransportPolicyError as exc:
            result.status, result.error = "rejected", str(exc)
        except ProviderUnavailableError as exc:
            result.status, result.error = "unavailable", redact(str(exc), self._secrets())
        except (ProviderAuthError, ProviderResponseError) as exc:
            result.error = redact(str(exc), self._secrets())
        return None

    def run(self, request: RunRequest) -> RunResult:  # pylint: disable=too-many-return-statements
        """Drive the model for one task. Never raises for provider problems; reports them."""
        try:
            endpoint = resolve_endpoint(self.profile, self.env)
            check_transport(self.profile, endpoint, carries_code=request.carries_code)
        except TransportPolicyError as exc:
            return RunResult(self.name, "rejected", error=str(exc))
        except ProviderError as exc:
            return RunResult(self.name, "unavailable", error=redact(str(exc), self._secrets()))
        tools_enabled = self.profile.capability_enabled("tool_calling")
        tools = [TOOL_DEFINITIONS[t] for t in self.profile.client_edit_tools if t in TOOL_DEFINITIONS] \
            if tools_enabled else []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"You are the {request.role} agent. Edit only files of your role, "
                                          "using the provided tools."},
            {"role": "user", "content": request.prompt},
        ]
        result = RunResult(self.name, "failed")
        for _turn in range(request.max_turns):
            message = self._request(endpoint, messages, tools, result)
            if message is None:
                return result
            calls = message.get("tool_calls") or []
            if message.get("content"):
                result.transcript.append(redact(str(message["content"]), self._secrets()))
            if not calls:
                result.status = "completed" if tools_enabled else "advisory"
                result.changed_paths = list(request.workspace.changed)
                return result
            if not tools_enabled:
                result.status = "rejected"
                result.rejected_tool_calls = [str((c.get("function") or {}).get("name")) for c in calls]
                result.error = "the model requested tools, but tool calling has not been validated (V5-5)"
                return result
            messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": list(calls)})
            for call in calls:
                try:
                    outcome = self._execute(call, request.workspace)
                except (ValueError, ScopeViolationError, OSError) as exc:
                    outcome = f"refused: {exc}"
                    result.rejected_tool_calls.append(f"{(call.get('function') or {}).get('name')}: {exc}")
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": outcome})
        result.error = f"no final answer within {request.max_turns} turns"
        result.changed_paths = list(request.workspace.changed)
        return result
