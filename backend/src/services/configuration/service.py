"""Configuration service business logic: factory and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TypeVar

from typing_extensions import override

from src.core.log_client import emit_event_nowait
from src.services.configuration.models import (
    ConfigurationFileResult,
    ConfigurationMutationResult,
    ConfigurationProvider,
    ConfigurationProviderUnavailableError,
    ConfigurationSummary,
)
from src.services.logging.models import LogLevel
from src.services.models import Service, ServiceProvider
from src.services.provider_factory import ServiceProviderFactory

ResultT = TypeVar("ResultT")

# Identifies this service in records collected by the logging service, which
# runs in its own process and possibly on its own host.
LOG_SOURCE = "configuration"


class ConfigurationProviderFactory(ServiceProviderFactory[ConfigurationProvider]):
    """Configuration-specific provider registry."""

    unsupported_label: str = "configuration provider"


class ConfigurationService(Service):
    """Orchestrator for configuration provider operations."""

    def __init__(
            self,
            service_name: str,
            package_name: str,
            service_dir: Path,
            base_dir: Path,
    ) -> None:
        super().__init__(service_name, package_name, service_dir, base_dir)
        self._providers: list[ConfigurationProvider] = []

    @override
    def replace_providers(self, providers: list[ServiceProvider]) -> None:
        """Replace loaded configuration providers with *providers*."""
        self._providers = [provider for provider in providers if isinstance(provider, ConfigurationProvider)]

    @override
    def providers_snapshot(self) -> list[ServiceProvider]:
        """Return loaded configuration providers, including disabled ones."""
        return list(self._providers)

    @override
    def get_all_providers(self) -> list[ConfigurationProvider]:
        """Return all loaded configuration providers, including disabled ones."""
        return list(self._providers)

    @override
    def get_active_providers(self) -> list[ConfigurationProvider]:
        """Return configuration providers that are enabled."""
        return [provider for provider in self._providers if provider.is_active()]

    ################################################################################

    def list_configurations(self, service_name: str) -> list[ConfigurationSummary]:
        """Return configuration summaries for one registered service."""
        return self._run_operation(
            "list", service_name, None,
            lambda: self._select_provider().list_configurations(service_name),
        )

    def get_configuration(self, service_name: str, config_name: str) -> ConfigurationFileResult:
        """Return the full payload of one configuration file."""
        return self._run_operation(
            "read", service_name, config_name,
            lambda: self._select_provider().get_configuration(service_name, config_name),
        )

    def add_configuration(
            self,
            service_name: str,
            config_name: str,
            payload: dict[str, Any],
    ) -> ConfigurationMutationResult:
        """Create a configuration JSON file."""
        return self._run_operation(
            "create", service_name, config_name,
            lambda: self._select_provider().add_configuration(service_name, config_name, payload),
        )

    def remove_configuration(self, service_name: str, config_name: str) -> ConfigurationMutationResult:
        """Delete a configuration JSON file."""
        return self._run_operation(
            "delete", service_name, config_name,
            lambda: self._select_provider().remove_configuration(service_name, config_name),
        )

    def patch_configuration(
            self,
            service_name: str,
            config_name: str,
            patch: dict[str, Any],
    ) -> ConfigurationMutationResult:
        """Merge patch payload into an existing configuration JSON file."""
        return self._run_operation(
            "patch", service_name, config_name,
            lambda: self._select_provider().patch_configuration(service_name, config_name, patch),
        )

    def enable_configurations(
            self,
            service_name: str,
            config_names: list[str],
    ) -> list[ConfigurationMutationResult]:
        """Enable selected configurations for one service."""
        return self._run_operation(
            "enable", service_name, None,
            lambda: self._select_provider().set_configurations_enabled(service_name, config_names, enabled=True),
            config_names=config_names,
        )

    def disable_configurations(
            self,
            service_name: str,
            config_names: list[str],
    ) -> list[ConfigurationMutationResult]:
        """Disable selected configurations for one service."""
        return self._run_operation(
            "disable", service_name, None,
            lambda: self._select_provider().set_configurations_enabled(service_name, config_names, enabled=False),
            config_names=config_names,
        )

    def enable_all_configurations(self, service_name: str) -> list[ConfigurationMutationResult]:
        """Enable all configurations for one service."""
        return self._run_operation(
            "enable_all", service_name, None,
            lambda: self._select_provider().set_all_configurations_enabled(service_name, enabled=True),
        )

    def disable_all_configurations(self, service_name: str) -> list[ConfigurationMutationResult]:
        """Disable all configurations for one service."""
        return self._run_operation(
            "disable_all", service_name, None,
            lambda: self._select_provider().set_all_configurations_enabled(service_name, enabled=False),
        )

    def _run_operation(
            self,
            operation: str,
            service_name: str,
            config_name: str | None,
            task: Callable[[], ResultT],
            **fields: Any,
    ) -> ResultT:
        """Run a provider task and report its outcome through internal logging."""
        log_fields: dict[str, Any] = {
            "operation": operation,
            "service_name": service_name,
        }
        if config_name is not None:
            log_fields["config_name"] = config_name
        log_fields.update(fields)
        self._log("configuration_file_operation", LogLevel.INFO, **log_fields)
        try:
            result = task()
        except Exception as exc:
            self._log(
                "configuration_operation_failed",
                LogLevel.ERROR,
                **log_fields,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        self._log_result_errors(result, **log_fields)
        return result

    def _log_result_errors(self, result: Any, **fields: Any) -> None:
        """Log failures returned as result data instead of raised exceptions."""
        items = result if isinstance(result, list) else [result]
        for item in items:
            error = getattr(item, "error", None)
            if error:
                error_fields = dict(fields)
                error_fields["config_name"] = getattr(
                    item, "config_name", fields.get("config_name")
                )
                self._log(
                    "configuration_operation_failed",
                    LogLevel.ERROR,
                    **error_fields,
                    error=str(error),
                )

    @staticmethod
    def _log(event: str, level: LogLevel, **fields: Any) -> None:
        """Report operation telemetry over the internal logging API.

        These methods run synchronously in FastAPI's threadpool and cannot
        await, so the post is scheduled fire-and-forget: a slow or unreachable
        logging host must never stall a configuration operation.
        """
        emit_event_nowait(LOG_SOURCE, event, level, **fields)

    def _select_provider(self) -> ConfigurationProvider:
        provider = self.get_provider("file_system")
        if provider is None:
            provider = self._providers[0] if self._providers else None
        if provider is None:
            raise ConfigurationProviderUnavailableError("No configuration provider is available.")
        return provider
