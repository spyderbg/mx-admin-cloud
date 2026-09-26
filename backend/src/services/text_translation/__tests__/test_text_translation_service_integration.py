"""HTTP integration tests for the text_translation routes (ASGI + httpx)."""

# pylint: disable=missing-function-docstring,redefined-outer-name,too-few-public-methods

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_registry, require_internal
from src.core.config import settings
from src.services import text_translation as text_translation_pkg
from src.services.text_translation import routes as text_translation_routes
from src.services.text_translation.models import TextTranslationServiceProvider, TranslateTextRequest
from src.services.text_translation.service import TextTranslationService


class _FakeRegistry:
    """Minimal registry stub that returns a fixed translation service instance."""

    def __init__(self, translation_service: Any) -> None:
        self._translation_service = translation_service

    def get_service(self, name: str) -> Any:
        if name == text_translation_pkg.SERVICE_NAME:
            return self._translation_service
        raise KeyError(name)

    def get_typed_service(self, service_name: str, expected_type: type[Any]) -> Any:
        service = self.get_service(service_name)
        if not isinstance(service, expected_type):
            raise TypeError(
                f"Service registry mismatch for {service_name}: "
                f"expected {expected_type.__name__}, got {type(service).__name__}.",
            )
        return service


class _StubTranslatorProvider(TextTranslationServiceProvider):
    """Deterministic async translator for route tests."""

    def __init__(self, base: Path, *, priority: int, enabled: bool = True) -> None:
        super().__init__(
            name="stub",
            service_name="text_translation",
            provider_name="stub",
            provider_category="",
            enabled=enabled,
            priority=priority,
            config={"input_language": "en", "output_language": "es"},
            config_path=base / "stub.json",
            base_dir=base,
        )
        self._skip_text: str | None = None

    def skip_for(self, text: str) -> _StubTranslatorProvider:
        self._skip_text = text
        return self

    def supports(self, input_lang_code: str, target_lang_code: str) -> bool:  # pylint: disable=unused-argument
        return True

    async def translate_text(self, request: TranslateTextRequest) -> str | None:  # pylint: disable=unused-argument
        if self._skip_text is not None and request.text == self._skip_text:
            return None
        return f"es::{request.text}"


class _RaisingTranslatorProvider(TextTranslationServiceProvider):
    """Translator that always fails, e.g. simulating a missing API key."""

    def __init__(self, base: Path) -> None:
        super().__init__(
            name="raising",
            service_name="text_translation",
            provider_name="raising",
            provider_category="",
            enabled=True,
            priority=1,
            config={"input_language": "en", "output_language": "es"},
            config_path=base / "raising.json",
            base_dir=base,
        )

    def supports(self, input_lang_code: str, target_lang_code: str) -> bool:  # pylint: disable=unused-argument
        return True

    async def translate_text(self, request: TranslateTextRequest) -> str | None:  # pylint: disable=unused-argument
        raise RuntimeError("OPENAI_API_KEY is not set.")


