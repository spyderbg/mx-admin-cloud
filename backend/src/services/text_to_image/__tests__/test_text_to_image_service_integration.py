"""HTTP integration tests for text_to_image routes (ASGI + httpx)."""

# pylint: disable=duplicate-code,missing-function-docstring,redefined-outer-name,too-few-public-methods

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_registry, require_internal
from src.core.config import settings
from src.services import text_to_image as text_to_image_pkg
from src.services.text_to_image import routes as image_routes
from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImagePayload, ImageRequest, TextToImageProvider
from src.services.text_to_image.service import TextToImageService


class _FakeRegistry:
    def __init__(self, image_service: Any) -> None:
        self._image_service = image_service

    def get_service(self, name: str) -> Any:
        if name == text_to_image_pkg.SERVICE_NAME:
            return self._image_service
        raise KeyError(name)

    def get_typed_service(self, service_name: str, expected_type: type[Any]) -> Any:
        service = self.get_service(service_name)
        if not isinstance(service, expected_type):
            raise TypeError(
                f"Service registry mismatch for {service_name}: "
                f"expected {expected_type.__name__}, got {type(service).__name__}.",
            )
        return service


class _StubImageProvider(TextToImageProvider):
    """Deterministic async image provider for route tests."""

    def __init__(
            self,
            base: Path,
            *,
            priority: int,
            enabled: bool = True,
            fail: bool = False,
            mime_type: str = "image/png",
    ) -> None:
        super().__init__(
            name="stub",
            service_name="text_to_image",
            provider_name="stub",
            provider_category="",
            enabled=enabled,
            priority=priority,
            config={
                "generate": {
                    "model": "stub-model",
                    "size": "1024x1024",
                    "quality": "low",
                    "workflow": {"secret": "omitted from the list response"},
                },
                "traffic": {"max_concurrency": 1, "max_retries": 0},
                "pricing": {"stub-model": 0.01},
            },
            config_path=base / "stub.json",
            base_dir=base,
        )
        self._fail = fail
        self._mime_type = mime_type

    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        del request, client
        if self._fail:
            raise NonRetryableError("stub generation failed")
        return ImagePayload(data=b"FAKEIMAGE", mime_type=self._mime_type)


def _make_image_service(
        tmp_path: Path,
        providers: list[TextToImageProvider],
) -> TextToImageService:
    svc = TextToImageService(
        service_name=text_to_image_pkg.SERVICE_NAME,
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    svc.replace_providers(providers)
    return svc


def _make_app(registry: _FakeRegistry) -> FastAPI:
    app = FastAPI()
    app.include_router(image_routes.router)
    app.dependency_overrides[require_internal] = lambda: {"username": "itest", "role": "admin"}
    app.dependency_overrides[get_registry] = lambda: registry
    return app


class _NotAnImageService:
    """Placeholder to trigger registry type mismatch (500)."""


_GENERATE_BODY = {"prompt": "a friendly cat", "filename_text": "cat"}


@pytest.fixture
def tmp_path_resolved(tmp_path: Path) -> Path:
    return tmp_path.resolve()


@pytest.fixture(autouse=True)
def _isolate_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path / "output"))


