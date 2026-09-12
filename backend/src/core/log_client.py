"""HTTP client helpers for structured logging via internal endpoints.

Call: `configure` function once during application startup (after the internal
token is created) so that: `emit` function and the typed request helpers can post
to the logging service.

When the client is not yet configured, every function is a silent no-op so that
early startup code never crashes.
"""

from __future__ import annotations

import asyncio
import logging as _stdlib_logging
from typing import Any, Coroutine

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
from src.services.logging.models import LogLevel
from src.services.logging.schemas import (
    LogApiRequest,
    LogRequest,
    LogServiceRequest,
    LogProviderRequest,
)

_client: httpx.AsyncClient | None = None  # pylint: disable=invalid-name
_loop: asyncio.AbstractEventLoop | None = None  # pylint: disable=invalid-name

# Strong references to in-flight fire-and-forget posts, so the event loop does
# not garbage-collect a task before it runs.
_pending: set[asyncio.Task] = set()


def _internal_endpoint(endpoint: str) -> str:
    return f"{ROUTER_PREFIX}{endpoint}"


def configure(base_url: str, internal_token: str) -> None:
    """Create the persistent "httpx.AsyncClient" for logging calls."""
    global _client, _loop  # pylint: disable=global-statement
    _client = httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {internal_token}"},
        timeout=5.0,
    )
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        # Configured outside a running loop (scripts, tests): "emit_event_nowait"
        # then has no loop to hand work to and degrades to a no-op.
        _loop = None


async def shutdown() -> None:
    """Close the persistent client (call during application shutdown)."""
    global _client, _loop  # pylint: disable=global-statement
    if _client is not None:
        await _client.aclose()
        _client = None
    _loop = None


################################################################################

def build_record(
        service: str,
        event: str,
        level: LogLevel,
        fields: dict[str, Any],
) -> LogRequest:
    """Build a log body tagged with the service that produced it.

    The logging service collects records from hosts that run none of the
    emitting code, so the source service has to travel with the record.
    """
    return LogRequest(event=event, level=level, fields={"service": service, **fields})


async def emit_event(
        service: str,
        event: str,
        level: LogLevel = LogLevel.INFO,
        **fields: Any,
) -> None:
    """Emit one structured record; never let a logging failure reach the caller."""
    try:
        await emit(build_record(service, event, level, fields))
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug("logging emit_event failed", exc_info=True)


async def log_event(
        service: str,
        logger_name: str,
        event: str,
        level: LogLevel = LogLevel.INFO,
        **fields: Any,
) -> None:
    """Emit one record to a named logger; never let a logging failure reach the caller."""
    try:
        await log(build_record(service, event, level, fields), logger_name)
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug("logging log_event to '%s' failed", logger_name, exc_info=True)


def emit_event_nowait(
        service: str,
        event: str,
        level: LogLevel = LogLevel.INFO,
        **fields: Any,
) -> None:
    """Fire-and-forget "emit_event" for synchronous callers.

    Service methods that FastAPI runs in its threadpool cannot await, but the
    logging service may sit on another host, so the record still has to go out
    over HTTP. The post is handed to the application event loop and never
    awaited: a dropped record must not block or fail its caller.
    """
    _schedule(emit_event(service, event, level, **fields))


def _schedule(coro: Coroutine[Any, Any, None]) -> None:
    """Run *coro* on the application event loop, from that loop or any thread."""
    if _client is None:
        coro.close()
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is not None:
        task = running.create_task(coro)
        _pending.add(task)
        task.add_done_callback(_pending.discard)
        return

    if _loop is None or _loop.is_closed():
        coro.close()
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, _loop)
    except RuntimeError:
        coro.close()
        _stdlib_logging.debug("logging scheduler unavailable", exc_info=True)


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
