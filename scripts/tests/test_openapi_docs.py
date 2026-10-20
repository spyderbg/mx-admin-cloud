"""Tests for scripts/openapi_docs.py — the docs_agents/ per-owner OpenAPI export."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import openapi_docs
from openapi_docs import API_OWNER, OwnershipError

SERVICES = ["content", "evaluation", "logging"]


def _endpoint(module: str):
    def endpoint() -> None:  # pragma: no cover - never called
        return None

    endpoint.__module__ = module
    return endpoint


def _route(path: str, methods: set[str], module: str, include_in_schema: bool = True) -> SimpleNamespace:
    return SimpleNamespace(path=path, methods=methods, endpoint=_endpoint(module), include_in_schema=include_in_schema)


def _schema(paths: dict, schemas: dict | None = None) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "FastAPI Backend", "version": "0.1.0"},
        "paths": paths,
        "components": {
            "schemas": schemas or {},
            "securitySchemes": {"OAuth2PasswordBearer": {"type": "oauth2", "flows": {}}},
        },
    }


def _op(ref: str | None = None, secured: bool = False) -> dict:
    operation: dict = {"responses": {"200": {"description": "ok"}}}
    if ref:
        operation["responses"]["200"]["content"] = {"application/json": {"schema": {"$ref": ref}}}
    if secured:
        operation["security"] = [{"OAuth2PasswordBearer": []}]
    return operation


@pytest.mark.parametrize("module,expected", [
    ("src.services.content.routes.items", "content"),
    ("src.services.logging.routes", "logging"),
    ("src.api.v1.routes_auth", API_OWNER),
    ("src.api.internal.jobs_routes", API_OWNER),
    ("src.main", API_OWNER),
    ("src.services.service_registry", None),  # shared infrastructure, not a service package
    ("src.services.unknown.routes", None),
    ("src.core.logging_route", None),
    ("somewhere.else", None),
])
def test_owner_for_module(module: str, expected: str | None) -> None:
    assert openapi_docs.owner_for_module(module, SERVICES) == expected


def test_iter_api_routes_flattens_included_routers_and_skips_hidden_routes() -> None:
    inner = _route("/api/v1/content/x", {"GET"}, "src.services.content.routes")
    nested = SimpleNamespace(effective_route_contexts=lambda: [inner])
    routes = [
        nested,
        _route("/", {"GET"}, "src.main"),
        _route("/docs", {"GET"}, "fastapi.applications", include_in_schema=False),
        SimpleNamespace(path="/content", app=object()),  # a mount: no endpoint, no methods
    ]
    assert [(path, methods) for path, methods, _ in openapi_docs.iter_api_routes(routes)] == [
        ("/api/v1/content/x", {"GET"}),
        ("/", {"GET"}),
    ]


def test_partition_splits_operations_of_one_path_between_owners() -> None:
    routes = [
        _route("/shared", {"GET"}, "src.services.content.routes"),
        _route("/shared", {"POST"}, "src.services.logging.routes"),
    ]
    index = openapi_docs.index_operations(routes, SERVICES)
    partitioned = openapi_docs.partition_paths(_schema({"/shared": {"get": _op(), "post": _op()}}), index)
    assert set(partitioned["content"]["/shared"]) == {"get"}
    assert set(partitioned["logging"]["/shared"]) == {"post"}


def test_partition_fails_closed_on_unowned_ambiguous_and_unmapped_operations() -> None:
    routes = [
        _route("/twice", {"GET"}, "src.services.content.routes"),
        _route("/twice", {"GET"}, "src.services.logging.routes"),
        _route("/stray", {"GET"}, "src.core.something"),
    ]
    schema = _schema({"/twice": {"get": _op()}, "/orphan": {"get": _op()}, "/stray": {"get": _op()}})
    with pytest.raises(OwnershipError) as excinfo:
        openapi_docs.partition_paths(schema, openapi_docs.index_operations(routes, SERVICES))
    message = str(excinfo.value)
    assert "GET /twice: owned by ['content', 'logging']" in message
    assert "GET /orphan: no owning route" in message
    assert "src.core.something" in message


def test_build_document_keeps_only_reachable_schemas_and_used_security() -> None:
    schemas = {
        "Outer": {"properties": {"inner": {"$ref": "#/components/schemas/Inner"}}},
        "Inner": {"type": "string"},
        "Unused": {"type": "integer"},
    }
    schema = _schema({}, schemas)
    paths = {"/a": {"get": _op("#/components/schemas/Outer", secured=True)}}
    document = openapi_docs.build_document(schema, "content", paths, ["src.services.content.routes"])
    assert list(document["components"]["schemas"]) == ["Outer", "Inner"]
    assert list(document["components"]["securitySchemes"]) == ["OAuth2PasswordBearer"]
    assert document["info"]["title"] == "mx-admin backend content service HTTP API"
    assert "src.services.content.routes" in document["info"]["description"]


def test_a_service_without_routes_gets_a_valid_empty_document(tmp_path: Path) -> None:
    rendered = openapi_docs.generate(_schema({}), [], SERVICES, tmp_path)
    document = yaml.safe_load(rendered[tmp_path / "backend" / "services" / "evaluation" / "openapi.yaml"])
    assert document["paths"] == {}
    assert "components" not in document
    assert document["info"]["description"] == "This service package defines no HTTP endpoints."
    assert set(rendered) == {openapi_docs.output_path(tmp_path, owner) for owner in [*SERVICES, API_OWNER]}


def test_render_is_deterministic_and_marked_generated() -> None:
    paths = {"/b": {"get": _op()}, "/a": {"get": _op()}}
    document = openapi_docs.build_document(_schema({}), API_OWNER, paths, ["src.main"])
    first, second = openapi_docs.render(document), openapi_docs.render(document)
    assert first == second
    assert first.startswith("# GENERATED FILE")
    assert list(yaml.safe_load(first)["paths"]) == ["/a", "/b"]


def test_compare_reports_missing_drift_and_stale_files(tmp_path: Path) -> None:
    rendered = openapi_docs.generate(_schema({}), [], ["content", "logging"], tmp_path)
    openapi_docs.write(rendered)
    assert not openapi_docs.compare(rendered, tmp_path)

    drifted = openapi_docs.output_path(tmp_path, "content")
    drifted.write_text(drifted.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    openapi_docs.output_path(tmp_path, "logging").unlink()
    stale = tmp_path / "backend" / "services" / "removed" / "openapi.yaml"
    stale.parent.mkdir(parents=True)
    stale.write_text("openapi: 3.1.0\n", encoding="utf-8")

    problems = openapi_docs.compare(rendered, tmp_path)
    assert any(p.startswith("drift:") and "content" in p for p in problems)
    assert any(p.startswith("missing:") and "logging" in p for p in problems)
    assert any(p.startswith("stale:") and "removed" in p for p in problems)


def test_discover_service_packages_ignores_non_packages(tmp_path: Path) -> None:
    for name in ("content", "logging", "__tests__"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "models.py").write_text("", encoding="utf-8")
    assert openapi_docs.discover_service_packages(tmp_path) == ["content", "logging"]


def test_the_real_backend_schema_is_fully_and_uniquely_owned(tmp_path: Path) -> None:
    """Every operation of the live app lands in exactly one docs_agents document."""
    pytest.importorskip("fastapi")
    backend_root = Path(__file__).resolve().parents[2] / "backend"
    sys.path.insert(0, str(backend_root))
    try:
        from src.main import app  # pylint: disable=import-outside-toplevel,import-error
    finally:
        sys.path.remove(str(backend_root))

    schema = app.openapi()
    services = openapi_docs.discover_service_packages(backend_root / "src" / "services")
    rendered = openapi_docs.generate(schema, app.routes, services, tmp_path)

    exported = [
        (method, path)
        for text in rendered.values()
        for path, item in (yaml.safe_load(text)["paths"] or {}).items()
        for method in item
        if method in openapi_docs.HTTP_METHODS
    ]
    expected = [
        (method, path)
        for path, item in schema["paths"].items()
        for method in item
        if method in openapi_docs.HTTP_METHODS
    ]
    assert sorted(exported) == sorted(expected)
    assert len(exported) == len(set(exported))
