"""Pydantic request/response schemas for internal logging endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.services.logging.models import LogLevel


class StatusResponse(BaseModel):
    """Generic acknowledgement returned by fire-and-forget logging endpoints."""
    status: str


class ResetLogsResponse(StatusResponse):
    """Acknowledgement payload for reset endpoint with affected files."""

    reset_files: list[str] = Field(default_factory=list)


class LogRequest(BaseModel):
    """Base body for "POST /internal/loggers/emit" and "POST /internal/loggers/log/api-request"."""
    level: LogLevel = Field(LogLevel.INFO, description="Log level, defaults to 'info'")
    event: str = Field(..., description="Log event")
    fields: dict[str, Any] = Field(default_factory=dict)


class LogNameRequest(LogRequest):
    """Body for "POST /internal/loggers/emit"."""

    logger_name: str | None = Field(None, description="Logger name; omit to use the default logger")


class LogApiRequest(LogRequest):
    """Body for "POST /internal/loggers/logging/log/api-request"."""
    api_path: str = Field(..., description="HTTP path")
    api_method: str = Field(..., description="HTTP method")
    api_status: int = Field(..., description="HTTP status code")
    api_duration: float = Field(..., description="Request duration in seconds")


class LogServiceRequest(LogRequest):
    """Body for "POST /internal/loggers/log/service"."""
    service: str = Field(..., description="Service name")


class LogProviderRequest(BaseModel):
    """Body for "POST /internal/loggers/log/provider".

    The trailing fields are optional extras used by image generation
    ("request_kind='image'"); omitting them keeps TTS/translation records
    byte-identical because "to_request_emit_fields()" excludes "None".
    """
    provider: str | None = None
    tts_model: str | None = Field(None, alias="model")
    actor: str | None = None
    voice_id: str | None = None
    language: str | None = None
    input_data: Any | None = Field(None, alias="input")
    response_duration: float | None = None
    request_kind: str | None = Field(None, description="Request family, e.g. 'tts' or 'image'")
    size: str | None = Field(None, description="Requested output size, e.g. '1024x1024'")
    quality: str | None = Field(None, description="Requested output quality tier")
    cost_estimate: float | None = Field(None, description="Estimated cost in USD")
    status: str | None = Field(None, description="'success' or 'error'")
    error: str | None = Field(None, description="Failure reason when status is 'error'")
    attempts: int | None = Field(None, description="Provider attempts made, retries included")

    model_config = {"populate_by_name": True}

    def to_request_emit_fields(self) -> dict[str, Any]:
        """Build "**fields" for "requests" provider structured emit ("input" -> "input_data")."""
        fields = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        if "input" in fields:
            fields["input_data"] = fields.pop("input")
        return fields


class LogPaymentRequest(LogRequest):
    """Body for "POST /internal/loggers/log/payment"."""
    payment_id: str = Field(..., description="Payment ID")
    payment_status: str = Field(..., description="Payment status")
    payment_amount: float = Field(..., description="Payment amount")
    payment_currency: str = Field(..., description="Payment currency")
    payment_provider: str = Field(..., description="Payment provider")
