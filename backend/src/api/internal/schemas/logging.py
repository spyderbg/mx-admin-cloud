"""Pydantic request/response schemas for internal logging endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StatusResponse(BaseModel):
    """Generic acknowledgement returned by fire-and-forget logging endpoints."""

    status: str


class LogEmitRequest(BaseModel):
    """Body for ``POST /internal/logging/emit``."""

    logger_name: str
    level: str = "info"
    event: str
    fields: dict[str, Any] = Field(default_factory=dict)


class LogProviderRequest(BaseModel):
    """Body for ``POST /internal/logging/provider-request``."""

    provider: str | None = None
    tts_model: str | None = Field(None, alias="model")
    actor: str | None = None
    voice_id: str | None = None
    language: str | None = None
    input_data: Any | None = Field(None, alias="input")
    response_duration: float | None = None

    model_config = {"populate_by_name": True}


class LogTranslatorRequest(BaseModel):
    """Body for ``POST /internal/logging/translator-request``."""

    translator: str | None = None
    tts_model: str | None = Field(None, alias="model")
    actor: str | None = None
    voice_id: str | None = None
    language: str | None = None
    input_data: Any | None = Field(None, alias="input")
    response_duration: float | None = None

    model_config = {"populate_by_name": True}
