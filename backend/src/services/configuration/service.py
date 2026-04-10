"""Configuration service business logic: factory and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from typing_extensions import override

from src.services.configuration.models import (
    ConfigurationMutationResult,
    ConfigurationProvider,
    ConfigurationProviderUnavailableError,
    ConfigurationSummary,
)
from src.services.models import Service, ServiceProvider
from src.services.provider_factory import ServiceProviderFactory


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

    @property
    def all_providers(self) -> list[ConfigurationProvider]:
        """Return all loaded configuration providers."""
        return list(self._providers)

    @property
    def active_providers(self) -> list[ConfigurationProvider]:
        """Return configuration providers that are enabled."""
        return [provider for provider in self._providers if provider.is_active()]

    def list_configurations(self, service_name: str) -> list[ConfigurationSummary]:
        """Return configuration summaries for one registered service."""
        return self._select_provider().list_configurations(service_name)

    def add_configuration(
            self,
            service_name: str,
            config_name: str,
            payload: dict[str, Any],
    ) -> ConfigurationMutationResult:
        """Create a configuration JSON file."""
        return self._select_provider().add_configuration(service_name, config_name, payload)

    def remove_configuration(self, service_name: str, config_name: str) -> ConfigurationMutationResult:
        """Delete a configuration JSON file."""
        return self._select_provider().remove_configuration(service_name, config_name)

    def patch_configuration(
            self,
            service_name: str,
            config_name: str,
            patch: dict[str, Any],
    ) -> ConfigurationMutationResult:
        """Merge patch payload into an existing configuration JSON file."""
        return self._select_provider().patch_configuration(service_name, config_name, patch)

    def enable_configurations(
            self,
            service_name: str,
            config_names: list[str],
    ) -> list[ConfigurationMutationResult]:
        """Enable selected configurations for one service."""
        return self._select_provider().set_configurations_enabled(service_name, config_names, enabled=True)

    def disable_configurations(
            self,
            service_name: str,
            config_names: list[str],
    ) -> list[ConfigurationMutationResult]:
        """Disable selected configurations for one service."""
        return self._select_provider().set_configurations_enabled(service_name, config_names, enabled=False)

    def enable_all_configurations(self, service_name: str) -> list[ConfigurationMutationResult]:
        """Enable all configurations for one service."""
        return self._select_provider().set_all_configurations_enabled(service_name, enabled=True)

    def disable_all_configurations(self, service_name: str) -> list[ConfigurationMutationResult]:
        """Disable all configurations for one service."""
        return self._select_provider().set_all_configurations_enabled(service_name, enabled=False)

    def _select_provider(self) -> ConfigurationProvider:
        provider = self._provider_by_name("file_system")
        if provider is None:
            provider = self._providers[0] if self._providers else None
        if provider is None:
            raise ConfigurationProviderUnavailableError("No configuration provider is available.")
        return provider

    def _provider_by_name(self, provider_name: str) -> ConfigurationProvider | None:
        normalized = provider_name.strip().lower()
        for provider in self.active_providers:
            if provider.provider.strip().lower() == normalized:
                return provider
        for provider in self.all_providers:
            if provider.provider.strip().lower() == normalized:
                return provider
        return None
