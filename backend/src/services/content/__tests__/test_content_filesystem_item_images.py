"""Unit tests for FileSystemContentProvider — item images and the item main image."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.services.content.models import (
    CategoryNotFoundError,
    ContentOperationError,
)
from src.services.content.providers.file_system import CreationMethod, FileSystemContentProvider
from src.services.content.providers.file_system.internal_api import _GeneratedBytes
from src.services.content.__tests__.content_fs_helpers import (
    patch_fs_settings,
    make_fs_provider,
    write_category_registry,
    write_item_registry,
)


# ---------------------------------------------------------------------------
# add_item_image
# ---------------------------------------------------------------------------

def test_add_item_image_item_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    write_category_registry(content_root, [("C_Foods", "Foods")])
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.add_item_image("C_Foods", "I_Apple", b"fake-png", ".png")


def test_add_item_image_root_missing_names_the_cause(tmp_path: Path) -> None:
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = tmp_path / "does_not_exist"
        mock_settings.CONTENT_DIR = "/app/content"
        with pytest.raises(ContentOperationError, match="mounted"):
            provider.add_item_image("C_Foods", "I_Apple", b"fake-png", ".png")


def test_add_item_image_writes_file(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "Apple")])
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.add_item_image("C_Foods", "I_Apple", b"fake-png-2", ".png")

    assert result.id == "apple_01"
    assert (category_path / "apple" / "apple_01.png").read_bytes() == b"fake-png-2"


def test_add_item_image_first_image_is_copied_into_main_and_01(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "Apple")])
    (category_path / "apple" / "01.png").unlink()  # start from an item with no images yet
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.add_item_image("C_Foods", "I_Apple", b"fake-png", ".png")

    assert result.id == "apple"
    assert (category_path / "apple" / "apple.png").read_bytes() == b"fake-png"
    assert (category_path / "apple" / "apple_01.png").read_bytes() == b"fake-png"


def test_add_item_image_after_main_exists_gets_index(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "Apple")])
    (category_path / "apple" / "01.png").unlink()
    (category_path / "apple" / "apple.png").write_bytes(b"main-image")
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.add_item_image("C_Foods", "I_Apple", b"fake-png-2", ".png")

    assert result.id == "apple_01"
    assert (category_path / "apple" / "apple_01.png").read_bytes() == b"fake-png-2"
    assert (category_path / "apple" / "apple.png").read_bytes() == b"main-image"


# ---------------------------------------------------------------------------
# item_images / set_main_item_image / delete_item_image — main image convention
# ---------------------------------------------------------------------------

def _prepare_item_with_images(tmp_path: Path, images: dict[str, bytes]) -> tuple[Path, Path]:
    """Write a Foods/Apple registry and *images* ({stem: bytes}) into the item folder."""
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "Apple")])
    item_path = category_path / "apple"
    (item_path / "01.png").unlink()
    for stem, data in images.items():
        (item_path / f"{stem}.png").write_bytes(data)
    return content_root, item_path


def test_item_images_excludes_main_image_from_the_list(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_images(tmp_path, {
        "apple": b"first-variant",  # the main image is a copy of apple_01
        "apple_01": b"first-variant",
        "apple_02": b"second-variant",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.item_images("C_Foods", "I_Apple")

    assert {image.id for image in result.images} == {"apple_01", "apple_02"}
    assert result.thumbnail_uri == "/content/foods/apple/apple.png"
    # The main image is a copy of apple_01, so that variant is the selected one.
    assert result.main_image_id == "apple_01"


def test_category_summary_excludes_dedicated_main_image_from_count(tmp_path: Path) -> None:
    """The item tag reports variants, not the unindexed main-image copy."""
    content_root, _ = _prepare_item_with_images(tmp_path, {
        "apple": b"first-variant",
        "apple_01": b"first-variant",
        "apple_02": b"second-variant",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        item = provider.category_details("C_Foods").items[0]

    assert item.images_count == 2
    assert item.thumbnail_uri == "/content/foods/apple/apple.png"


def test_category_summary_counts_all_legacy_variants(tmp_path: Path) -> None:
    """Without a dedicated main file, the first variant is still a real variant."""
    content_root, _ = _prepare_item_with_images(tmp_path, {
        "apple_01": b"first-variant",
        "apple_02": b"second-variant",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        item = provider.category_details("C_Foods").items[0]

    assert item.images_count == 2
    assert item.thumbnail_uri == "/content/foods/apple/apple_01.png"


def test_item_images_main_image_matching_no_variant_selects_none(tmp_path: Path) -> None:
    """A main image whose bytes match no variant leaves the selection unresolved."""
    content_root, _ = _prepare_item_with_images(tmp_path, {
        "apple": b"stale-content",
        "apple_01": b"first-variant",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.item_images("C_Foods", "I_Apple")

    assert [image.id for image in result.images] == ["apple_01"]
    assert result.thumbnail_uri == "/content/foods/apple/apple.png"
    assert result.main_image_id is None


def test_item_images_with_only_main_image_has_empty_list(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_images(tmp_path, {"apple": b"main-content"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.item_images("C_Foods", "I_Apple")

    assert not result.images
    assert result.thumbnail_uri == "/content/foods/apple/apple.png"
    assert result.main_image_id is None


def test_item_images_without_main_falls_back_to_first_variant(tmp_path: Path) -> None:
    """Legacy items with no dedicated main file keep the old first-sorted-is-thumbnail behavior."""
    content_root, _ = _prepare_item_with_images(tmp_path, {
        "apple_01": b"first-variant",
        "apple_02": b"second-variant",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.item_images("C_Foods", "I_Apple")

    assert {image.id for image in result.images} == {"apple_01", "apple_02"}
    assert result.thumbnail_uri == "/content/foods/apple/apple_01.png"
    assert result.main_image_id == "apple_01"


def test_set_main_item_image_copies_the_variant_over_the_main_image(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {
        "apple": b"main-content",
        "apple_01": b"variant-content",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.set_main_item_image("C_Foods", "I_Apple", "apple_01")

    assert (item_path / "apple.png").read_bytes() == b"variant-content"
    # The variant is copied, never moved: it stays exactly where it was.
    assert (item_path / "apple_01.png").read_bytes() == b"variant-content"
    assert {image.id for image in result.images} == {"apple_01"}
    assert result.thumbnail_uri == "/content/foods/apple/apple.png"
    assert result.main_image_id == "apple_01"


def test_set_main_item_image_already_main_is_noop(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {
        "apple": b"main-content",
        "apple_01": b"variant-content",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        provider.set_main_item_image("C_Foods", "I_Apple", "apple")

    assert (item_path / "apple.png").read_bytes() == b"main-content"
    assert (item_path / "apple_01.png").read_bytes() == b"variant-content"


def test_set_main_item_image_with_no_existing_main_copies_target(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {
        "apple_01": b"first-content",
        "apple_02": b"second-content",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        provider.set_main_item_image("C_Foods", "I_Apple", "apple_02")

    assert (item_path / "apple.png").read_bytes() == b"second-content"
    assert (item_path / "apple_01.png").read_bytes() == b"first-content"
    assert (item_path / "apple_02.png").read_bytes() == b"second-content"


def test_set_main_item_image_keeps_every_variant_listed(tmp_path: Path) -> None:
    """Choosing a main image must never make a variant disappear from the item."""
    content_root, item_path = _prepare_item_with_images(tmp_path, {
        "apple_01": b"first-content",
        "apple_02": b"second-content",
        "apple_03": b"third-content",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        before = provider.item_images("C_Foods", "I_Apple")
        after = provider.set_main_item_image("C_Foods", "I_Apple", "apple_03")

    assert [i.id for i in before.images] == [i.id for i in after.images] == ["apple_01", "apple_02", "apple_03"]
    assert after.main_image_id == "apple_03"
    assert (item_path / "apple.png").read_bytes() == b"third-content"


def test_set_main_item_image_repeated_selection_keeps_every_variant(tmp_path: Path) -> None:
    """Re-selecting leaves the variants untouched — only the main copy is rewritten."""
    content_root, item_path = _prepare_item_with_images(tmp_path, {
        "apple": b"first-content",
        "apple_01": b"first-content",
        "apple_02": b"second-content",
        "apple_03": b"third-content",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        for target, expected in [("apple_02", b"second-content"), ("apple_03", b"third-content"),
                                 ("apple_01", b"first-content")]:
            result = provider.set_main_item_image("C_Foods", "I_Apple", target)
            assert [i.id for i in result.images] == ["apple_01", "apple_02", "apple_03"]
            assert result.main_image_id == target
            assert (item_path / "apple.png").read_bytes() == expected

    assert (item_path / "apple_01.png").read_bytes() == b"first-content"
    assert (item_path / "apple_02.png").read_bytes() == b"second-content"
    assert (item_path / "apple_03.png").read_bytes() == b"third-content"


def test_set_main_item_image_replaces_main_stored_under_another_extension(tmp_path: Path) -> None:
    """Copying a .jpg variant must not leave the old .png main file behind."""
    content_root, item_path = _prepare_item_with_images(tmp_path, {"apple": b"main-content"})
    (item_path / "apple_01.jpg").write_bytes(b"jpg-content")
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.set_main_item_image("C_Foods", "I_Apple", "apple_01")

    assert not (item_path / "apple.png").exists()
    assert (item_path / "apple.jpg").read_bytes() == b"jpg-content"
    assert (item_path / "apple_01.jpg").read_bytes() == b"jpg-content"
    assert result.main_image_id == "apple_01"


def test_set_main_item_image_not_found(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_images(tmp_path, {"apple": b"main-content"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(CategoryNotFoundError):
            provider.set_main_item_image("C_Foods", "I_Apple", "apple_99")


def test_delete_item_image_promotes_lowest_index_when_main_deleted(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {
        "apple": b"main-content",
        "apple_01": b"first-content",
        "apple_02": b"second-content",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.item_images("C_Foods", "I_Apple")
        provider.delete_item_image("C_Foods", "I_Apple", "apple")
        after = provider.item_images("C_Foods", "I_Apple")

    assert (item_path / "apple.png").read_bytes() == b"first-content"
    assert (item_path / "apple_02.png").read_bytes() == b"second-content"
    # The promoted variant is copied, not moved, so the item keeps both variants.
    assert (item_path / "apple_01.png").read_bytes() == b"first-content"
    assert [i.id for i in result.images] == [i.id for i in after.images] == ["apple_01", "apple_02"]
    assert after.main_image_id == "apple_01"


def test_delete_item_image_non_main_leaves_main_untouched(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {
        "apple": b"main-content",
        "apple_01": b"first-content",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        provider.delete_item_image("C_Foods", "I_Apple", "apple_01")

    assert (item_path / "apple.png").read_bytes() == b"main-content"
    assert not (item_path / "apple_01.png").exists()


def test_delete_item_image_last_remaining_is_allowed(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {"apple": b"main-content"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        provider.delete_item_image("C_Foods", "I_Apple", "apple")

    assert not list(item_path.glob("*.png"))
    assert not provider.item_images("C_Foods", "I_Apple").images


# ---------------------------------------------------------------------------
# generate_item_image
# ---------------------------------------------------------------------------

def _prepare_apple(tmp_path: Path) -> tuple[Path, Path]:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "Apple")])
    return content_root, category_path / "apple"


def test_generate_item_image_adds_the_generated_image_as_the_next_variant(tmp_path: Path) -> None:
    content_root, item_path = _prepare_apple(tmp_path)
    (item_path / "apple.png").write_bytes(b"main")
    (item_path / "apple_01.png").write_bytes(b"main")
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_image", return_value=(b"generated", ".jpg")) as generate,
    ):
        mock_settings.content_path = content_root
        result = provider.generate_item_image("C_Foods", "I_Apple", "  a red apple  ", " comfyui ")

    generate.assert_called_once_with("a red apple", "apple", "comfyui")
    assert result.id == "apple_02"
    assert (item_path / "apple_02.jpg").read_bytes() == b"generated"
    assert (item_path / "apple.png").read_bytes() == b"main"


def test_generate_item_image_without_configuration_uses_the_default(tmp_path: Path) -> None:
    content_root, item_path = _prepare_apple(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_image", return_value=(b"generated", ".png")) as generate,
    ):
        mock_settings.content_path = content_root
        result = provider.generate_item_image("C_Foods", "I_Apple", "apple")

    generate.assert_called_once_with("apple", "apple", None)
    assert (item_path / f"{result.id}.png").read_bytes() == b"generated"


@pytest.mark.parametrize(
    "error",
    [RuntimeError("No active text to image services configured."), httpx.ConnectError("down")],
)
def test_generate_item_image_reports_generation_failures_and_changes_nothing(tmp_path: Path, error: Exception) -> None:
    content_root, item_path = _prepare_apple(tmp_path)
    before = sorted(item_path.iterdir())
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_image", side_effect=error),
    ):
        mock_settings.content_path = content_root
        with pytest.raises(ContentOperationError, match="Image generation failed"):
            provider.generate_item_image("C_Foods", "I_Apple", "apple")

    assert sorted(item_path.iterdir()) == before


def test_generate_item_image_requires_a_prompt(tmp_path: Path) -> None:
    content_root, _ = _prepare_apple(tmp_path)
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        with pytest.raises(ContentOperationError, match="prompt"):
            provider.generate_item_image("C_Foods", "I_Apple", "   ")


# ---------------------------------------------------------------------------
# Image metadata sidecars ({image}.csv), kept like the voices' ones
# ---------------------------------------------------------------------------

_GENERATED = _GeneratedBytes(b"generated", "comfyui", {"model": "sdxl", "steps": 20, "output_format": "png"})


def _sidecar(image_path: Path) -> dict[str, str]:
    with image_path.with_suffix(".csv").open(encoding="utf-8", newline="") as file_handle:
        return next(csv.DictReader(file_handle))


def _generate(provider: FileSystemContentProvider, prompt: str = "a red apple",
              configuration_name: str | None = None, extension: str = ".png"):
    with patch.object(FileSystemContentProvider, "_generate_image", return_value=(_GENERATED, extension)):
        return provider.generate_item_image("C_Foods", "I_Apple", prompt, configuration_name)


def test_generate_item_image_records_the_prompt_and_configuration_in_a_sidecar(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {"apple": b"main", "apple_01": b"main"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = _generate(provider, "  a red apple  ", "comfyui")

    row = _sidecar(item_path / "apple_02.png")
    parameters = json.loads(row["parameters"])
    assert (row["creation_method"], row["provider"]) == ("generated", "comfyui")
    assert row["hash"] == hashlib.sha256(b"generated").hexdigest()
    assert parameters.pop("date")
    assert parameters == {
        "model": "sdxl", "steps": 20, "output_format": "png",
        "_prompt": "a red apple", "configuration_name": "comfyui",
    }
    assert result.provider == "comfyui"
    assert result.parameters["_prompt"] == "a red apple"
    assert not (item_path / "apple.csv").exists()  # the main image is left as it was


def test_generate_item_image_first_image_records_the_main_image_and_its_variant(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = _generate(provider)

    assert result.id == "apple"
    for name in ("apple.png", "apple_01.png"):
        row = _sidecar(item_path / name)
        assert (row["creation_method"], row["provider"]) == ("generated", "comfyui")
        # No configuration was chosen, so none is recorded as chosen.
        assert "configuration_name" not in json.loads(row["parameters"])


def test_add_item_image_records_an_upload_by_the_custom_provider(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {"apple": b"main", "apple_01": b"main"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        result = provider.add_item_image("C_Foods", "I_Apple", b"uploaded", ".png")

    row = _sidecar(item_path / "apple_02.png")
    assert (row["creation_method"], row["provider"]) == ("uploaded", "custom")
    assert list(json.loads(row["parameters"])) == ["date"]
    assert row["hash"] == hashlib.sha256(b"uploaded").hexdigest()
    assert result.provider == "custom"


def test_item_images_lists_each_variant_with_its_recorded_metadata(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_images(tmp_path, {"apple": b"main", "apple_01": b"main"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        _generate(provider, "a red apple", "comfyui")
        provider.add_item_image("C_Foods", "I_Apple", b"uploaded", ".png")
        result = provider.item_images("C_Foods", "I_Apple")

    by_id = {image.id: image for image in result.images}
    assert (by_id["apple_01"].provider, by_id["apple_01"].parameters) == ("unknown", {})
    assert by_id["apple_02"].provider == "comfyui"
    assert by_id["apple_02"].parameters["_prompt"] == "a red apple"
    assert by_id["apple_03"].provider == "custom"


def test_item_images_resets_the_metadata_of_an_image_changed_on_disk(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {"apple": b"main", "apple_01": b"main"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        _generate(provider)
        (item_path / "apple_02.png").write_bytes(b"edited elsewhere")
        result = provider.item_images("C_Foods", "I_Apple")

    image = next(image for image in result.images if image.id == "apple_02")
    assert (image.provider, image.parameters) == ("custom", {})
    row = _sidecar(item_path / "apple_02.png")
    assert row["creation_method"] == "unknown"
    assert row["hash"] == hashlib.sha256(b"edited elsewhere").hexdigest()


def test_set_main_item_image_copies_the_variant_metadata_with_it(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {"apple": b"main"})
    provider = make_fs_provider()
    provider._write_metadata(item_path / "apple.png", CreationMethod.UPLOADED, "custom")
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        _generate(provider)
        provider.set_main_item_image("C_Foods", "I_Apple", "apple_01")

    assert (item_path / "apple.csv").read_text() == (item_path / "apple_01.csv").read_text()


def test_set_main_item_image_drops_the_main_metadata_when_the_variant_has_none(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {"apple": b"main", "apple_01": b"legacy"})
    provider = make_fs_provider()
    provider._write_metadata(item_path / "apple.png", CreationMethod.UPLOADED, "custom")
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        provider.set_main_item_image("C_Foods", "I_Apple", "apple_01")

    assert not (item_path / "apple.csv").exists()


def test_delete_item_image_removes_its_metadata_and_a_promoted_main_takes_the_variants(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {"apple": b"main"})
    provider = make_fs_provider()
    provider._write_metadata(item_path / "apple.png", CreationMethod.UPLOADED, "custom")
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        _generate(provider)
        provider.delete_item_image("C_Foods", "I_Apple", "apple")
        assert (item_path / "apple.csv").read_text() == (item_path / "apple_01.csv").read_text()
        deleted = provider.delete_item_image("C_Foods", "I_Apple", "apple_01")

    assert not (item_path / "apple_01.csv").exists()
    assert (item_path / "apple.csv").exists()  # the main image keeps its own copy
    assert deleted.provider == "comfyui"


def test_update_item_image_records_the_replacement_as_an_upload(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_images(tmp_path, {"apple": b"main", "apple_01": b"main"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        _generate(provider, extension=".jpg")
        result = provider.update_item_image("C_Foods", "I_Apple", "apple_02", b"replacement", ".png")

    row = _sidecar(item_path / "apple_02.png")
    assert (row["creation_method"], row["provider"]) == ("uploaded", "custom")
    assert "_prompt" not in json.loads(row["parameters"])
    assert result.provider == "custom"
