"""Schemas for internal runtime configuration endpoints."""

from typing import Any
from enum import Enum

from pydantic import BaseModel, Field


class ApplyConfigurationRequest(BaseModel):
    """Body model for direct JSON payload submission."""

    payload: dict[str, Any]


class ApplyConfigurationResponse(BaseModel):
    """Result model for runtime config apply endpoints."""

    config_name: str
    accepted: bool
    error: str | None = None


class ConfigurationFileSummaryResponse(BaseModel):
    """One configuration file entry returned by list operations."""

    config_name: str
    filename: str
    provider: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    valid_json: bool
    error: str | None = None
    version: str | None = None


class ConfigurationServicesResponse(BaseModel):
    """Names of services that accept configuration files."""

    services: list[str]


class ConfigurationProvidersResponse(BaseModel):
    """Provider types available to one configurable service."""

    service_name: str
    providers: list[str]


class ProviderSelectionStrategy(str, Enum):
    """Planned provider dispatch policy for a service."""

    ALL_PROVIDERS = "ALL_PROVIDERS"
    FIRST_SUCCESS = "FIRST_SUCCESS"


class ProviderStrategyRequest(BaseModel):
    """Selection strategy to persist for a service."""

    strategy: ProviderSelectionStrategy


class ProviderStrategyResponse(BaseModel):
    """Effective strategy and available choices for a service."""

    service_name: str
    strategy: ProviderSelectionStrategy | None
    supported_strategies: list[ProviderSelectionStrategy]
    default_strategy: ProviderSelectionStrategy | None


class ConfigurationFileResponse(BaseModel):
    """Full JSON payload of one configuration file."""

    config_name: str
    filename: str
    payload: dict[str, Any]


class ConfigurationMutationResponse(BaseModel):
    """Result model for one add/remove/patch mutation."""

    config_name: str
    changed: bool
    error: str | None = None


class AddConfigurationRequest(BaseModel):
    """Body model for creating a new configuration file."""

    config_name: str = Field(min_length=1)
    payload: dict[str, Any]


class PatchConfigurationRequest(BaseModel):
    """Body model for merge-patching an existing configuration file."""

    patch: dict[str, Any]


class ReplaceConfigurationRequest(BaseModel):
    """Body model for replacing the complete JSON payload of one file."""

    payload: dict[str, Any]


class BatchToggleConfigurationsRequest(BaseModel):
    """Body model for enabling/disabling selected config files."""

    config_names: list[str]


class BatchToggleConfigurationsResponse(BaseModel):
    """Result model for batch enable/disable operations."""

    service_name: str
    enabled: bool
    results: list[ConfigurationMutationResponse]
