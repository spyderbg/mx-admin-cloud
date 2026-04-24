"""Shared Pydantic response schemas used across service routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ServiceProviderInfo(BaseModel):
    """Metadata for one configured service provider."""

    name: str = Field(..., description="Human-readable name")
    service: str = Field(..., description="Service name")
    provider: str = Field(..., description="Provider name")
    provider_category: str = Field("normal", description="Category of the provider")
    enabled: bool = Field(..., description="Whether the provider is enabled")
    priority: int = Field(..., description="Priority of the provider")


class ErrorMessage(BaseModel):
    """Error message returned by the API"""

    message: str = Field(..., description="Human-readable error message")
    error: Any | None = None


class ErrorHttpCode(BaseModel):
    """Error message returned by the API, with HTTP status code."""

    http_code: int = Field(..., description="HTTP status code")
    message: str = Field(..., description="Human-readable error message")
    error: Any | None = None
