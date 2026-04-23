"""HTTP client helpers for structured logging via internal endpoints.

Call: `configure` function once during application startup (after the internal
token is created) so that: `emit` function and the typed request helpers can post
to the logging service.

When the client is not yet configured, every function is a silent no-op so that
early startup code never crashes.
"""

from __future__ import annotations

import logging as _stdlib_logging
from typing import Any

from src.services.logging.routes import LOGGING_API_REQUEST, LOGGING_EMIT, ROUTER_PREFIX
from src.services.logging.schemas import (
    LogApiRequest,
    LogEmitRequest,
    LogLevel,
    LogProviderRequest,
)

_client: Any | None = None  # pylint: disable=invalid-name # httpx.AsyncClient when configured


def _internal_endpoint(endpoint: str) -> str:
    return f"{ROUTER_PREFIX}{endpoint}"


def configure(base_url: str, internal_token: str) -> None:
    """Create the persistent "httpx.AsyncClient" for logging calls."""
    global _client  # pylint: disable=global-statement
    try:
        import httpx  # pylint: disable=import-outside-toplevel
        _client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {internal_token}"},
            timeout=5.0,
        )
    except ImportError:
        _stdlib_logging.debug("httpx not available — logging HTTP client disabled")


async def shutdown() -> None:
    """Close the persistent client (call during application shutdown)."""
    global _client  # pylint: disable=global-statement
    if _client is not None:
        await _client.aclose()
        _client = None


async def emit(request: LogEmitRequest) -> None:
    """Post a structured log message to "/internal/loggers/logging/emit"."""
    if _client is None:
        return
    try:
        await _client.post(
            _internal_endpoint(LOGGING_EMIT),
            json=request.model_dump(mode="json"),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug("logging HTTP emit failed", exc_info=True)


async def log_api_request(request: LogApiRequest) -> None:
    if _client is None:
        return
    try:
        await _client.post(
            _internal_endpoint(LOGGING_API_REQUEST),
            json=request.model_dump(mode="json"),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug("logging HTTP api-request failed", exc_info=True)


async def log_provider_request(
        request: LogProviderRequest,
) -> None:
    """Post a provider request log via "POST /internal/loggers/logging/emit"."""
    fields = request.to_request_emit_fields()
    await emit(
        LogEmitRequest(
            logger_name="requests",
            level=LogLevel.INFO,
            event="provider_request",
            fields=fields,
        ),
    )
