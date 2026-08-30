"""HTTP integration tests for configuration routes (ASGI + httpx)."""

# pylint: disable=duplicate-code,missing-function-docstring,redefined-outer-name,too-few-public-methods

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_registry_builder, require_internal
from src.core.config import settings
from src.services.acceptance import ConfigApplyResult, ServiceAcceptCriteria
from src.services.configuration import routes as cfg_routes
from src.services.configuration.models import (
    ConfigurationConflictError,
    ConfigurationFileResult,
    ConfigurationMutationResult,
    ConfigurationNotFoundError,
    ConfigurationProvider,
    ConfigurationSummary,
    ConfigurationValidationError,
    InvalidConfigurationNameError,
    UnknownConfigurationServiceError,
)
from src.services.configuration.routes import get_configuration_service
from src.services.configuration.service import ConfigurationService
from src.services.service_registry import ServiceRegistry, ServiceRegistryBuilder


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _StubConfigurationProvider(ConfigurationProvider):
    """Controllable stub for route-level tests."""

    def __init__(self, base: Path) -> None:
        super().__init__(
            name="stub",
            service_name="configuration",
            provider_name="file_system",
            provider_category="",
            enabled=True,
            priority=1,
            config={},
            config_path=base / "stub.json",
            base_dir=base,
        )
        self._list_result: list[ConfigurationSummary] = []
        self._get_result: ConfigurationFileResult | None = None
        self._get_exc: Exception | None = None
        self._add_exc: Exception | None = None
        self._remove_exc: Exception | None = None
        self._patch_exc: Exception | None = None
        self._toggle_exc: Exception | None = None

    def with_summaries(self, summaries: list[ConfigurationSummary]) -> "_StubConfigurationProvider":
        self._list_result = summaries
        return self

    def with_payload(self, result: ConfigurationFileResult) -> "_StubConfigurationProvider":
        self._get_result = result
        return self

    def raise_on_get(self, exc: Exception) -> "_StubConfigurationProvider":
        self._get_exc = exc
        return self

    def raise_on_add(self, exc: Exception) -> "_StubConfigurationProvider":
        self._add_exc = exc
        return self

    def raise_on_remove(self, exc: Exception) -> "_StubConfigurationProvider":
        self._remove_exc = exc
        return self

    def raise_on_patch(self, exc: Exception) -> "_StubConfigurationProvider":
        self._patch_exc = exc
        return self

    def raise_on_toggle(self, exc: Exception) -> "_StubConfigurationProvider":
        self._toggle_exc = exc
        return self

    def with_list_error(self, exc: Exception) -> "_StubConfigurationProvider":
        self._list_result = exc  # type: ignore[assignment]
        return self

    def list_configurations(self, service_name: str) -> list[ConfigurationSummary]:
        if isinstance(self._list_result, Exception):
            raise self._list_result  # type: ignore[misc]
        return self._list_result

    def get_configuration(self, service_name: str, config_name: str) -> ConfigurationFileResult:
        if self._get_exc:
            raise self._get_exc
        if self._get_result is not None:
            return self._get_result
        return ConfigurationFileResult(
            config_name=config_name,
            filename=f"{config_name}.json",
            payload={},
        )

    def add_configuration(
            self, service_name: str, config_name: str, payload: dict[str, Any]
    ) -> ConfigurationMutationResult:
        if self._add_exc:
            raise self._add_exc
        return ConfigurationMutationResult(config_name=config_name, changed=True)

    def remove_configuration(self, service_name: str, config_name: str) -> ConfigurationMutationResult:
        if self._remove_exc:
            raise self._remove_exc
        return ConfigurationMutationResult(config_name=config_name, changed=True)

    def patch_configuration(
            self, service_name: str, config_name: str, patch: dict[str, Any]
    ) -> ConfigurationMutationResult:
        if self._patch_exc:
            raise self._patch_exc
        return ConfigurationMutationResult(config_name=config_name, changed=True)

    def set_configurations_enabled(
            self, service_name: str, config_names: list[str], enabled: bool
    ) -> list[ConfigurationMutationResult]:
        if self._toggle_exc:
            raise self._toggle_exc
        return [ConfigurationMutationResult(config_name=n, changed=True) for n in config_names]

    def set_all_configurations_enabled(
            self, service_name: str, enabled: bool
    ) -> list[ConfigurationMutationResult]:
        if self._toggle_exc:
            raise self._toggle_exc
        return [ConfigurationMutationResult(config_name="cfg", changed=True)]