@pytest.fixture
async def client_active(tmp_path_resolved: Path) -> AsyncClient:
    stub = _StubImageProvider(tmp_path_resolved, priority=1)
    svc = _make_image_service(tmp_path_resolved, [stub])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_two_providers(tmp_path_resolved: Path) -> AsyncClient:
    p_lo = _StubImageProvider(tmp_path_resolved, priority=50)
    p_hi = _StubImageProvider(tmp_path_resolved, priority=5)
    svc = _make_image_service(tmp_path_resolved, [p_lo, p_hi])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_no_active(tmp_path_resolved: Path) -> AsyncClient:
    disabled = _StubImageProvider(tmp_path_resolved, priority=1, enabled=False)
    svc = _make_image_service(tmp_path_resolved, [disabled])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_failing(tmp_path_resolved: Path) -> AsyncClient:
    failing = _StubImageProvider(tmp_path_resolved, priority=1, fail=True)
    svc = _make_image_service(tmp_path_resolved, [failing])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_bad_registry() -> AsyncClient:
    app = _make_app(_FakeRegistry(_NotAnImageService()))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_no_internal_auth(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    monkeypatch.setattr(settings, "AUTH_DISABLED", False)
    app = FastAPI()
    app.include_router(image_routes.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# GET /internal/image-generators/
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_image_generators_returns_all_providers(client_two_providers: AsyncClient) -> None:
    response = await client_two_providers.get("/internal/image-generators/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    for row in data:
        assert set(row.keys()) == {
            "name", "provider", "enabled", "priority", "config_path", "generate", "traffic", "pricing",
        }


@pytest.mark.asyncio
async def test_list_omits_workflow_and_exposes_limits(client_active: AsyncClient) -> None:
    response = await client_active.get("/internal/image-generators/")
    assert response.status_code == 200
    row = response.json()[0]
    assert row["enabled"] is True
    assert row["provider"] == "stub"
    assert "workflow" not in row["generate"]
    assert row["generate"]["model"] == "stub-model"
    assert row["traffic"]["max_concurrency"] == 1
    assert row["pricing"]["stub-model"] == 0.01


@pytest.mark.asyncio
async def test_list_includes_disabled(client_no_active: AsyncClient) -> None:
    response = await client_no_active.get("/internal/image-generators/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["enabled"] is False


@pytest.mark.asyncio
async def test_list_registry_mismatch_returns_500(client_bad_registry: AsyncClient) -> None:
    response = await client_bad_registry.get("/internal/image-generators/")
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_list_requires_auth(client_no_internal_auth: AsyncClient) -> None:
    response = await client_no_internal_auth.get("/internal/image-generators/")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /internal/image-generators/{provider}/generate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_single_success(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/image-generators/stub/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["provider"] == "stub"
    assert body["model"] == "stub-model"
    assert body["cost_estimate"] == 0.01
    assert body["attempts"] == 1
    assert body["error"] is None
    assert body["raw_path"].endswith("cat.png")
    assert body["request_id"].startswith("img-")


@pytest.mark.asyncio
async def test_generate_single_uses_explicit_request_id(client_active: AsyncClient) -> None:
    payload = {**_GENERATE_BODY, "request_id": "my-custom-id"}
    response = await client_active.post("/internal/image-generators/stub/generate", json=payload)
    assert response.status_code == 200
    assert response.json()["request_id"] == "my-custom-id"


@pytest.mark.asyncio
async def test_generate_single_provider_failure_is_in_band(client_failing: AsyncClient) -> None:
    response = await client_failing.post(
        "/internal/image-generators/stub/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "stub generation failed"
    assert body["cost_estimate"] == 0.0


@pytest.mark.asyncio
async def test_generate_single_empty_prompt_returns_400(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/image-generators/stub/generate",
        json={"prompt": "   "},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_generate_single_unknown_provider_returns_404(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/image-generators/nonexistent/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 404
    assert "nonexistent" in response.json()["detail"]


@pytest.mark.asyncio
async def test_generate_single_no_active_providers_returns_503(client_no_active: AsyncClient) -> None:
    response = await client_no_active.post(
        "/internal/image-generators/stub/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_generate_single_registry_mismatch_returns_500(client_bad_registry: AsyncClient) -> None:
    response = await client_bad_registry.post(
        "/internal/image-generators/stub/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_generate_single_requires_auth(client_no_internal_auth: AsyncClient) -> None:
    response = await client_no_internal_auth.post(
        "/internal/image-generators/stub/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /internal/image-generators/generate-image
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_image_success(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/image-generators/generate-image",
        json={"prompt": "a friendly cat"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"FAKEIMAGE"


@pytest.mark.asyncio
async def test_generate_image_reports_the_configuration_and_its_settings(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/image-generators/generate-image",
        json={"prompt": "a friendly cat"},
    )

    assert response.status_code == 200
    assert response.headers["X-Image-Provider"] == "stub"
    parameters = json.loads(base64.urlsafe_b64decode(response.headers["X-Image-Parameters"]))
    # The inline workflow stays out, as it does in the list response.
    assert parameters == {"model": "stub-model", "size": "1024x1024", "quality": "low", "output_format": "png"}


@pytest.mark.asyncio
async def test_generate_image_jpeg_content_type(tmp_path_resolved: Path) -> None:
    stub = _StubImageProvider(tmp_path_resolved, priority=1, mime_type="image/jpeg")
    svc = _make_image_service(tmp_path_resolved, [stub])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/image-generators/generate-image",
            json={"prompt": "a friendly cat"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_generate_image_empty_prompt_returns_400(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/image-generators/generate-image",
        json={"prompt": "   "},
    )
    assert response.status_code == 400
    assert "non-empty" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_image_no_active_providers_returns_503(client_no_active: AsyncClient) -> None:
    response = await client_no_active.post(
        "/internal/image-generators/generate-image",
        json={"prompt": "a friendly cat"},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_generate_image_provider_failure_returns_500(client_failing: AsyncClient) -> None:
    response = await client_failing.post(
        "/internal/image-generators/generate-image",
        json={"prompt": "a friendly cat"},
    )
    assert response.status_code == 500
    assert "stub generation failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_generate_image_requires_auth(client_no_internal_auth: AsyncClient) -> None:
    response = await client_no_internal_auth.post(
        "/internal/image-generators/generate-image",
        json={"prompt": "a friendly cat"},
    )
    assert response.status_code == 401
