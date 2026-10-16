"""Unit tests for FileSystemContentProvider — validation helpers, categories (list/create/archive), category images."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.services.content.models import (
    CategoryConflictError,
    CategoryNotFoundError,
    ContentOperationError,
    InvalidCategoryNameError,
)
from src.core.localization import LocalizedString
from src.services.content.providers.file_system import (
    CreationMethod,
    FileSystemContentProvider,
)
from src.services.content.__tests__.content_fs_helpers import (
    patch_fs_settings,
    make_fs_provider,
    write_category_registry,
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
# _write_image_bytes
# ---------------------------------------------------------------------------

def test_write_image_bytes_success(tmp_path: Path) -> None:
    target = tmp_path / "foods_01.png"
    FileSystemContentProvider._write_image_bytes(target, b"fake-png")
    assert target.read_bytes() == b"fake-png"


def test_write_image_bytes_missing_parent_directory_names_the_cause(tmp_path: Path) -> None:
    """A vanished parent dir (e.g. an unmounted Docker volume) must not read as a generic OSError."""
    target = tmp_path / "not_mounted" / "foods_01.png"
    with patch_fs_settings() as mock_settings:
        mock_settings.CONTENT_DIR = "/app/content"
        with pytest.raises(ContentOperationError, match="does not exist"):
            FileSystemContentProvider._write_image_bytes(target, b"fake-png")


def test_write_image_bytes_permission_denied_names_the_cause(tmp_path: Path) -> None:
    """A UID mismatch between host and container surfaces as PermissionError — call it out by name."""
    target = tmp_path / "foods_01.png"
    with patch(
            "src.services.content.providers.file_system.provider.open",
            side_effect=PermissionError(13, "Permission denied"),
            create=True,
    ):
        with pytest.raises(ContentOperationError, match="Permission denied"):
            FileSystemContentProvider._write_image_bytes(target, b"fake-png")


# ---------------------------------------------------------------------------
# _ensure_content_root_exists / _validate_category_path
# ---------------------------------------------------------------------------

def test_ensure_content_root_exists_raises_when_root_missing(tmp_path: Path) -> None:
    """A missing content root (unmounted volume, misconfigured CONTENT_DIR) must not look like a 404."""
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = tmp_path / "does_not_exist"
        mock_settings.CONTENT_DIR = "/app/content"
        with pytest.raises(ContentOperationError, match="mounted"):
            provider._ensure_content_root_exists()


def test_validate_category_path_returns_name_and_path_when_category_exists(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    (content_root / "foods").mkdir(parents=True)
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        assert provider._validate_category_path("C_Foods") == ("foods", content_root / "foods")


def test_validate_category_path_category_not_found_when_folder_missing(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    write_category_registry(content_root, [("C_Foods", "Foods")])  # registered, but no folder on disk
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider._validate_category_path("C_Foods")


def test_validate_category_path_category_not_found_when_id_unregistered(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider._validate_category_path("C_Foods")


def test_validate_category_path_reports_missing_root_instead_of_missing_category(tmp_path: Path) -> None:
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = tmp_path / "does_not_exist"
        mock_settings.CONTENT_DIR = "/app/content"
        with pytest.raises(ContentOperationError, match="mounted"):
            provider._validate_category_path("C_Foods")


def test_folder_name_for_id_reports_missing_root_instead_of_missing_id(tmp_path: Path) -> None:
    """Same failure, reached through the registry lookup every category-scoped call goes through."""
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = tmp_path / "does_not_exist"
        mock_settings.CONTENT_DIR = "/app/content"
        with pytest.raises(ContentOperationError, match="mounted"):
            provider._folder_name_for_id("C_Foods")


# ---------------------------------------------------------------------------
# _list_archived_entries
# ---------------------------------------------------------------------------

def test_list_archived_entries_empty_when_archive_dir_not_set() -> None:
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.ARCHIVE_DIR = ""
        entries = provider._list_archived_entries()
    assert entries == []


def test_list_archived_entries_empty_when_archive_root_missing(tmp_path: Path) -> None:
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
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
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
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
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
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

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.list_categories()

    assert [c.id for c in result.categories] == ["apple", "zebra"]


def test_list_categories_thumbnail_uri_empty_when_icon_missing(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    (content_root / "foods").mkdir()

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
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

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
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

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.ARCHIVE_DIR = ""
        result = provider.list_categories()

    assert result.categories[0].thumbnail_uri == "/content/foods/foods.jpg"


def test_list_categories_error_when_root_missing(tmp_path: Path) -> None:
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = tmp_path / "nonexistent"
        mock_settings.ARCHIVE_DIR = ""
        with pytest.raises(ContentOperationError):
            provider.list_categories()


def test_list_categories_archived_only_skips_content_root(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "foods_1000").mkdir()

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.ARCHIVE_DIR = str(archive_root)
        mock_settings.archive_path = archive_root
        result = provider.list_categories(archived_only=True)

    assert not result.categories
    assert len(result.archived) == 1


def test_list_categories_include_archived(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    (content_root / "foods").mkdir()
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "drinks_1000").mkdir()

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
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
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = tmp_path
        with pytest.raises(InvalidCategoryNameError):
            provider.category_id_by_name("bad name!")


def test_category_id_by_name_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.category_id_by_name("foods")


def test_category_id_by_name_success(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        assert provider.category_id_by_name("foods") == "C_Foods"


# ---------------------------------------------------------------------------
# category_details
# ---------------------------------------------------------------------------

def test_category_details_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.category_details("C_Foods")


def test_category_details_returns_items_and_csv(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    cat = content_root / "foods"
    cat.mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])
    (content_root / "categories_fr.csv").write_text("Aliments\n", encoding="utf-8")
    (content_root / "descriptions_en.csv").write_text("Delicious meals and snacks.\n", encoding="utf-8")
    (content_root / "descriptions_fr.csv").write_text("Repas delicieux et collations.\n", encoding="utf-8")
    (cat / "items_en.csv").write_text("apple\nbanana\n", encoding="utf-8")
    (cat / "items_fr.csv").write_text("pomme\nbanane\n", encoding="utf-8")
    (cat / "items_ids.csv").write_text("I_Apple\nI_Banana\n", encoding="utf-8")

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.category_details("C_Foods")

    assert result.id == "C_Foods"
    assert result.name == LocalizedString({"en": "Foods", "fr": "Aliments"})
    assert result.description == LocalizedString({
        "en": "Delicious meals and snacks.",
        "fr": "Repas delicieux et collations.",
    })
    assert len(result.items) == 2
    assert result.items[0].id == "I_Apple"
    assert result.items[0].name == LocalizedString({"en": "apple", "fr": "pomme"})
    assert result.items[1].id == "I_Banana"
    assert result.items[1].name == LocalizedString({"en": "banana", "fr": "banane"})


# ---------------------------------------------------------------------------
# create_category
# ---------------------------------------------------------------------------

def test_create_category_invalid_name(tmp_path: Path) -> None:
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = tmp_path
        with pytest.raises(InvalidCategoryNameError):
            provider.create_category("bad name!")


def test_create_category_already_exists(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryConflictError):
            provider.create_category("foods")


def test_create_category_success(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", return_value="translated"),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
        patch.object(FileSystemContentProvider, "_generate_image", return_value=(b"icon", ".png")),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_category("animals")

    assert result.category == "animals"
    assert (content_root / "animals").is_dir()
    assert (content_root / "animals" / "animals_en.mp3").exists()
    assert (content_root / "animals" / "animals_fr.mp3").exists()
    assert (content_root / "animals" / "animals.png").exists()


def test_create_category_icon_failure_rolls_back(tmp_path: Path) -> None:
    """A failing image service must leave no partially created category behind."""
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", return_value="translated"),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
        patch.object(
            FileSystemContentProvider,
            "_generate_image",
            side_effect=RuntimeError("No active text to image services configured."),
        ),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(ContentOperationError, match="Icon generation failed"):
            provider.create_category("animals")

    assert not (content_root / "animals").exists()


def test_create_category_writes_jpeg_icon_when_provider_returns_jpeg(tmp_path: Path) -> None:
    """The icon extension follows the image service's response content type."""
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", return_value="translated"),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
        patch.object(FileSystemContentProvider, "_generate_image", return_value=(b"icon", ".jpg")),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.create_category("animals")

    assert (content_root / "animals" / "animals.jpg").exists()
    assert not (content_root / "animals" / "animals.png").exists()


