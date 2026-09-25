"""Shared helpers for the FileSystemContentProvider unit tests (test_content_filesystem_*.py)."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import src.services.content.providers.file_system as fs_package
from src.services.content.providers.file_system import (
    FileSystemContentProvider,
)


@contextmanager
def patch_fs_settings() -> Iterator[MagicMock]:
    """Patch `settings` in every file_system provider module with one shared mock.

    The provider is split across several modules (paths.py, images.py, voices.py, ...) that
    each import `settings` themselves, so patching a single module would leave the others
    reading the real settings. Modules are discovered, not listed, so moving code between
    them never silently un-patches anything.
    """
    mock_settings = MagicMock()
    with ExitStack() as stack:
        for module_info in pkgutil.iter_modules(fs_package.__path__, fs_package.__name__ + "."):
            module = importlib.import_module(module_info.name)
            if hasattr(module, "settings"):
                stack.enter_context(patch.object(module, "settings", mock_settings))
        yield mock_settings


def make_fs_provider() -> FileSystemContentProvider:
    """A file_system provider with an empty config; tests patch `settings` for its paths."""
    return FileSystemContentProvider(
        name="file_system",
        service_name="content",
        provider_name="file_system",
        provider_category="",
        enabled=True,
        priority=1,
        config={},
        config_path=Path("/tmp/fs.json"),
        base_dir=Path("/tmp"),
    )


def write_category_registry(content_root: Path, entries: list[tuple[str, str]]) -> None:
    """Write categories_ids.csv/categories_en.csv rows. *entries* is a list of (category_id, english_name)."""
    content_root.mkdir(parents=True, exist_ok=True)
    (content_root / "categories_ids.csv").write_text(
        "".join(f"{cid}\n" for cid, _ in entries), encoding="utf-8",
    )
    (content_root / "categories_en.csv").write_text(
        "".join(f"{name}\n" for _, name in entries), encoding="utf-8",
    )


def write_item_registry(category_path: Path, entries: list[tuple[str, str]]) -> None:
    """Write items_ids.csv/items_en.csv rows. *entries* is a list of (item_id, english_word)."""
    category_path.mkdir(parents=True, exist_ok=True)
    (category_path / "items_ids.csv").write_text(
        "".join(f"{iid}\n" for iid, _ in entries), encoding="utf-8",
    )
    (category_path / "items_en.csv").write_text(
        "".join(f"{word}\n" for _, word in entries), encoding="utf-8",
    )
    for _, word in entries:
        (category_path / word.lower()).mkdir(parents=True, exist_ok=True)
        (category_path / word.lower() / "01.png").write_bytes(b"fake-png")
