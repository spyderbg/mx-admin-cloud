"""Tests for content API access by role: everyone signed in reads, only admins change content."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_current_user
from src.services.content.__tests__.content_http_helpers import PNG_BYTES, StubContentProvider, make_app

_CATEGORIES = "/api/v1/content/categories"
_ITEM = "/api/v1/content/categories/foods/items/I_Foods"


def _client_as(role: str, tmp_resolved: Path) -> AsyncClient:
    app = make_app(StubContentProvider(tmp_resolved), tmp_resolved)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"a-{role}", "role": role}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_a_viewer_can_read_content(tmp_resolved: Path) -> None:
    async with _client_as("viewer", tmp_resolved) as c:
        assert (await c.get(_CATEGORIES)).status_code == 200
        assert (await c.get(f"{_ITEM}/voices")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url,kwargs", [
    ("POST", _CATEGORIES, {"json": {"name": "toys"}}),
    ("PUT", _ITEM, {"json": {"item_name": {"en": "apple"}}}),
    ("DELETE", _ITEM, {}),
    ("POST", f"{_ITEM}/images", {"files": {"image": ("a.png", PNG_BYTES, "image/png")}}),
    ("POST", f"{_ITEM}/voices/generate", {"json": {"language": "en", "text": "apple", "configuration_name": "x"}}),
])
async def test_a_viewer_cannot_change_content(tmp_resolved: Path, method: str, url: str, kwargs: dict) -> None:
    async with _client_as("viewer", tmp_resolved) as c:
        response = await c.request(method, url, **kwargs)
    assert response.status_code == 403
    assert response.json()["detail"] == "Viewers have read-only access."


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "internal"])
async def test_admins_and_internal_services_can_change_content(tmp_resolved: Path, role: str) -> None:
    async with _client_as(role, tmp_resolved) as c:
        response = await c.delete(_ITEM)
    assert response.status_code == 204
