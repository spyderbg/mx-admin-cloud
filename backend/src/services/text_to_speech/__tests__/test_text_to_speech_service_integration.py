"""HTTP integration tests for text_to_speech routes (ASGI + httpx)."""

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
from src.services import text_to_speech as text_to_speech_pkg
from src.services.text_to_speech import routes as tts_routes
from src.services.text_to_speech.models import GeneratedAudio, TTSProvider, TTSRequest
from src.services.text_to_speech.service import TextToSpeechService


class _FakeRegistry:
    def __init__(self, tts_service: Any) -> None:
        self._tts_service = tts_service

    def get_service(self, name: str) -> Any:
        if name == text_to_speech_pkg.SERVICE_NAME:
            return self._tts_service
        raise KeyError(name)

    def get_typed_service(self, service_name: str, expected_type: type[Any]) -> Any:
        service = self.get_service(service_name)
        if not isinstance(service, expected_type):
            raise TypeError(
                f"Service registry mismatch for {service_name}: "
                f"expected {expected_type.__name__}, got {type(service).__name__}.",
            )
        return service


class _StubTTSProvider(TTSProvider):
    """Deterministic async TTS provider for route tests."""

    def __init__(
            self,
            base: Path,
            *,
            priority: int,
            enabled: bool = True,
            fail: bool = False,
    ) -> None:
        super().__init__(
            name="stub",
            service_name="text_to_speech",
            provider_name="stub",
            provider_category="",
            enabled=enabled,
            priority=priority,
            config={
                "generate": {
                    "enabled": enabled,
                    "model": "stub-model",
                    "voice": "stub-voice",
                },
                "evaluate": {"enabled": False},
            },
            config_path=base / "stub.json",
            base_dir=base,
        )
        self._fail = fail

    def is_active(self) -> bool:
        return self.enabled

    async def generate(self, request: TTSRequest, client: Any) -> GeneratedAudio:
        raw = self.base_dir / f"{request.request_id}.mp3"
        if self._fail:
            return GeneratedAudio(
                provider=self.name,
                request_id=request.request_id,
                language_code=request.language_code,
                language_name=request.language_name,
                expected_text=request.expected_text,
                filename_text=request.filename_text,
                output_format=request.output_format,
                raw_path=raw,
                raw_extension="mp3",
                latency_seconds=0.0,
                success=False,
                error="stub generation failed",
            )
        raw.write_bytes(b"FAKEAUDIO")
        return GeneratedAudio(
            provider=self.name,
            request_id=request.request_id,
            language_code=request.language_code,
            language_name=request.language_name,
            expected_text=request.expected_text,
            filename_text=request.filename_text,
            output_format=request.output_format,
            raw_path=raw,
            raw_extension="mp3",
            latency_seconds=0.05,
            success=True,
        )


