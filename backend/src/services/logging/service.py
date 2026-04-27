"""Logging service business logic: factory and orchestration."""

from __future__ import annotations

import logging
from itertools import groupby
from pathlib import Path
from typing import Any

from typing_extensions import override

from src.services.models import Service, ServiceProvider, ServiceProviderInfo
from src.services.provider_factory import ServiceProviderFactory
from src.services.logging.models import LogMessage, LoggingServiceProvider


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
        # If startup has completed, apply config changes to active providers immediately.
        # Fresh provider instances (from a runtime config update) have never had configure()
        # called, so we run it now to recreate their handlers with the new settings.
        # Already-configured providers (unchanged config) only need a level patch.
        if self._configure_kwargs is not None:
            for provider in self.get_active_providers():
                if not getattr(provider, "_is_configured", True):
                    provider.configure(**self._configure_kwargs)
                else:
                    provider.apply_levels()

    @override
    def providers_snapshot(self) -> list[ServiceProvider]:
        return list(self._providers)

    @override
    def get_all_providers(self) -> list[LoggingServiceProvider]:
        """Return all loaded providers, including disabled ones."""
        return list(self._providers)

    @override
    def get_active_providers(self) -> list[LoggingServiceProvider]:
        """Return providers that report themselves as active."""
        return [p for p in self._providers if p.is_active()]

    # ------------------------------------------------------------------
    # Startup configuration
    # ------------------------------------------------------------------

    def configure(self, *, output_dir: Path, profile: str) -> None:
        """Ask each active provider to apply its logging configuration."""
        self._configure_kwargs = {"output_dir": output_dir, "profile": profile}
        for provider in self.get_active_providers():
            provider.configure(output_dir=output_dir, profile=profile)

    def reset(self) -> list[str]:
        """Reset all active logging providers and return affected file paths/details."""
        reset_items: list[str] = []
        for provider in self.get_active_providers():
            reset_items.extend(provider.reset())
        return sorted(set(reset_items))

    # ------------------------------------------------------------------
    # Runtime helpers — name-based provider routing
    # ------------------------------------------------------------------

    def _provider_by_name(self, name: str) -> LoggingServiceProvider | None:
        """Look up an active provider whose config-file stem matches *name*."""
        for provider in self.get_active_providers():
            if provider.name == name:
                return provider
        return None

    def find_provider(self, name: str) -> LoggingServiceProvider | None:
        """Find a provider by name from all providers, including disabled ones."""
        for provider in self._providers:
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
        active = self.get_active_providers()
        return active[0] if active else None

    @staticmethod
    def _provider_info_list(providers: list[LoggingServiceProvider]) -> list[ServiceProviderInfo]:
        return [
            p.get_info()
            for p in sorted(providers, key=lambda p: p.priority)
        ]

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
        return self._provider_info_list(self.get_active_providers())

    def get_all_loggers(self) -> list[ServiceProviderInfo]:
        """Return info about every configured logging service provider, sorted by priority."""
        return self._provider_info_list(self._providers)

    def bind_request_id(self, request_id: str) -> None:
        """Bind a request ID to the logging context (all active providers)."""
        for provider in self.get_active_providers():
            provider.bind_request_id(request_id)

    def clear_request_context(self) -> None:
        """Clear the logging request context (all active providers)."""
        for provider in self.get_active_providers():
            provider.clear_request_context()

    def emit(self, msg: LogMessage) -> None:
        """Emit *msg* through "normal"-category providers using priority-ordered fallback.

        Providers are tried in ascending priority order (1 = highest).  When a
        provider logs the message successfully, every *other* provider that
        shares the same priority is also invoked, then the method returns.
        Lower-priority groups are only attempted when all providers in a
        higher-priority group fail.
        """
        normal = [
            p for p in self.get_active_providers()
            if p.provider_category == "normal"
        ]
        if not normal:
            return

        sorted_providers = sorted(normal, key=lambda p: p.priority)
        for _priority, group_iter in groupby(sorted_providers, key=lambda p: p.priority):
            group = list(group_iter)
            success_idx = self._try_emit_group(group, msg)
            if success_idx is not None:
                self._emit_remaining(group, msg, skip=success_idx)
                return

    @staticmethod
    def _try_emit_group(
            group: list[LoggingServiceProvider], msg: LogMessage,
    ) -> int | None:
        """Try each provider in *group*; return the index of the first success."""
        for idx, provider in enumerate(group):
            try:
                provider.log("default", msg)
                return idx
            except Exception:  # pylint: disable=broad-exception-caught
                continue
        return None

    @staticmethod
    def _emit_remaining(
            group: list[LoggingServiceProvider], msg: LogMessage, *, skip: int,
    ) -> None:
        """Log through every provider in *group* except the one at *skip*."""
        for idx, provider in enumerate(group):
            if idx == skip:
                continue
            try:
                provider.log("default", msg)
            except Exception:  # pylint: disable=broad-exception-caught
                continue

    def log(self, logger_name: str, msg: LogMessage) -> None:
        """Emit *msg* through the provider matching *logger_name*.

        Falls back to the "default" provider, then the first active provider.
        """
        provider = self._resolve_provider(logger_name)
        if provider is not None:
            provider.log(logger_name, msg)

    def log_to_all_active_loggers(self, msg: LogMessage) -> None:
        """Broadcast *msg* through every active provider."""
        for provider in self.get_active_providers():
            provider.log_to_all_active_loggers(msg)
