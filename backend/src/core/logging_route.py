"""Route class that traces requests and reports endpoint failures.

Attach it to any router so that every endpoint under it emits a trace record on
entry and an error record when the handler raises::

    router = APIRouter(
        prefix="/internal/configurations",
        route_class=LoggingAPIRoute.for_source(LOG_SOURCE),
    )

Records go out over the internal logging API through ``src.core.log_client``,
never to an in-process ``LoggingService``: the service that owns the endpoint
and the service that collects its records need not share a host.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine

from fastapi import HTTPException, Request, Response  # pylint: disable=import-error
from fastapi.routing import APIRoute

from src.core.log_client import emit_event, log_event
from src.services.logging import LogLevel

# Endpoint tracing goes to the dedicated "trace" logger config.
TRACE_LOGGER = "trace"

# Used when a router attaches the class without naming its service.
DEFAULT_LOG_SOURCE = "api"


class LoggingAPIRoute(APIRoute):
    """Trace requests and report endpoint failures over the logging API.

    Subclass through "for_source" so each router stamps its own service name and
    event prefix onto the records it emits.
    """

    log_source: str = DEFAULT_LOG_SOURCE
    event_prefix: str = DEFAULT_LOG_SOURCE

    @classmethod
    def for_source(cls, log_source: str, *, event_prefix: str | None = None) -> type[LoggingAPIRoute]:
        """Build a route class whose records carry this service's identity.

        "event_prefix" names the events when the wire name differs from the log
        source, as it does for the image generators ("text_to_image" records
        under "image_generator_endpoint_*").
        """
        prefix = event_prefix or log_source
        return type(
            f"{prefix.title().replace('_', '')}LoggingAPIRoute",
            (cls,),
            {"log_source": log_source, "event_prefix": prefix},
        )

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def logged_handler(request: Request) -> Response:
            fields = {"method": request.method, "path": request.url.path}
            await self._trace_request(fields)
            try:
                return await original_handler(request)
            except Exception as exc:
                await self._report_failure(exc, fields)
                raise

        return logged_handler

    @classmethod
    async def _trace_request(cls, fields: dict[str, Any]) -> None:
        """Record that the endpoint was entered."""
        await log_event(
            cls.log_source,
            TRACE_LOGGER,
            f"{cls.event_prefix}_endpoint_requested",
            LogLevel.TRACE,
            **fields,
        )

    @classmethod
    async def _report_failure(cls, exc: Exception, fields: dict[str, Any]) -> None:
        """Record a raised failure; rejections the caller caused stay below ERROR."""
        is_http = isinstance(exc, HTTPException)
        status_code = exc.status_code if is_http else 500
        await emit_event(
            cls.log_source,
            f"{cls.event_prefix}_endpoint_failed",
            LogLevel.WARNING if status_code < 500 else LogLevel.ERROR,
            **fields,
            status_code=status_code,
            error=str(exc.detail) if is_http else str(exc),
            error_type=type(exc).__name__,
        )
