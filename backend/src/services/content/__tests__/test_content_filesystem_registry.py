"""Unit tests for the row-aligned registries (items and categories): their files stay aligned under every write."""

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
from src.services.content.providers.file_system import FileSystemContentProvider
from src.services.content.providers.file_system.registry import (
    CATEGORIES,
    ITEMS,
    read_registry,
    registry_lock,
    write_registry,
)
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
        with registry_lock(category_path, ITEMS):
            registry = read_registry(category_path, ITEMS)
            registry.append("I_HotChocolate", names={"en": "hot, chocolate"})
            write_registry(category_path, ITEMS, registry)
            assert read_registry(category_path, ITEMS).names["en"] == ["hot, chocolate"]

    assert (category_path / "items_en.csv").read_text(encoding="utf-8") == '"hot, chocolate"\n'


def test_a_hand_edited_value_with_unquoted_commas_survives_a_write(tmp_path: Path) -> None:
    content_root, category_path = _foods(tmp_path, [("I_Apple", "apple"), ("I_Pear", "pear")])
    (category_path / "items_en.csv").write_text("apple\nhot, sweet pear\n", encoding="utf-8")
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.update_item_name("C_Foods", "I_Apple", LocalizedString({"en": "green apple"}))
        names = _names(provider, category_path)

    assert names["I_Pear"] == {"en": "hot, sweet pear"}
    assert _rows(category_path / "items_en.csv") == ["green apple", '"hot, sweet pear"']


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
            "src.services.content.providers.file_system.items.write_registry",
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

    with registry_lock(category_path, ITEMS):
        with registry_lock(category_path, ITEMS):
            assert not lockable_from_elsewhere()
        assert not lockable_from_elsewhere()  # the inner exit must not release the outer hold
        other_thread_got_in = threading.Event()

        def contend() -> None:
            with registry_lock(category_path, ITEMS):
                other_thread_got_in.set()

        worker = threading.Thread(target=contend, daemon=True)
        worker.start()
        worker.join(timeout=0.2)
        assert not other_thread_got_in.is_set()
    worker.join(timeout=2)
    assert other_thread_got_in.is_set()


# ---------------------------------------------------------------------------
# Category registry (content root)
# ---------------------------------------------------------------------------

def _categories(tmp_path: Path, rows: dict[str, list[str]]) -> Path:
    """A content root with categories animals/birds/colors and the given registry files (name -> rows)."""
    content_root = tmp_path / "content"
    content_root.mkdir()
    (content_root / "categories_ids.csv").write_text("C_Animals\nC_Birds\nC_Colors\n", encoding="utf-8")
    for name, values in rows.items():
        (content_root / name).write_text("".join(f"{value}\n" for value in values), encoding="utf-8")
    for folder in ("animals", "birds", "colors"):
        (content_root / folder).mkdir()
    return content_root


def _descriptions(provider: FileSystemContentProvider, lang: str) -> dict[str, str]:
    return {
        folder: provider._load_category_localized_descriptions(folder).get(lang, "")
        for folder in ("animals", "birds", "colors")
    }


def test_clearing_a_category_description_keeps_the_others_aligned(tmp_path: Path) -> None:
    content_root = _categories(tmp_path, {"descriptions_fr.csv": ["A-fr", "B-fr", "C-fr"]})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        provider.update_category_details("C_Birds", LocalizedString({}), LocalizedString({"fr": ""}))
        provider.update_category_details("C_Animals", LocalizedString({}), LocalizedString({"fr": "A2-fr"}))
        descriptions = _descriptions(provider, "fr")

    assert descriptions == {"animals": "A2-fr", "birds": "", "colors": "C-fr"}
    assert _rows(content_root / "descriptions_fr.csv") == ["A2-fr", '""', "C-fr"]


