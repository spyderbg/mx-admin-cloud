"""Integration tests for /internal/translators and /internal/translate endpoints."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_registry, require_internal
from src.services.text_translation.models import TranslatorConfig
from src.core.registry import ServiceRegistry
from src.main import app
from src.security.tokens import create_access_token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def internal_token() -> str:
    """JWT with role='internal' accepted by require_internal."""
    return create_access_token({"sub": "test", "role": "internal"}, timedelta(minutes=5))


@pytest.fixture()
def auth_headers(internal_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {internal_token}"}


@pytest.fixture()
def empty_registry() -> ServiceRegistry:
    return ServiceRegistry()


@pytest.fixture()
def client_no_translators(empty_registry: ServiceRegistry) -> TestClient:
    """TestClient with an empty registry (no translators)."""
    app.dependency_overrides[get_registry] = lambda: empty_registry
    app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


def _make_mock_translator(result: str | None = "translated") -> object:
    """Return a mock translator that returns *result* from translate_text."""
    mock = AsyncMock()
    mock.default_input_language.return_value = "en"
    mock.default_output_language.return_value = "fr"
    mock.translate_text = AsyncMock(return_value=result)
    return mock


@pytest.fixture()
def client_with_translator() -> TestClient:
    """TestClient with one mock translator that returns 'bonjour'."""
    registry = ServiceRegistry()
    registry.set_service("translators", [_make_mock_translator("bonjour")])

    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /internal/translators
# ---------------------------------------------------------------------------

class TestListTranslators:
    def test_returns_200(self, client_no_translators: TestClient, auth_headers: dict) -> None:
        resp = client_no_translators.get("/internal/translators", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_list(self, client_no_translators: TestClient, auth_headers: dict) -> None:
        resp = client_no_translators.get("/internal/translators", headers=auth_headers)
        assert isinstance(resp.json(), list)

    def test_requires_auth(self, empty_registry: ServiceRegistry) -> None:
        """Endpoint should reject unauthenticated requests."""
        app.dependency_overrides[get_registry] = lambda: empty_registry
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/internal/translators")
            assert resp.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    def test_each_entry_has_expected_fields(
        self, tmp_path: Path, auth_headers: dict
    ) -> None:
        """Entries from a real config file contain the documented fields."""
        # Write a minimal translator config file
        translators_dir = tmp_path / "text_translation"
        translators_dir.mkdir(parents=True)
        config_file = translators_dir / "test_translator.json"
        config_file.write_text(
            json.dumps({
                "service_provider": {
                    "service": "translator",
                    "provider": "static_translate",
                    "enabled": True,
                    "priority": 1,
                    "config": {},
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
            resp = client.get("/internal/translators", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            if data:
                entry = data[0]
                for field in ("name", "service_type", "enabled", "priority", "config_path"):
                    assert field in entry, f"Missing field: {field}"
        finally:
            settings.__dict__["config_path"] = original_config_path
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /internal/translate
# ---------------------------------------------------------------------------

class TestTranslateText:
    def test_translate_returns_200(
        self, client_with_translator: TestClient, auth_headers: dict
    ) -> None:
        resp = client_with_translator.post(
            "/internal/translate",
            json={"text": "hello", "target_lang_code": "fr"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_translate_returns_translated_text(
        self, client_with_translator: TestClient, auth_headers: dict
    ) -> None:
        resp = client_with_translator.post(
            "/internal/translate",
            json={"text": "hello", "target_lang_code": "fr"},
            headers=auth_headers,
        )
        assert resp.json()["translated_text"] == "bonjour"

    def test_translate_with_explicit_input_lang(
        self, client_with_translator: TestClient, auth_headers: dict
    ) -> None:
        resp = client_with_translator.post(
            "/internal/translate",
            json={"text": "hello", "input_lang_code": "en", "target_lang_code": "fr"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "translated_text" in resp.json()

    def test_translate_with_category(
        self, client_with_translator: TestClient, auth_headers: dict
    ) -> None:
        resp = client_with_translator.post(
            "/internal/translate",
            json={"text": "apple", "target_lang_code": "fr", "category": "foods"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_translate_returns_503_when_no_translators(
        self, client_no_translators: TestClient, auth_headers: dict
    ) -> None:
        resp = client_no_translators.post(
            "/internal/translate",
            json={"text": "hello", "target_lang_code": "fr"},
            headers=auth_headers,
        )
        assert resp.status_code == 503

    def test_translate_requires_auth(self) -> None:
        registry = ServiceRegistry()
        app.dependency_overrides[get_registry] = lambda: registry
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/translate",
                json={"text": "hello", "target_lang_code": "fr"},
            )
            assert resp.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    def test_translate_missing_target_lang_returns_422(
        self, client_with_translator: TestClient, auth_headers: dict
    ) -> None:
        resp = client_with_translator.post(
            "/internal/translate",
            json={"text": "hello"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_translate_returns_original_when_translator_returns_none(
        self, auth_headers: dict
    ) -> None:
        registry = ServiceRegistry()
        registry.set_service("translators", [_make_mock_translator(result=None)])

        app.dependency_overrides[get_registry] = lambda: registry
        app.dependency_overrides[require_internal] = lambda: {"sub": "test", "role": "internal"}
        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/internal/translate",
                json={"text": "hello", "target_lang_code": "fr"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            assert resp.json()["translated_text"] == "hello"
        finally:
            app.dependency_overrides.clear()
