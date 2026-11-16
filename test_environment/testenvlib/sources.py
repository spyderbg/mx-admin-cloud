"""Which content/ and config/ directories `.testenv/` is built from.

By default the sandbox is cut from the repo's own `content/` (the curated 4-category
subset, see paths.KEPT_CATEGORY_ROWS) and `backend/config/`. Either can be swapped for
another directory. Resolution order, first match wins, per directory independently:

    1. CLI flag            --content-dir / --config-dir on seed / up / reset
    2. environment         TESTENV_CONTENT_SOURCE / TESTENV_CONFIG_SOURCE
    3. recorded choice     .testenv/sources.json, written by the last seed (and by `up
                           --config-dir`), so a later plain `up`/`reset` keeps using the
                           same directories instead of silently reverting
    4. repo default        content/ and backend/config/

A content directory other than the repo default is copied WHOLE: the curated subset
picks categories by CSV row index, which only means something for the repo's own
content/. A config directory is always copied and then neutralised the same way
(testenv_config.py), whichever one it is.

`down --purge` (or `reset` after deleting .testenv/sources.json) forgets the recorded
choice and returns to the repo defaults.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import paths

CONTENT_ENV_VAR = "TESTENV_CONTENT_SOURCE"
CONFIG_ENV_VAR = "TESTENV_CONFIG_SOURCE"
SOURCES_FILE = paths.TESTENV_DIR / "sources.json"


class SourceError(ValueError):
    """A content/config source directory that can't be used."""


@dataclass(frozen=True)
class Sources:
    content_dir: Path
    config_dir: Path

    @property
    def full_content(self) -> bool:
        """True when content_dir isn't the repo's content/ — copied whole, not subset."""
        return self.content_dir != paths.REAL_CONTENT_DIR.resolve()

    @property
    def custom_config(self) -> bool:
        return self.config_dir != paths.REAL_CONFIG_DIR.resolve()

    def describe(self) -> list[str]:
        content_how = "copied whole" if self.full_content else "curated 4-category subset"
        return [
            f"content: {self.content_dir}  ({content_how})",
            f"config:  {self.config_dir}  ({'custom' if self.custom_config else 'repo default'}, redirected to the stub)",
        ]


def load_recorded() -> dict[str, str]:
    """Return the recorded {"content_dir", "config_dir"} (either may be absent)."""
    try:
        data = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in ("content_dir", "config_dir") and isinstance(v, str)}


def record(sources: Sources) -> None:
    SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SOURCES_FILE.write_text(
        json.dumps({"content_dir": str(sources.content_dir), "config_dir": str(sources.config_dir)}, indent=2) + "\n",
        encoding="utf-8",
    )


def _pick(cli: str | os.PathLike[str] | None, env_var: str, recorded: str | None, default: Path) -> Path:
    for candidate in (cli, os.environ.get(env_var) or None, recorded):
        if candidate:
            return Path(candidate).expanduser().resolve()
    return default.resolve()


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _validate(kind: str, directory: Path) -> None:
    if not directory.is_dir():
        raise SourceError(f"{kind} directory not found: {directory}")
    # Seeding wipes and rewrites .testenv/; a source inside it (or containing it) would be
    # deleted or copied into itself.
    testenv = paths.TESTENV_DIR.resolve()
    if _overlaps(directory, testenv):
        raise SourceError(f"{kind} directory {directory} overlaps {testenv}, which seeding rebuilds")


def resolve(
        content_dir: str | os.PathLike[str] | None = None,
        config_dir: str | os.PathLike[str] | None = None,
) -> Sources:
    """Resolve both directories (CLI > env > recorded > default) and validate them."""
    recorded = load_recorded()
    sources = Sources(
        content_dir=_pick(content_dir, CONTENT_ENV_VAR, recorded.get("content_dir"), paths.REAL_CONTENT_DIR),
        config_dir=_pick(config_dir, CONFIG_ENV_VAR, recorded.get("config_dir"), paths.REAL_CONFIG_DIR),
    )
    _validate("content", sources.content_dir)
    _validate("config", sources.config_dir)
    return sources
