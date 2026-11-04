"""The `broker` section of the project configuration (W1: D-20, D-21).

Kept apart from config.py, which re-exports BrokerConfig, so the core configuration stays within its
size limit. It holds no secret: only where the host-side broker reads its upstream credential (an
absolute host path per provider) and the limits it applies to every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from .errors import ConfigError
from .providers import IGNORED_PROFILE_KEYS, OpenAICompatibleProfile

if TYPE_CHECKING:  # pragma: no cover
    from .config import ProjectConfig

# W1 (D-20): where the host-side broker reads the upstream credential. `none` (the default)
# leaves real runners unavailable; `api-key-file` is a per-token-billed provider API key that
# the operator provisions on the host. A subscription login cannot be brokered.
BROKER_CREDENTIAL_SOURCES = ("none", "api-key-file")


@dataclass(frozen=True)
class BrokerConfig:  # pylint: disable=too-many-instance-attributes
    """The host-side authentication broker (W1: D-20, D-21). It holds no secret itself: only
    where the broker reads its upstream credential (an absolute host path per provider) and
    the limits it applies to every run."""

    credential_source: str = "none"
    api_key_files: Mapping[str, str] = field(default_factory=dict)
    max_request_bytes: int = 32 * 1024 * 1024
    max_response_bytes: int = 64 * 1024 * 1024
    upstream_idle_timeout_seconds: float = 300.0
    max_request_seconds: float = 1800.0
    max_requests_per_run: int = 1000
    max_concurrent_requests: int = 8
    handshake_timeout_seconds: float = 30.0


def validate_broker(broker: BrokerConfig, providers: Mapping[str, Any]) -> None:
    """Raise ConfigError unless *broker* is valid for the configured *providers*."""
    cli_providers = {name for name, p in providers.items() if not isinstance(p, OpenAICompatibleProfile)}
    if broker.credential_source not in BROKER_CREDENTIAL_SOURCES:
        raise ConfigError(f"broker.credential_source must be one of {BROKER_CREDENTIAL_SOURCES}")
    for name, path in broker.api_key_files.items():
        if name not in cli_providers:
            raise ConfigError(f"broker.api_key_files: {name!r} is not a Claude Code or Codex provider")
        if not str(path).startswith("/") or "\x00" in str(path) or ".." in str(path).split("/"):
            raise ConfigError(f"broker.api_key_files[{name!r}] must be an absolute host path without '..'")
    if broker.api_key_files and broker.credential_source != "api-key-file":
        raise ConfigError("broker.api_key_files is only used with credential_source: api-key-file")
    for name in ("max_request_bytes", "max_response_bytes", "upstream_idle_timeout_seconds", "max_request_seconds",
                 "max_requests_per_run", "max_concurrent_requests", "handshake_timeout_seconds"):
        if getattr(broker, name) <= 0:
            raise ConfigError(f"broker.{name} must be positive")
    if broker.max_request_bytes > 256 * 1024 * 1024 or broker.max_concurrent_requests > 64:
        raise ConfigError("broker.max_request_bytes is capped at 256 MiB and max_concurrent_requests at 64")


def broker_from_mapping(data: Mapping[str, Any]) -> BrokerConfig:
    """`broker:` from the project-config YAML. Unknown keys are refused: credentials never belong here."""
    defaults = BrokerConfig()
    known = set(BrokerConfig.__dataclass_fields__)  # pylint: disable=no-member
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown broker keys: {sorted(unknown)} (credentials never belong here, only file paths)")
    values: dict[str, Any] = {}
    for key in known & set(data):
        default, raw = getattr(defaults, key), data[key]
        if key == "api_key_files":
            values[key] = {str(k): str(v) for k, v in dict(raw or {}).items()}
        else:
            values[key] = type(default)(raw)
    return BrokerConfig(**values)


def ignored_settings(config: "ProjectConfig") -> list[str]:
    """W1: values accepted so that existing (W2-W4) configurations keep loading, but without effect."""
    notes = []
    if config.runtime.agent_network:
        notes.append("runtime.agent_network: true has no effect: bwrap runner sandboxes never share the host "
                     "network (D-21); the native-cli backend is unsandboxed either way")
    for name, profile in sorted(config.providers.items()):
        for key in getattr(profile, "ignored_keys", ()):
            notes.append(f"providers.{name}.{key} has no effect: {IGNORED_PROFILE_KEYS[key]}")
    return notes
