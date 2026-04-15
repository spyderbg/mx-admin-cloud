"""Pydantic request/response schemas for internal logging endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


################################################################################
# Common models

class ServiceProviderInfo(BaseModel):
    """Metadata for one configured translator provider (GET /internal/translators)."""

    name: str
    service: str
    provider: str
    enabled: bool
    priority: int
    config_path: str


################################################################################
# Error models

class ErrorMessage(BaseModel):
    """Error message returned by the API."""

    message: str = Field(..., description="Human-readable error message")
    error: Any | None = None
