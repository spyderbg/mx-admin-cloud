"""Integration tests for /internal/generators endpoints."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_registry, require_internal
from src.core.models import GeneratedAudio, TTSRequest
from src.core.registry import ServiceRegistry
from src.main import app
from src.security.tokens import create_access_token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def internal_token() -> str:
    return create_access_token({"sub": "test", "role": "internal"}, timedelta(minutes=5))


@pytest.fixture()
def auth_headers(internal_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {internal_token}"}


@pytest.fixture()
def empty_registry() -> ServiceRegistry:
    return ServiceRegistry()


@pytest.fixture()
def client_no_providers(empty_registry: ServiceRegistry):
    """TestClient with an empty registry (no providers)."""
    app.dependency_overrides[get_registry] = lambda: empty_registry
    app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


def _make_mock_provider(name: str = "openai", success: bool = True) -> Any:
    """Return a mock TTS provider that returns a successful GeneratedAudio."""
    mock = MagicMock()
    mock.name = name
    mock.base_dir = Path("/tmp")
    generated = GeneratedAudio(
        provider=name,
        request_id="test-001",
        language_code="en",
        language_name="English",
        expected_text="hello",
        filename_text="hello",
        output_format="mp3",
        raw_path=Path("/tmp/hello_en.mp3"),
        raw_extension="mp3",
        latency_seconds=0.1,
        success=success,
        error=None if success else "generation failed",
    )
    mock.generate = AsyncMock(return_value=generated)
    return mock


@pytest.fixture()
def client_with_provider():
    """TestClient with one mock provider that succeeds."""
    registry = ServiceRegistry()
    registry.set_service("providers", [_make_mock_provider("openai")])

    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /internal/generators
# ---------------------------------------------------------------------------

class TestListGenerators:
    def test_returns_200(self, client_no_providers: TestClient, auth_headers: dict) -> None:
        resp = client_no_providers.get("/internal/generators", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_list(self, client_no_providers: TestClient, auth_headers: dict) -> None:
        resp = client_no_providers.get("/internal/generators", headers=auth_headers)
        assert isinstance(resp.json(), list)

    def test_requires_auth(self, empty_registry: ServiceRegistry) -> None:
        app.dependency_overrides[get_registry] = lambda: empty_registry
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/internal/generators")
            assert resp.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    def test_each_entry_has_expected_fields(
        self, tmp_path: Path, auth_headers: dict
    ) -> None:
        generators_dir = tmp_path / "text_to_speech"
        generators_dir.mkdir(parents=True)
        config_file = generators_dir / "test_openai.json"
        config_file.write_text(
            json.dumps({
                "service_provider": {
                    "provider": "openai",
                    "enabled": False,
                    "version": "1.0",
                    "config": {
                        "generate": {"model": "tts-1-hd"},
                        "evaluate": {"enabled": False},
                    },
                }
            }),
            encoding="utf-8",
        )

        from src.core.config import settings

        original_config_path = settings.config_path
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            settings.__dict__["config_path"] = tmp_path
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/internal/generators", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            if data:
                entry = data[0]
                for field in ("name", "enabled", "config_path", "generate", "evaluate_enabled"):
                    assert field in entry, f"Missing field: {field}"
        finally:
            settings.__dict__["config_path"] = original_config_path
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /internal/generators/{provider}/generate
# ---------------------------------------------------------------------------

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


class TestGenerateSingle:
    def test_returns_404_when_provider_not_found(
        self, client_no_providers: TestClient, auth_headers: dict
    ) -> None:
        resp = client_no_providers.post(
            "/internal/generators/openai/generate",
            json=_GENERATE_BODY,
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_requires_auth(self, empty_registry: ServiceRegistry) -> None:
        app.dependency_overrides[get_registry] = lambda: empty_registry
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/generators/openai/generate",
                json=_GENERATE_BODY,
            )
            assert resp.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    def test_returns_200_on_success(
        self, client_with_provider: TestClient, auth_headers: dict, tmp_path: Path
    ) -> None:
        dummy_audio = tmp_path / "hello_en.mp3"
        dummy_audio.write_bytes(b"audio")

        registry = ServiceRegistry()
        mock_provider = _make_mock_provider("openai")
        mock_provider.generate.return_value = GeneratedAudio(
            provider="openai",
            request_id="test-001",
            language_code="en",
            language_name="English",
            expected_text="hello",
            filename_text="hello",
            output_format="mp3",
            raw_path=dummy_audio,
            raw_extension="mp3",
            latency_seconds=0.1,
            success=True,
        )
        registry.set_service("providers", [mock_provider])

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch(
                "src.api.internal.text_to_speech_routes.normalize_audio",
                new=AsyncMock(side_effect=lambda base_dir, audio: audio),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                resp = client.post(
                    "/internal/generators/openai/generate",
                    json=_GENERATE_BODY,
                    headers={"Authorization": f"Bearer {create_access_token({'sub': 'test', 'role': 'internal'}, timedelta(minutes=5))}"},
                )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_response_has_expected_fields(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        dummy_audio = tmp_path / "hello_en.mp3"
        dummy_audio.write_bytes(b"audio")

        registry = ServiceRegistry()
        mock_provider = _make_mock_provider("openai")
        mock_provider.generate.return_value = GeneratedAudio(
            provider="openai",
            request_id="api-0-0-en",
            language_code="en",
            language_name="English",
            expected_text="hello",
            filename_text="hello",
            output_format="mp3",
            raw_path=dummy_audio,
            raw_extension="mp3",
            latency_seconds=0.1,
            success=True,
        )
        registry.set_service("providers", [mock_provider])

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch(
                "src.api.internal.text_to_speech_routes.normalize_audio",
                new=AsyncMock(side_effect=lambda base_dir, audio: audio),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                resp = client.post(
                    "/internal/generators/openai/generate",
                    json=_GENERATE_BODY,
                    headers=auth_headers,
                )
            assert resp.status_code == 200
            data = resp.json()
            for field in (
                "provider", "request_id", "language_code", "language_name",
                "expected_text", "filename_text", "output_format",
                "raw_path", "raw_extension", "latency_seconds", "success",
            ):
                assert field in data, f"Missing field: {field}"
        finally:
            app.dependency_overrides.clear()

    def test_missing_required_fields_returns_422(
        self, client_with_provider: TestClient, auth_headers: dict
    ) -> None:
        resp = client_with_provider.post(
            "/internal/generators/openai/generate",
            json={"text": "hello"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /internal/generators/generate-word
# ---------------------------------------------------------------------------

class TestGenerateWord:
    def test_returns_503_when_no_providers(
        self, client_no_providers: TestClient, auth_headers: dict
    ) -> None:
        resp = client_no_providers.post(
            "/internal/generators/generate-word",
            json={"word": "apple", "lang": "en"},
            headers=auth_headers,
        )
        assert resp.status_code == 503

    def test_requires_auth(self, empty_registry: ServiceRegistry) -> None:
        app.dependency_overrides[get_registry] = lambda: empty_registry
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/generators/generate-word",
                json={"word": "apple", "lang": "en"},
            )
            assert resp.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    def test_returns_400_for_empty_word(
        self, client_with_provider: TestClient, auth_headers: dict
    ) -> None:
        resp = client_with_provider.post(
            "/internal/generators/generate-word",
            json={"word": "   ", "lang": "en"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_returns_audio_bytes_on_success(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        dummy_audio = tmp_path / "apple_en.mp3"
        dummy_audio.write_bytes(b"fake-mp3-bytes")

        registry = ServiceRegistry()
        mock_provider = _make_mock_provider("openai")
        mock_provider.generate.return_value = GeneratedAudio(
            provider="openai",
            request_id="word-apple-en",
            language_code="en",
            language_name="English",
            expected_text="apple",
            filename_text="apple",
            output_format="mp3",
            raw_path=dummy_audio,
            raw_extension="mp3",
            latency_seconds=0.1,
            success=True,
        )
        registry.set_service("providers", [mock_provider])

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/generators/generate-word",
                json={"word": "apple", "lang": "en"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            assert resp.content == b"fake-mp3-bytes"
            assert resp.headers["content-type"] == "audio/mpeg"
        finally:
            app.dependency_overrides.clear()

    def test_returns_500_when_generation_fails(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        registry = ServiceRegistry()
        mock_provider = _make_mock_provider("openai", success=False)
        mock_provider.generate.return_value = GeneratedAudio(
            provider="openai",
            request_id="word-apple-en",
            language_code="en",
            language_name="English",
            expected_text="apple",
            filename_text="apple",
            output_format="mp3",
            raw_path=tmp_path / "apple_en.mp3",
            raw_extension="mp3",
            latency_seconds=0.0,
            success=False,
            error="generation failed",
        )
        registry.set_service("providers", [mock_provider])

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/generators/generate-word",
                json={"word": "apple", "lang": "en"},
                headers=auth_headers,
            )
            assert resp.status_code == 500
        finally:
            app.dependency_overrides.clear()
