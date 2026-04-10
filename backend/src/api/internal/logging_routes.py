"""Internal logging endpoints.

Exposes structured-log emission through the ``LoggingService``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import get_registry, require_internal
from src.api.internal.schemas.logging import (
    LogEmitRequest,
    LogProviderRequest,
    LogTranslatorRequest,
)
from src.api.service_resolver import get_typed_service
from src.services import logging
from src.services.logging.service import LoggingService
from src.services.service_registry import ServiceRegistry

router = APIRouter(prefix="/internal", tags=["internal-logging"])


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
        registry: ServiceRegistry = Depends(get_registry),
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Emit a structured log message through the logging service."""
    service = get_typed_service(
        registry=registry,
        service_name=logging.SERVICE_NAME,
        expected_type=LoggingService,
    )
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
        registry: ServiceRegistry = Depends(get_registry),
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Log a provider request through the logging service."""
    service = get_typed_service(
        registry=registry,
        service_name=logging.SERVICE_NAME,
        expected_type=LoggingService,
    )
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
        registry: ServiceRegistry = Depends(get_registry),
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Log a translator request through the logging service."""
    service = get_typed_service(
        registry=registry,
        service_name=logging.SERVICE_NAME,
        expected_type=LoggingService,
    )
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
