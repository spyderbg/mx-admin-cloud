"""Unit tests for pure helpers on FileSystemCategoriesMixin: icon prompts and category image slots."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.content.providers.file_system.categories import FileSystemCategoriesMixin


# ---------------------------------------------------------------------------
# _category_name_to_icon_prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["", " ", "   ", "\t", "_", "___", " _ "])
def test_icon_prompt_empty_or_blank_returns_input_unchanged(name: str) -> None:
    assert FileSystemCategoriesMixin._category_name_to_icon_prompt(name) == name


@pytest.mark.parametrize(("name", "label"), [
    ("foods", "Foods"),
    ("sea_animals", "Sea Animals"),
    ("big_red_cars", "Big Red Cars"),
    ("_leading", "Leading"),
    ("trailing_", "Trailing"),
    ("FOODS", "Foods"),
])
def test_icon_prompt_formats_label(name: str, label: str) -> None:
    assert (
        FileSystemCategoriesMixin._category_name_to_icon_prompt(name)
        == f"Simple, friendly icon for children: {label}"
    )


# ---------------------------------------------------------------------------
# _first_available_category_image_index
# ---------------------------------------------------------------------------

@pytest.fixture(name="category_dir")
def fixture_category_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "foods"
    folder.mkdir()
    return folder


def _touch(folder: Path, name: str) -> None:
    (folder / name).write_bytes(b"")


def test_first_index_empty_folder(category_dir: Path) -> None:
    assert FileSystemCategoriesMixin._first_available_category_image_index(category_dir, "foods") == 1


@pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg"])
def test_first_index_skips_slot_occupied_by_any_image_extension(category_dir: Path, ext: str) -> None:
    _touch(category_dir, f"foods_01{ext}")
    assert FileSystemCategoriesMixin._first_available_category_image_index(category_dir, "foods") == 2


def test_first_index_returns_first_gap(category_dir: Path) -> None:
    _touch(category_dir, "foods_01.png")
    _touch(category_dir, "foods_02.jpg")
    _touch(category_dir, "foods_04.jpeg")
    assert FileSystemCategoriesMixin._first_available_category_image_index(category_dir, "foods") == 3


def test_first_index_ignores_unrelated_files(category_dir: Path) -> None:
    _touch(category_dir, "foods_01.txt")
    _touch(category_dir, "foods_01.mp3")
    _touch(category_dir, "other_01.png")
    _touch(category_dir, "foods_1.png")
    assert FileSystemCategoriesMixin._first_available_category_image_index(category_dir, "foods") == 1


def test_first_index_uses_two_digit_padding_past_nine(category_dir: Path) -> None:
    for i in range(1, 10):
        _touch(category_dir, f"foods_{i:02d}.png")
    assert FileSystemCategoriesMixin._first_available_category_image_index(category_dir, "foods") == 10


def test_first_index_does_not_create_files(category_dir: Path) -> None:
    FileSystemCategoriesMixin._first_available_category_image_index(category_dir, "foods")
    assert not list(category_dir.iterdir())
