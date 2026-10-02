"""Service config acceptance contract and result models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from typing_extensions import override
from src.services.models import ServiceProvider

if TYPE_CHECKING:
    from src.services.service_registry import ServiceRegistryBuilder

ValidateProviderFn = Callable[[ServiceProvider], None]
BuildProviderFn = Callable[[ServiceProvider, Path], Any]
BuildRuntimeProvidersFn = Callable[[Path, "ServiceRegistryBuilder"], list[Any]]
AvailableProvidersFn = Callable[[], list[str]]


@dataclass(frozen=True)
class ServiceAcceptCriteria:
    """Rules used to validate and materialize providers for one service.

    "build_runtime_providers" is optional: it creates providers that are wired
    from runtime dependencies (config root, registry builder) instead of a config
    file. They are added on every load and replace any loaded provider of the
    same provider type.
    """

    service_name: str
    config_subdir: str
    validate_provider: ValidateProviderFn
    build_provider: BuildProviderFn
    build_runtime_providers: BuildRuntimeProvidersFn | None = None
    available_providers: AvailableProvidersFn | None = None

    def can_accept(self, provider: ServiceProvider) -> bool:
        """Return whether this service can accept *provider* envelope."""
        return provider.service in ("", self.service_name)


@dataclass
class ConfigApplyResult:
    """Result of attempting to apply a config payload or config file."""

    def __init__(self, config_name: str, accepted: bool = False, error: str | None = None) -> None:
        self.config_name: str = config_name
        self.accepted: bool = accepted
        self.error: str | None = error

    @override
    def __str__(self) -> str:
        """Return a human-readable summary of the result."""
        text = f"{self.config_name}\n"
        text = text + f" accepted:{str(self.accepted).lower()}\n"
        if self.error:
            text = text + f" error:{self.error}"
        return text
