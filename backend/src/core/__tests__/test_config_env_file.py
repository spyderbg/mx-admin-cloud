"""Tests for the env file resolution in src.core.config (MX_ADMIN_ENV_FILE, XDG app.env, legacy .env).

Every test runs with HOME and XDG_CONFIG_HOME pointing at temporary directories and the
repository root patched to a temporary directory, so neither the real user configuration nor
the real repository `.env` is ever looked at. All values are synthetic.
"""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.core import config
from src.core.config import ENV_FILE_VAR, MissingEnvFileError, Settings, load_environment, resolve_env_file

DUMMY_KEY = "MX_ADMIN_TEST_DUMMY_KEY"
DUMMY_VALUE = "DUMMY_VALUE_123"
DUMMY_SECRET = "DUMMY_SECRET_VALUE_456"


@pytest.fixture(name="isolated")
def fixture_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate HOME, XDG_CONFIG_HOME, the repo root and the variables load_dotenv may set."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    repo = tmp_path / "repo"
    for directory in (home, xdg, repo):
        directory.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv(ENV_FILE_VAR, raising=False)
    monkeypatch.setattr(config, "_repo_root", lambda: repo)
    # setenv then delenv: monkeypatch removes anything load_dotenv adds to os.environ on undo.
    for name in (DUMMY_KEY, "PROJECT_NAME"):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    return tmp_path


def _write_env(path: Path, **values: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    return path


def _app_env(root: Path) -> Path:
    return root / "xdg" / "mx-admin-cloud" / "app.env"


def test_explicit_env_file_loads_its_value(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = _write_env(isolated / "elsewhere" / "custom.env", PROJECT_NAME=DUMMY_VALUE, **{DUMMY_KEY: DUMMY_VALUE})
    # An XDG file also exists: the explicit file must win.
    _write_env(_app_env(isolated), PROJECT_NAME="FROM_XDG")
    monkeypatch.setenv(ENV_FILE_VAR, str(explicit))

    path = load_environment()

    assert path == explicit
    assert os.environ[DUMMY_KEY] == DUMMY_VALUE
    assert Settings(_env_file=path).PROJECT_NAME == DUMMY_VALUE


def test_explicit_env_file_expands_home(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = _write_env(isolated / "home" / "my.env", PROJECT_NAME=DUMMY_VALUE)
    monkeypatch.setenv(ENV_FILE_VAR, "~/my.env")

    assert resolve_env_file() == explicit


def test_missing_explicit_env_file_raises_without_fallback(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = isolated / "nope" / "missing.env"
    # Fallback candidates exist and carry a dummy secret: none may be used or reported.
    _write_env(_app_env(isolated), OPENAI_API_KEY=DUMMY_SECRET)
    _write_env(isolated / "repo" / ".env", OPENAI_API_KEY=DUMMY_SECRET)
    monkeypatch.setenv(ENV_FILE_VAR, str(missing))

    with pytest.raises(MissingEnvFileError) as excinfo:
        load_environment()

    message = str(excinfo.value)
    assert isinstance(excinfo.value, FileNotFoundError)
    assert ENV_FILE_VAR in message
    assert str(missing) in message
    assert "unset" in message
    assert DUMMY_SECRET not in message
    assert os.environ.get("OPENAI_API_KEY") != DUMMY_SECRET


def test_xdg_app_env_is_used_without_override(isolated: Path) -> None:
    app_env = _write_env(_app_env(isolated), PROJECT_NAME=DUMMY_VALUE)
    _write_env(isolated / "repo" / ".env", PROJECT_NAME="FROM_LEGACY")

    path = load_environment()

    assert path == app_env
    assert Settings(_env_file=path).PROJECT_NAME == DUMMY_VALUE


def test_home_config_is_used_when_xdg_config_home_is_unset(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME")
    app_env = _write_env(isolated / "home" / ".config" / "mx-admin-cloud" / "app.env", PROJECT_NAME=DUMMY_VALUE)

    assert resolve_env_file() == app_env


def test_legacy_repo_env_is_used_when_no_external_file_exists(isolated: Path) -> None:
    legacy = _write_env(isolated / "repo" / ".env", PROJECT_NAME=DUMMY_VALUE)

    path = load_environment()

    assert path == legacy
    assert Settings(_env_file=path).PROJECT_NAME == DUMMY_VALUE


@pytest.mark.usefixtures("isolated")
def test_no_env_file_resolves_to_none() -> None:
    assert load_environment() is None
    assert Settings(_env_file=None).PROJECT_NAME == "FastAPI Backend"


def test_process_environment_beats_env_file(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_env(_app_env(isolated), PROJECT_NAME="FROM_FILE", **{DUMMY_KEY: "FROM_FILE"})
    monkeypatch.setenv("PROJECT_NAME", DUMMY_VALUE)
    monkeypatch.setenv(DUMMY_KEY, DUMMY_VALUE)

    path = load_environment()

    assert os.environ[DUMMY_KEY] == DUMMY_VALUE
    assert Settings(_env_file=path).PROJECT_NAME == DUMMY_VALUE


def test_resolution_does_not_depend_on_cwd(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = _write_env(isolated / "repo" / ".env", PROJECT_NAME=DUMMY_VALUE)
    # A decoy .env in the working directory must never be picked up.
    decoy_dir = isolated / "cwd"
    _write_env(decoy_dir / ".env", PROJECT_NAME="FROM_CWD")

    monkeypatch.chdir(isolated)
    first = resolve_env_file()
    monkeypatch.chdir(decoy_dir)
    second = resolve_env_file()

    assert first == second == legacy
    assert Settings(_env_file=second).PROJECT_NAME == DUMMY_VALUE


def test_settings_env_file_is_the_resolved_path() -> None:
    assert Settings.model_config["env_file"] == config.ENV_FILE
    assert config.ENV_FILE is None or config.ENV_FILE.is_absolute()


def test_repo_root_is_derived_from_the_module_location() -> None:
    assert config.REPO_ROOT == Path(config.__file__).resolve().parents[3]
    assert (config.REPO_ROOT / "backend" / "src" / "core" / "config.py").is_file()