def _make_tts_service(
        tmp_path: Path,
        providers: list[TTSProvider],
) -> TextToSpeechService:
    svc = TextToSpeechService(
        service_name=text_to_speech_pkg.SERVICE_NAME,
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    svc.replace_providers(providers)
    return svc


def _make_app(registry: _FakeRegistry) -> FastAPI:
    app = FastAPI()
    app.include_router(tts_routes.router)
    app.dependency_overrides[require_internal] = lambda: {"username": "itest", "role": "admin"}
    app.dependency_overrides[get_registry] = lambda: registry
    return app


class _NotATTSService:
    """Placeholder to trigger registry type mismatch (500)."""


_GENERATE_BODY = {
    "text": "hello",
    "language_code": "en",
    "language_name": "English",
    "filename_text": "hello",
    "expected_text": "hello",
    "source_file": "test.txt",
    "source_type": "txt",
    "output_format": "mp3",
    "row_index": 0,
}


@pytest.fixture
def tmp_path_resolved(tmp_path: Path) -> Path:
    return tmp_path.resolve()


@pytest.fixture
async def client_two_providers(tmp_path_resolved: Path) -> AsyncClient:
    p_lo = _StubTTSProvider(tmp_path_resolved, priority=50)
    p_hi = _StubTTSProvider(tmp_path_resolved, priority=5)
    svc = _make_tts_service(tmp_path_resolved, [p_lo, p_hi])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_active(tmp_path_resolved: Path) -> AsyncClient:
    stub = _StubTTSProvider(tmp_path_resolved, priority=1)
    svc = _make_tts_service(tmp_path_resolved, [stub])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_no_active(tmp_path_resolved: Path) -> AsyncClient:
    disabled = _StubTTSProvider(tmp_path_resolved, priority=1, enabled=False)
    svc = _make_tts_service(tmp_path_resolved, [disabled])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_bad_registry() -> AsyncClient:
    app = _make_app(_FakeRegistry(_NotATTSService()))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_no_internal_auth(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    # The dev default AUTH_DISABLED=True bypasses verify_token; the 401 paths
    # under test only exist with auth enabled.
    monkeypatch.setattr(settings, "AUTH_DISABLED", False)
    app = FastAPI()
    app.include_router(tts_routes.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# GET /internal/generators/
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_generators_returns_all_providers(client_two_providers: AsyncClient) -> None:
    response = await client_two_providers.get("/internal/generators/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    for row in data:
        assert set(row.keys()) == {"name", "enabled", "config_path", "generate", "evaluate_enabled"}


@pytest.mark.asyncio
async def test_list_generators_enabled_field(client_active: AsyncClient) -> None:
    response = await client_active.get("/internal/generators/")
    assert response.status_code == 200
    row = response.json()[0]
    assert row["enabled"] is True
    assert row["evaluate_enabled"] is False


@pytest.mark.asyncio
async def test_list_generators_includes_disabled(client_no_active: AsyncClient) -> None:
    response = await client_no_active.get("/internal/generators/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["enabled"] is False


@pytest.mark.asyncio
async def test_list_generators_registry_mismatch_returns_500(client_bad_registry: AsyncClient) -> None:
    response = await client_bad_registry.get("/internal/generators/")
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_list_generators_requires_auth(client_no_internal_auth: AsyncClient) -> None:
    response = await client_no_internal_auth.get("/internal/generators/")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /internal/generators/{provider}/generate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_single_success(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/generators/stub/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["provider"] == "stub"
    assert body["language_code"] == "en"
    assert body["output_format"] == "mp3"
    assert "raw_path" in body
    assert body["error"] is None


@pytest.mark.asyncio
async def test_generate_single_uses_explicit_request_id(client_active: AsyncClient) -> None:
    payload = {**_GENERATE_BODY, "request_id": "my-custom-id"}
    response = await client_active.post("/internal/generators/stub/generate", json=payload)
    assert response.status_code == 200
    assert response.json()["request_id"] == "my-custom-id"


@pytest.mark.asyncio
async def test_generate_single_auto_request_id_without_column(client_active: AsyncClient) -> None:
    payload = {**_GENERATE_BODY, "row_index": 3, "column_index": None}
    response = await client_active.post("/internal/generators/stub/generate", json=payload)
    assert response.status_code == 200
    assert response.json()["request_id"] == "api-3-0-en"


@pytest.mark.asyncio
async def test_generate_single_auto_request_id_with_column(client_active: AsyncClient) -> None:
    payload = {**_GENERATE_BODY, "row_index": 2, "column_index": 5}
    response = await client_active.post("/internal/generators/stub/generate", json=payload)
    assert response.status_code == 200
    assert response.json()["request_id"] == "api-2-5-en"


@pytest.mark.asyncio
async def test_generate_single_unknown_provider_returns_404(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/generators/nonexistent/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 404
    assert "nonexistent" in response.json()["detail"]


@pytest.mark.asyncio
async def test_generate_single_no_active_providers_returns_503(client_no_active: AsyncClient) -> None:
    response = await client_no_active.post(
        "/internal/generators/stub/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_generate_single_registry_mismatch_returns_500(client_bad_registry: AsyncClient) -> None:
    response = await client_bad_registry.post(
        "/internal/generators/stub/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_generate_single_requires_auth(client_no_internal_auth: AsyncClient) -> None:
    response = await client_no_internal_auth.post(
        "/internal/generators/stub/generate",
        json=_GENERATE_BODY,
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /internal/generators/generate-word
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_word_success(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/generators/generate-word",
        json={"word": "apple", "lang": "en"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-tts-provider"] == "stub"
    parameters = json.loads(base64.urlsafe_b64decode(response.headers["x-tts-parameters"]))
    assert parameters == {
        "enabled": True,
        "model": "stub-model",
        "output_format": "mp3",
        "voice": "stub-voice",
    }
    assert response.content == b"FAKEAUDIO"


@pytest.mark.asyncio
async def test_generate_word_empty_word_returns_400(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/generators/generate-word",
        json={"word": "   ", "lang": "en"},
    )
    assert response.status_code == 400
    assert "non-empty" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_word_no_active_providers_returns_503(client_no_active: AsyncClient) -> None:
    response = await client_no_active.post(
        "/internal/generators/generate-word",
        json={"word": "hello", "lang": "en"},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_generate_word_provider_failure_returns_500(tmp_path_resolved: Path) -> None:
    failing = _StubTTSProvider(tmp_path_resolved, priority=1, fail=True)
    svc = _make_tts_service(tmp_path_resolved, [failing])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/generators/generate-word",
            json={"word": "hello", "lang": "en"},
        )
    assert response.status_code == 500
    assert "generation failed" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_word_requires_auth(client_no_internal_auth: AsyncClient) -> None:
    response = await client_no_internal_auth.post(
        "/internal/generators/generate-word",
        json={"word": "hello", "lang": "en"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_generate_word_strips_whitespace(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/generators/generate-word",
        json={"word": "  cat  ", "lang": "en"},
    )
    assert response.status_code == 200
    assert response.content == b"FAKEAUDIO"


@pytest.mark.asyncio
async def test_generate_word_with_a_disabled_configuration(client_no_active: AsyncClient) -> None:
    response = await client_no_active.post(
        "/internal/generators/generate-word",
        json={"word": "apple", "lang": "en", "configuration_name": "stub"},
    )
    assert response.status_code == 200
    assert response.headers["x-tts-provider"] == "stub"
    assert response.content == b"FAKEAUDIO"


@pytest.mark.asyncio
async def test_generate_word_unknown_configuration_returns_404(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/generators/generate-word",
        json={"word": "apple", "lang": "en", "configuration_name": "missing"},
    )
    assert response.status_code == 404
    assert "'missing' not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_generate_word_settings_override_the_configuration(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/generators/generate-word",
        json={
            "word": "apple", "lang": "en", "configuration_name": "stub",
            "settings": {"speed": 0.9, "stability": 0.4, "similarity": None},
        },
    )
    assert response.status_code == 200
    parameters = json.loads(base64.urlsafe_b64decode(response.headers["x-tts-parameters"]))
    assert parameters == {
        "enabled": True,
        "model": "stub-model",
        "output_format": "mp3",
        "speed": 0.9,
        "voice": "stub-voice",
        "voice_settings": {"stability": 0.4},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("settings_body", [{"speed": 0}, {"stability": 1.2}, {"similarity": -1}])
async def test_generate_word_rejects_invalid_settings(client_active: AsyncClient, settings_body: dict) -> None:
    response = await client_active.post(
        "/internal/generators/generate-word",
        json={"word": "apple", "lang": "en", "settings": settings_body},
    )
    assert response.status_code == 422
