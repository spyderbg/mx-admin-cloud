"""Tests for the /status and /list-files payloads built by src.core.tasks."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

from pathlib import Path

import pytest

from src.core import tasks
from src.core.config import settings


def test_status_masks_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # Any signed-in user, including an automatically registered viewer, can read /status.
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "s3cret-admin")
    monkeypatch.setattr(settings, "SECRET_KEY", "s3cret-jwt")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "s3cret-key")

    env = tasks.status()["env"]

    assert env["DEFAULT_ADMIN_PASSWORD"] == "***"
    assert env["SECRET_KEY"] == "***"
    assert env["GEMINI_API_KEY"] == "***"
    assert "s3cret" not in repr(env)


@pytest.fixture(name="content_tree")
def fixture_content_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A small content tree: category -> item -> files, plus a top-level registry file."""
    root = tmp_path / "content"
    (root / "drinks" / "water").mkdir(parents=True)
    (root / "drinks" / "water" / "water_01.png").write_bytes(b"")
    (root / "drinks" / "items_ids.csv").write_text("id\n")
    (root / "animals").mkdir()
    (root / "categories_ids.csv").write_text("id\n")
    monkeypatch.setattr(settings, "CONTENT_DIR", str(root))
    return root


def test_list_files_depth_one_lists_the_top_level_only(content_tree: Path) -> None:
    result = tasks.list_files(1)

    assert result["directory"] == content_tree
    assert result["depth"] == 1
    assert result["files"] == ["animals/", "categories_ids.csv", "drinks/"]


@pytest.mark.usefixtures("content_tree")
def test_list_files_depth_descends_one_folder_level_per_step() -> None:
    assert tasks.list_files(2)["files"] == [
        "animals/",
        "categories_ids.csv",
        "drinks/",
        "drinks/items_ids.csv",
        "drinks/water/",
    ]
    assert tasks.list_files(3)["files"] == [
        "animals/",
        "categories_ids.csv",
        "drinks/",
        "drinks/items_ids.csv",
        "drinks/water/",
        "drinks/water/water_01.png",
    ]


@pytest.mark.usefixtures("content_tree")
def test_list_files_depth_beyond_the_tree_lists_everything() -> None:
    assert tasks.list_files(50)["files"] == tasks.list_files(3)["files"]


@pytest.mark.usefixtures("content_tree")
def test_list_files_depth_below_one_is_treated_as_one() -> None:
    assert tasks.list_files(0)["files"] == tasks.list_files(1)["files"]


def test_list_files_does_not_follow_symlinks_out_of_the_content_tree(
    content_tree: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not content")
    (content_tree / "animals" / "escape").symlink_to(outside, target_is_directory=True)

    files = tasks.list_files(5)["files"]

    assert "animals/escape" in files
    assert not any("secret.txt" in f for f in files)


def test_list_files_reports_a_missing_content_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CONTENT_DIR", str(tmp_path / "missing"))

    assert "does not exist" in tasks.list_files(1)["error"]
