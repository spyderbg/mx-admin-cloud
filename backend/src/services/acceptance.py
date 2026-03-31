"""Service config acceptance contract and result models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.services.models import ServiceProvider

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
        return provider.service_type in ("", self.service_name)


@dataclass(frozen=True)
class ConfigApplyResult:
    """Result of attempting to apply a config payload or config file."""

    accepted: bool
    source: str
    service_name: str | None = None
    provider_name: str | None = None
    error: str | None = None
