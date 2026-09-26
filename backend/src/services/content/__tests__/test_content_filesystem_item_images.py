"""Unit tests for FileSystemContentProvider — item images and the item main image."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.content.models import (
    CategoryNotFoundError,
    ContentOperationError,
)
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
