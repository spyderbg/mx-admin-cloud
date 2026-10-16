"""Unit tests for the item registry: items_ids.csv / items_<lang>.csv stay row-aligned under every write."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

import fcntl
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.localization import LocalizedString
from src.services.content.models import CategoryConflictError, ContentOperationError
from src.services.content.providers.file_system import FileSystemContentProvider, item_registry
from src.services.content.__tests__.content_fs_helpers import (
    make_fs_provider,
    patch_fs_settings,
    write_category_registry,
    write_item_registry,
)

_LANGS = ["en", "fr"]


def _foods(tmp_path: Path, entries: list[tuple[str, str]], fr: list[str] | None = None) -> tuple[Path, Path]:
    """A 'foods' category holding *entries* (id, English name), with French names when given."""
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, entries)
    if fr is not None:
        (category_path / "items_fr.csv").write_text("".join(f"{name}\n" for name in fr), encoding="utf-8")
    return content_root, category_path


def _fake_translate(text: str, target_lang_code: str | None = None, *_args: object, **_kwargs: object) -> str:  # pylint: disable=keyword-arg-before-vararg
    return f"{text}-{target_lang_code}"


def _rows(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _names(provider: FileSystemContentProvider, category_path: Path) -> dict[str, dict[str, str]]:
    item_ids, names_by_lang = provider._read_item_csv_data(category_path)
    return {
        item_id: {lang: names[index] for lang, names in names_by_lang.items() if names[index]}
        for index, item_id in enumerate(item_ids)
    }


def test_a_blank_name_does_not_shift_the_names_below_it(tmp_path: Path) -> None:
    content_root, category_path = _foods(
        tmp_path, [("I_Apple", "apple"), ("I_Pear", "pear"), ("I_Plum", "plum")], fr=["pomme", "", "prune"],
    )
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        assert _names(provider, category_path)["I_Plum"] == {"en": "plum", "fr": "prune"}
        assert provider._item_detail(category_path, "foods", "I_Pear").name.to_dict() == {"en": "pear"}

        provider.remove_item("C_Foods", "I_Apple")

    # Rewriting keeps the blank row, so Plum's French name stays on Plum's row.
    assert _rows(category_path / "items_ids.csv") == ["I_Pear", "I_Plum"]
    assert _rows(category_path / "items_fr.csv") == ['""', "prune"]


def test_clearing_a_name_keeps_the_other_items_aligned(tmp_path: Path) -> None:
    content_root, category_path = _foods(tmp_path, [("I_Apple", "apple"), ("I_Pear", "pear")], fr=["pomme", "poire"])
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        provider.update_item_name("C_Foods", "I_Apple", LocalizedString({"fr": "  "}))
        names = _names(provider, category_path)

    assert names == {"I_Apple": {"en": "apple"}, "I_Pear": {"en": "pear", "fr": "poire"}}


def test_a_short_language_file_is_padded_not_truncated(tmp_path: Path) -> None:
    content_root, category_path = _foods(tmp_path, [("I_Apple", "apple"), ("I_Pear", "pear")], fr=["pomme"])
    (category_path / "items_ids.csv").write_text("I_Apple\nI_Pear\n\n\n", encoding="utf-8")  # trailing blank lines
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        provider.create_item("C_Foods", "plum", generate_voices=False)

    assert _rows(category_path / "items_ids.csv") == ["I_Apple", "I_Pear", "I_Plum"]
    assert _rows(category_path / "items_en.csv") == ["apple", "pear", "plum"]
    assert _rows(category_path / "items_fr.csv") == ["pomme", '""', "plum-fr"]


def test_values_are_quoted_and_every_row_is_terminated(tmp_path: Path) -> None:
    category_path = tmp_path / "foods"
    category_path.mkdir()
    with patch_fs_settings() as mock_settings:
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with item_registry.item_registry_lock(category_path):
            registry = item_registry.read_item_registry(category_path)
            registry.append("I_HotChocolate", {"en": "hot, chocolate"})
            item_registry.write_item_registry(category_path, registry)
            assert item_registry.read_item_registry(category_path).names["en"] == ["hot, chocolate"]

    assert (category_path / "items_en.csv").read_text(encoding="utf-8") == '"hot, chocolate"\n'


def test_an_interrupted_committed_write_is_completed_on_the_next_access(tmp_path: Path) -> None:
    content_root, category_path = _foods(tmp_path, [("I_Apple", "apple")], fr=["pomme"])
    # A worker died after the commit marker, having applied ids but not en/fr yet.
    (category_path / "items_ids.csv").write_text("I_Apple\nI_Pear\n", encoding="utf-8")
    (category_path / ".items_en.csv.tmp").write_text("apple\npear\n", encoding="utf-8")
    (category_path / ".items_fr.csv.tmp").write_text("pomme\npoire\n", encoding="utf-8")
    (category_path / ".items_registry.commit").touch()
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        names = _names(provider, category_path)

    assert names == {"I_Apple": {"en": "apple", "fr": "pomme"}, "I_Pear": {"en": "pear", "fr": "poire"}}
    assert not (category_path / ".items_registry.commit").exists()
    assert not list(category_path.glob(".items_*.csv.tmp"))


def test_an_uncommitted_write_is_discarded_on_the_next_access(tmp_path: Path) -> None:
    content_root, category_path = _foods(tmp_path, [("I_Apple", "apple")], fr=["pomme"])
    # A worker died while staging, before the commit marker: none of it may apply.
    (category_path / ".items_ids.csv.tmp").write_text("I_Apple\nI_Pear\n", encoding="utf-8")
    (category_path / ".items_en.csv.tmp").write_text("apple\npe", encoding="utf-8")
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        assert _names(provider, category_path) == {"I_Apple": {"en": "apple", "fr": "pomme"}}

    assert not list(category_path.glob(".items_*.csv.tmp"))
    assert _rows(category_path / "items_ids.csv") == ["I_Apple"]


def test_an_unreadable_file_fails_the_write_instead_of_being_overwritten(tmp_path: Path) -> None:
    content_root, category_path = _foods(tmp_path, [("I_Apple", "apple")])
    (category_path / "items_fr.csv").write_bytes(b"pomme\n\xff\xfe broken\n")  # not UTF-8
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        with pytest.raises(ContentOperationError, match="Could not read 'items_fr.csv'"):
            provider.update_item_name("C_Foods", "I_Apple", LocalizedString({"en": "green apple"}))

    assert _rows(category_path / "items_en.csv") == ["apple"]
    assert (category_path / "items_fr.csv").read_bytes() == b"pomme\n\xff\xfe broken\n"


def test_remove_item_puts_the_folder_back_when_the_registry_cannot_be_written(tmp_path: Path) -> None:
    content_root, category_path = _foods(tmp_path, [("I_Apple", "apple"), ("I_Pear", "pear")])
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch(
            "src.services.content.providers.file_system.items.write_item_registry",
            side_effect=ContentOperationError("read-only"),
        ),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(ContentOperationError, match="read-only"):
            provider.remove_item("C_Foods", "I_Apple")

    assert (category_path / "apple" / "01.png").is_file()
    assert not (category_path / ".apple.deleting").exists()
    assert _rows(category_path / "items_ids.csv") == ["I_Apple", "I_Pear"]


def test_a_concurrently_created_folder_is_a_conflict_and_is_left_alone(tmp_path: Path) -> None:
    content_root, category_path = _foods(tmp_path, [("I_Pear", "pear")])

    def translate_while_another_request_creates_apple(*_args: object, **_kwargs: object) -> dict[str, str]:
        (category_path / "apple").mkdir()
        (category_path / "apple" / "apple.png").write_bytes(b"theirs")
        return {"en": "apple", "fr": "pomme"}

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(
            FileSystemContentProvider, "_translate_item_word",
            side_effect=translate_while_another_request_creates_apple,
        ),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        with pytest.raises(CategoryConflictError):
            provider.create_item("C_Foods", "apple", generate_voices=False)

    assert (category_path / "apple" / "apple.png").read_bytes() == b"theirs"


def test_concurrent_creates_keep_every_file_aligned(tmp_path: Path) -> None:
    content_root, category_path = _foods(tmp_path, [("I_Pear", "pear")], fr=["pear-fr"])
    words = [f"fruit {index}" for index in range(24)]
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda word: provider.create_item("C_Foods", word, generate_voices=False), words))

    ids = _rows(category_path / "items_ids.csv")
    english = _rows(category_path / "items_en.csv")
    french = _rows(category_path / "items_fr.csv")
    assert len(ids) == len(english) == len(french) == len(words) + 1
    for item_id, en, fr in zip(ids, english, french):
        assert item_id == provider._english_word_to_image_id(en)
        assert fr == f"{en}-fr"


def test_the_lock_excludes_other_processes_and_is_reentrant(tmp_path: Path) -> None:
    category_path = tmp_path / "foods"
    category_path.mkdir()
    lock_file = category_path / ".items_registry.lock"

    def lockable_from_elsewhere() -> bool:
        # A separate open file description, as another process would have.
        with open(lock_file, "ab") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return True

    with item_registry.item_registry_lock(category_path):
        with item_registry.item_registry_lock(category_path):
            assert not lockable_from_elsewhere()
        assert not lockable_from_elsewhere()  # the inner exit must not release the outer hold
        other_thread_got_in = threading.Event()

        def contend() -> None:
            with item_registry.item_registry_lock(category_path):
                other_thread_got_in.set()

        worker = threading.Thread(target=contend, daemon=True)
        worker.start()
        worker.join(timeout=0.2)
        assert not other_thread_got_in.is_set()
    worker.join(timeout=2)
    assert other_thread_got_in.is_set()
