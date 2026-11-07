"""Unit tests for the common commands' requests (kiddo_cli.commands.common_commands)."""

import json

import pytest
import typer
from typer.testing import CliRunner

from kiddo_cli.commands import common_commands


@pytest.fixture(name="sent")
def fixture_sent(monkeypatch):
    """Skip authentication and record every POST the commands make instead of sending it."""
    calls = []

    def fake_post(path, token=None, **kwargs):
        calls.append({"path": path, "token": token, **kwargs})
        return {"files": ["drinks/"], "depth": (kwargs.get("json") or {}).get("depth")}

    monkeypatch.setattr(common_commands, "run_with_auth", lambda url, token, run: run("test-token"))
    monkeypatch.setattr(common_commands, "post", fake_post)
    return calls


@pytest.fixture(name="app")
def fixture_app():
    app = typer.Typer()
    common_commands.register(app)
    return app


@pytest.mark.parametrize(("args", "depth"), [([], 1), (["--depth", "3"], 3)])
def test_list_files_sends_depth_in_the_json_body(app, sent, args, depth):
    result = CliRunner().invoke(app, ["list-files", *args])

    assert result.exit_code == 0, result.output
    assert sent == [{"path": "/list-files", "token": "test-token", "json": {"depth": depth}}]
    assert json.loads(result.output)["depth"] == depth