def _make_accept_criteria(service_name: str) -> ServiceAcceptCriteria:
    return ServiceAcceptCriteria(
        service_name=service_name,
        config_subdir=service_name,
        validate_provider=lambda provider: None,
        build_provider=lambda provider, base_dir: provider,
    )


class _FakeRegistryBuilder:
    """Minimal builder stub for apply/upload route tests."""

    def __init__(
            self,
            *,
            accepted: bool = True,
            error: str | None = None,
            service_names: list[str] | None = None,
    ) -> None:
        self._accepted = accepted
        self._error = error
        self._service_names = service_names or ["configuration", "logging", "text_to_speech"]

    def apply_runtime_payload(  # pylint: disable=unused-argument
            self,
            payload: dict[str, Any],
            source: str = "<test>",
    ) -> ConfigApplyResult:
        return ConfigApplyResult(
            config_name="stub_config",
            accepted=self._accepted,
            error=self._error,
        )

    def get_accept_criteria(self) -> dict[str, ServiceAcceptCriteria]:
        return {name: _make_accept_criteria(name) for name in self._service_names}


def _make_configuration_service(
        tmp_path: Path,
        provider: _StubConfigurationProvider,
) -> ConfigurationService:
    svc = ConfigurationService(
        service_name="configuration",
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    svc.replace_providers([provider])
    return svc


def _make_app(
        provider: _StubConfigurationProvider | None = None,
        tmp_path: Path | None = None,
        builder: _FakeRegistryBuilder | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(cfg_routes.router)
    app.dependency_overrides[require_internal] = lambda: {"username": "itest", "role": "admin"}
    if provider is not None and tmp_path is not None:
        svc = _make_configuration_service(tmp_path, provider)
        app.dependency_overrides[get_configuration_service] = lambda: svc
    if builder is not None:
        app.dependency_overrides[get_registry_builder] = lambda: builder
    return app


_SUMMARY = ConfigurationSummary(
    config_name="openai",
    filename="openai.json",
    provider="openai_tts",
    enabled=True,
    priority=1,
    valid_json=True,
    version="1.0",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_resolved(tmp_path: Path) -> Path:
    return tmp_path.resolve()


def test_builder_keeps_runtime_configuration_provider(tmp_resolved: Path) -> None:
    config_root = tmp_resolved / "config"
    config_root.mkdir(parents=True, exist_ok=True)

    registry = ServiceRegistry.build_runtime(tmp_resolved)
    builder = ServiceRegistryBuilder(registry, tmp_resolved, config_root)
    builder.load_all_from_disk()

    typed_service = registry.get_typed_service("configuration", ConfigurationService)
    assert typed_service is registry.get_service("configuration")
    assert any(
        provider.provider == "file_system"
        for provider in typed_service.get_active_providers()
    )


@pytest.fixture
async def client_with_summaries(tmp_resolved: Path) -> AsyncClient:
    stub = _StubConfigurationProvider(tmp_resolved).with_summaries([_SUMMARY])
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_empty(tmp_resolved: Path) -> AsyncClient:
    stub = _StubConfigurationProvider(tmp_resolved).with_summaries([])
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client_no_auth(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    # The dev default AUTH_DISABLED=True bypasses verify_token; the 401 paths
    # under test only exist with auth enabled.
    monkeypatch.setattr(settings, "AUTH_DISABLED", False)
    app = FastAPI()
    app.include_router(cfg_routes.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# GET /internal/configurations  (list configurable services)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_services_returns_sorted_names_without_configuration() -> None:
    builder = _FakeRegistryBuilder(service_names=["text_to_speech", "configuration", "logging"])
    app = _make_app(builder=builder)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/configurations")
    assert response.status_code == 200
    assert response.json() == {"services": ["logging", "text_to_speech"]}


@pytest.mark.asyncio
async def test_list_services_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.get("/internal/configurations")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /internal/configurations/{service_name}/{config_name}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_config_returns_full_payload(tmp_resolved: Path) -> None:
    payload = {"service_provider": {"service": "logging", "version": "1.0", "config": {"a": 1}}}
    stub = _StubConfigurationProvider(tmp_resolved).with_payload(
        ConfigurationFileResult(config_name="default", filename="default.json", payload=payload),
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/configurations/logging/default")
    assert response.status_code == 200
    body = response.json()
    assert body["config_name"] == "default"
    assert body["filename"] == "default.json"
    assert body["payload"] == payload


@pytest.mark.asyncio
async def test_get_config_not_found_returns_404(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_get(
        ConfigurationNotFoundError("Not found.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/configurations/logging/missing")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_config_unknown_service_returns_404(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_get(
        UnknownConfigurationServiceError("Unknown service.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/configurations/foo/bar")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_config_invalid_name_returns_422(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_get(
        InvalidConfigurationNameError("Bad name.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/configurations/logging/bad-name!")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_config_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.get("/internal/configurations/logging/default")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /internal/configurations/apply
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_accepted_payload_returns_200() -> None:
    builder = _FakeRegistryBuilder(accepted=True)
    app = _make_app(builder=builder)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/configurations/apply",
            json={"payload": {"service_provider": {"service": "text_to_speech"}}},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["config_name"] == "stub_config"


@pytest.mark.asyncio
async def test_apply_rejected_payload_returns_422() -> None:
    builder = _FakeRegistryBuilder(accepted=False, error="Unsupported provider")
    app = _make_app(builder=builder)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/configurations/apply",
            json={"payload": {"service_provider": {}}},
        )
    assert response.status_code == 422
    assert "Unsupported provider" in response.json()["detail"]


@pytest.mark.asyncio
async def test_apply_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post(
        "/internal/configurations/apply",
        json={"payload": {}},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /internal/configurations/upload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_valid_json_file_returns_200() -> None:
    builder = _FakeRegistryBuilder(accepted=True)
    app = _make_app(builder=builder)
    transport = ASGITransport(app=app)
    payload_bytes = json.dumps({"service_provider": {"service": "text_to_speech"}}).encode()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/configurations/upload",
            files={"file": ("my_config.json", payload_bytes, "application/json")},
        )
    assert response.status_code == 200
    assert response.json()["accepted"] is True


@pytest.mark.asyncio
async def test_upload_invalid_json_returns_422() -> None:
    builder = _FakeRegistryBuilder(accepted=True)
    app = _make_app(builder=builder)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/configurations/upload",
            files={"file": ("bad.json", b"not-json{{{", "application/json")},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_rejected_payload_returns_422() -> None:
    builder = _FakeRegistryBuilder(accepted=False, error="Bad provider")
    app = _make_app(builder=builder)
    transport = ASGITransport(app=app)
    payload_bytes = json.dumps({"service_provider": {}}).encode()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/configurations/upload",
            files={"file": ("cfg.json", payload_bytes, "application/json")},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_requires_auth(client_no_auth: AsyncClient) -> None:
    payload_bytes = json.dumps({}).encode()
    response = await client_no_auth.post(
        "/internal/configurations/upload",
        files={"file": ("cfg.json", payload_bytes, "application/json")},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /internal/configurations/{service_name}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_configs_returns_summaries(client_with_summaries: AsyncClient) -> None:
    response = await client_with_summaries.get("/internal/configurations/text_to_speech")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["config_name"] == "openai"
    assert data[0]["enabled"] is True
    assert data[0]["valid_json"] is True
    assert data[0]["version"] == "1.0"


@pytest.mark.asyncio
async def test_list_configs_returns_empty_list(client_empty: AsyncClient) -> None:
    response = await client_empty.get("/internal/configurations/text_to_speech")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_configs_unknown_service_returns_404(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).with_list_error(
        UnknownConfigurationServiceError("Unknown service 'foo'."),
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/configurations/foo")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_configs_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.get("/internal/configurations/text_to_speech")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /internal/configurations/{service_name}  (add)
# ---------------------------------------------------------------------------

_ADD_BODY = {"config_name": "new_provider", "payload": {"service_provider": {"service": "text_to_speech"}}}


@pytest.mark.asyncio
async def test_add_config_success(client_empty: AsyncClient) -> None:
    response = await client_empty.post("/internal/configurations/text_to_speech", json=_ADD_BODY)
    assert response.status_code == 201
    body = response.json()
    assert body["config_name"] == "new_provider"
    assert body["changed"] is True
    assert body["error"] is None


@pytest.mark.asyncio
async def test_add_config_unknown_service_returns_404(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_add(
        UnknownConfigurationServiceError("Unknown service.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/internal/configurations/foo", json=_ADD_BODY)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_add_config_conflict_returns_409(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_add(
        ConfigurationConflictError("Already exists.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/internal/configurations/text_to_speech", json=_ADD_BODY)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_add_config_invalid_name_returns_422(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_add(
        InvalidConfigurationNameError("Bad name.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/internal/configurations/text_to_speech", json=_ADD_BODY)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_add_config_validation_error_returns_422(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_add(
        ConfigurationValidationError("Schema mismatch.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/internal/configurations/text_to_speech", json=_ADD_BODY)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_add_config_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post("/internal/configurations/text_to_speech", json=_ADD_BODY)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /internal/configurations/{service_name}/{config_name}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_config_success(client_empty: AsyncClient) -> None:
    response = await client_empty.request(
        "DELETE", "/internal/configurations/text_to_speech/openai"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["config_name"] == "openai"
    assert body["changed"] is True


@pytest.mark.asyncio
async def test_remove_config_not_found_returns_404(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_remove(
        ConfigurationNotFoundError("Not found.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(
            "DELETE", "/internal/configurations/text_to_speech/missing"
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_config_unknown_service_returns_404(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_remove(
        UnknownConfigurationServiceError("Unknown.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(
            "DELETE", "/internal/configurations/foo/bar"
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_config_invalid_name_returns_422(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_remove(
        InvalidConfigurationNameError("Bad name.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(
            "DELETE", "/internal/configurations/text_to_speech/bad-name!"
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_remove_config_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.request(
        "DELETE", "/internal/configurations/text_to_speech/openai"
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /internal/configurations/{service_name}/{config_name}
# ---------------------------------------------------------------------------

_PATCH_BODY = {"patch": {"service_provider": {"enabled": False}}}


@pytest.mark.asyncio
async def test_patch_config_success(client_empty: AsyncClient) -> None:
    response = await client_empty.patch(
        "/internal/configurations/text_to_speech/openai", json=_PATCH_BODY
    )
    assert response.status_code == 200
    body = response.json()
    assert body["config_name"] == "openai"
    assert body["changed"] is True


@pytest.mark.asyncio
async def test_patch_config_not_found_returns_404(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_patch(
        ConfigurationNotFoundError("Not found.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/internal/configurations/text_to_speech/missing", json=_PATCH_BODY
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_config_validation_error_returns_422(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_patch(
        ConfigurationValidationError("Schema mismatch.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/internal/configurations/text_to_speech/openai", json=_PATCH_BODY
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_config_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.patch(
        "/internal/configurations/text_to_speech/openai", json=_PATCH_BODY
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /internal/configurations/{service_name}/enable-configurations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enable_configurations_success(client_empty: AsyncClient) -> None:
    response = await client_empty.post(
        "/internal/configurations/text_to_speech/enable-configurations",
        json={"config_names": ["openai", "elevenlabs"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["service_name"] == "text_to_speech"
    assert body["enabled"] is True
    assert len(body["results"]) == 2
    assert all(r["changed"] for r in body["results"])


@pytest.mark.asyncio
async def test_enable_configurations_unknown_service_returns_404(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_toggle(
        UnknownConfigurationServiceError("Unknown service.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/configurations/foo/enable-configurations",
            json={"config_names": ["cfg"]},
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /internal/configurations/{service_name}/disable-configurations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disable_configurations_success(client_empty: AsyncClient) -> None:
    response = await client_empty.post(
        "/internal/configurations/text_to_speech/disable-configurations",
        json={"config_names": ["openai"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["results"][0]["config_name"] == "openai"


# ---------------------------------------------------------------------------
# POST /internal/configurations/{service_name}/enable-all-configurations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enable_all_configurations_success(client_empty: AsyncClient) -> None:
    response = await client_empty.post(
        "/internal/configurations/text_to_speech/enable-all-configurations"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["service_name"] == "text_to_speech"
    assert body["enabled"] is True
    assert len(body["results"]) >= 1


@pytest.mark.asyncio
async def test_enable_all_configurations_unknown_service_returns_404(tmp_resolved: Path) -> None:
    stub = _StubConfigurationProvider(tmp_resolved).raise_on_toggle(
        UnknownConfigurationServiceError("Unknown service.")
    )
    app = _make_app(stub, tmp_resolved)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/configurations/foo/enable-all-configurations"
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /internal/configurations/{service_name}/disable-all-configurations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disable_all_configurations_success(client_empty: AsyncClient) -> None:
    response = await client_empty.post(
        "/internal/configurations/text_to_speech/disable-all-configurations"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False


@pytest.mark.asyncio
async def test_disable_all_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post(
        "/internal/configurations/text_to_speech/disable-all-configurations"
    )
    assert response.status_code == 401
