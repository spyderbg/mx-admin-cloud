"""Unit tests for StaticTextTranslate: lookups built from a content directory's items_<lang>.csv files."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path

from src.services.text_translation.providers.static_translate import StaticTextTranslate


def _lookup(content_root: Path) -> dict[str, dict[str, dict[str, str]]]:
    translator = StaticTextTranslate.__new__(StaticTextTranslate)  # skips config loading
    translator._lookup = {}
    for category_dir in sorted(content_root.iterdir()):
        if category_dir.is_dir() and not category_dir.name.startswith("."):
            translator._update_lookup_from_category(translator._load_category_csvs(category_dir))
    return translator._lookup


def _fruits(tmp_path: Path, french: str) -> Path:
    category = tmp_path / "content" / "fruits"
    category.mkdir(parents=True)
    (category / "items_ids.csv").write_text("I_Apple\nI_Pear\nI_Plum\n", encoding="utf-8")
    (category / "items_en.csv").write_text("apple\npear\nplum\n", encoding="utf-8")
    (category / "items_fr.csv").write_text(french, encoding="utf-8")
    return tmp_path / "content"


def test_a_blank_translation_does_not_shift_the_words_below_it(tmp_path: Path) -> None:
    lookup = _lookup(_fruits(tmp_path, 'pomme\n""\nprune\n'))

    assert lookup["en"]["apple"]["fr"] == "pomme"
    assert "fr" not in lookup["en"]["pear"]  # no translation rather than the next word
    assert lookup["en"]["plum"]["fr"] == "prune"
    assert lookup["fr"]["prune"]["en"] == "plum"
    assert "" not in lookup["fr"]


def test_the_ids_file_is_not_a_language(tmp_path: Path) -> None:
    lookup = _lookup(_fruits(tmp_path, "pomme\npoire\nprune\n"))

    assert sorted(lookup) == ["en", "fr"]
    assert "ids" not in lookup["en"]["apple"]


def test_an_unreadable_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    content_root = _fruits(tmp_path, "pomme\npoire\nprune\n")
    (content_root / "fruits" / "items_bg.csv").write_bytes(b"\xff\xfe broken\n")  # not UTF-8

    lookup = _lookup(content_root)

    assert lookup["en"]["pear"] == {"fr": "poire"}


def test_folders_being_deleted_are_ignored(tmp_path: Path) -> None:
    content_root = _fruits(tmp_path, "pomme\npoire\nprune\n")
    hidden = content_root / ".drinks.deleting"
    hidden.mkdir()
    (hidden / "items_en.csv").write_text("water\n", encoding="utf-8")
    (hidden / "items_fr.csv").write_text("eau\n", encoding="utf-8")

    translator = StaticTextTranslate.__new__(StaticTextTranslate)
    translator._lookup = {}
    translator.config = {"content_dir": str(content_root)}
    translator.base_dir = tmp_path
    translator._load()

    assert "water" not in translator._lookup["en"]
    assert translator._lookup["en"]["pear"]["fr"] == "poire"
