"""Model provider profiles, independent of roles and permissions (v5 §10, §10.1).

Three provider kinds exist: the Claude Code and Codex CLIs (local agent runners) and an
OpenAI-compatible Chat Completions endpoint — the operator's remote Qwen server. A role
picks a provider by name in the project configuration; swapping it never changes the
role's write scope, task identity or branch.

The remote Qwen profile below is the operator-supplied profile from v5 §10.1.1, kept
exact: model ID, declared limits, declared capabilities, client-side edit tools and
temperature. It is configuration input, not verified behavior:

  * declared capabilities stay disabled until validated (`validated_capabilities`);
  * the endpoint comes only from the environment (`AGENT_QWEN_BASE_URL`), never from a
    tracked file, and a plain-HTTP non-loopback endpoint is refused (D-15) as is sending
    repository code to a remote model (D-16) until the operator records an exception;
  * the usable input budget is window − output − margin = 110,592 tokens, not the
    declared 128,000.

W2: a role chooses its agent (a provider) AND, optionally, its model (`roles.<r>.model`, which
overrides the provider's default). `check_model()` rejects a model the agent kind does not
support — a Claude model for Codex, an unknown alias — instead of substituting another one;
"default" selects the CLI's own default model explicitly.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Union
from urllib.parse import urlsplit, urlunsplit

from .errors import ContextBudgetError, ProviderError, ProviderUnavailableError, TransportPolicyError

CHAT_COMPLETIONS_SUFFIX = "/chat/completions"
KNOWN_CAPABILITIES = ("tool_calling", "vision", "streaming", "thinking")
KNOWN_EDIT_TOOLS = ("apply-patch", "multi-find-replace", "find-replace")
_PER_MESSAGE_OVERHEAD_TOKENS = 8


class ProviderKind(str, Enum):
    """How a provider is driven."""

    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    OPENAI_COMPATIBLE = "openai-compatible"


DEFAULT_CREDENTIAL_FILES = {
    # Home-relative files the runner needs to authenticate; bound READ-ONLY into the sandbox.
    "claude-code": (".claude/.credentials.json",),
    "codex": (".codex/auth.json",),
}


# W2: which model names each agent kind accepts. A model outside these is REJECTED, never
# replaced by another one. "default" (or "") selects the CLI's own default model explicitly.
MODEL_DEFAULT = "default"
CLAUDE_MODEL_ALIASES = ("fable", "opus", "sonnet", "haiku", "opusplan")  # `claude --help`: aliases or full names
_CLAUDE_MODEL_ID = re.compile(r"^claude-[a-z0-9][a-z0-9.-]*(\[1m\])?$")
_CODEX_MODEL_ID = re.compile(r"^(gpt-[0-9][a-z0-9.-]*|o[0-9][a-z0-9-]*|codex-[a-z0-9][a-z0-9.-]*)$")


def check_model(kind: "ProviderKind", model: str, *, where: str = "model") -> str:
    """*model* normalized ('' = the CLI default) if *kind* supports it; ProviderError otherwise."""
    model = (model or "").strip()
    if model in ("", MODEL_DEFAULT):
        return ""
    if kind is ProviderKind.CLAUDE_CODE and (model in CLAUDE_MODEL_ALIASES or _CLAUDE_MODEL_ID.fullmatch(model)):
        return model
    if kind is ProviderKind.CODEX and _CODEX_MODEL_ID.fullmatch(model):
        return model
    if kind is ProviderKind.OPENAI_COMPATIBLE:
        raise ProviderError(f"{where}: {model!r} — the OpenAI-compatible provider takes its model from its "
                            "profile and environment; a role-level model is not supported")
    raise ProviderError(f"{where}: {model!r} is not a {kind.value} model (unsupported agent/model combination; "
                        "it is never substituted)")


def model_family(model: str) -> tuple[str, ...]:
    """The Claude model families a requested model may resolve to (for the post-run check)."""
    if model == "opusplan":
        return ("opus", "sonnet")
    for family in CLAUDE_MODEL_ALIASES:
        if model == family or model.startswith(f"claude-{family}"):
            return (family,)
    return ()


@dataclass(frozen=True)
class CliRunnerProfile:
    """A local agent CLI (Claude Code or Codex) launched on the agent host."""

    name: str
    kind: ProviderKind
    executable: str
    model: str = ""  # empty or "default": the CLI's own default model (a role may override it)
    credential_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in (ProviderKind.CLAUDE_CODE, ProviderKind.CODEX):
            raise ProviderError(f"provider {self.name!r}: {self.kind} is not a CLI runner kind")
        check_model(self.kind, self.model, where=f"provider {self.name!r}")
        for rel in self.credential_files:
            if rel.startswith("/") or ".." in rel.split("/"):
                raise ProviderError(f"provider {self.name!r}: credential file {rel!r} must be home-relative")


@dataclass(frozen=True)
class OpenAICompatibleProfile:  # pylint: disable=too-many-instance-attributes
    """An OpenAI-compatible Chat Completions endpoint driven by a local runner."""

    name: str
    default_model: str
    display_name: str
    base_url_env: str
    model_env: str
    api_key_env: str
    declared_capabilities: Mapping[str, bool]
    context_window_tokens: int
    declared_max_input_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    temperature: float
    client_edit_tools: tuple[str, ...]
    connect_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 180.0
    max_retries: int = 2
    require_tool_use_validation: bool = True
    require_tls: bool = True
    api_route: str = "chat.completions"
    # Operator decisions, both False until recorded (D-15 transport, D-16 code disclosure).
    plain_http_exception: bool = False
    code_disclosure_allowed: bool = False
    # Capabilities proven by a validation run. Declared ones are NOT enabled by default.
    validated_capabilities: frozenset[str] = field(default_factory=frozenset)
    kind: ProviderKind = ProviderKind.OPENAI_COMPATIBLE

    def __post_init__(self) -> None:
        if self.api_route != "chat.completions":
            raise ProviderError(f"provider {self.name!r}: only the chat.completions route is supported")
        for value, label in ((self.context_window_tokens, "context_window_tokens"),
                             (self.max_output_tokens, "max_output_tokens"),
                             (self.declared_max_input_tokens, "declared_max_input_tokens")):
            if value <= 0:
                raise ProviderError(f"provider {self.name!r}: {label} must be positive")
        if not 0 <= self.safety_margin_tokens < self.context_window_tokens - self.max_output_tokens:
            raise ProviderError(f"provider {self.name!r}: safety margin leaves no input budget")
        unknown = set(self.declared_capabilities) - set(KNOWN_CAPABILITIES)
        unknown |= set(self.validated_capabilities) - set(KNOWN_CAPABILITIES)
        if unknown:
            raise ProviderError(f"provider {self.name!r}: unknown capabilities {sorted(unknown)}")
        if set(self.client_edit_tools) - set(KNOWN_EDIT_TOOLS):
            raise ProviderError(f"provider {self.name!r}: unknown edit tools {self.client_edit_tools}")
        if self.max_retries < 0 or self.connect_timeout_seconds <= 0 or self.request_timeout_seconds <= 0:
            raise ProviderError(f"provider {self.name!r}: timeouts must be positive and retries >= 0")

    @property
    def hard_max_input_tokens(self) -> int:
        """Input budget with the full output reservation: window − output (114,688 for Qwen)."""
        return self.context_window_tokens - self.max_output_tokens

    @property
    def effective_max_input_tokens(self) -> int:
        """Working input cap: hard maximum − safety margin (110,592 for Qwen)."""
        return self.hard_max_input_tokens - self.safety_margin_tokens

    def capability_enabled(self, capability: str) -> bool:
        """True only for capabilities that were validated, never for merely declared ones."""
        if capability not in KNOWN_CAPABILITIES:
            raise ProviderError(f"unknown capability {capability!r}")
        return capability in self.validated_capabilities


ProviderProfile = Union[CliRunnerProfile, OpenAICompatibleProfile]

CLAUDE_CODE = CliRunnerProfile(name="claude", kind=ProviderKind.CLAUDE_CODE, executable="claude",
                               credential_files=DEFAULT_CREDENTIAL_FILES["claude-code"])
CODEX = CliRunnerProfile(name="codex", kind=ProviderKind.CODEX, executable="codex",
                         credential_files=DEFAULT_CREDENTIAL_FILES["codex"])
REMOTE_QWEN = OpenAICompatibleProfile(
    name="remote_qwen",
    default_model="Qwen3.6-35B-A3B-Q4_K_M.gguf",
    display_name="Qwen 3.6 35B A3B",
    base_url_env="AGENT_QWEN_BASE_URL",
    model_env="AGENT_QWEN_MODEL",
    api_key_env="AGENT_QWEN_API_KEY",
    declared_capabilities={"tool_calling": True, "vision": True, "streaming": True, "thinking": True},
    context_window_tokens=131072,
    declared_max_input_tokens=128000,
    max_output_tokens=16384,
    safety_margin_tokens=4096,
    temperature=0.2,
    client_edit_tools=("apply-patch", "multi-find-replace", "find-replace"),
)


def profile_from_mapping(name: str, data: Mapping[str, Any]) -> ProviderProfile:
    """Build a provider profile from a configuration mapping (project-config YAML)."""
    try:
        kind = ProviderKind(data["kind"])
    except (KeyError, ValueError) as exc:
        raise ProviderError(f"provider {name!r}: missing or unknown kind") from exc
    if kind in (ProviderKind.CLAUDE_CODE, ProviderKind.CODEX):
        default_exe = "claude" if kind is ProviderKind.CLAUDE_CODE else "codex"
        return CliRunnerProfile(name=name, kind=kind, executable=str(data.get("executable", default_exe)),
                                model=str(data.get("model", "")),
                                credential_files=tuple(str(f) for f in data.get(
                                    "credential_files", DEFAULT_CREDENTIAL_FILES[kind.value])))
    try:
        return OpenAICompatibleProfile(
            name=name,
            default_model=str(data["default_model"]),
            display_name=str(data.get("display_name", data["default_model"])),
            base_url_env=str(data["base_url_env"]),
            model_env=str(data["model_env"]),
            api_key_env=str(data["api_key_env"]),
            api_route=str(data.get("api_route", "chat.completions")),
            declared_capabilities={str(k): bool(v) for k, v in dict(data.get("declared_capabilities", {})).items()},
            context_window_tokens=int(data["context_window_tokens"]),
            declared_max_input_tokens=int(data["declared_max_input_tokens"]),
            max_output_tokens=int(data["max_output_tokens"]),
            safety_margin_tokens=int(data["safety_margin_tokens"]),
            temperature=float(data["temperature"]),
            client_edit_tools=tuple(str(tool) for tool in data.get("client_edit_tools", ())),
            connect_timeout_seconds=float(data.get("connect_timeout_seconds", 10)),
            request_timeout_seconds=float(data.get("request_timeout_seconds", 180)),
            max_retries=int(data.get("max_retries", 2)),
            require_tool_use_validation=bool(data.get("require_tool_use_validation", True)),
            require_tls=bool(data.get("require_tls", True)),
            plain_http_exception=bool(data.get("plain_http_exception", False)),
            code_disclosure_allowed=bool(data.get("code_disclosure_allowed", False)),
            validated_capabilities=frozenset(str(c) for c in data.get("validated_capabilities", ())),
        )
    except KeyError as exc:
        raise ProviderError(f"provider {name!r}: missing field {exc.args[0]!r}") from exc


def check_effective_input(profile: OpenAICompatibleProfile, declared_effective: int | None) -> None:
    """Reject a configuration whose stated effective input cap exceeds the computed one."""
    if declared_effective is not None and declared_effective > profile.effective_max_input_tokens:
        raise ProviderError(
            f"provider {profile.name!r}: effective_max_input_tokens {declared_effective} exceeds "
            f"window − output − margin = {profile.effective_max_input_tokens}",
        )


# ---------------------------------------------------------------------------
# Endpoint resolution (runtime environment only)
# ---------------------------------------------------------------------------

def chat_completions_url(configured: str) -> str:
    """The Chat Completions URL for *configured*, appending `/chat/completions` exactly once.

    Accepts either an SDK-style base URL (`…/v1`) or the full endpoint
    (`…/v1/chat/completions`). Never adds `/v1` and never duplicates a suffix.
    """
    parts = urlsplit(configured.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ProviderError("the model endpoint must be an http(s) URL with a host")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ProviderError("the model endpoint must not carry a query, fragment or credentials")
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions/chat/completions") or "/v1/v1" in path + "/":
        raise ProviderError("the model endpoint duplicates /v1 or /chat/completions")
    if not path.endswith(CHAT_COMPLETIONS_SUFFIX):
        path += CHAT_COMPLETIONS_SUFFIX
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


@dataclass(frozen=True)
class Endpoint:
    """A resolved endpoint. The API key never appears in repr() or str()."""

    url: str
    model: str
    api_key: str | None = field(default=None, repr=False)

    @property
    def scheme(self) -> str:
        """URL scheme, 'http' or 'https'."""
        return urlsplit(self.url).scheme

    @property
    def is_loopback(self) -> bool:
        """True for localhost / loopback addresses (mock servers)."""
        host = urlsplit(self.url).hostname or ""
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False


def resolve_endpoint(profile: OpenAICompatibleProfile, env: Mapping[str, str]) -> Endpoint:
    """Read endpoint, model and optional key from *env*. Raises ProviderUnavailableError."""
    base = (env.get(profile.base_url_env) or "").strip()
    if not base:
        raise ProviderUnavailableError(f"{profile.base_url_env} is not set; the {profile.name} role is unavailable")
    model = (env.get(profile.model_env) or "").strip() or profile.default_model
    key = (env.get(profile.api_key_env) or "").strip() or None
    return Endpoint(url=chat_completions_url(base), model=model, api_key=key)


def check_transport(profile: OpenAICompatibleProfile, endpoint: Endpoint, *, carries_code: bool) -> None:
    """Enforce the transport (D-15) and code-disclosure (D-16) gates. Raises TransportPolicyError."""
    if endpoint.is_loopback:
        return
    if endpoint.scheme == "http" and profile.require_tls and not profile.plain_http_exception:
        raise TransportPolicyError(
            f"{profile.name}: plain HTTP to a non-loopback host is blocked until the transport is secured "
            "or an explicit, documented exception is recorded (D-15)",
        )
    if carries_code and not profile.code_disclosure_allowed:
        raise TransportPolicyError(
            f"{profile.name}: sending repository code to a remote model is not approved (D-16)",
        )


# ---------------------------------------------------------------------------
# Context budget
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Conservative token estimate (one token per 3 UTF-8 bytes) — no tokenizer is available,
    so this deliberately over-counts relative to typical ~4 bytes/token ratios."""
    return math.ceil(len(text.encode("utf-8")) / 3)


