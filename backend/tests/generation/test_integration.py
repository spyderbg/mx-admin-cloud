"""Integration tests for /internal/generators endpoints — normalization pipeline."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_registry, require_internal
from src.core.models import GeneratedAudio
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


def _make_audio(tmp_path: Path, provider: str = "openai", success: bool = True) -> GeneratedAudio:
    raw = tmp_path / f"hello_en.mp3"
    raw.write_bytes(b"audio-bytes")
    return GeneratedAudio(
        provider=provider,
        request_id="api-0-0-en",
        language_code="en",
        language_name="English",
        expected_text="hello",
        filename_text="hello",
        output_format="mp3",
        raw_path=raw,
        raw_extension="mp3",
        latency_seconds=0.1,
        success=success,
        error=None if success else "generation failed",
    )


def _make_mock_provider(name: str = "openai", audio: GeneratedAudio | None = None) -> Any:
    mock = MagicMock()
    mock.name = name
    mock.base_dir = Path("/tmp")
    if audio is not None:
        mock.generate = AsyncMock(return_value=audio)
    return mock


def _registry_with(provider: Any) -> ServiceRegistry:
    registry = ServiceRegistry()
    registry.providers = [(provider, MagicMock())]
    return registry


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

_PASSTHROUGH_NORMALIZE = AsyncMock(side_effect=lambda base_dir, audio: audio)


# ---------------------------------------------------------------------------
# POST /internal/generators/{provider}/generate — normalization in response
# ---------------------------------------------------------------------------

class TestGenerateSingleNormalization:
    def test_normalized_path_present_when_normalization_succeeds(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        audio = _make_audio(tmp_path)
        normalized = tmp_path / "results" / "normalized" / "en" / "openai" / "hello_en.wav"

        async def _normalize(base_dir: Path, a: GeneratedAudio) -> GeneratedAudio:
            a.normalized_path = normalized
            return a

        registry = _registry_with(_make_mock_provider("openai", audio=audio))
        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch("src.api.internal.generators_routes.normalize_audio", new=_normalize):
                client = TestClient(app, raise_server_exceptions=True)
                resp = client.post(
                    "/internal/generators/openai/generate",
                    json=_GENERATE_BODY,
                    headers=auth_headers,
                )
            assert resp.status_code == 200
            assert resp.json()["normalized_path"] == str(normalized)
        finally:
            app.dependency_overrides.clear()

    def test_normalization_error_propagated_in_response(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        audio = _make_audio(tmp_path)

        async def _normalize_fail(base_dir: Path, a: GeneratedAudio) -> GeneratedAudio:
            a.normalization_error = "ffmpeg not found in PATH."
            return a

        registry = _registry_with(_make_mock_provider("openai", audio=audio))
        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch("src.api.internal.generators_routes.normalize_audio", new=_normalize_fail):
                client = TestClient(app, raise_server_exceptions=True)
                resp = client.post(
                    "/internal/generators/openai/generate",
                    json=_GENERATE_BODY,
                    headers=auth_headers,
                )
            assert resp.status_code == 200
            data = resp.json()
            assert data["normalization_error"] == "ffmpeg not found in PATH."
            assert data["normalized_path"] is None
        finally:
            app.dependency_overrides.clear()

    def test_normalized_path_is_none_when_normalization_not_run(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        audio = _make_audio(tmp_path)
        registry = _registry_with(_make_mock_provider("openai", audio=audio))
        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch(
                "src.api.internal.generators_routes.normalize_audio",
                new=AsyncMock(side_effect=lambda base_dir, a: a),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                resp = client.post(
                    "/internal/generators/openai/generate",
                    json=_GENERATE_BODY,
                    headers=auth_headers,
                )
            assert resp.status_code == 200
            assert resp.json()["normalized_path"] is None
        finally:
            app.dependency_overrides.clear()

    def test_normalize_audio_called_with_provider_base_dir(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        audio = _make_audio(tmp_path)
        provider_base_dir = Path("/custom/base")

        mock_provider = _make_mock_provider("openai", audio=audio)
        mock_provider.base_dir = provider_base_dir
        registry = _registry_with(mock_provider)

        captured_base_dirs: list[Path] = []

        async def _capture_normalize(base_dir: Path, a: GeneratedAudio) -> GeneratedAudio:
            captured_base_dirs.append(base_dir)
            return a

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch("src.api.internal.generators_routes.normalize_audio", new=_capture_normalize):
                client = TestClient(app, raise_server_exceptions=True)
                client.post(
                    "/internal/generators/openai/generate",
                    json=_GENERATE_BODY,
                    headers=auth_headers,
                )
            assert captured_base_dirs == [provider_base_dir]
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /internal/generators/{provider}/generate — request_id construction
# ---------------------------------------------------------------------------

class TestGenerateSingleRequestId:
    def test_request_id_encodes_row_index_and_language(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        audio = _make_audio(tmp_path)
        captured: list[Any] = []

        async def _mock_generate(request: Any, output_format: str, client: Any) -> GeneratedAudio:
            captured.append(request)
            return audio

        mock_provider = _make_mock_provider("openai")
        mock_provider.generate = _mock_generate
        registry = _registry_with(mock_provider)

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch(
                "src.api.internal.generators_routes.normalize_audio",
                new=AsyncMock(side_effect=lambda base_dir, a: a),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                client.post(
                    "/internal/generators/openai/generate",
                    json={**_GENERATE_BODY, "row_index": 5, "language_code": "fr"},
                    headers=auth_headers,
                )
            assert len(captured) == 1
            req = captured[0]
            assert "5" in req.request_id
            assert "fr" in req.request_id
        finally:
            app.dependency_overrides.clear()

    def test_request_id_includes_column_index_when_provided(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        audio = _make_audio(tmp_path)
        captured: list[Any] = []

        async def _mock_generate(request: Any, output_format: str, client: Any) -> GeneratedAudio:
            captured.append(request)
            return audio

        mock_provider = _make_mock_provider("openai")
        mock_provider.generate = _mock_generate
        registry = _registry_with(mock_provider)

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch(
                "src.api.internal.generators_routes.normalize_audio",
                new=AsyncMock(side_effect=lambda base_dir, a: a),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                client.post(
                    "/internal/generators/openai/generate",
                    json={**_GENERATE_BODY, "row_index": 3, "column_index": 7},
                    headers=auth_headers,
                )
            req = captured[0]
            assert "3" in req.request_id
            assert "7" in req.request_id
        finally:
            app.dependency_overrides.clear()

    def test_column_index_defaults_to_zero_when_absent(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        audio = _make_audio(tmp_path)
        captured: list[Any] = []

        async def _mock_generate(request: Any, output_format: str, client: Any) -> GeneratedAudio:
            captured.append(request)
            return audio

        mock_provider = _make_mock_provider("openai")
        mock_provider.generate = _mock_generate
        registry = _registry_with(mock_provider)

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch(
                "src.api.internal.generators_routes.normalize_audio",
                new=AsyncMock(side_effect=lambda base_dir, a: a),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                client.post(
                    "/internal/generators/openai/generate",
                    json=_GENERATE_BODY,
                    headers=auth_headers,
                )
            req = captured[0]
            assert "-0-" in req.request_id
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /internal/generators/{provider}/generate — provider matching
# ---------------------------------------------------------------------------

class TestGenerateSingleProviderMatching:
    def test_returns_404_for_unknown_provider(self, auth_headers: dict, tmp_path: Path) -> None:
        audio = _make_audio(tmp_path)
        registry = _registry_with(_make_mock_provider("openai", audio=audio))
        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/generators/elevenlabs/generate",
                json=_GENERATE_BODY,
                headers=auth_headers,
            )
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_routes_to_correct_provider_by_name(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        audio_a = _make_audio(tmp_path, provider="providerA")
        audio_b = _make_audio(tmp_path, provider="providerB")

        prov_a = _make_mock_provider("providerA", audio=audio_a)
        prov_b = _make_mock_provider("providerB", audio=audio_b)

        registry = ServiceRegistry()
        registry.providers = [(prov_a, MagicMock()), (prov_b, MagicMock())]

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            with patch(
                "src.api.internal.generators_routes.normalize_audio",
                new=AsyncMock(side_effect=lambda base_dir, a: a),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                resp = client.post(
                    "/internal/generators/providerB/generate",
                    json=_GENERATE_BODY,
                    headers=auth_headers,
                )
            assert resp.status_code == 200
            assert resp.json()["provider"] == "providerB"
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /internal/generators/generate-word — language normalisation
# ---------------------------------------------------------------------------

class TestGenerateWordLanguage:
    def _make_word_audio(self, tmp_path: Path, lang: str) -> GeneratedAudio:
        raw = tmp_path / f"apple_{lang}.mp3"
        raw.write_bytes(b"audio-data")
        return GeneratedAudio(
            provider="openai",
            request_id=f"word-apple-{lang}",
            language_code=lang,
            language_name="English",
            expected_text="apple",
            filename_text="apple",
            output_format="mp3",
            raw_path=raw,
            raw_extension="mp3",
            latency_seconds=0.1,
            success=True,
        )

    def test_returns_audio_bytes(self, auth_headers: dict, tmp_path: Path) -> None:
        audio = self._make_word_audio(tmp_path, "en")
        registry = _registry_with(_make_mock_provider("openai", audio=audio))
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
            assert resp.content == b"audio-data"
        finally:
            app.dependency_overrides.clear()

    def test_returns_audio_mpeg_content_type(self, auth_headers: dict, tmp_path: Path) -> None:
        audio = self._make_word_audio(tmp_path, "en")
        registry = _registry_with(_make_mock_provider("openai", audio=audio))
        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/generators/generate-word",
                json={"word": "apple", "lang": "en"},
                headers=auth_headers,
            )
            assert resp.headers["content-type"] == "audio/mpeg"
        finally:
            app.dependency_overrides.clear()

    def test_accepts_bcp47_language_code(self, auth_headers: dict, tmp_path: Path) -> None:
        """BCP-47 codes like 'en-US' should be normalised to 'en'."""
        audio = self._make_word_audio(tmp_path, "en")
        registry = _registry_with(_make_mock_provider("openai", audio=audio))
        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/generators/generate-word",
                json={"word": "apple", "lang": "en-US"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_returns_400_for_whitespace_only_word(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        audio = self._make_word_audio(tmp_path, "en")
        registry = _registry_with(_make_mock_provider("openai", audio=audio))
        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/generators/generate-word",
                json={"word": "   ", "lang": "en"},
                headers=auth_headers,
            )
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.clear()

    def test_returns_503_when_no_providers(self, auth_headers: dict) -> None:
        registry = ServiceRegistry()
        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/generators/generate-word",
                json={"word": "apple", "lang": "en"},
                headers=auth_headers,
            )
            assert resp.status_code == 503
        finally:
            app.dependency_overrides.clear()

    def test_returns_500_when_generation_fails(
        self, auth_headers: dict, tmp_path: Path
    ) -> None:
        failed_audio = GeneratedAudio(
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
            error="API key missing",
        )
        registry = _registry_with(_make_mock_provider("openai", audio=failed_audio))
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
