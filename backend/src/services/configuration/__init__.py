"""Configuration subsystem: service class, factory, and provider implementations."""
# pylint: disable=invalid-name

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import src.services.configuration.providers  # noqa: F401 — triggers provider auto-import

from src.services.acceptance import ServiceAcceptCriteria
from src.services.configuration.models import (
    ConfigurationConflictError,
    ConfigurationError,
    ConfigurationMutationResult,
    ConfigurationNotFoundError,
    ConfigurationProvider,
    ConfigurationProviderUnavailableError,
    ConfigurationSummary,
    ConfigurationValidationError,
    InvalidConfigurationNameError,
    UnknownConfigurationServiceError,
)
from src.services.configuration.providers.file_system import FileSystemConfigurationProvider
from src.services.configuration.service import ConfigurationProviderFactory, ConfigurationService
from src.services.models import ServiceProvider

if TYPE_CHECKING:
    from src.services.service_registry import ServiceRegistryBuilder

__all__ = [
    "ConfigurationService",
    "ConfigurationProvider",
    "ConfigurationProviderFactory",
    "ConfigurationError",
    "UnknownConfigurationServiceError",
    "InvalidConfigurationNameError",
    "ConfigurationConflictError",
    "ConfigurationNotFoundError",
    "ConfigurationValidationError",
    "ConfigurationProviderUnavailableError",
    "ConfigurationSummary",
    "ConfigurationMutationResult",
]


def _validate_configuration_provider(service_provider: ServiceProvider) -> None:
    provider_name = service_provider.provider
    if not ConfigurationProviderFactory.supports(provider_name):
        raise ValueError(
            f"Unsupported configuration provider: '{provider_name}' "
            f"(file='{service_provider.config_path}')"
        )


def _build_configuration_provider(
        provider: ServiceProvider,
        base_dir: Path,
) -> ConfigurationProvider:
    return ConfigurationProviderFactory.create(provider, base_dir)


def _build_runtime_configuration_providers(
        config_root: Path,
        registry_builder: ServiceRegistryBuilder,
) -> list[ConfigurationProvider]:
    # The file-system provider validates and reloads through the registry
    # builder, which no config file can supply.
    return [FileSystemConfigurationProvider.build_runtime(config_root, registry_builder)]


SERVICE_CLASS = ConfigurationService
SERVICE_NAME = "configuration"
SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(
    service_name=SERVICE_NAME,
    config_subdir="configuration",
    validate_provider=_validate_configuration_provider,
    build_provider=_build_configuration_provider,
    build_runtime_providers=_build_runtime_configuration_providers,
)
