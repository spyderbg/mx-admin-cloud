"""Backend test session: isolation from the operator's real env file, before any application import.

`src.core.config` resolves and loads one env file when it is imported: MX_ADMIN_ENV_FILE, then
`${XDG_CONFIG_HOME:-~/.config}/mx-admin-cloud/app.env`, then the repository-root `.env`. Pytest loads
this conftest (the rootdir's, next to pytest.ini) before any conftest or test module under `src/`, so
the module code below runs before that import. It points MX_ADMIN_ENV_FILE at an empty synthetic file
and XDG_CONFIG_HOME at an empty directory, both in a new temporary directory, whatever the inherited
environment says. The import-time lookup therefore stops at the synthetic file: it never reaches the
real app.env or the repository `.env`. Production code is unchanged; only the test process is affected.

Tests that exercise the lookup itself replace these variables with their own temporary ones
(`src/core/__tests__/test_config_env_file.py`). `src/core/__tests__/test_config_test_isolation.py`
checks this isolation, including in a child pytest whose HOME holds a stand-in user app.env.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# The names are repeated here: importing src.core.config for its constants would load an env file
# before the isolation below is in place.
_CONFIG_MODULE = "src.core.config"
_ENV_FILE_VAR = "MX_ADMIN_ENV_FILE"
_XDG_CONFIG_HOME_VAR = "XDG_CONFIG_HOME"

if _CONFIG_MODULE in sys.modules:
    # Fail closed: something imported the configuration before this conftest, so an env file may
    # already have been loaded into os.environ.
    raise RuntimeError(
        f"{_CONFIG_MODULE} was imported before backend/conftest.py isolated the test session from the "
        "real env file; run pytest from backend/ (or with backend/ as its rootdir) without plugins that "
        "import the application"
    )

SYNTHETIC_ROOT = Path(tempfile.mkdtemp(prefix="mx-admin-test-env-")).resolve()
SYNTHETIC_ENV_FILE = SYNTHETIC_ROOT / "synthetic.env"
SYNTHETIC_XDG_CONFIG_HOME = SYNTHETIC_ROOT / "xdg-config"

SYNTHETIC_XDG_CONFIG_HOME.mkdir()
SYNTHETIC_ENV_FILE.write_text("# Synthetic env file of the backend test session: it sets nothing.\n",
                              encoding="utf-8")
os.environ[_ENV_FILE_VAR] = str(SYNTHETIC_ENV_FILE)
os.environ[_XDG_CONFIG_HOME_VAR] = str(SYNTHETIC_XDG_CONFIG_HOME)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Remove the synthetic directory once the session is over."""
    del config
    shutil.rmtree(SYNTHETIC_ROOT, ignore_errors=True)
