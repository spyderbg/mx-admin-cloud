"""Internal logging endpoints.

Exposes structured-log emission through the "LoggingService".
All endpoints require a JWT with the role "internal" or "admin".
"""

from __future__ import annotations

import logging as _logging
from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import get_registry, require_internal
from src.api.service_resolver import get_typed_service
from src.services import logging
from src.services.logging.models import LogMessage
from src.services.logging.schemas import (
    LogRequest,
    StatusResponse,
    ResetLogsResponse,
    LogApiRequest,
    LogServiceRequest,
    LogProviderRequest,
    LogPaymentRequest,
)
from src.services.logging.service import LoggingService
from src.services.schemas import ServiceProviderInfo
from src.services.service_registry import ServiceRegistry

ROUTER_PREFIX = "/internal/loggers"

LOGGERS_LIST = "/"
LOGGING_EMIT = "/emit"
LOGGING_LOG = "/{logger_name}/log"
LOGGING_API_REQUEST = "/logging/log/api-request"
LOGGING_SERVICE = "/logging/log/service"
LOGGING_PROVIDER = "/logging/log/provider"
LOGGING_PAYMENT = "/logging/log/payment"
LOGGING_RESET = "/reset"

router = APIRouter(prefix=ROUTER_PREFIX, tags=["internal-loggers"])


@router.get(
    LOGGERS_LIST,
    response_model=list[ServiceProviderInfo],
    summary="List logging service providers",
    description=(
            "Return the logging service providers that the emit endpoint routes through. "
            "By default only enabled providers are returned. "
            "Pass 'all=true' to include every configured provider regardless of state."
    ),
)
async def list_loggers(
        service: LoggingServiceDep,
        all_loggers: bool = False,
        _user: dict = Depends(require_internal),
) -> list[ServiceProviderInfo]:
    """Return enabled (or all) logging service providers."""
    return service.get_all_loggers() if all_loggers else service.get_active_loggers()


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
        body: LogRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Emit a structured log message through the logging service."""
    service.emit(_log_message_from_request(body))
    return {"status": "ok"}


@router.post(
    LOGGING_LOG,
    response_model=StatusResponse,
    summary="Emit a log message to a specific logger",
    description=(
            "Post a structured log message to the logger specified by path parameter. "
            "``level`` and ``event`` mirror structlog semantics."
    ),
)
async def emit_log_by_name(
        logger_name: str,
        body: LogRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Emit a log message to a specific logger through the logging service."""
    service.log(logger_name, _log_message_from_request(body))
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
        body: LogApiRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Emit an API request log message through the logging service."""
    service.log("requests", _log_message_from_request(body))
    return {"status": "ok"}


@router.post(
    LOGGING_SERVICE,
    response_model=StatusResponse,
    summary="Emit a service log message",
    description=(
            "Post a structured log message to the 'service' logger. "
            "``level`` and ``event`` mirror structlog semantics."
    ),
)
async def emit_service_log(
        body: LogServiceRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Emit a service log message through the logging service."""
    service.log("service", _log_message_from_request(body))
    return {"status": "ok"}


@router.post(
    LOGGING_PROVIDER,
    response_model=StatusResponse,
    summary="Log a TTS/STT provider request",
    description=(
            "Record a provider request (model, actor, latency, …) through the "
            "request-logger channel."
    ),
)
async def log_provider_request(
        body: LogProviderRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Log a provider request through the logging service."""
    service.log("requests", LogMessage(
        event="provider_request",
        level=_logging.INFO,
        fields=body.to_request_emit_fields(),
    ))
    return {"status": "ok"}


@router.post(
    LOGGING_PAYMENT,
    response_model=StatusResponse,
    summary="Emit a payment log message",
    description=(
            "Post a structured log message to the 'payment' logger. "
            "``level`` and ``event`` mirror structlog semantics."
    ),
)
async def emit_payment_log(
        body: LogRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> dict[str, str]:
    """Emit a payment log message through the logging service."""
    service.log("payment", _log_message_from_request(body))
    return {"status": "ok"}


@router.post(
    LOGGING_RESET,
    response_model=ResetLogsResponse,
    summary="Reset active logger providers",
    description=(
            "Trigger reset on all active logging providers. "
            "For file-backed providers this truncates active log files."
    ),
)
async def reset_loggers(
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> ResetLogsResponse:
    """Reset active logging providers and return affected files."""
    return ResetLogsResponse(status="ok", reset_files=service.reset())


################################################################################

def _log_message_from_request(body: LogRequest) -> LogMessage:
    return LogMessage(
        event=body.event,
        level=_logging.getLevelName(body.level.value.upper()),
        fields=body.fields,
    )


def get_logging_service(
        registry: ServiceRegistry = Depends(get_registry),
) -> LoggingService:
    return get_typed_service(
        registry=registry,
        service_name=logging.SERVICE_NAME,
        expected_type=LoggingService,
    )


LoggingServiceDep = Annotated[LoggingService, Depends(get_logging_service)]
