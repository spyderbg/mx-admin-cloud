"""Unit tests for the list commands' requests (kiddo_cli.commands.list_commands)."""

import pytest
import typer
from typer.testing import CliRunner

from kiddo_cli.commands import list_commands

_LOGGERS = [
    {"name": "requests", "provider": "struct_log", "enabled": True, "priority": 1},
    {"name": "trace", "provider": "struct_log", "enabled": False, "priority": 10},
]


@pytest.fixture(name="sent")
def fixture_sent(monkeypatch):
    """Skip authentication and record every GET the commands make instead of sending it."""
    calls = []

    def fake_get(path, token=None, **kwargs):
        calls.append({"path": path, "token": token, **kwargs})
        return _LOGGERS

    monkeypatch.setattr(list_commands, "run_with_auth", lambda url, token, run: run("test-token"))
    monkeypatch.setattr(list_commands, "internal_get", fake_get)
    return calls


@pytest.fixture(name="app")
def fixture_app():
    app = typer.Typer()
    list_commands.register(app)
    return app


@pytest.mark.parametrize(
    ("args", "params"),
    [([], None), (["--all"], {"all_loggers": "true"})],
)
def test_loggers_asks_for_disabled_loggers_only_with_all(app, sent, args, params):
    # GET /internal/loggers/ reads the "all_loggers" query parameter; any other name is ignored.
    result = CliRunner().invoke(app, ["loggers", *args])

    assert result.exit_code == 0, result.output
    assert sent == [{"path": "/loggers", "token": "test-token", "params": params}]
    assert "trace" in result.output and "disabled" in result.output
