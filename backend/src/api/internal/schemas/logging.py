"""Pydantic request/response schemas for internal logging endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StatusResponse(BaseModel):
    """Generic acknowledgement returned by fire-and-forget logging endpoints."""

    status: str


class LogRequest(BaseModel):
    """Body for ``POST /internal/logging/api-request``."""

    level: str = Field("info", description="Log level, defaults to 'info'")
    event: str = Field(..., description="Log event name")
    fields: dict[str, Any] = Field(default_factory=dict)


class LogEmitRequest(LogRequest):
    """Body for ``POST /internal/logging/emit``."""

    logger_name: str = Field(..., description="Logger name")


# TODO: not final
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


# TODO: not final
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
