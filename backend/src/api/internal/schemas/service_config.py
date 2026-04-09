"""Schemas for internal runtime service-config endpoints."""

from typing import Any

from pydantic import BaseModel


class ApplyConfigRequest(BaseModel):
    """Body model for direct JSON payload submission."""

    payload: dict[str, Any]


class ApplyConfigResponse(BaseModel):
    """Result model for runtime config apply endpoints."""

    config_name: str
    accepted: bool
    error: str | None = None
