"""Internal logging endpoints.

Exposes structured-log emission through the "LoggingService".
All endpoints require a JWT with the role "internal" or "admin".
"""

from __future__ import annotations

import logging as _logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

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
)
from src.services.logging.service import LoggingService
from src.services.schemas import ServiceProviderInfo
from src.services.service_registry import ServiceRegistry

ROUTER_PREFIX = "/internal/loggers"

LOGGERS_LIST = "/"
LOGGING_RESET = "/reset"
LOGGING_EMIT = "/emit"
LOGGING_LOG = "/{logger_name}/log"
LOGGING_API_REQUEST = "/logging/log/api-request"
LOGGING_SERVICE = "/logging/log/service"
LOGGING_PROVIDER = "/logging/log/provider"
LOGGING_PAYMENT = "/logging/log/payment"

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
) -> StatusResponse:
    """Emit a structured log message through the logging service."""
    service.emit(log_message_from_request(body))
    return StatusResponse(status="ok")


@router.post(
    LOGGING_LOG,
    response_model=StatusResponse,
    summary="Emit a log message to a specific logger",
    description=(
            "Post a structured log message to the logger specified by path parameter. "
            "``level`` and ``event`` mirror structlog semantics."
    ),
)
async def log_by_logger_name(
        logger_name: str,
        body: LogRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> StatusResponse:
    """Emit a log message to a specific logger through the logging service."""
    validate_logger_exists(service, logger_name)
    service.log(logger_name, log_message_from_request(body))
    return StatusResponse(status="ok")


@router.post(
    LOGGING_API_REQUEST,
    response_model=StatusResponse,
    summary="Emit an API request log message",
    description=(
            "Post a structured log message to the 'requests' logger. "
            "``level`` and ``event`` mirror structlog semantics."
    ),
)
async def log_api_request(
        body: LogApiRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> StatusResponse:
    """Emit an API request log message through the logging service."""
    logger_name = "requests"
    validate_logger_exists(service, logger_name)
    service.log(logger_name, log_message_from_request(body))
    return StatusResponse(status="ok")


@router.post(
    LOGGING_SERVICE,
    response_model=StatusResponse,
    summary="Emit a service log message",
    description=(
            "Post a structured log message to the 'service' logger. "
            "``level`` and ``event`` mirror structlog semantics."
    ),
)
async def log_service_request(
        body: LogServiceRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> StatusResponse:
    """Emit a service log message through the logging service."""
    logger_name = "service"
    validate_logger_exists(service, logger_name)
    service.log(logger_name, log_message_from_request(body))
    return StatusResponse(status="ok")


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
) -> StatusResponse:
    """Log a provider request through the logging service."""
    logger_name = "requests"
    validate_logger_exists(service, logger_name)
    log_message = LogMessage(
        event="provider_request",
        level=_logging.INFO,
        fields=body.to_request_emit_fields(),
    )
    service.log(logger_name, log_message)
    return StatusResponse(status="ok")


@router.post(
    LOGGING_PAYMENT,
    response_model=StatusResponse,
    summary="Emit a payment log message",
    description=(
            "Post a structured log message to the 'payment' logger. "
            "``level`` and ``event`` mirror structlog semantics."
    ),
)
async def log_payment_request(
        body: LogRequest,
        service: LoggingServiceDep,
        _user: dict = Depends(require_internal),
) -> StatusResponse:
    """Emit a payment log message through the logging service."""
    logger_name = "payment"
    validate_logger_exists(service, logger_name)
    service.log(logger_name, log_message_from_request(body))
    return StatusResponse(status="ok")


################################################################################

def validate_logger_exists(service: LoggingService, logger_name: str) -> None:
    """Validate that a logger exists and is active.

    Args:
        service: The logging service instance.
        logger_name: The name of the logger to validate.

    Raises:
        HTTPException: If the logger is not found or is disabled.
    """
    provider = service.find_provider(logger_name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Logger '{logger_name}' not found.")
    if not provider.is_active():
        raise HTTPException(status_code=404, detail=f"Logger '{logger_name}' is disabled.")


def log_message_from_request(body: LogRequest) -> LogMessage:
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
