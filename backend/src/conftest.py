"""Backend-wide test isolation: no test may reach the real data locations (content, archive,
output, temp, users database) through the application settings."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.core.config import settings

# Settings that name a location the application writes to, and where each one points during a
# test. CONFIG_DIR stays real: tests read the tracked configuration files.
_DATA_LOCATIONS = {
    "CONTENT_DIR": "content",
    "ARCHIVE_DIR": "archive",
    "OUTPUT_DIR": "output",
    "TEMP_DIR": "temp",
    "DB_PATH": "data/users.db",
}


@pytest.fixture(scope="session")
def unpatched_data_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The stand-in for the real data locations: a path under the session's temporary
    directory that is never created by the fixtures."""
    return tmp_path_factory.mktemp("isolation") / "unpatched"


@pytest.fixture(autouse=True)
def isolate_data_locations(unpatched_data_root: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the real settings' data locations at `unpatched_data_root` for every test.

    A test that needs one of them sets it itself (monkeypatch, or `patch_fs_settings` for the
    file_system content provider). A call that escapes that patch then resolves a missing
    temporary path instead of the real `content/` tree or `data/users.db`: it can neither read
    nor write user data, and the test fails here if it created anything there.
    """
    for name, relative in _DATA_LOCATIONS.items():
        monkeypatch.setattr(settings, name, str(unpatched_data_root / relative))
    yield
    if unpatched_data_root.exists():
        leaked = sorted(str(p.relative_to(unpatched_data_root)) for p in unpatched_data_root.rglob("*"))
        shutil.rmtree(unpatched_data_root)
        pytest.fail(
            "the test wrote through the real settings' data locations instead of its own "
            f"temporary paths: {', '.join(leaked) or '(empty directory)'}",
        )
