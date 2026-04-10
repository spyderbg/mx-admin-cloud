"""Internal logging endpoints.

Exposes structured-log emission through the ``LoggingService``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.dependencies import require_internal
from src.api.internal.schemas.logging import (
    LogEmitRequest,
    LogProviderRequest,
    LogTranslatorRequest,
)
from src.services.logging.service import LoggingService

router = APIRouter(prefix="/internal", tags=["internal-logging"])


def _get_logging_service(request: Request) -> LoggingService:
    """Resolve the: class:`LoggingService` from the service registry."""
    from src.services.service_registry import ServiceRegistry  # pylint: disable=import-outside-toplevel

    registry: ServiceRegistry = request.app.state.registry
    service = registry.get_service("logging")
    if not isinstance(service, LoggingService):
        raise HTTPException(
            status_code=500,
            detail="Logging service not available in the registry.",
        )
    return service


@router.post(
    "/logging/emit",
    summary="Emit a structured log message",
    description=(
            "Post a structured log message to the named logger. "
            "The ``logger_name`` selects the target channel; ``level`` and "
            "``event`` mirror structlog semantics."
    ),
)
async def emit_log(
        body: LogEmitRequest,
        request: Request,
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Emit a structured log message through the logging service."""
    service = _get_logging_service(request)
    service.emit(body.logger_name, body.level, body.event, **body.fields)
    return {"status": "ok"}


@router.post(
    "/logging/provider-request",
    summary="Log a TTS/STT provider request",
    description=(
            "Record a provider request (model, actor, latency, …) through the "
            "request-logger channel."
    ),
)
async def log_provider_request(
        body: LogProviderRequest,
        request: Request,
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Log a provider request through the logging service."""
    service = _get_logging_service(request)
    service.log_provider_request(
        provider=body.provider,
        model=body.tts_model,
        actor=body.actor,
        voice_id=body.voice_id,
        language=body.language,
        input_data=body.input_data,
        response_duration=body.response_duration,
    )
    return {"status": "ok"}


@router.post(
    "/logging/translator-request",
    summary="Log a translator request",
    description=(
            "Record a translator request through the request-logger channel."
    ),
)
async def log_translator_request(
        body: LogTranslatorRequest,
        request: Request,
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Log a translator request through the logging service."""
    service = _get_logging_service(request)
    service.log_translator_request(
        translator=body.translator,
        model=body.tts_model,
        actor=body.actor,
        voice_id=body.voice_id,
        language=body.language,
        input_data=body.input_data,
        response_duration=body.response_duration,
    )
    return {"status": "ok"}
