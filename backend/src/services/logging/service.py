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
        """Return a logger for *name* via the active provider (or stdlib)."""
        provider = self._active()
        if provider is not None:
            return provider.get_logger(name)
        return logging.getLogger(name)

    def get_active_loggers(self) -> list[dict]:
        """Return info about enabled logging service providers, sorted by priority."""
        return self._provider_info_list(self.active_providers)

    def get_all_loggers(self) -> list[dict]:
        """Return info about every configured logging service provider, sorted by priority."""
        return self._provider_info_list(self._providers)

    @staticmethod
    def _provider_info_list(providers: list[LoggingServiceProvider]) -> list[dict]:
        return [
            {
                "name": p.name,
                "service": p.service,
                "provider": p.provider,
                "enabled": p.enabled,
                "priority": p.priority,
                "config_path": str(p.config_path),
            }
            for p in sorted(providers, key=lambda p: p.priority)
        ]

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
