"""Configuration service models and provider contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.services.models import ServiceProvider


class ConfigurationError(Exception):
    """Base exception for configuration service errors."""


class UnknownConfigurationServiceError(ConfigurationError):
    """Raised when service name is not registered."""


class InvalidConfigurationNameError(ConfigurationError):
    """Raised when config name is unsafe or malformed."""


class ConfigurationConflictError(ConfigurationError):
    """Raised when adding a config that already exists."""


class ConfigurationNotFoundError(ConfigurationError):
    """Raised when a requested config file does not exist."""


class ConfigurationValidationError(ConfigurationError):
    """Raised when payload validation fails."""


class ConfigurationProviderUnavailableError(ConfigurationError):
    """Raised when no provider is loaded for configuration operations."""


@dataclass(frozen=True)
class ConfigurationSummary:
    """Lightweight metadata returned by list operations."""

    config_name: str
    filename: str
    provider: str | None
    enabled: bool | None
    priority: int | None
    valid_json: bool
    error: str | None = None


@dataclass(frozen=True)
class ConfigurationMutationResult:
    """Result payload for a single configuration mutation."""

    config_name: str
    changed: bool
    error: str | None = None


class ConfigurationProvider(ServiceProvider, ABC):
    """Provider contract for CRUD and batch config operations."""

    @abstractmethod
    def list_configurations(self, service_name: str) -> list[ConfigurationSummary]:
        """Return configuration file summaries for one service."""

    @abstractmethod
    def add_configuration(
            self,
            service_name: str,
            config_name: str,
            payload: dict[str, Any],
    ) -> ConfigurationMutationResult:
        """Create a new configuration JSON file."""

    @abstractmethod
    def remove_configuration(self, service_name: str, config_name: str) -> ConfigurationMutationResult:
        """Delete one configuration JSON file."""

    @abstractmethod
    def patch_configuration(
            self,
            service_name: str,
            config_name: str,
            patch: dict[str, Any],
    ) -> ConfigurationMutationResult:
        """Merge-patch one existing configuration file."""

    @abstractmethod
    def set_configurations_enabled(
            self,
            service_name: str,
            config_names: list[str],
            enabled: bool,
    ) -> list[ConfigurationMutationResult]:
        """Set `enabled` state on selected configuration files."""

    @abstractmethod
    def set_all_configurations_enabled(
            self,
            service_name: str,
            enabled: bool,
    ) -> list[ConfigurationMutationResult]:
        """Set `enabled` state for all configuration files for one service."""
