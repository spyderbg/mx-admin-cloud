"""Logging service business logic: factory and orchestration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from typing_extensions import override

from src.services.models import Service, ServiceProvider, ServiceProviderInfo
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
        self._configure_kwargs: dict | None = None

    @override
    def replace_providers(self, providers: list[ServiceProvider]) -> None:
        self._providers = [
            p for p in providers if isinstance(p, LoggingServiceProvider)
        ]
        # If the service was already configured (startup has completed), apply
        # the new providers' level changes immediately so a restart isn't required.
        if self._configure_kwargs is not None:
            for provider in self.active_providers:
                provider.apply_levels()

    @override
    def providers_snapshot(self) -> list[ServiceProvider]:
        return list(self._providers)

    @property
    def active_providers(self) -> list[LoggingServiceProvider]:
        """Return providers that report themselves as active."""
        return [p for p in self._providers if p.is_active()]

    @override
    def get_all_providers(self) -> list[LoggingServiceProvider]:
        """Return all loaded providers, including disabled ones."""
        return list(self._providers)

    @override
    def get_active_providers(self) -> list[LoggingServiceProvider]:
        """Return active providers."""
        return self.active_providers

    # ------------------------------------------------------------------
    # Startup configuration
    # ------------------------------------------------------------------

    def configure(self, *, output_dir: Path, profile: str) -> None:
        """Ask each active provider to apply its logging configuration."""
        self._configure_kwargs = {"output_dir": output_dir, "profile": profile}
        for provider in self.active_providers:
            provider.configure(output_dir=output_dir, profile=profile)

    # ------------------------------------------------------------------
    # Runtime helpers — name-based provider routing
    # ------------------------------------------------------------------

    def _provider_by_name(self, name: str) -> LoggingServiceProvider | None:
        """Look up an active provider whose config-file stem matches *name*."""
        for provider in self.active_providers:
            if provider.name == name:
                return provider
        return None

    def _resolve_provider(self, name: str) -> LoggingServiceProvider | None:
        """Find a provider by *name*, fall back to "default", then first active."""
        provider = self._provider_by_name(name)
        if provider is not None:
            return provider
        provider = self._provider_by_name("default")
        if provider is not None:
            return provider
        active = self.active_providers
        return active[0] if active else None

    @staticmethod
    def list_logging_managers(*, include_all: bool = False) -> list[dict]:
        """Return info about stdlib loggers registered in the logging manager.

        By default, only loggers that have at least one handler and are not
        disabled are returned.  Pass "include_all=True" to include every
        logger that has been instantiated, regardless of state.
        """
        result: list[dict] = []

        root = logging.getLogger()
        root_handlers = [type(h).__name__ for h in root.handlers]
        root_active = bool(root.handlers)
        if include_all or root_active:
            result.append({
                "name": "root",
                "level": logging.getLevelName(root.level),
                "active": root_active,
                "handlers": root_handlers,
            })

        manager = logging.Logger.manager
        for name, obj in sorted(manager.loggerDict.items()):
            if not isinstance(obj, logging.Logger):
                continue
            handler_names = [type(h).__name__ for h in obj.handlers]
            active = bool(obj.handlers) and not obj.disabled
            if not include_all and not active:
                continue
            result.append({
                "name": name,
                "level": logging.getLevelName(obj.level),
                "active": active,
                "handlers": handler_names,
            })

        return result

    def get_logger(self, name: str) -> Any:
        """Return a logger for *name* via the matching provider (or stdlib)."""
        provider = self._resolve_provider(name)
        if provider is not None:
            return provider.get_logger(name)
        return logging.getLogger(name)

    def get_active_loggers(self) -> list[ServiceProviderInfo]:
        """Return info about enabled logging service providers, sorted by priority."""
        return self._provider_info_list(self.active_providers)

    def get_all_loggers(self) -> list[ServiceProviderInfo]:
        """Return info about every configured logging service provider, sorted by priority."""
        return self._provider_info_list(self._providers)

    @staticmethod
    def _provider_info_list(providers: list[LoggingServiceProvider]) -> list[ServiceProviderInfo]:
        return [
            p.get_info()
            for p in sorted(providers, key=lambda p: p.priority)
        ]

    def emit(self, logger_name: str, level: str, event: str, **fields: Any) -> None:
        """Emit a structured log through the provider matching *logger_name*.

        Falls back to the ""default"" provider, then the first active
        provider if no name match is found.
        """
        provider = self._resolve_provider(logger_name)
        if provider is not None:
            provider.emit(logger_name, level, event, **fields)

    def bind_request_id(self, request_id: str) -> None:
        """Bind a request ID to the logging context (all active providers)."""
        for provider in self.active_providers:
            provider.bind_request_id(request_id)

    def clear_request_context(self) -> None:
        """Clear the logging request context (all active providers)."""
        for provider in self.active_providers:
            provider.clear_request_context()

    def log_to_all_active_loggers(
            self, message: str, level: int = logging.INFO,
    ) -> None:
        """Broadcast *message* through every active provider."""
        for provider in self.active_providers:
            provider.log_to_all_active_loggers(message, level)
