"""Internal logging endpoints.

Exposes structured-log emission through the "LoggingService".
All endpoints require a JWT with role "internal" or "admin".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import get_registry, require_internal
from src.api.internal.schemas.logging import (
    LogRequest,
    LogEmitRequest,
    LogProviderRequest,
    LogTranslatorRequest,
    StatusResponse,
)
from src.api.service_resolver import get_typed_service
from src.services import logging
from src.services.logging.service import LoggingService
from src.services.models import ServiceProviderInfo
from src.services.service_registry import ServiceRegistry

LOGGING_LOGGERS = "/logging/loggers"
LOGGING_EMIT = "/logging/emit"
LOGGING_API_REQUEST = "/logging/api-request"
LOGGING_PROVIDER_REQUEST = "/logging/provider-request"
LOGGING_TRANSLATOR_REQUEST = "/logging/translator-request"

router = APIRouter(prefix="/internal", tags=["internal-logging"])


@router.get(
    LOGGING_LOGGERS,
    response_model=list[ServiceProviderInfo],
    summary="List logging service providers",
    description=(
        "Return the logging service providers that the emit endpoint routes through. "
        "By default only enabled providers are returned. "
        "Pass 'all=true' to include every configured provider regardless of state."
    ),
)
async def list_loggers(
        all: bool = False,
        registry: ServiceRegistry = Depends(get_registry),
        _user: dict = Depends(require_internal),
) -> list[dict]:
    """Return enabled (or all) logging service providers."""
    service = get_typed_service(
        registry=registry,
        service_name=logging.SERVICE_NAME,
        expected_type=LoggingService,
    )
    return service.get_all_loggers() if all else service.get_active_loggers()


@router.post(
    LOGGING_EMIT,
    response_model=StatusResponse,
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
    logger_name = body.logger_name or "default"
    service.emit(logger_name, body.level, body.event, **body.fields)
    return {"status": "ok"}


@router.post(
    LOGGING_API_REQUEST,
    response_model=StatusResponse,
    summary="Emit an API request log message",
    description=(
            "Post a structured log message to the 'requests' logger. "
            "``level`` and ``event`` mirror structlog semantics."
    ),
)
async def emit_api_request_log(
        body: LogRequest,
        registry: ServiceRegistry = Depends(get_registry),
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Emit an API request log message through the logging service."""
    service = get_typed_service(
        registry=registry,
        service_name=logging.SERVICE_NAME,
        expected_type=LoggingService,
    )
    service.emit("requests", body.level, body.event, **body.fields)
    return {"status": "ok"}


@router.post(
    LOGGING_PROVIDER_REQUEST,
    response_model=StatusResponse,
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
    service.emit(
        "requests",
        "info",
        "provider_request",
        **body.to_request_emit_fields(),
    )
    return {"status": "ok"}


@router.post(
    LOGGING_TRANSLATOR_REQUEST,
    response_model=StatusResponse,
    summary="Log a translator request",
    description=(
            "Record a translator request through the request-logger channel."
    ),
)
async def log_text_translation_request(
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
    service.emit(
        "requests",
        "info",
        "translator_request",
        **body.to_request_emit_fields(),
    )
    return {"status": "ok"}
