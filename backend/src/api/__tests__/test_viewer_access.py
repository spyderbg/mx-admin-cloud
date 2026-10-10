"""Viewers may look but never change anything: every non-read endpoint refuses them."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import READ_ONLY_METHODS, get_current_user, require_uds
from src.main import app

# Endpoints a viewer may still POST to: signing in (there is no user yet) and listing files,
# which only reads but takes its options in a body.
OPEN_TO_VIEWERS = {
    ("POST", "/api/v1/login"),
    ("POST", "/api/v1/login/google"),
    ("POST", "/api/v1/cli-login"),
    ("POST", "/api/v1/list-files"),
}


def _changing_endpoints() -> list[tuple[str, str]]:
    return [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() not in READ_ONLY_METHODS
    ]


def _signed_in_as(role: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: {"username": f"a-{role}", "role": role})
    # The Unix-socket check would refuse first; this test is about the role check behind it.
    monkeypatch.setitem(app.dependency_overrides, require_uds, lambda: None)
    # The lifespan does not run here. Stand-ins let the dependencies that read the app state
    # resolve, so the role check decides the response; a handler reached by mistake fails loudly.
    for name in ("registry", "registry_builder", "job_store", "internal_token"):
        monkeypatch.setattr(app.state, name, MagicMock(), raising=False)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def viewer(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return _signed_in_as("viewer", monkeypatch)


@pytest.mark.parametrize("method,path", [
    endpoint for endpoint in _changing_endpoints() if endpoint not in OPEN_TO_VIEWERS
])
def test_a_viewer_cannot_change_anything(viewer: TestClient, method: str, path: str) -> None:
    response = viewer.request(method, re.sub(r"\{[^}]+\}", "1", path))
    assert response.status_code == 403, f"{method} {path} answered a viewer with {response.status_code}"


def test_the_endpoints_open_to_viewers_still_exist() -> None:
    assert OPEN_TO_VIEWERS <= set(_changing_endpoints())


def test_a_viewer_can_read_the_status_and_files(viewer: TestClient) -> None:
    assert viewer.get("/api/v1/status").status_code == 200
    assert viewer.post("/api/v1/list-files", json={"depth": 1}).status_code == 200


@pytest.mark.parametrize("role,expected", [("viewer", 403), ("admin", 200)])
def test_only_admins_run_operations(monkeypatch: pytest.MonkeyPatch, role: str, expected: int) -> None:
    client = _signed_in_as(role, monkeypatch)
    assert client.post("/api/v1/operation", params={"name": "noop"}).status_code == expected