def test_create_category_normalizes_name_to_snake_case(tmp_path: Path) -> None:
    """Folders must be lowercase snake_case or _folder_name_from_category_id() can't find them back."""
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", return_value="translated"),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
        patch.object(FileSystemContentProvider, "_generate_image", return_value=(b"icon", ".png")),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        result = provider.create_category("At School")

        assert result.category == "at_school"
        assert (content_root / "at_school").is_dir()
        assert (content_root / "categories_ids.csv").read_text(encoding="utf-8").strip() == "C_AtSchool"
        # The created category must be resolvable through the normal id lookup path.
        assert provider.category_id_by_name("at_school") == "C_AtSchool"
        assert provider._folder_name_for_id("C_AtSchool") == "at_school"  # pylint: disable=protected-access


def test_create_category_rejects_unsafe_name(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(InvalidCategoryNameError):
            provider.create_category("foods/../etc")


def test_create_category_translation_failure(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(
            FileSystemContentProvider,
            "_translate_word",
            side_effect=httpx.HTTPError("conn failed"),
        ),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
        patch.object(FileSystemContentProvider, "_generate_image", return_value=(b"icon", ".png")),
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
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = tmp_path / "archive"
        with pytest.raises(CategoryNotFoundError):
            provider.archive_category("C_Nonexistent")


def test_archive_category_moves_to_archive(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    archive_root = tmp_path / "archive"
    (content_root / "foods").mkdir(parents=True)
    (content_root / "foods" / "apple").mkdir()
    write_category_registry(content_root, [("C_Foods", "Foods")])

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
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
# delete_category_image
# ---------------------------------------------------------------------------

def test_delete_category_image_category_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.delete_category_image("C_Nonexistent", "foods_01")


def test_delete_category_image_image_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.delete_category_image("C_Foods", "foods_01")


def test_delete_category_image_removes_file(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    image_path = content_root / "foods" / "foods_01.png"
    image_path.write_bytes(b"fake-png")
    write_category_registry(content_root, [("C_Foods", "Foods")])
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        provider.delete_category_image("C_Foods", "foods_01")

    assert not image_path.exists()


# ---------------------------------------------------------------------------
# add_category_image
# ---------------------------------------------------------------------------

def test_add_category_image_category_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    content_root.mkdir()
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.add_category_image("C_Nonexistent", b"fake-png", ".png")


def test_add_category_image_root_missing_names_the_cause(tmp_path: Path) -> None:
    """The Docker corner case this error path exists for: an unmounted content volume."""
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = tmp_path / "does_not_exist"
        mock_settings.CONTENT_DIR = "/app/content"
        with pytest.raises(ContentOperationError, match="mounted"):
            provider.add_category_image("C_Foods", b"fake-png", ".png")


def test_add_category_image_writes_file(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.add_category_image("C_Foods", b"fake-png", ".png")

    assert result.id == "foods_01"
    assert (content_root / "foods" / "foods_01.png").read_bytes() == b"fake-png"


# ---------------------------------------------------------------------------
# metadata sidecars
# ---------------------------------------------------------------------------

def test_a_failed_metadata_write_leaves_the_previous_sidecar_intact(tmp_path: Path) -> None:
    voice = tmp_path / "apple_en.mp3"
    voice.write_bytes(b"audio")
    FileSystemContentProvider._write_metadata(voice, CreationMethod.UPLOADED, provider="custom")
    before = (tmp_path / "apple_en.csv").read_text(encoding="utf-8")

    with (
        patch("csv.DictWriter.writerow", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        FileSystemContentProvider._write_metadata(voice, CreationMethod.GENERATED, provider="openai")

    assert (tmp_path / "apple_en.csv").read_text(encoding="utf-8") == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["apple_en.csv", "apple_en.mp3"]
    assert FileSystemContentProvider._read_metadata(voice)[0] == "custom"
