"""Unit tests for `content export-items` with mocked HTTP (no backend needed)."""

import pytest
import requests
from typer.testing import CliRunner

from kiddo_cli.main import app

runner = CliRunner()
URL = "http://backend.test"
EXPORT = f"{URL}/api/v1/content/categories/C_Foods/items/export"


class FakeResponse:
    def __init__(self, status=200, content=b"", url=EXPORT):
        self.status_code = status
        self.content = content
        self.url = url
        self.reason = "Err"
        self.text = content.decode("utf-8", "replace")

    def json(self):
        raise ValueError("not json")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("KIDDO_TOKEN", raising=False)
    monkeypatch.delenv("BACKEND_URL", raising=False)


def _run(tmp_path, monkeypatch, fake_get, name="C_Foods"):
    monkeypatch.setattr("kiddo_cli.api.client.requests.get", fake_get)
    out = tmp_path / "out.csv"
    result = runner.invoke(
        app,
        ["content", "export-items", name, "--output", str(out), "--url", URL, "--token", "tok"],
    )
    return result, out


def test_export_writes_exact_bytes_with_auth(tmp_path, monkeypatch):
    body = "id,name\r\n1,ябълка\r\n2,\"pear, ripe\"\r\n".encode("utf-8")
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, headers))
        return FakeResponse(content=body)

    result, out = _run(tmp_path, monkeypatch, fake_get)
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == body
    assert calls[0][0] == EXPORT
    assert calls[0][1]["Authorization"] == "Bearer tok"


def test_missing_category_exits_nonzero_without_file(tmp_path, monkeypatch):
    result, out = _run(tmp_path, monkeypatch, lambda *a, **k: FakeResponse(404, b'{"detail":"nope"}'))
    assert result.exit_code == 1
    assert not out.exists()


def test_unauthorized_exits_nonzero_without_file(tmp_path, monkeypatch):
    result, out = _run(tmp_path, monkeypatch, lambda *a, **k: FakeResponse(401, b'{"detail":"no"}'))
    assert result.exit_code == 1
    assert not out.exists()


def test_network_error_exits_nonzero_without_file(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")

    result, out = _run(tmp_path, monkeypatch, boom)
    assert result.exit_code == 1
    assert not out.exists()


def test_server_error_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    out = tmp_path / "out.csv"
    out.write_bytes(b"old")
    monkeypatch.setattr("kiddo_cli.api.client.requests.get", lambda *a, **k: FakeResponse(500, b"x"))
    result = runner.invoke(
        app, ["content", "export-items", "C_Foods", "--output", str(out), "--url", URL, "--token", "t"]
    )
    assert result.exit_code == 1
    assert out.read_bytes() == b"old"
