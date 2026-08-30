"""HTTP client helpers for structured logging via internal endpoints.

Call: `configure` function once during application startup (after the internal
token is created) so that: `emit` function and the typed request helpers can post
to the logging service.

When the client is not yet configured, every function is a silent no-op so that
early startup code never crashes.
"""

from __future__ import annotations

import logging as _stdlib_logging

import httpx

from src.services.logging.routes import (
    LOGGING_API_REQUEST,
    LOGGING_EMIT,
    LOGGING_LOG,
    LOGGING_PAYMENT,
    LOGGING_PROVIDER,
    LOGGING_SERVICE,
    ROUTER_PREFIX,
)
from src.services.logging.schemas import (
    LogApiRequest,
    LogRequest,
    LogServiceRequest,
    LogProviderRequest,
)

_client: httpx.AsyncClient | None = None  # pylint: disable=invalid-name


def _internal_endpoint(endpoint: str) -> str:
    return f"{ROUTER_PREFIX}{endpoint}"


def configure(base_url: str, internal_token: str) -> None:
    """Create the persistent "httpx.AsyncClient" for logging calls."""
    global _client  # pylint: disable=global-statement
    _client = httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {internal_token}"},
        timeout=5.0,
    )


async def shutdown() -> None:
    """Close the persistent client (call during application shutdown)."""
    global _client  # pylint: disable=global-statement
    if _client is not None:
        await _client.aclose()
        _client = None


################################################################################

async def emit(request: LogRequest) -> None:
    """Post a structured log message to "/internal/loggers/emit"."""
    if _client is None:
        return
    try:
        await _client.post(
            _internal_endpoint(LOGGING_EMIT),
            json=request.model_dump(mode="json"),
        )
    except httpx.RequestError:
        _stdlib_logging.debug("logging HTTP emit failed", exc_info=True)


async def log(request: LogRequest, logger_name: str | None = None) -> None:
    """Post a structured log message to a specific logger or emit by default.

    Args:
        request: The log request to send.
        logger_name: Optional logger name. If not provided, calls emit() instead.
    """
    if logger_name is None:
        await emit(request)
        return

    if _client is None:
        return
    try:
        await _client.post(
            _internal_endpoint(LOGGING_LOG.replace("{logger_name}", logger_name)),
            json=request.model_dump(mode="json"),
        )
    except httpx.RequestError:
        _stdlib_logging.debug("logging HTTP log to '%s' failed", logger_name, exc_info=True)


async def log_api_request(request: LogApiRequest) -> None:
    """
    Logs an API request by sending the request data to a configured logging client.

    Attempts to log the provided API request data by sending it to the logging
    endpoint using an asynchronous HTTP client. If the client is not configured,
    the function will exit without performing any action. In the event of an
    HTTP request error, the failure is logged without raising further exceptions.

    Parameters:
        request (LogApiRequest): The API request to be logged. It must be an
            instance of LogApiRequest, containing the data to be sent to the
            logging endpoint.
    """
    if _client is None:
        return
    try:
        await _client.post(
            _internal_endpoint(LOGGING_API_REQUEST),
            json=request.model_dump(mode="json"),
        )
    except httpx.RequestError:
        _stdlib_logging.debug("logging HTTP api-request failed", exc_info=True)


async def log_service_request(
        request: LogServiceRequest,
) -> None:
    """Post a structured log message to "/internal/loggers/logging/log/service"."""
    if _client is None:
        return
    try:
        await _client.post(
            _internal_endpoint(LOGGING_SERVICE),
            json=request.model_dump(mode="json"),
        )
    except httpx.RequestError:
        _stdlib_logging.debug("logging HTTP service-request failed", exc_info=True)


async def log_provider_request(
        request: LogProviderRequest,
) -> None:
    """Post a structured log message to "/internal/loggers/logging/log/provider"."""
    if _client is None:
        return
    try:
        await _client.post(
            _internal_endpoint(LOGGING_PROVIDER),
            json=request.model_dump(mode="json"),
        )
    except httpx.RequestError:
        _stdlib_logging.debug("logging HTTP provider-request failed", exc_info=True)


async def log_payment_request(
        request: LogRequest,
) -> None:
    """Post a structured log message to "/internal/loggers/logging/log/payment"."""
    if _client is None:
        return
    try:
        await _client.post(
            _internal_endpoint(LOGGING_PAYMENT),
            json=request.model_dump(mode="json"),
        )
    except httpx.RequestError:
        _stdlib_logging.debug("logging HTTP payment-request failed", exc_info=True)