def test_an_unconfigured_language_file_stays_aligned(tmp_path: Path) -> None:
    content_root = _categories(tmp_path, {
        "categories_en.csv": ["animals", "birds", "colors"],
        "descriptions_it.csv": ["A-it", "B-it", "C-it"],  # Italian is not a configured language
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        mock_settings.ARCHIVE_DIR = ""
        provider.remove_category("C_Birds")
        names = provider._load_category_localized_names()[1]

    assert _rows(content_root / "categories_ids.csv") == ["C_Animals", "C_Colors"]
    assert _rows(content_root / "descriptions_it.csv") == ["A-it", "C-it"]
    assert "it" not in names["colors"]  # kept aligned on disk, but not served
    assert not (content_root / "birds").exists()
    assert not (content_root / ".birds.deleting").exists()


def test_archive_and_restore_keep_every_language(tmp_path: Path) -> None:
    content_root = _categories(tmp_path, {
        "categories_en.csv": ["animals", "birds", "colors"],
        "descriptions_it.csv": ["A-it", "B-it", "C-it"],
    })
    archive_root = tmp_path / "archive"
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.archive_path = archive_root
        mock_settings.ARCHIVE_DIR = str(archive_root)
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        provider.remove_category("C_Birds")
        assert _rows(content_root / "descriptions_it.csv") == ["A-it", "C-it"]
        provider.restore_category("birds")

    assert _rows(content_root / "categories_ids.csv") == ["C_Animals", "C_Colors", "C_Birds"]
    assert _rows(content_root / "categories_en.csv") == ["animals", "colors", "birds"]
    assert _rows(content_root / "descriptions_it.csv") == ["A-it", "C-it", "B-it"]


@pytest.mark.parametrize("archive", [True, False])
def test_removing_a_category_puts_the_folder_back_when_the_registry_cannot_be_written(
        tmp_path: Path, archive: bool,
) -> None:
    content_root = _categories(tmp_path, {"categories_en.csv": ["animals", "birds", "colors"]})
    (content_root / "birds" / "birds.png").write_bytes(b"icon")
    archive_root = tmp_path / "archive"
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch(
            "src.services.content.providers.file_system.categories.write_registry",
            side_effect=ContentOperationError("read-only"),
        ),
    ):
        mock_settings.content_path = content_root
        mock_settings.archive_path = archive_root
        mock_settings.ARCHIVE_DIR = str(archive_root) if archive else ""
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        with pytest.raises(ContentOperationError, match="read-only"):
            provider.remove_category("C_Birds")

    assert (content_root / "birds" / "birds.png").read_bytes() == b"icon"
    assert sorted(p.name for p in (content_root / "birds").iterdir()) == ["birds.png"]  # no manifest left
    assert not archive_root.exists() or not list(archive_root.glob("birds_*"))
    assert _rows(content_root / "categories_ids.csv") == ["C_Animals", "C_Birds", "C_Colors"]


def test_changing_a_category_id_to_a_registered_one_is_a_conflict(tmp_path: Path) -> None:
    content_root = _categories(tmp_path, {"categories_en.csv": ["animals", "birds", "colors"]})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        with pytest.raises(CategoryConflictError, match="Category id already exists: C_Colors"):
            provider.update_category_id("C_Birds", "C_Colors")

    assert _rows(content_root / "categories_ids.csv") == ["C_Animals", "C_Birds", "C_Colors"]


def test_changing_a_category_id_moves_the_folder_back_when_the_registry_cannot_be_written(tmp_path: Path) -> None:
    content_root = _categories(tmp_path, {"categories_en.csv": ["animals", "birds", "colors"]})
    (content_root / "birds" / "birds.png").write_bytes(b"icon")
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch(
            "src.services.content.providers.file_system.categories.write_registry",
            side_effect=ContentOperationError("read-only"),
        ),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        with pytest.raises(ContentOperationError, match="read-only"):
            provider.update_category_id("C_Birds", "C_Parrots")

    assert (content_root / "birds" / "birds.png").read_bytes() == b"icon"
    assert not (content_root / "parrots").exists()
    assert _rows(content_root / "categories_ids.csv") == ["C_Animals", "C_Birds", "C_Colors"]


def test_an_interrupted_category_registry_write_is_completed_on_the_next_access(tmp_path: Path) -> None:
    content_root = _categories(tmp_path, {"categories_en.csv": ["animals", "birds", "colors"]})
    # A worker died after the commit marker, having applied the ids but not the names yet.
    (content_root / "categories_ids.csv").write_text("C_Animals\nC_Colors\n", encoding="utf-8")
    (content_root / ".categories_en.csv.tmp").write_text("animals\ncolors\n", encoding="utf-8")
    (content_root / ".categories_registry.commit").touch()
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        ids_by_folder, names_by_folder = provider._load_category_localized_names()

    assert ids_by_folder == {"animals": "C_Animals", "colors": "C_Colors"}
    assert names_by_folder["colors"] == {"en": "colors"}
    assert not (content_root / ".categories_registry.commit").exists()


def test_concurrent_category_edits_keep_every_file_aligned(tmp_path: Path) -> None:
    content_root = _categories(tmp_path, {
        "categories_en.csv": ["animals", "birds", "colors"],
        "descriptions_en.csv": ["", "", ""],
    })
    edits = [(category_id, round_) for round_ in range(8) for category_id in ("C_Animals", "C_Birds", "C_Colors")]
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = _LANGS
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda edit: provider.update_category_details(
                edit[0], LocalizedString({"fr": f"{edit[0]}-fr"}), LocalizedString({"en": f"{edit[0]}-{edit[1]}"}),
            ), edits))

    assert _rows(content_root / "categories_ids.csv") == ["C_Animals", "C_Birds", "C_Colors"]
    assert _rows(content_root / "categories_en.csv") == ["animals", "birds", "colors"]
    assert _rows(content_root / "categories_fr.csv") == ["C_Animals-fr", "C_Birds-fr", "C_Colors-fr"]
    assert [row.rsplit("-", 1)[0] for row in _rows(content_root / "descriptions_en.csv")] == [
        "C_Animals", "C_Birds", "C_Colors",
    ]


def test_the_category_lock_is_separate_from_the_item_locks(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    (content_root / "foods").mkdir(parents=True)
    with registry_lock(content_root, CATEGORIES), registry_lock(content_root / "foods", ITEMS):
        assert (content_root / ".categories_registry.lock").exists()
        assert (content_root / "foods" / ".items_registry.lock").exists()
