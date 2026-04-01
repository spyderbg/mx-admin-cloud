"""Service config acceptance contract and result models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.services.models import ServiceProvider
from typing_extensions import override

ValidateProviderFn = Callable[[ServiceProvider], None]
BuildProviderFn = Callable[[ServiceProvider, Path], Any]


@dataclass(frozen=True)
class ServiceAcceptCriteria:
    """Rules used to validate and materialize providers for one service."""

    service_name: str
    config_subdir: str
    validate_provider: ValidateProviderFn
    build_provider: BuildProviderFn

    def can_accept(self, provider: ServiceProvider) -> bool:
        """Return whether this service can accept *provider* envelope."""
        return provider.service in ("", self.service_name)


@dataclass(frozen=False)
class ConfigApplyResult:
    """Result of attempting to apply a config payload or config file."""

    def __init__(self, config_name: str, accepted: bool = False, error: str | None = None) -> None:
        self.config_name = config_name
        self.accepted = accepted
        self.error = error

    @override
    def __str__(self) -> str:
        """Return a human-readable summary of the result."""
        text = f"{self.config_name}\n"
        text = text + f" accepted:{str(self.accepted).lower()}\n"
        if self.error:
            text = text + f" error:{self.error}"
        return text
