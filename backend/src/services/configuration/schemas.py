"""Schemas for internal runtime configuration endpoints."""

from typing import Any

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


class BatchToggleConfigurationsRequest(BaseModel):
    """Body model for enabling/disabling selected config files."""

    config_names: list[str]


class BatchToggleConfigurationsResponse(BaseModel):
    """Result model for batch enable/disable operations."""

    service_name: str
    enabled: bool
    results: list[ConfigurationMutationResponse]
