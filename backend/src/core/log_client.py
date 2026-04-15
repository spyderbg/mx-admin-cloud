"""HTTP client helpers for structured logging via internal endpoints.

Call: func:`configure 'once during application startup (after the
internal token is created) so that: func:`emit`, :func:`log_provider_request`,
and: func:`log_translator_request` can post to the logging service.

When the client is not yet configured, every function is a silent no-op
so that early startup code never crashes.
"""

from __future__ import annotations

import logging as _stdlib_logging
from typing import Any

from src.api.internal.schemas.logging import (
    LogRequest,
    LogEmitRequest,
    LogProviderRequest,
    LogTranslatorRequest,
)

_client: Any | None = None  # pylint: disable=invalid-name # httpx.AsyncClient when configured


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
    """Post a structured log message to "/internal/logging/emit"."""
    if _client is None:
        return
    try:
        await _client.post(
            "/internal/logging/emit",
            json=request.model_dump(mode="json"),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug("logging HTTP emit failed", exc_info=True)


async def log_api_request(request: LogRequest) -> None:
    if _client is None:
        return
    try:
        await _client.post(
            "/internal/logging/api-request",
            json=request.model_dump(mode="json"),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug("logging HTTP api-request failed", exc_info=True)


async def log_provider_request(
        request: LogProviderRequest,
) -> None:
    """Post a provider-request log to "/internal/logging/provider-request"."""
    if _client is None:
        return
    try:
        await _client.post(
            "/internal/logging/provider-request",
            json=request.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug(
            "logging HTTP provider-request failed", exc_info=True,
        )


async def log_text_translation_request(
        request: LogTranslatorRequest,
) -> None:
    """Post a translator-request log to "/internal/logging/translator-request"."""
    if _client is None:
        return
    try:
        await _client.post(
            "/internal/logging/translator-request",
            json=request.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug(
            "logging HTTP translator-request failed", exc_info=True,
        )
