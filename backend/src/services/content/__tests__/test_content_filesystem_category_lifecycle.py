"""Unit tests for FileSystemContentProvider — category details, rename, remove, restore, archived-category deletion."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.content.models import (
    ArchiveUnavailableError,
    CategoryConflictError,
    CategoryNotFoundError,
    InvalidCategoryNameError,
)
from src.core.localization import LocalizedString
from src.services.content.providers.file_system import (
    FileSystemContentProvider,
)
from src.services.content.__tests__.content_fs_helpers import (
    make_fs_provider,
    write_category_registry,
)


# ---------------------------------------------------------------------------
# update_category_details
# ---------------------------------------------------------------------------

def test_update_category_details_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(CategoryNotFoundError):
            provider.update_category_details(
                "C_Foods", LocalizedString({"en": "Meals"}), LocalizedString({}),
            )


def test_update_category_details_writes_name_and_description(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])
    (content_root / "categories_fr.csv").write_text("Aliments\n", encoding="utf-8")

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.update_category_details(
            "C_Foods",
            LocalizedString({"en": "Meals", "fr": "Repas"}),
            LocalizedString({"en": "Tasty meals", "fr": "Des repas savoureux"}),
        )

    assert result.category == "foods"
    assert (content_root / "categories_en.csv").read_text(encoding="utf-8") == "Meals\n"
    assert (content_root / "categories_fr.csv").read_text(encoding="utf-8") == "Repas\n"
    assert (content_root / "descriptions_en.csv").read_text(encoding="utf-8") == "Tasty meals\n"
    assert (content_root / "descriptions_fr.csv").read_text(encoding="utf-8") == "Des repas savoureux\n"


def test_update_category_details_preserves_other_rows(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    (content_root / "drinks").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods"), ("C_Drinks", "Drinks")])

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.update_category_details(
            "C_Drinks", LocalizedString({"en": "Beverages"}), LocalizedString({"en": "Cold drinks"}),
        )

    assert (content_root / "categories_en.csv").read_text(encoding="utf-8") == "Foods\nBeverages\n"
    assert (content_root / "descriptions_en.csv").read_text(encoding="utf-8") == '""\nCold drinks\n'


# ---------------------------------------------------------------------------
# rename_category
# ---------------------------------------------------------------------------

def test_rename_category_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.rename_category("C_Foods", "C_Meals")


def test_rename_category_invalid_new_id(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])
    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(InvalidCategoryNameError):
            provider.rename_category("C_Foods", "not a valid id!")


def test_rename_category_conflict(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    (content_root / "drinks").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods"), ("C_Drinks", "Drinks")])
    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(CategoryConflictError):
            provider.rename_category("C_Foods", "C_Drinks")

    assert (content_root / "categories_ids.csv").read_text(encoding="utf-8") == "C_Foods\nC_Drinks\n"
    assert (content_root / "foods").is_dir()
    assert (content_root / "drinks").is_dir()


def test_rename_category_moves_folder_and_updates_id_only(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    (content_root / "drinks").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods"), ("C_Drinks", "Drinks")])
    (content_root / "categories_fr.csv").write_text("Aliments\nBoissons\n", encoding="utf-8")

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.rename_category("C_Foods", "C_Meals")

    assert result.category == "meals"
    assert not (content_root / "foods").exists()
    assert (content_root / "meals").is_dir()
    assert (content_root / "categories_ids.csv").read_text(encoding="utf-8") == "C_Meals\nC_Drinks\n"
    # categories_{lang}.csv must be untouched by the rename step.
    assert (content_root / "categories_en.csv").read_text(encoding="utf-8") == "Foods\nDrinks\n"
    assert (content_root / "categories_fr.csv").read_text(encoding="utf-8") == "Aliments\nBoissons\n"


def test_rename_category_list_categories_returns_new_id_not_folder_name(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.rename_category("C_Foods", "C_Meals")
        result = provider.list_categories()

    assert [c.id for c in result.categories] == ["C_Meals"]
    assert result.categories[0].name.to_dict() == {"en": "Foods"}


def test_rename_category_details_resolve_by_new_id(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])
    (content_root / "descriptions_en.csv").write_text("Tasty meals\n", encoding="utf-8")

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.rename_category("C_Foods", "C_Meals")

        with pytest.raises(CategoryNotFoundError):
            provider.category_details("C_Foods")

        details = provider.category_details("C_Meals")

    assert details.id == "C_Meals"
    assert details.name.to_dict() == {"en": "Foods"}
    assert details.description.to_dict() == {"en": "Tasty meals"}


def test_rename_category_renames_prefixed_voice_and_icon_files(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    foods = content_root / "foods"
    foods.mkdir(parents=True)
    (foods / "foods_en.mp3").write_bytes(b"english voice")
    (foods / "foods_fr.mp3").write_bytes(b"french voice")
    (foods / "foods.png").write_bytes(b"icon")
    (foods / "foods_01.png").write_bytes(b"extra image")
    write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        provider.rename_category("C_Foods", "C_Meals")

    meals = content_root / "meals"
    assert (meals / "meals_en.mp3").read_bytes() == b"english voice"
    assert (meals / "meals_fr.mp3").read_bytes() == b"french voice"
    assert (meals / "meals.png").read_bytes() == b"icon"
    assert (meals / "meals_01.png").read_bytes() == b"extra image"
    assert not (meals / "foods_en.mp3").exists()
    assert not (meals / "foods.png").exists()


def test_rename_category_same_id_is_noop(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        result = provider.rename_category("C_Foods", "C_Foods")

    assert result.category == "foods"
    assert (content_root / "categories_ids.csv").read_text(encoding="utf-8") == "C_Foods\n"
    assert (content_root / "foods").is_dir()


# ---------------------------------------------------------------------------
# remove_category
# ---------------------------------------------------------------------------

def test_remove_category_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        with pytest.raises(CategoryNotFoundError):
            provider.remove_category("C_Nonexistent")


def test_remove_category_permanently_deletes_when_no_archive_dir(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.remove_category("C_Foods")

    assert result.removed is True
    assert not (content_root / "foods").exists()


def test_remove_category_deletes_registry_rows(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(
        content_root, [("C_Animals", "Animals"), ("C_Foods", "Foods"), ("C_Colors", "Colors")],
    )

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.remove_category("C_Foods")

    assert (content_root / "categories_ids.csv").read_text(encoding="utf-8").split() == [
        "C_Animals", "C_Colors",
    ]
    assert (content_root / "categories_en.csv").read_text(encoding="utf-8").split() == [
        "Animals", "Colors",
    ]


def test_remove_then_create_same_category_leaves_one_registry_row(tmp_path: Path) -> None:
    """A duplicate id row would make _category_row_index() resolve to the stale first row."""
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = make_fs_provider()
    with (
        patch("src.services.content.providers.file_system.settings") as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", return_value="translated"),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
        patch.object(FileSystemContentProvider, "_generate_image", return_value=(b"icon", ".png")),
    ):
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.remove_category("C_Foods")
        provider.create_category("foods")

    assert (content_root / "categories_ids.csv").read_text(encoding="utf-8").split() == ["C_Foods"]


def test_remove_category_archives_when_archive_dir_set(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    archive_root = tmp_path / "archive"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = make_fs_provider()
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
    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = tmp_path / "archive"
        mock_settings.content_path = tmp_path / "content"
        with pytest.raises(InvalidCategoryNameError):
            provider.restore_category("")


def test_restore_category_archive_dir_missing(tmp_path: Path) -> None:
    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = tmp_path / "nonexistent"
        mock_settings.content_path = tmp_path / "content"
        with pytest.raises(ArchiveUnavailableError):
            provider.restore_category("foods")


def test_restore_category_no_archive_found(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    content_root = tmp_path / "content"
    write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError, match="No archive found"):
            provider.restore_category("foods")


def test_restore_category_conflict_with_existing(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "foods_1000").mkdir()
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        with pytest.raises(CategoryConflictError):
            provider.restore_category("foods")


def test_restore_category_success(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archived = archive_root / "foods_1000"
    archived.mkdir(parents=True)
    (archived / "apple").mkdir()
    content_root = tmp_path / "content"
    content_root.mkdir(parents=True, exist_ok=True)

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.restore_category("foods")

    assert result.restored is True
    assert result.name == "foods"
    assert result.from_archive == "foods_1000"
    assert (content_root / "foods" / "apple").is_dir()
    # The snapshot is consumed, so restoring twice cannot conflict with itself.
    assert not archived.exists()


def test_restore_category_accepts_full_archive_folder_name(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    (archive_root / "foods_1000").mkdir(parents=True)
    (archive_root / "foods_2000").mkdir(parents=True)
    content_root = tmp_path / "content"
    content_root.mkdir(parents=True, exist_ok=True)

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        result = provider.restore_category("foods_1000")

    assert result.from_archive == "foods_1000"
    assert (archive_root / "foods_2000").is_dir()


def test_restore_category_picks_most_recent_archive(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    (archive_root / "foods_1000").mkdir(parents=True)
    (archive_root / "foods_2000").mkdir(parents=True)
    content_root = tmp_path / "content"
    content_root.mkdir(parents=True, exist_ok=True)

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        result = provider.restore_category("foods")

    assert result.from_archive == "foods_2000"


def test_restore_category_rebuilds_registry_rows_from_manifest(tmp_path: Path) -> None:
    """Archiving deletes the root CSV rows, so restore must rebuild them from the manifest."""
    content_root = tmp_path / "content"
    archive_root = tmp_path / "archive"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Animals", "Animals"), ("C_Foods", "Foods")])
    (content_root / "categories_fr.csv").write_text("Animaux\nAliments\n", encoding="utf-8")

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = archive_root
        mock_settings.ARCHIVE_DIR = str(archive_root)
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        provider.remove_category("C_Foods")

        ids_after_archive = (content_root / "categories_ids.csv").read_text(encoding="utf-8")
        assert "C_Foods" not in ids_after_archive
        assert "C_Animals" in ids_after_archive

        provider.restore_category("foods")

    assert (content_root / "categories_ids.csv").read_text(encoding="utf-8").split() == [
        "C_Animals", "C_Foods",
    ]
    assert (content_root / "categories_en.csv").read_text(encoding="utf-8").split() == [
        "Animals", "Foods",
    ]
    assert (content_root / "categories_fr.csv").read_text(encoding="utf-8").split() == [
        "Animaux", "Aliments",
    ]
    # The manifest is an archive-only artifact and must not linger in the content dir.
    assert not (content_root / "foods" / "_category.json").exists()


# ---------------------------------------------------------------------------
# delete_archived_category
# ---------------------------------------------------------------------------

def test_delete_archived_category_empty_name(tmp_path: Path) -> None:
    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = tmp_path / "archive"
        with pytest.raises(InvalidCategoryNameError):
            provider.delete_archived_category("")


def test_delete_archived_category_archive_dir_missing(tmp_path: Path) -> None:
    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = tmp_path / "nonexistent"
        with pytest.raises(ArchiveUnavailableError):
            provider.delete_archived_category("foods")


def test_delete_archived_category_no_archive_found(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        with pytest.raises(CategoryNotFoundError, match="No archive found"):
            provider.delete_archived_category("foods")


def test_delete_archived_category_removes_folder(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archived = archive_root / "foods_1000"
    archived.mkdir(parents=True)
    (archived / "apple").mkdir()

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        result = provider.delete_archived_category("foods")

    assert result.deleted is True
    assert result.name == "foods"
    assert result.archive_folder == "foods_1000"
    assert not archived.exists()


def test_delete_archived_category_accepts_full_archive_folder_name(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    (archive_root / "foods_1000").mkdir(parents=True)
    (archive_root / "foods_2000").mkdir(parents=True)

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        result = provider.delete_archived_category("foods_1000")

    assert result.archive_folder == "foods_1000"
    assert not (archive_root / "foods_1000").exists()
    # The other snapshot for the same base name must be left alone.
    assert (archive_root / "foods_2000").is_dir()


def test_delete_archived_category_picks_most_recent_archive(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    (archive_root / "foods_1000").mkdir(parents=True)
    (archive_root / "foods_2000").mkdir(parents=True)

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        result = provider.delete_archived_category("foods")

    assert result.archive_folder == "foods_2000"
    assert (archive_root / "foods_1000").is_dir()
    assert not (archive_root / "foods_2000").exists()


def test_delete_archived_category_does_not_modify_registry_csvs(tmp_path: Path) -> None:
    """Registry rows were already dropped by archive_category(); deletion must not touch the CSVs."""
    content_root = tmp_path / "content"
    archive_root = tmp_path / "archive"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(
        content_root, [("C_Animals", "Animals"), ("C_Foods", "Foods")],
    )

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = archive_root
        mock_settings.ARCHIVE_DIR = str(archive_root)
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.archive_category("C_Foods")

        ids_after_archive = (content_root / "categories_ids.csv").read_text(encoding="utf-8")
        names_after_archive = (content_root / "categories_en.csv").read_text(encoding="utf-8")
        assert ids_after_archive.split() == ["C_Animals"]
        assert names_after_archive.split() == ["Animals"]

        result = provider.delete_archived_category("foods")

    assert result.deleted is True
    assert not (archive_root / result.archive_folder).exists()
    # The archived snapshot is gone permanently, but the live registry CSVs (already
    # missing the archived row) must remain exactly as they were after archiving.
    assert (content_root / "categories_ids.csv").read_text(encoding="utf-8") == ids_after_archive
    assert (content_root / "categories_en.csv").read_text(encoding="utf-8") == names_after_archive


def test_delete_archived_category_after_restore_cannot_find_snapshot(tmp_path: Path) -> None:
    """restore_category() consumes the archive folder, so a later delete must report not-found."""
    archive_root = tmp_path / "archive"
    archived = archive_root / "foods_1000"
    archived.mkdir(parents=True)
    content_root = tmp_path / "content"
    content_root.mkdir(parents=True, exist_ok=True)

    provider = make_fs_provider()
    with patch("src.services.content.providers.file_system.settings") as mock_settings:
        mock_settings.archive_path = archive_root
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.restore_category("foods")

        with pytest.raises(CategoryNotFoundError, match="No archive found"):
            provider.delete_archived_category("foods")
