"""HTTP tests for the /content static file mount, which serves the content directory only
while a content provider is active.
"""

# pylint: disable=missing-function-docstring

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from src.services.content.__tests__.content_http_helpers import PNG_BYTES, StubContentProvider
from src.services.content.routes import ContentStaticFiles
from src.services.service_registry import ServiceRegistry


def _make_app(tmp_path: Path, provider: StubContentProvider) -> FastAPI:
    """An app mounting a content directory holding foods/foods.png, backed by *provider*."""
    content_dir = tmp_path / "content"
    (content_dir / "foods").mkdir(parents=True)
    (content_dir / "foods" / "foods.png").write_bytes(PNG_BYTES)
    registry = ServiceRegistry.build_runtime(tmp_path)
    registry.replace_service_providers("content", [provider])
    app = FastAPI()
    app.state.registry = registry
    app.mount("/content", ContentStaticFiles(directory=str(content_dir)), name="content")
    return app


async def _get(app: FastAPI, path: str) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_serves_content_files_while_a_provider_is_active(tmp_path: Path) -> None:
    app = _make_app(tmp_path, StubContentProvider(tmp_path))
    response = await _get(app, "/content/foods/foods.png")
    assert response.status_code == 200
    assert response.content == PNG_BYTES


@pytest.mark.asyncio
async def test_missing_content_file_is_404_while_a_provider_is_active(tmp_path: Path) -> None:
    app = _make_app(tmp_path, StubContentProvider(tmp_path))
    response = await _get(app, "/content/foods/missing.png")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_refuses_content_files_while_no_provider_is_active(tmp_path: Path) -> None:
    provider = StubContentProvider(tmp_path)
    provider.enabled = False
    app = _make_app(tmp_path, provider)
    response = await _get(app, "/content/foods/foods.png")
    assert response.status_code == 503
    assert response.json()["detail"] == "No active content provider is configured."


@pytest.mark.asyncio
async def test_follows_the_provider_being_enabled_at_runtime(tmp_path: Path) -> None:
    provider = StubContentProvider(tmp_path)
    provider.enabled = False
    app = _make_app(tmp_path, provider)
    assert (await _get(app, "/content/foods/foods.png")).status_code == 503

    provider.enabled = True

    assert (await _get(app, "/content/foods/foods.png")).status_code == 200
