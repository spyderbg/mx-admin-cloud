"""Pydantic request/response schemas for internal logging endpoints."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LogLevel(str, Enum):
    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    FATAL = "fatal"


class StatusResponse(BaseModel):
    """Generic acknowledgement returned by fire-and-forget logging endpoints."""

    status: str


class LogRequest(BaseModel):
    """Base body for "POST /internal/logging/emit" and "POST /internal/logging/api-request"."""

    level: LogLevel = Field(LogLevel.INFO, description="Log level, defaults to 'info'")
    event: str = Field(..., description="Log event name")
    fields: dict[str, Any] = Field(default_factory=dict)


class LogEmitRequest(LogRequest):
    """Body for "POST /internal/logging/emit"."""

    logger_name: str | None = Field(None, description="Logger name; omit to use the default logger")


class LogApiRequest(LogRequest):
    """Body for "POST /internal/logging/api-request"."""

    api_path: str = Field(..., description="HTTP path")
    api_method: str = Field(..., description="HTTP method")
    api_status: int = Field(..., description="HTTP status code")
    api_duration: float = Field(..., description="Request duration in seconds")


# TODO: not final
class LogProviderRequest(BaseModel):
    """Body for "POST /internal/logging/provider-request"."""

    provider: str | None = None
    tts_model: str | None = Field(None, alias="model")
    actor: str | None = None
    voice_id: str | None = None
    language: str | None = None
    input_data: Any | None = Field(None, alias="input")
    response_duration: float | None = None

    model_config = {"populate_by_name": True}

    def to_request_emit_fields(self) -> dict[str, Any]:
        """Build "**fields" for "requests" provider structured emit ("input" -> "input_data")."""
        fields = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        if "input" in fields:
            fields["input_data"] = fields.pop("input")
        return fields
