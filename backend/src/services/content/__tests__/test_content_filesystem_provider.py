"""Unit tests for FileSystemContentProvider — pure filesystem logic (no HTTP, no external APIs)."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.services.content.models import (
    ArchiveUnavailableError,
    CategoryConflictError,
    CategoryNotFoundError,
    ContentOperationError,
    InvalidCategoryNameError,
)
from src.core.localization import LocalizedString
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


def _write_category_registry(content_root: Path, entries: list[tuple[str, str]]) -> None:
    """Write categories_ids.csv/categories_en.csv rows. *entries* is a list of (category_id, english_name)."""
    content_root.mkdir(parents=True, exist_ok=True)
    (content_root / "categories_ids.csv").write_text(
        "".join(f"{cid}\n" for cid, _ in entries), encoding="utf-8",
    )
    (content_root / "categories_en.csv").write_text(
        "".join(f"{name}\n" for _, name in entries), encoding="utf-8",
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

    assert [c.id for c in result.categories] == ["apple", "zebra"]


def test_list_categories_thumbnail_uri_empty_when_icon_missing(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    (content_root / "foods").mkdir()

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.list_categories()

    assert result.categories[0].thumbnail_uri == ""


def test_list_categories_thumbnail_uri_set_when_icon_exists(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    cat = content_root / "foods"
    cat.mkdir()
    (cat / "foods.png").write_bytes(b"icon")

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.list_categories()

    assert result.categories[0].thumbnail_uri == "/content/foods/foods.png"


def test_list_categories_thumbnail_uri_matches_non_png_extension(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    cat = content_root / "foods"
    cat.mkdir()
    (cat / "foods.jpg").write_bytes(b"icon")

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.list_categories()

    assert result.categories[0].thumbnail_uri == "/content/foods/foods.jpg"


def test_list_categories_error_when_root_missing(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path / "nonexistent"
        mock_settings.ARCHIVE_DIR = ""
        with pytest.raises(ContentOperationError):
            provider.list_categories()


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

    assert any(c.id == "foods" for c in result.categories)
    assert len(result.archived) == 1


# ---------------------------------------------------------------------------
# category_id_by_name
# ---------------------------------------------------------------------------

def test_category_id_by_name_invalid_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path
        with pytest.raises(InvalidCategoryNameError):
            provider.category_id_by_name("bad name!")


def test_category_id_by_name_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.category_id_by_name("foods")


def test_category_id_by_name_success(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    _write_category_registry(content_root, [("C_Foods", "Foods")])
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        assert provider.category_id_by_name("foods") == "C_Foods"


# ---------------------------------------------------------------------------
# category_details
# ---------------------------------------------------------------------------

def test_category_details_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.category_details("C_Foods")


def test_category_details_returns_items_and_csv(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    cat = content_root / "foods"
    cat.mkdir(parents=True)
    _write_category_registry(content_root, [("C_Foods", "Foods")])
    (content_root / "categories_fr.csv").write_text("Aliments\n", encoding="utf-8")
    (content_root / "descriptions_en.csv").write_text("Delicious meals and snacks.\n", encoding="utf-8")
    (cat / "items_en.csv").write_text("apple\nbanana\n", encoding="utf-8")
    (cat / "items_fr.csv").write_text("pomme\nbanane\n", encoding="utf-8")
    (cat / "items_ids.csv").write_text("I_Apple\nI_Banana\n", encoding="utf-8")

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.category_details("C_Foods")

    assert result.id == "C_Foods"
    assert result.name == LocalizedString({"en": "Foods", "fr": "Aliments"})
    assert result.description == "Delicious meals and snacks."
    assert len(result.items) == 2
    assert result.items[0].id == "I_Apple"
    assert result.items[0].name == LocalizedString({"en": "apple", "fr": "pomme"})
    assert result.items[1].id == "I_Banana"
    assert result.items[1].name == LocalizedString({"en": "banana", "fr": "banane"})


# ---------------------------------------------------------------------------
# create_category
# ---------------------------------------------------------------------------

def test_create_category_invalid_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = tmp_path
        with pytest.raises(InvalidCategoryNameError):
            provider.create_category("bad name!")


def test_create_category_already_exists(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryConflictError):
            provider.create_category("foods")


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
        with pytest.raises(ContentOperationError, match="Translation failed"):
            provider.create_category("animals")


# ---------------------------------------------------------------------------
# archive_category
# ---------------------------------------------------------------------------

def test_archive_category_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = tmp_path / "archive"
        with pytest.raises(CategoryNotFoundError):
            provider.archive_category("C_Nonexistent")


def test_archive_category_moves_to_archive(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    archive_root = tmp_path / "archive"
    (content_root / "foods").mkdir(parents=True)
    (content_root / "foods" / "apple").mkdir()
    _write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = archive_root
        result = provider.archive_category("C_Foods")

    assert result.archived is True
    assert not (content_root / "foods").exists()
    assert archive_root.is_dir()
    archived_folders = list(archive_root.iterdir())
    assert len(archived_folders) == 1
    assert archived_folders[0].name.startswith("foods_")


# ---------------------------------------------------------------------------
# rename_category
# ---------------------------------------------------------------------------

def test_rename_category_invalid_new_name(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    _write_category_registry(content_root, [("C_Foods", "Foods")])
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(InvalidCategoryNameError):
            provider.rename_category("C_Foods", "bad name!")


def test_rename_category_old_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.rename_category("C_Foods", "meals")


def test_rename_category_target_already_exists(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    (content_root / "meals").mkdir(parents=True)
    _write_category_registry(content_root, [("C_Foods", "Foods")])
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryConflictError):
            provider.rename_category("C_Foods", "meals")


def test_rename_category_success(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    cat = content_root / "foods"
    cat.mkdir(parents=True)
    (cat / "foods_en.mp3").write_bytes(b"en")
    (cat / "foods_fr.mp3").write_bytes(b"fr")
    (cat / "foods.png").write_bytes(b"icon")
    _write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = _make_fs_provider()
    with (
        patch("src.services.content.providers.file_system.settings") as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", return_value="repas"),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"new_audio"),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.rename_category("C_Foods", "meals")

    assert result.category == "meals"
    assert (content_root / "meals").is_dir()
    assert not (content_root / "foods").exists()
    assert not (content_root / "meals" / "foods_en.mp3").exists()
    assert (content_root / "meals" / "meals_en.mp3").exists()
    assert (content_root / "meals" / "meals.png").exists()


# ---------------------------------------------------------------------------
# remove_category
# ---------------------------------------------------------------------------

def test_remove_category_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        with pytest.raises(CategoryNotFoundError):
            provider.remove_category("C_Nonexistent")


def test_remove_category_permanently_deletes_when_no_archive_dir(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    _write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.remove_category("C_Foods")

    assert result.removed is True
    assert not (content_root / "foods").exists()


def test_remove_category_archives_when_archive_dir_set(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    archive_root = tmp_path / "archive"
    (content_root / "foods").mkdir(parents=True)
    _write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = archive_root
        mock_settings.ARCHIVE_DIR = str(archive_root)
        result = provider.remove_category("C_Foods")

    assert result.archived is True


# ---------------------------------------------------------------------------
# restore_category
# ---------------------------------------------------------------------------

def test_restore_category_empty_name(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = tmp_path / "archive"
        mock_settings.content_path = tmp_path / "content"
        with pytest.raises(InvalidCategoryNameError):
            provider.restore_category("")


def test_restore_category_archive_dir_missing(tmp_path: Path) -> None:
    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = tmp_path / "nonexistent"
        mock_settings.content_path = tmp_path / "content"
        with pytest.raises(ArchiveUnavailableError):
            provider.restore_category("foods")


def test_restore_category_no_archive_found(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    content_root = tmp_path / "content"
    _write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError, match="No archive found"):
            provider.restore_category("C_Foods")


def test_restore_category_conflict_with_existing(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "foods_1000").mkdir()
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    _write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        with pytest.raises(CategoryConflictError):
            provider.restore_category("C_Foods")


def test_restore_category_success(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archived = archive_root / "foods_1000"
    archived.mkdir(parents=True)
    (archived / "apple").mkdir()
    content_root = tmp_path / "content"
    _write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = _make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        result = provider.restore_category("C_Foods")

    assert result.restored is True
    assert result.name == "foods"
    assert result.from_archive == "foods_1000"
    assert (content_root / "foods").is_dir()
