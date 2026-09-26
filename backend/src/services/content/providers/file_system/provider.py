"""File-system backed content provider.

Migrated from src.core.content_service — manages categories and items on the
local filesystem, delegates translation and TTS to internal HTTP endpoints.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import settings
from src.services.content.models import CategoryConflictError, CategoryNotFoundError, ContentOperationError
from src.services.content.service import ContentProviderFactory
from src.services.content.providers.file_system.constants import (
    VoiceCreationMethod,
    _CATEGORIES_IDS,
    _CATEGORIES_LANG_CSV,
    _DESCRIPTIONS_LANG_CSV,
    _IMAGE_EXTENSIONS,
    _ITEMS_IDS,
    _ITEMS_LANG_CSV,
    _VOICE_METADATA_COLUMNS,
)
from src.services.content.providers.file_system.categories import FileSystemCategoriesMixin
from src.services.content.providers.file_system.internal_api import FileSystemInternalApiMixin
from src.services.content.providers.file_system.items import FileSystemItemsMixin


_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


class FileSystemContentProvider(
        FileSystemCategoriesMixin,
        FileSystemItemsMixin,
        FileSystemInternalApiMixin,
):
    """Manage content categories and items under the local content directory.

    This class holds what categories and items share — content/archive roots, safe names,
    the CSV registry files, id -> folder lookups, and the image-file and voice-metadata
    helpers — and combines the category, item and internal-API methods from
    categories.py, items.py and internal_api.py.
    """

    @staticmethod
    def _content_root() -> Path:
        return settings.content_path

    def _category_path(self, category: str) -> Path:
        return self._content_root() / category

    def _ensure_content_root_exists(self) -> Path:
        """Return the content root, or raise a diagnosable error when it's missing.

        A missing root is almost always an environment problem, not a per-category one, so
        it's checked before any per-category lookup: CONTENT_DIR misconfigured for this
        process, or (in Docker) a volume that never mounted. Every category/item lookup
        ultimately routes through here so the error names the actual cause instead of
        surfacing as an unrelated "category/id not found".
        """
        root = self._content_root()
        if not root.exists() or not root.is_dir():
            raise ContentOperationError(
                f"Content root '{root}' does not exist (CONTENT_DIR='{settings.CONTENT_DIR}'). "
                "If this is running in a container, verify the content directory is mounted as "
                "a volume (not baked into a read-only image layer) and that CONTENT_DIR resolves "
                "to that mount point inside the container."
            )
        return root

    def _validate_category_path(self, category_id: str) -> tuple[str, Path]:
        """Resolve *category_id* to its folder name and directory, raising if either is missing."""
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")
        return category_name, category_path

    def _validate_item_path(self, category_path: Path, folder_name: str, item_id: str) -> Path:
        """Resolve *folder_name* to the new item's directory, raising if it or its id already exists."""
        item_path = category_path / folder_name
        if item_path.exists():
            raise CategoryConflictError(f"Item folder already exists: {folder_name}")
        if item_id in self._read_csv_column(category_path / _ITEMS_IDS):
            raise CategoryConflictError(f"Item id already exists: {item_id}")
        return item_path

    @staticmethod
    def _archive_root() -> Path:
        return settings.archive_path

    @staticmethod
    def _voice_metadata_path(voice_path: Path) -> Path:
        """Return the CSV sidecar path belonging to an MP3 voice file."""
        return voice_path.with_suffix(".csv")

    @classmethod
    def _write_voice_metadata(
            cls,
            voice_path: Path,
            creation_method: VoiceCreationMethod,
            provider: str = "",
            parameters: dict[str, object] | None = None,
    ) -> None:
        """Persist how a voice was created in a one-row CSV next to its MP3.

        Stamps a "date" parameter with the write time, alongside whatever
        the caller passed (e.g. "_text" for generated voices).
        """
        metadata_path = cls._voice_metadata_path(voice_path)
        all_parameters = dict(parameters or {})
        all_parameters["date"] = datetime.now(timezone.utc).isoformat()
        with metadata_path.open("w", encoding="utf-8", newline="") as file_handle:
            writer = csv.DictWriter(file_handle, fieldnames=_VOICE_METADATA_COLUMNS)
            writer.writeheader()
            writer.writerow({
                "creation_method": creation_method.value,
                "provider": provider,
                "parameters": json.dumps(all_parameters, ensure_ascii=False, sort_keys=True),
            })

    @classmethod
    def _write_generated_voice_metadata(
            cls,
            voice_path: Path,
            audio_bytes: bytes,
            text: str,
    ) -> None:
        """Persist generated-voice settings together with the synthesized text."""
        parameters = dict(getattr(audio_bytes, "parameters", {}))
        parameters["_text"] = text.strip()
        cls._write_voice_metadata(
            voice_path,
            VoiceCreationMethod.GENERATED,
            getattr(audio_bytes, "provider", "unknown"),
            parameters,
        )

    @classmethod
    def _read_voice_metadata(cls, voice_path: Path) -> tuple[str, dict[str, object]]:
        """Return (provider, parameters) recorded for *voice_path*'s CSV sidecar.

        Falls back to ("unknown", {}) when the sidecar is missing, unreadable, or the
        provider/parameters columns are empty or not valid JSON.
        """
        metadata_path = cls._voice_metadata_path(voice_path)
        try:
            with metadata_path.open("r", encoding="utf-8", newline="") as file_handle:
                row = next(csv.DictReader(file_handle), None)
        except (OSError, csv.Error):
            return "unknown", {}
        if not row:
            return "unknown", {}

        provider = (row.get("provider") or "").strip() or "unknown"
        try:
            parameters = json.loads(row.get("parameters") or "{}")
        except (TypeError, ValueError):
            parameters = {}
        if not isinstance(parameters, dict):
            parameters = {}
        return provider, parameters

    @classmethod
    def _delete_voice_with_metadata(cls, voice_path: Path) -> None:
        voice_path.unlink()
        cls._voice_metadata_path(voice_path).unlink(missing_ok=True)

    @staticmethod
    def _is_safe_category_name(name: str) -> bool:
        """Allow alphanumeric and underscore only."""
        return bool(name) and bool(_SAFE_NAME_RE.match(name))

    @staticmethod
    def _normalize_category_name(name: str) -> str:
        """Return the canonical folder name for a requested category name.

        Folder names must be lowercase snake_case: every read path derives the folder
        back from the id via _folder_name_from_category_id(), which snake_cases it, so
        a folder created as 'AtSchool' would never be found again ('at_school').
        """
        return re.sub(r"[\s-]+", "_", (name or "").strip()).lower()

    @staticmethod
    def _append_csv_line(file_path: Path, line: str) -> None:
        """Append one row to a single-column CSV file, creating the file if needed."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        needs_newline = False
        if file_path.exists() and file_path.stat().st_size > 0:
            with open(file_path, "rb") as fh:
                fh.seek(-1, 2)
                needs_newline = fh.read(1) != b"\n"
        with open(file_path, "a", newline="", encoding="utf-8") as fh:
            if needs_newline:
                fh.write("\n")
            csv.writer(fh, lineterminator="\n").writerow([line.strip()])

    @staticmethod
    def _read_csv_rows_aligned(file_path: Path) -> list[str]:
        """Return first-column values, keeping blank rows so row indexes stay aligned.

        Unlike _read_csv_column(), blanks are preserved. The root category registry
        addresses every language CSV by row index, so dropping a blank row here would
        silently shift every category below it onto the wrong name or description.
        """
        try:
            with open(file_path, newline="", encoding="utf-8") as fh:
                return [(r[0].strip() if r else "") for r in csv.reader(fh)]
        except (OSError, csv.Error):
            return []

    @classmethod
    def _set_csv_row_aligned(cls, file_path: Path, index: int, value: str) -> None:
        """Write *value* at *index*, padding with blank rows and preserving existing ones."""
        rows = cls._read_csv_rows_aligned(file_path)
        while len(rows) <= index:
            rows.append("")
        rows[index] = value
        cls._write_csv_rows(file_path, rows)

    @classmethod
    def _remove_csv_row_aligned(cls, file_path: Path, index: int) -> None:
        """Remove the row at *index*, shifting later rows up and preserving blanks."""
        rows = cls._read_csv_rows_aligned(file_path)
        if 0 <= index < len(rows):
            del rows[index]
            cls._write_csv_rows(file_path, rows)

    @staticmethod
    def _write_csv_rows(file_path: Path, rows: list[str]) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            for row in rows:
                writer.writerow([row])

    @staticmethod
    def _normalize_image_extension(image_extension: str) -> str:
        """Ensure a leading dot and fall back to ".png" for an unsupported extension."""
        ext = image_extension if image_extension.startswith(".") else f".{image_extension}"
        if ext.lower() not in _IMAGE_EXTENSIONS:
            ext = ".png"
        return ext

    @staticmethod
    def _write_image_bytes(path: Path, data: bytes) -> None:
        """Write image bytes to *path*, translating common Docker deployment failures.

        A UID/GID mismatch between the host and the container leaves an otherwise correctly
        mounted content directory unwritable by the container's user and surfaces as
        PermissionError; a missing parent directory usually means the volume never mounted at
        all. Both are far more actionable than the generic OSError message they'd otherwise
        produce, so they're reported separately from other I/O failures.
        """
        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except PermissionError as exc:
            raise ContentOperationError(
                f"Permission denied writing '{path.name}' to '{path.parent}'. The content "
                "directory must be writable by the process — check the volume mount permissions "
                "and that the host and container users/UIDs match. "
                f"Original error: {exc}"
            ) from exc
        except FileNotFoundError as exc:
            raise ContentOperationError(
                f"Cannot write '{path.name}': the directory '{path.parent}' does not exist. If "
                f"running in a container, verify CONTENT_DIR ('{settings.CONTENT_DIR}') is set "
                "correctly and that the content directory is mounted into the container. "
                f"Original error: {exc}"
            ) from exc
        except OSError as exc:
            raise ContentOperationError(f"Failed to write '{path.name}': {exc}") from exc

    @staticmethod
    def _first_available_image_index(object_folder: Path) -> int:
        """First 1-based index where no image file exists yet."""
        for i in range(1, 1000):
            if not any(
                    (object_folder / f"{i:02d}{ext}").exists()
                    for ext in _IMAGE_EXTENSIONS
            ):
                return i
        return 1

    def _folder_name_for_id(self, category_id: str) -> str:
        """Return the on-disk folder name registered for *category_id*.

        Pure registry lookup (categories_ids.csv / categories_en.csv) — does not
        check whether the folder currently exists under the content root, so it
        also resolves categories that have been moved to the archive.
        """
        self._ensure_content_root_exists()
        ids_by_folder, _ = self._load_category_localized_names()
        for folder, cid in ids_by_folder.items():
            if cid == category_id:
                return folder
        raise CategoryNotFoundError(f"Category id '{category_id}' is not found in categories_ids.csv.")

    def _load_category_localized_names(self) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        """Return (ids_by_folder, names_by_folder) built from root CSVs.

        Folder keys are derived from each category's id (see _folder_name_from_category_id),
        matching the on-disk folder name — not from the mutable English display name, which
        can be updated independently of the id/folder (see update_category_id).
        """
        root = self._content_root()
        try:
            with open(root / _CATEGORIES_IDS, newline="", encoding="utf-8") as fh:
                id_entries = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
        except (OSError, csv.Error):
            return {}, {}

        folder_keys: list[str] = [self._folder_name_from_category_id(cid) for cid in id_entries]

        ids_by_folder: dict[str, str] = {}
        names_by_folder: dict[str, dict[str, str]] = {}
        for fk, cid in zip(folder_keys, id_entries):
            if fk:
                ids_by_folder[fk] = cid
                names_by_folder[fk] = {}

        for lang in settings.SUPPORTED_LANGUAGES:
            try:
                with open(root / _CATEGORIES_LANG_CSV.format(lang), newline="", encoding="utf-8") as fh:
                    lang_names = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
                for i, fk in enumerate(folder_keys):
                    if fk and i < len(lang_names):
                        names_by_folder[fk][lang] = lang_names[i]
            except (OSError, csv.Error):
                pass

        return ids_by_folder, names_by_folder

    def _category_label_for_language(self, category_name: str, language: str) -> str:
        """Return the category's current localized name for *language*, falling back to English."""
        _, names_by_folder = self._load_category_localized_names()
        names = names_by_folder.get(category_name.lower(), {})
        for lang in (language, "en"):
            label = names.get(lang, "").strip()
            if label:
                return label
        return ""

    def _load_category_localized_descriptions(self, category_name: str) -> dict[str, str]:
        """Return per-language descriptions for *category_name* (a folder name) from descriptions_{lang}.csv.

        Descriptions share row position with the category's entry in categories_ids.csv,
        the same convention used by _load_category_localized_names.
        """
        root = self._content_root()
        try:
            with open(root / _CATEGORIES_IDS, newline="", encoding="utf-8") as fh:
                id_entries = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
        except (OSError, csv.Error):
            return {}

        index = next(
            (
                i for i, cid in enumerate(id_entries)
                if self._folder_name_from_category_id(cid) == category_name.lower()
            ),
            None,
        )
        if index is None:
            return {}

        descriptions: dict[str, str] = {}
        for lang in settings.SUPPORTED_LANGUAGES:
            desc_path = root / _DESCRIPTIONS_LANG_CSV.format(lang)
            try:
                with open(desc_path, newline="", encoding="utf-8") as fh:
                    rows = [r[0].strip() for r in csv.reader(fh) if r]
                if index < len(rows):
                    descriptions[lang] = rows[index]
            except (OSError, csv.Error):
                pass
        return descriptions

    def _read_item_csv_data(
            self,
            category_path: Path,
    ) -> tuple[list[str], dict[str, list[str]]]:
        """Read item IDs and per-language names from the category CSV files."""
        item_ids: list[str] = []
        ids_path = category_path / _ITEMS_IDS
        if ids_path.exists():
            try:
                with open(ids_path, newline="", encoding="utf-8") as fh:
                    item_ids = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
            except (OSError, csv.Error):
                pass

        items_by_lang: dict[str, list[str]] = {}
        for lang in settings.SUPPORTED_LANGUAGES:
            csv_path = category_path / _ITEMS_LANG_CSV.format(lang)
            if csv_path.exists():
                try:
                    with open(csv_path, newline="", encoding="utf-8") as fh:
                        items_by_lang[lang] = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
                except (OSError, csv.Error):
                    items_by_lang[lang] = []

        return item_ids, items_by_lang

    def _category_row_index(self, category_id: str) -> int:
        """Return the CSV row index registered for *category_id* in categories_ids.csv."""
        id_entries = self._read_csv_column(self._content_root() / _CATEGORIES_IDS)
        try:
            return id_entries.index(category_id)
        except ValueError:
            raise CategoryNotFoundError(
                f"Category id '{category_id}' is not found in categories_ids.csv.",
            ) from None

    @staticmethod
    def _read_csv_column(file_path: Path) -> list[str]:
        """Return non-blank first-column values from a single-column CSV file."""
        try:
            with open(file_path, newline="", encoding="utf-8") as fh:
                return [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
        except (OSError, csv.Error):
            return []

    @classmethod
    def _write_csv_row_at_index(cls, file_path: Path, index: int, value: str) -> None:
        """Overwrite the row at *index* in a single-column CSV file, padding with blanks as needed."""
        rows = cls._read_csv_column(file_path)
        while len(rows) <= index:
            rows.append("")
        rows[index] = value
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            for row in rows:
                writer.writerow([row])

    @classmethod
    def _delete_csv_row_at_index(cls, file_path: Path, index: int) -> None:
        """Remove the row at *index* from a single-column CSV file, shifting later rows up."""
        rows = cls._read_csv_column(file_path)
        if 0 <= index < len(rows):
            del rows[index]
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            for row in rows:
                writer.writerow([row])

    def _item_folder_for_id(self, category_path: Path, item_id: str) -> str | None:
        """Return the filesystem folder name for the given item_id, or None if not found."""
        ids_path = category_path / _ITEMS_IDS
        en_path = category_path / _ITEMS_LANG_CSV.format("en")
        try:
            with open(ids_path, newline="", encoding="utf-8") as fh:
                item_ids = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
        except (OSError, csv.Error):
            return None
        try:
            index = item_ids.index(item_id)
        except ValueError:
            return None
        try:
            with open(en_path, newline="", encoding="utf-8") as fh:
                en_names = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
        except (OSError, csv.Error):
            return None
        if index >= len(en_names):
            return None
        return self._english_word_to_folder_name(en_names[index])

    def _resolve_item_path(self, category_path: Path, category_name: str, item_id: str) -> tuple[str, Path]:
        """Return (item_folder_name, item_path) for *item_id*, or raise CategoryNotFoundError."""
        item_folder_name = self._item_folder_for_id(category_path, item_id)
        if item_folder_name:
            item_path = category_path / item_folder_name
            if item_path.exists() and item_path.is_dir():
                return item_folder_name, item_path
        raise CategoryNotFoundError(f"Item '{item_id}' not found in category '{category_name}'.")

    def _item_word_for_language(self, category_path: Path, item_id: str, language: str) -> str:
        """Return the item's current word for *language*, falling back to English."""
        item_ids, items_by_lang = self._read_item_csv_data(category_path)
        try:
            index = item_ids.index(item_id)
        except ValueError:
            return ""
        for lang in (language, "en"):
            words = items_by_lang.get(lang, [])
            if index < len(words) and words[index].strip():
                return words[index].strip()
        return ""

    @classmethod
    def register(cls) -> None:
        """Register this provider in the content provider factory."""
        ContentProviderFactory.register("file_system", cls)


FileSystemContentProvider.register()
