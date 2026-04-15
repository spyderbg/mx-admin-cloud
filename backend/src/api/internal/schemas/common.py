"""Pydantic request/response schemas for internal logging endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


################################################################################
# Common models

class ServiceProviderInfo(BaseModel):
    """Metadata for one configured translator provider (GET /internal/translators)."""

    name: str = Field(..., description="Human-readable name")
    service: str = Field(..., description="Service name")
    provider: str = Field(..., description="Provider name")
    enabled: bool = Field(..., description="Whether the provider is enabled")
    priority: int = Field(..., description="Priority of the provider")
    config_path: str = Field(..., description="Path to the provider's config file")


################################################################################
# Error models

class ErrorMessage(BaseModel):
    """Error message returned by the API"""

    message: str = Field(..., description="Human-readable error message")
    error: Any | None = None


class ErrorHttpCode(BaseModel):
    """Error message returned by the API, with HTTP status code."""

    http_code: int = Field(..., description="HTTP status code")
    message: str = Field(..., description="Human-readable error message")
    error: Any | None = None