def _make_translation_service(
        tmp_path: Path,
        providers: list[TextTranslationServiceProvider],
) -> TextTranslationService:
    svc = TextTranslationService(
        service_name=text_translation_pkg.SERVICE_NAME,
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    svc.replace_providers(providers)
    return svc


def _make_app(registry: _FakeRegistry) -> FastAPI:
    app = FastAPI()
    app.include_router(text_translation_routes.router)
    app.dependency_overrides[require_internal] = lambda: {"username": "itest", "role": "admin"}
    app.dependency_overrides[get_registry] = lambda: registry
    return app


class _NotATranslationService:
    """Placeholder to trigger registry type mismatch (500)."""


@pytest.fixture
def tmp_path_resolved(tmp_path: Path) -> Path:
    return tmp_path.resolve()


@pytest.fixture
async def client_two_translators(tmp_path_resolved: Path) -> AsyncClient:
    p_lo = _StubTranslatorProvider(tmp_path_resolved, priority=50)
    p_hi = _StubTranslatorProvider(tmp_path_resolved, priority=5)
    svc = _make_translation_service(tmp_path_resolved, [p_lo, p_hi])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_active(tmp_path_resolved: Path) -> AsyncClient:
    stub = _StubTranslatorProvider(tmp_path_resolved, priority=1)
    svc = _make_translation_service(tmp_path_resolved, [stub])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_no_active(tmp_path_resolved: Path) -> AsyncClient:
    disabled = _StubTranslatorProvider(tmp_path_resolved, priority=1, enabled=False)
    svc = _make_translation_service(tmp_path_resolved, [disabled])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_raising_provider(tmp_path_resolved: Path) -> AsyncClient:
    stub = _RaisingTranslatorProvider(tmp_path_resolved)
    svc = _make_translation_service(tmp_path_resolved, [stub])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_bad_registry() -> AsyncClient:
    app = _make_app(_FakeRegistry(_NotATranslationService()))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_no_internal_auth(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    # The dev default AUTH_DISABLED=True bypasses verify_token; the 401 paths
    # under test only exist with auth enabled.
    monkeypatch.setattr(settings, "AUTH_DISABLED", False)
    app = FastAPI()
    app.include_router(text_translation_routes.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_list_translators_sorted_by_priority(client_two_translators: AsyncClient) -> None:
    response = await client_two_translators.get("/internal/translators/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert [row["priority"] for row in data] == [5, 50]
    for row in data:
        assert set(row.keys()) == {
            "name", "service", "provider", "provider_category", "enabled", "priority",
        }


@pytest.mark.asyncio
async def test_translate_success(client_active: AsyncClient) -> None:
    response = await client_active.post(
        "/internal/translators/translate",
        json={
            "text": "hello",
            "input_lang_code": "en",
            "target_lang_code": "es",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["lang_text"]["lang_code"] == "es"
    assert body["lang_text"]["text"] == "es::hello"


@pytest.mark.asyncio
async def test_translate_no_active_translators_returns_503(client_no_active: AsyncClient) -> None:
    response = await client_no_active.post(
        "/internal/translators/translate",
        json={"text": "hello", "target_lang_code": "es"},
    )
    assert response.status_code == 503
    assert "No active translators" in response.json()["detail"]


@pytest.mark.asyncio
async def test_translate_provider_exception_surfaces_reason_in_errors(
        client_raising_provider: AsyncClient,
) -> None:
    response = await client_raising_provider.post(
        "/internal/translators/translate",
        json={"text": "hello", "input_lang_code": "en", "target_lang_code": "es"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["errors"] == ["raising: OPENAI_API_KEY is not set."]


@pytest.mark.asyncio
async def test_translate_registry_mismatch_returns_500(client_bad_registry: AsyncClient) -> None:
    response = await client_bad_registry.post(
        "/internal/translators/translate",
        json={"text": "x", "target_lang_code": "es"},
    )
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_translate_failure_returns_original_with_normalized_input_lang(
        tmp_path_resolved: Path,
) -> None:
    stub = _StubTranslatorProvider(tmp_path_resolved, priority=1).skip_for("hello")
    svc = _make_translation_service(tmp_path_resolved, [stub])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/translators/translate",
            json={
                "text": "hello",
                "input_lang_code": "EN",
                "target_lang_code": "es",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["lang_text"]["lang_code"] == "en"
    assert body["lang_text"]["text"] == "hello"


@pytest.mark.asyncio
async def test_translate_file_txt(monkeypatch: pytest.MonkeyPatch, tmp_path_resolved: Path) -> None:
    out = tmp_path_resolved / "out"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        text_translation_routes,
        "settings",
        type("S", (), {"output_path": out})(),
    )

    stub = _StubTranslatorProvider(tmp_path_resolved, priority=1)
    svc = _make_translation_service(tmp_path_resolved, [stub])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/translators/translate-file",
            files={"file": ("words.txt", b"one\ntwo\n", "text/plain")},
            params={"input_lang_code": "en", "target_lang_code": "es"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [
        {"original": "one", "translated": "es::one", "language": "es"},
        {"original": "two", "translated": "es::two", "language": "es"},
    ]
    assert not any(out.glob("temp_translate_*"))


@pytest.mark.asyncio
async def test_translate_file_no_active_returns_503(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path_resolved: Path,
) -> None:
    out = tmp_path_resolved / "out"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        text_translation_routes,
        "settings",
        type("S", (), {"output_path": out})(),
    )
    disabled = _StubTranslatorProvider(tmp_path_resolved, priority=1, enabled=False)
    svc = _make_translation_service(tmp_path_resolved, [disabled])
    app = _make_app(_FakeRegistry(svc))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/translators/translate-file",
            files={"file": ("w.txt", b"a\n", "text/plain")},
        )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_internal_routes_require_auth(client_no_internal_auth: AsyncClient) -> None:
    response = await client_no_internal_auth.get("/internal/translators/")
    assert response.status_code == 401
