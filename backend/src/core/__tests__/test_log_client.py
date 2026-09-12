"""Tests for the out-of-process logging client.

The logging service may run on another host, so every record leaves over HTTP.
These cover the fire-and-forget path synchronous service code depends on.
"""

# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from src.core import log_client
from src.services.logging.models import LogLevel


@pytest.fixture
def posts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Point the client at a transport that records requests instead of sending them."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(
        log_client,
        "_client",
        httpx.AsyncClient(
            base_url="http://logging-host:8000",
            transport=httpx.MockTransport(_capture),
        ),
    )
    return captured


@pytest.mark.asyncio
async def test_emit_event_tags_the_source_service(posts: list[Any]) -> None:
    await log_client.emit_event("configuration", "config_changed", LogLevel.INFO, key="value")

    path, body = posts[0]
    assert path == "/internal/loggers/emit"
    assert body["event"] == "config_changed"
    assert body["level"] == "info"
    assert body["fields"] == {"service": "configuration", "key": "value"}


@pytest.mark.asyncio
async def test_log_event_targets_a_named_logger(posts: list[Any]) -> None:
    await log_client.log_event("text_to_image", "trace", "endpoint_hit", LogLevel.TRACE)

    path, body = posts[0]
    assert path == "/internal/loggers/trace/log"
    assert body["level"] == "trace"
    assert body["fields"] == {"service": "text_to_image"}


@pytest.mark.asyncio
async def test_emit_event_swallows_an_unreachable_collector(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    monkeypatch.setattr(
        log_client,
        "_client",
        httpx.AsyncClient(base_url="http://down:8000", transport=httpx.MockTransport(_refuse)),
    )

    await log_client.emit_event("configuration", "config_changed")


@pytest.mark.asyncio
async def test_emit_event_nowait_posts_from_a_worker_thread(
        posts: list[Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sync service code runs in FastAPI's threadpool, off the event loop."""
    monkeypatch.setattr(log_client, "_loop", asyncio.get_running_loop())

    await asyncio.to_thread(
        log_client.emit_event_nowait, "configuration", "config_changed", LogLevel.INFO
    )
    await asyncio.sleep(0.05)  # let the loop drain the scheduled post

    assert [path for path, _ in posts] == ["/internal/loggers/emit"]
    assert posts[0][1]["fields"] == {"service": "configuration"}


@pytest.mark.asyncio
async def test_emit_event_nowait_posts_from_the_event_loop(
        posts: list[Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(log_client, "_loop", asyncio.get_running_loop())

    log_client.emit_event_nowait("configuration", "config_changed")
    await asyncio.sleep(0.05)

    assert len(posts) == 1


def test_emit_event_nowait_is_a_noop_when_unconfigured(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup code and CLI scripts log before "configure" runs."""
    monkeypatch.setattr(log_client, "_client", None)
    monkeypatch.setattr(log_client, "_loop", None)

    log_client.emit_event_nowait("configuration", "config_changed")


@pytest.mark.asyncio
async def test_emit_event_nowait_drops_records_when_no_loop_is_known(
        posts: list[Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker thread with no captured loop has nowhere to hand the post."""
    monkeypatch.setattr(log_client, "_loop", None)

    await asyncio.to_thread(log_client.emit_event_nowait, "configuration", "config_changed")
    await asyncio.sleep(0.05)

    assert posts == []