def estimate_request_tokens(messages: Iterable[Mapping[str, Any]], tools: Iterable[Mapping[str, Any]] = ()) -> int:
    """Estimated prompt size: every message, every tool definition and per-message overhead."""
    total = 0
    for message in messages:
        total += estimate_tokens(json.dumps(message, ensure_ascii=False, sort_keys=True)) + _PER_MESSAGE_OVERHEAD_TOKENS
    for tool in tools:
        total += estimate_tokens(json.dumps(tool, ensure_ascii=False, sort_keys=True))
    return total


def check_budget(
        profile: OpenAICompatibleProfile,
        messages: Iterable[Mapping[str, Any]],
        tools: Iterable[Mapping[str, Any]],
        max_tokens: int,
) -> int:
    """Return the estimated input size, or raise ContextBudgetError when over budget."""
    if not 0 < max_tokens <= profile.max_output_tokens:
        raise ContextBudgetError(f"max_tokens {max_tokens} must be in 1..{profile.max_output_tokens}")
    estimated = estimate_request_tokens(messages, tools)
    if estimated > profile.effective_max_input_tokens:
        raise ContextBudgetError(
            f"estimated input {estimated} tokens exceeds the working cap "
            f"{profile.effective_max_input_tokens} (window {profile.context_window_tokens} − output "
            f"{profile.max_output_tokens} − margin {profile.safety_margin_tokens}); truncate or summarize",
        )
    return estimated


def redact(text: str, secrets: Iterable[str | None]) -> str:
    """Replace every non-empty secret in *text* with ***."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
