"""Pytest fixtures shared by the content API integration tests (test_content_service_integration_*.py)."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

from pathlib import Path
import pytest

from httpx import ASGITransport, AsyncClient

from src.core.config import settings
from src.services.content.__tests__.content_http_helpers import StubContentProvider, make_app


@pytest.fixture
def tmp_resolved(tmp_path: Path) -> Path:
    """tmp_path with symlinks resolved, so paths compare equal to what the app reports."""
    return tmp_path.resolve()


@pytest.fixture
async def client(tmp_resolved: Path) -> AsyncClient:
    """An authenticated client for an app backed by a fresh StubContentProvider."""
    provider = StubContentProvider(tmp_resolved)
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def client_no_auth(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    """A client for an app with no auth override, for asserting 401 responses."""
    # The dev default AUTH_DISABLED=True bypasses verify_token; the 401 paths
    # under test only exist with auth enabled.
    monkeypatch.setattr(settings, "AUTH_DISABLED", False)
    app = make_app(auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
