"""HTTP client helpers for structured logging via internal endpoints.

Call :func:`configure` once during application startup (after the
internal token is created) so that :func:`emit`, :func:`log_provider_request`,
and :func:`log_translator_request` can post to the logging service.

When the client is not yet configured, every function is a silent no-op
so that early startup code never crashes.
"""

from __future__ import annotations

import logging as _stdlib_logging
from typing import Any

_client: Any | None = None  # httpx.AsyncClient when configured


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


async def emit(
    logger_name: str,
    level: str,
    event: str,
    **fields: Any,
) -> None:
    """Post a structured log message to "/internal/logging/emit"."""
    if _client is None:
        return
    try:
        await _client.post(
            "/internal/logging/emit",
            json={
                "logger_name": logger_name,
                "level": level,
                "event": event,
                "fields": fields,
            },
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug("logging HTTP emit failed", exc_info=True)


async def log_provider_request(
    *,
    provider: str | None = None,
    model: str | None = None,
    actor: str | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    input: Any | None = None,  # pylint: disable=redefined-builtin
    response_duration: float | None = None,
) -> None:
    """Post a provider-request log to "/internal/logging/provider-request"."""
    if _client is None:
        return
    payload: dict[str, Any] = {}
    if provider is not None:
        payload["provider"] = provider
    if model is not None:
        payload["model"] = model
    if actor is not None:
        payload["actor"] = actor
    if voice_id is not None:
        payload["voice_id"] = voice_id
    if language is not None:
        payload["language"] = language
    if input is not None:
        payload["input"] = input
    if response_duration is not None:
        payload["response_duration"] = response_duration
    try:
        await _client.post("/internal/logging/provider-request", json=payload)
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug(
            "logging HTTP provider-request failed", exc_info=True,
        )


async def log_translator_request(
    *,
    translator: str | None = None,
    model: str | None = None,
    actor: str | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    input: Any | None = None,  # pylint: disable=redefined-builtin
    response_duration: float | None = None,
) -> None:
    """Post a translator-request log to ``/internal/logging/translator-request``."""
    if _client is None:
        return
    payload: dict[str, Any] = {}
    if translator is not None:
        payload["translator"] = translator
    if model is not None:
        payload["model"] = model
    if actor is not None:
        payload["actor"] = actor
    if voice_id is not None:
        payload["voice_id"] = voice_id
    if language is not None:
        payload["language"] = language
    if input is not None:
        payload["input"] = input
    if response_duration is not None:
        payload["response_duration"] = response_duration
    try:
        await _client.post(
            "/internal/logging/translator-request", json=payload,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _stdlib_logging.debug(
            "logging HTTP translator-request failed", exc_info=True,
        )
