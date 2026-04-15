"""Logging service business logic: factory and orchestration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from typing_extensions import override

from src.services.models import Service, ServiceProvider
from src.services.provider_factory import ServiceProviderFactory
from src.services.logging.models import LoggingServiceProvider


class LoggingProviderFactory(ServiceProviderFactory[LoggingServiceProvider]):
    """Logging-specific provider registry."""

    unsupported_label: str = "logging provider"


class LoggingService(Service):
    """Orchestrator for all logging providers.

    After the registry loads config files, the service holds materialized
    provider instances.  Call `configure` once at startup so the
    active provider(s) can wire stdlib "dictConfig", structlog, and the
    request logger.
    """

    def __init__(
            self,
            service_name: str,
            package_name: str,
            service_dir: Path,
            base_dir: Path,
    ) -> None:
        super().__init__(service_name, package_name, service_dir, base_dir)
        self._providers: list[LoggingServiceProvider] = []

    @override
    def replace_providers(self, providers: list[ServiceProvider]) -> None:
        self._providers = [
            p for p in providers if isinstance(p, LoggingServiceProvider)
        ]

    @override
    def providers_snapshot(self) -> list[ServiceProvider]:
        return list(self._providers)

    @property
    def active_providers(self) -> list[LoggingServiceProvider]:
        """Return providers that report themselves as active."""
        return [p for p in self._providers if p.is_active()]

    # ------------------------------------------------------------------
    # Startup configuration
    # ------------------------------------------------------------------

    def configure(self, *, output_dir: Path, profile: str) -> None:
        """Ask each active provider to apply its logging configuration."""
        for provider in self.active_providers:
            provider.configure(output_dir=output_dir, profile=profile)

    # ------------------------------------------------------------------
    # Runtime helpers delegated to the first active provider
    # ------------------------------------------------------------------

    def _active(self) -> LoggingServiceProvider | None:
        providers = self.active_providers
        return providers[0] if providers else None

    def get_logger(self, name: str) -> Any:
        """Return a logger for *name* via the active provider (or stdlib)."""
        provider = self._active()
        if provider is not None:
            return provider.get_logger(name)
        return logging.getLogger(name)

    def emit(self, logger_name: str, level: str, event: str, **fields: Any) -> None:
        """Emit a structured log through the active provider."""
        provider = self._active()
        if provider is not None:
            provider.emit(logger_name, level, event, **fields)

    def bind_request_id(self, request_id: str) -> None:
        """Bind a request ID to the logging context."""
        provider = self._active()
        if provider is not None:
            provider.bind_request_id(request_id)

    def clear_request_context(self) -> None:
        """Clear the logging request context."""
        provider = self._active()
        if provider is not None:
            provider.clear_request_context()

    def log_to_all_active_loggers(
            self, message: str, level: int = logging.INFO,
    ) -> None:
        """Broadcast *message* through the active provider."""
        provider = self._active()
        if provider is not None:
            provider.log_to_all_active_loggers(message, level)
