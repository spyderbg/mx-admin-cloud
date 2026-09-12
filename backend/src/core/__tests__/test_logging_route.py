"""Tests for the shared request-tracing route class.

Every router that attaches it emits the same two records — one on entry, one on a
raised failure — under its own service name and event prefix.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core import logging_route
from src.core.logging_route import LoggingAPIRoute
from src.services.logging.models import LogLevel


@pytest.fixture
def records(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """Capture what the route hands to the logging client instead of posting it."""
    captured: dict[str, list[dict[str, Any]]] = {"traced": [], "failed": []}

    async def fake_log_event(source, logger, event, level, **fields):
        captured["traced"].append(
            {"source": source, "logger": logger, "event": event, "level": level, **fields}
        )

    async def fake_emit_event(source, event, level, **fields):
        captured["failed"].append(
            {"source": source, "event": event, "level": level, **fields}
        )

    monkeypatch.setattr(logging_route, "log_event", fake_log_event)
    monkeypatch.setattr(logging_route, "emit_event", fake_emit_event)
    return captured


@pytest.fixture
def client() -> TestClient:
    """A router carrying the route class, with one endpoint per outcome."""
    router = APIRouter(
        route_class=LoggingAPIRoute.for_source("demo_service", event_prefix="widget")
    )

    @router.get("/ok")
    def ok() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/rejected")
    def rejected() -> None:
        raise HTTPException(status_code=404, detail="gone")

    @router.get("/broken")
    def broken() -> None:
        raise ValueError("kaboom")

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_for_source_keeps_the_service_name_and_event_prefix_apart() -> None:
    route_class = LoggingAPIRoute.for_source("text_to_image", event_prefix="image_generator")

    assert issubclass(route_class, LoggingAPIRoute)
    assert route_class.log_source == "text_to_image"
    assert route_class.event_prefix == "image_generator"


def test_event_prefix_defaults_to_the_log_source() -> None:
    assert LoggingAPIRoute.for_source("configuration").event_prefix == "configuration"


def test_successful_request_is_traced(client: TestClient, records: dict[str, list]) -> None:
    assert client.get("/ok").status_code == 200

    assert records["failed"] == []
    assert records["traced"] == [
        {
            "source": "demo_service",
            "logger": logging_route.TRACE_LOGGER,
            "event": "widget_endpoint_requested",
            "level": LogLevel.TRACE,
            "method": "GET",
            "path": "/ok",
        }
    ]


def test_caller_rejection_is_reported_below_error(
    client: TestClient, records: dict[str, list]
) -> None:
    assert client.get("/rejected").status_code == 404

    (failure,) = records["failed"]
    assert failure["event"] == "widget_endpoint_failed"
    assert failure["level"] == LogLevel.WARNING
    assert failure["status_code"] == 404
    assert failure["error"] == "gone"
    assert failure["error_type"] == "HTTPException"


def test_handler_crash_is_reported_as_an_error(
    client: TestClient, records: dict[str, list]
) -> None:
    assert client.get("/broken").status_code == 500

    (failure,) = records["failed"]
    assert failure["level"] == LogLevel.ERROR
    assert failure["status_code"] == 500
    assert failure["error"] == "kaboom"
    assert failure["error_type"] == "ValueError"
    # The record is a side effect; the request still fails.
    assert records["traced"][0]["event"] == "widget_endpoint_requested"
