"""Unit tests for FileSystemContentProvider — pure filesystem logic (no HTTP, no external APIs)."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.services.content.providers.file_system import FileSystemContentProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fs_provider() -> FileSystemContentProvider:
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


# ---------------------------------------------------------------------------
# _is_safe_category_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "foods",
    "Foods",
    "foods123",
    "my_foods",
    "FOODS",
    "a",
])
def test_is_safe_category_name_valid(name: str) -> None:
    assert FileSystemContentProvider._is_safe_category_name(name) is True


@pytest.mark.parametrize("name", [
    "",
    "  ",
    "foods items",
    "foods-items",
    "foods.items",
    "../traversal",
    "foods/items",
    "foods!",
])
def test_is_safe_category_name_invalid(name: str) -> None:
    assert FileSystemContentProvider._is_safe_category_name(name) is False


# ---------------------------------------------------------------------------
# _category_name_to_icon_prompt
# ---------------------------------------------------------------------------

def test_category_name_to_icon_prompt_simple() -> None:
    prompt = FileSystemContentProvider._category_name_to_icon_prompt("foods")
    assert "Foods" in prompt
    assert "children" in prompt.lower()


def test_category_name_to_icon_prompt_with_underscores() -> None:
    prompt = FileSystemContentProvider._category_name_to_icon_prompt("sea_animals")
    assert "Sea Animals" in prompt


def test_category_name_to_icon_prompt_empty() -> None:
    prompt = FileSystemContentProvider._category_name_to_icon_prompt("")
    assert prompt == ""


# ---------------------------------------------------------------------------
# _first_available_image_index
# ---------------------------------------------------------------------------

def test_first_available_image_index_empty_folder(tmp_path: Path) -> None:
    folder = tmp_path / "item"
    folder.mkdir()
    assert FileSystemContentProvider._first_available_image_index(folder) == 1


def test_first_available_image_index_skips_existing(tmp_path: Path) -> None:
    folder = tmp_path / "item"
    folder.mkdir()
    (folder / "01.png").write_bytes(b"")
    (folder / "02.png").write_bytes(b"")
    assert FileSystemContentProvider._first_available_image_index(folder) == 3


def test_first_available_image_index_skips_jpg_too(tmp_path: Path) -> None:
    folder = tmp_path / "item"
    folder.mkdir()
    (folder / "01.jpg").write_bytes(b"")
    assert FileSystemContentProvider._first_available_image_index(folder) == 2


# ---------------------------------------------------------------------------
# _list_archived_entries
# ---------------------------------------------------------------------------

def test_list_archived_entries_empty_when_archive_dir_not_set() -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.ARCHIVE_DIR = ""
        entries = provider._list_archived_entries()
    assert entries == []


def test_list_archived_entries_empty_when_archive_root_missing(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.ARCHIVE_DIR = str(tmp_path / "nonexistent")
        mock_settings.archive_path = tmp_path / "nonexistent"
        entries = provider._list_archived_entries()
    assert entries == []


def test_list_archived_entries_returns_matching_folders(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "foods_1700000000").mkdir()
    (archive_root / "drinks_1600000000").mkdir()
    (archive_root / "not_a_match").mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.ARCHIVE_DIR = str(archive_root)
        mock_settings.archive_path = archive_root
        entries = provider._list_archived_entries()
    assert len(entries) == 2
    base_names = {e.base_name for e in entries}
    assert base_names == {"foods", "drinks"}
    assert entries[0].archived_at > entries[1].archived_at


def test_list_archived_entries_skips_dotfiles(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / ".hidden_1000").mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.ARCHIVE_DIR = str(archive_root)
        mock_settings.archive_path = archive_root
        entries = provider._list_archived_entries()
    assert entries == []


# ---------------------------------------------------------------------------
# _archive_folders_for_base
# ---------------------------------------------------------------------------

def test_archive_folders_for_base_returns_matching(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "foods_1000").mkdir()
    (archive_root / "foods_2000").mkdir()
    (archive_root / "drinks_1000").mkdir()
    results = FileSystemContentProvider._archive_folders_for_base(archive_root, "foods")
    folder_names = [r[0] for r in results]
    assert set(folder_names) == {"foods_1000", "foods_2000"}
    assert results[0][1] > results[1][1]


def test_archive_folders_for_base_empty_when_none_match(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "drinks_1000").mkdir()
    results = FileSystemContentProvider._archive_folders_for_base(archive_root, "foods")
    assert results == []


# ---------------------------------------------------------------------------
# list_categories
# ---------------------------------------------------------------------------

def test_list_categories_returns_sorted_names(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    (content_root / "zebra").mkdir()
    (content_root / "apple").mkdir()
    (content_root / ".hidden").mkdir()

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.list_categories()

    assert result.categories == ["apple", "zebra"]
    assert result.error is None


def test_list_categories_error_when_root_missing(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path / "nonexistent"
        mock_settings.ARCHIVE_DIR = ""
        result = provider.list_categories()

    assert result.error is not None
    assert result.categories == []


def test_list_categories_archived_only_skips_content_root(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "foods_1000").mkdir()

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.ARCHIVE_DIR = str(archive_root)
        mock_settings.archive_path = archive_root
        result = provider.list_categories(archived_only=True)

    assert result.categories == []
    assert len(result.archived) == 1


def test_list_categories_include_archived(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    (content_root / "foods").mkdir()
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "drinks_1000").mkdir()

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = str(archive_root)
        mock_settings.archive_path = archive_root
        result = provider.list_categories(include_archived=True)

    assert "foods" in result.categories
    assert len(result.archived) == 1


# ---------------------------------------------------------------------------
# category_details
# ---------------------------------------------------------------------------

def test_category_details_invalid_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path
        result = provider.category_details("bad name!")

    assert result.error == "Invalid category name"


def test_category_details_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        result = provider.category_details("foods")

    assert result.error == "Category not found"


def test_category_details_returns_objects_and_csv(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    cat = content_root / "foods"
    cat.mkdir(parents=True)
    (cat / "apple").mkdir()
    (cat / "banana").mkdir()
    (cat / "images_en.csv").write_text("apple\nbanana\n", encoding="utf-8")
    (cat / "images_fr.csv").write_text("pomme\nbanane\n", encoding="utf-8")
    (cat / "images_names.csv").write_text("I_Apple\nI_Banana\n", encoding="utf-8")

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.category_details("foods")

    assert result.name == "foods"
    assert sorted(result.objects) == ["apple", "banana"]
    assert result.images_by_lang["en"] == ["apple", "banana"]
    assert result.images_by_lang["fr"] == ["pomme", "banane"]
    assert result.images_names == ["I_Apple", "I_Banana"]


# ---------------------------------------------------------------------------
# create_category
# ---------------------------------------------------------------------------

def test_create_category_invalid_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path
        result = provider.create_category("bad name!")

    assert result.error is not None


def test_create_category_already_exists(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        result = provider.create_category("foods")

    assert result.error is not None
    assert "already exists" in result.error


def test_create_category_success(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = _make_fs_provider()
    with (
        patch("src.services.content.providers.file_system.settings") as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", return_value="translated"),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
        patch.object(FileSystemContentProvider, "_generate_icon", return_value=b"icon"),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_category("animals")

    assert result.category == "animals"
    assert result.error is None
    assert (content_root / "animals").is_dir()
    assert (content_root / "animals" / "animals_en.mp3").exists()
    assert (content_root / "animals" / "animals_fr.mp3").exists()
    assert (content_root / "animals" / "animals.png").exists()


def test_create_category_translation_failure(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = _make_fs_provider()
    with (
        patch("src.services.content.providers.file_system.settings") as mock_settings,
        patch.object(
            FileSystemContentProvider,
            "_translate_word",
            side_effect=httpx.HTTPError("conn failed"),
        ),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
        patch.object(FileSystemContentProvider, "_generate_icon", return_value=b"icon"),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_category("animals")

    assert result.error is not None
    assert "Translation failed" in result.error


# ---------------------------------------------------------------------------
# archive_category
# ---------------------------------------------------------------------------

def test_archive_category_invalid_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path
        mock_settings.archive_path = tmp_path / "archive"
        result = provider.archive_category("bad name!")

    assert result.archived is False
    assert result.error is not None


def test_archive_category_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = tmp_path / "archive"
        result = provider.archive_category("nonexistent")

    assert result.archived is False
    assert result.error == "Category not found"


def test_archive_category_moves_to_archive(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    archive_root = tmp_path / "archive"
    (content_root / "foods").mkdir(parents=True)
    (content_root / "foods" / "apple").mkdir()

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = archive_root
        result = provider.archive_category("foods")

    assert result.archived is True
    assert not (content_root / "foods").exists()
    assert archive_root.is_dir()
    archived_folders = list(archive_root.iterdir())
    assert len(archived_folders) == 1
    assert archived_folders[0].name.startswith("foods_")


# ---------------------------------------------------------------------------
# rename_category
# ---------------------------------------------------------------------------

def test_rename_category_invalid_old_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path
        result = provider.rename_category("bad name!", "newname")

    assert result.error is not None


def test_rename_category_invalid_new_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path
        result = provider.rename_category("foods", "bad name!")

    assert result.error is not None


def test_rename_category_old_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        result = provider.rename_category("foods", "meals")

    assert result.error == "Category not found"


def test_rename_category_target_already_exists(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    (content_root / "meals").mkdir(parents=True)
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        result = provider.rename_category("foods", "meals")

    assert "already exists" in result.error


def test_rename_category_success(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    cat = content_root / "foods"
    cat.mkdir(parents=True)
    (cat / "foods_en.mp3").write_bytes(b"en")
    (cat / "foods_fr.mp3").write_bytes(b"fr")
    (cat / "foods.png").write_bytes(b"icon")

    provider = _make_fs_provider()
    with (
        patch("src.services.content.providers.file_system.settings") as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", return_value="repas"),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"new_audio"),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.rename_category("foods", "meals")

    assert result.category == "meals"
    assert result.error is None
    assert (content_root / "meals").is_dir()
    assert not (content_root / "foods").exists()
    assert not (content_root / "meals" / "foods_en.mp3").exists()
    assert (content_root / "meals" / "meals_en.mp3").exists()
    assert (content_root / "meals" / "meals.png").exists()


# ---------------------------------------------------------------------------
# remove_category
# ---------------------------------------------------------------------------

def test_remove_category_invalid_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path
        mock_settings.ARCHIVE_DIR = ""
        result = provider.remove_category("bad name!")

    assert result.error is not None


def test_remove_category_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.remove_category("nonexistent")

    assert result.error == "Category not found"


def test_remove_category_permanently_deletes_when_no_archive_dir(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.remove_category("foods")

    assert result.removed is True
    assert not (content_root / "foods").exists()


def test_remove_category_archives_when_archive_dir_set(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    archive_root = tmp_path / "archive"
    (content_root / "foods").mkdir(parents=True)

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = archive_root
        mock_settings.ARCHIVE_DIR = str(archive_root)
        result = provider.remove_category("foods")

    assert result.archived is True


# ---------------------------------------------------------------------------
# restore_category
# ---------------------------------------------------------------------------

def test_restore_category_empty_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = tmp_path / "archive"
        mock_settings.content_path = tmp_path / "content"
        result = provider.restore_category("")

    assert result.restored is False


def test_restore_category_archive_dir_missing(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = tmp_path / "nonexistent"
        mock_settings.content_path = tmp_path / "content"
        result = provider.restore_category("foods")

    assert result.restored is False
    assert result.error == "Archive directory does not exist"


def test_restore_category_no_archive_found(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        result = provider.restore_category("foods")

    assert result.restored is False
    assert "No archive found" in result.error


def test_restore_category_conflict_with_existing(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "foods_1000").mkdir()
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        result = provider.restore_category("foods")

    assert result.restored is False
    assert "already exists" in result.error


def test_restore_category_success(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archived = archive_root / "foods_1000"
    archived.mkdir(parents=True)
    (archived / "apple").mkdir()
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        result = provider.restore_category("foods")

    assert result.restored is True
    assert result.name == "foods"
    assert result.from_archive == "foods_1000"
    assert (content_root / "foods").is_dir()


def test_restore_category_by_exact_archive_folder(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    (archive_root / "foods_1000").mkdir(parents=True)
    (archive_root / "foods_2000").mkdir()
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        result = provider.restore_category("foods_1000")

    assert result.restored is True
    assert result.from_archive == "foods_1000"
