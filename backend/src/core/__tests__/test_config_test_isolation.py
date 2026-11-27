"""Regression tests: the backend test session never loads the operator's real env file (PR #89 review).

`src.core.config` resolves and loads one env file when it is imported. `backend/conftest.py` points
MX_ADMIN_ENV_FILE at an empty synthetic file, and XDG_CONFIG_HOME at an empty temporary directory,
before pytest imports anything under `src/`. These tests check that state. They also run this
module's probe in a child pytest whose HOME holds a stand-in user app.env with a sentinel value,
which shows that the sentinel never reaches the test process. All values are synthetic, and no real
configuration file is ever opened.
"""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.core import config
from src.core.config import APP_CONFIG_DIR_NAME, APP_ENV_FILE_NAME, ENV_FILE_VAR, Settings

BACKEND_DIR = Path(__file__).resolve().parents[3]
SENTINEL_KEY = "MX_ADMIN_ISOLATION_SENTINEL"
SENTINEL_VALUE = "SYNTHETIC_SENTINEL_FROM_A_USER_APP_ENV"
# Set only for the child pytest: the stand-in user app.env that must not be loaded.
PROBE_VAR = "MX_ADMIN_ISOLATION_PROBE_FILE"


def _is_temporary(path: Path) -> bool:
    return path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


# Variables are read with os.environ.get() throughout: a failing lookup must not print the environment.


def test_import_time_env_file_is_the_synthetic_session_file() -> None:
    env_file = config.ENV_FILE

    assert env_file is not None, "no env file was resolved at import: backend/conftest.py did not run first"
    assert str(env_file) == os.environ.get(ENV_FILE_VAR)
    assert _is_temporary(env_file)
    assert Settings.model_config["env_file"] == env_file
    # It defines nothing: the session sees the settings a gate sandbox sees without an env file.
    assert not [line for line in env_file.read_text(encoding="utf-8").splitlines() if "=" in line]


def test_session_never_points_at_a_real_configuration() -> None:
    # Computed only, never opened: the default locations of the operator's configuration.
    user_app_env = Path.home() / ".config" / APP_CONFIG_DIR_NAME / APP_ENV_FILE_NAME
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "")

    assert config.ENV_FILE not in (user_app_env, config.REPO_ROOT / ".env")
    assert xdg_config_home, "XDG_CONFIG_HOME is not set: backend/conftest.py did not run first"
    assert _is_temporary(Path(xdg_config_home))
    assert not (Path(xdg_config_home) / APP_CONFIG_DIR_NAME).exists()


def test_probe_session_environment() -> None:
    """Runs in this session and, alone, in the child pytest of the test below.

    The checks compare single values only, so a failure never prints the process environment.
    """
    sentinel_loaded = os.environ.get(SENTINEL_KEY) == SENTINEL_VALUE
    settings_loaded = config.settings.PROJECT_NAME == SENTINEL_VALUE
    stand_in = os.environ.get(PROBE_VAR)
    stand_in_resolved = bool(stand_in) and config.ENV_FILE == Path(stand_in)

    assert not sentinel_loaded, "the user app.env was loaded into os.environ"
    assert not settings_loaded, "the user app.env reached the settings"
    assert not stand_in_resolved, "the user app.env was resolved at import"


@pytest.mark.parametrize("lookup", ["home", "xdg-config-home", "explicit-variable"])
def test_suite_never_loads_a_user_app_env(lookup: str, tmp_path: Path) -> None:
    """A child pytest, with a user app.env where the inherited environment says, never loads it."""
    home = tmp_path / "home"
    user_app_env = home / ".config" / APP_CONFIG_DIR_NAME / APP_ENV_FILE_NAME
    user_app_env.parent.mkdir(parents=True)
    user_app_env.write_text(f"{SENTINEL_KEY}={SENTINEL_VALUE}\nPROJECT_NAME={SENTINEL_VALUE}\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if key not in (ENV_FILE_VAR, "XDG_CONFIG_HOME") and not key.startswith("PYTEST_")}
    env.update({"HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1", PROBE_VAR: str(user_app_env)})
    if lookup == "xdg-config-home":
        env["XDG_CONFIG_HOME"] = str(home / ".config")
    elif lookup == "explicit-variable":
        env[ENV_FILE_VAR] = str(user_app_env)
    probe = f"{Path(__file__).resolve().relative_to(BACKEND_DIR)}::test_probe_session_environment"

    child = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", probe],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=300, check=False,
    )

    assert child.returncode == 0, child.stdout[-4000:] + child.stderr[-4000:]
    assert "1 passed" in child.stdout
