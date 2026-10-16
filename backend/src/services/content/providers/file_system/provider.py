"""File-system backed content provider.

Migrated from src.core.content_service — manages categories and items on the
local filesystem, delegates translation and TTS to internal HTTP endpoints.
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import settings
from src.services.content.models import CategoryConflictError, CategoryNotFoundError, ContentOperationError
from src.services.content.service import ContentProviderFactory
from src.services.content.providers.file_system.constants import (
    CreationMethod,
    _IMAGE_EXTENSIONS,
    _METADATA_COLUMNS,
)
from src.services.content.providers.file_system.categories import FileSystemCategoriesMixin
from src.services.content.providers.file_system.internal_api import FileSystemInternalApiMixin
from src.services.content.providers.file_system.registry import (
    CATEGORIES,
    ITEMS,
    Registry,
    read_registry,
    registry_lock,
)
from src.services.content.providers.file_system.items import FileSystemItemsMixin


_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


class FileSystemContentProvider(
        FileSystemCategoriesMixin,
        FileSystemItemsMixin,
        FileSystemInternalApiMixin,
):
    """Manage content categories and items under the local content directory.

    This class holds what categories and items share — content/archive roots, safe names,
    the CSV registry files, id -> folder lookups, and the image-file and metadata-sidecar
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
        if item_id in self._registered_item_ids(category_path):
            raise CategoryConflictError(f"Item id already exists: {item_id}")
        return item_path

    @staticmethod
    def _archive_root() -> Path:
        return settings.archive_path

    @staticmethod
    def _metadata_path(media_path: Path) -> Path:
        """Return the CSV sidecar path belonging to a voice (MP3) or item image file."""
        return media_path.with_suffix(".csv")

    @classmethod
    def _write_metadata(
            cls,
            media_path: Path,
            creation_method: CreationMethod,
            provider: str = "",
            parameters: dict[str, object] | None = None,
    ) -> None:
        """Persist how a voice or image was created in a one-row CSV next to its file.

        Stamps a "date" parameter with the write time, alongside whatever the caller passed
        (e.g. "_text" for generated voices, "_prompt" for generated images). The "hash"
        column holds the digest of the file as it is now, so the file must already be written.
        """
        all_parameters = dict(parameters or {})
        all_parameters["date"] = datetime.now(timezone.utc).isoformat()
        cls._write_metadata_row(media_path, {
            "creation_method": creation_method.value,
            "provider": provider,
            "parameters": json.dumps(all_parameters, ensure_ascii=False, sort_keys=True),
            "hash": cls._file_digest(media_path) if media_path.is_file() else "",
        })

    @classmethod
    def _read_metadata_row(cls, media_path: Path) -> dict[str, str] | None:
        """Return the sidecar row of *media_path*, or None when it is missing, unreadable or empty."""
        try:
            with cls._metadata_path(media_path).open("r", encoding="utf-8", newline="") as file_handle:
                return next(csv.DictReader(file_handle), None) or None
        except (OSError, csv.Error):
            return None

    @classmethod
    def _stored_hash(cls, media_path: Path) -> str:
        """Return the hash recorded in *media_path*'s sidecar, or "" when there is none."""
        row = cls._read_metadata_row(media_path)
        return ((row or {}).get("hash") or "").strip()

    @classmethod
    def _write_metadata_row(cls, media_path: Path, row: dict[str, str]) -> None:
        """Replace *media_path*'s sidecar with the one-row CSV *row*.

        Written to a hidden temp file and renamed over the sidecar, so a reader or a crash never
        sees half a sidecar: it is the old one or the new one.
        """
        metadata_path = cls._metadata_path(media_path)
        staged = metadata_path.with_name(f".{metadata_path.name}.tmp")
        try:
            with staged.open("w", encoding="utf-8", newline="") as file_handle:
                writer = csv.DictWriter(file_handle, fieldnames=_METADATA_COLUMNS)
                writer.writeheader()
                writer.writerow({column: row.get(column, "") for column in _METADATA_COLUMNS})
            os.replace(staged, metadata_path)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise

    @classmethod
    def _verify_hash(cls, media_path: Path) -> str:
        """Recalculate the hash of *media_path*, repair its sidecar when it disagrees, and return the hash.

        A file without a sidecar has nothing to repair and gets none. A sidecar without a
        hash (written before the column existed) only gains the hash.
        A sidecar whose hash differs from the file's means the file was changed behind the
        application's back, so what the sidecar says about its origin no longer holds: it
        is reset to an unknown creation method by a custom provider, with no parameters.
        """
        actual = cls._file_digest(media_path)
        row = cls._read_metadata_row(media_path)
        stored = ((row or {}).get("hash") or "").strip()
        if row is None or stored == actual:
            return actual
        if not stored:
            cls._write_metadata_row(media_path, {**row, "hash": actual})
        else:
            cls._write_metadata_row(media_path, {
                "creation_method": CreationMethod.UNKNOWN.value,
                "provider": "custom",
                "parameters": "{}",
                "hash": actual,
            })
        return actual

    @classmethod
    def _write_generated_voice_metadata(
            cls,
            voice_path: Path,
            audio_bytes: bytes,
            text: str,
            configuration_name: str | None = None,
    ) -> None:
        """Persist generated-voice settings together with the synthesized text.

        *configuration_name* records the text-to-speech configuration that was explicitly
        chosen for the voice, if any.
        """
        parameters = dict(getattr(audio_bytes, "parameters", {}))
        parameters["_text"] = text.strip()
        if configuration_name:
            parameters["configuration_name"] = configuration_name
        cls._write_metadata(
            voice_path,
            CreationMethod.GENERATED,
            getattr(audio_bytes, "provider", "unknown"),
            parameters,
        )

    @classmethod
    def _read_metadata(cls, media_path: Path) -> tuple[str, dict[str, object]]:
        """Return (provider, parameters) recorded in *media_path*'s CSV sidecar.

        Falls back to ("unknown", {}) when the sidecar is missing, unreadable, or the
        provider/parameters columns are empty or not valid JSON.
        """
        metadata_path = cls._metadata_path(media_path)
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
    def _delete_with_metadata(cls, media_path: Path) -> None:
        media_path.unlink()
        cls._metadata_path(media_path).unlink(missing_ok=True)

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

    def _read_category_registry(self) -> Registry:
        """The category registry (content root), read under its lock."""
        root = self._content_root()
        with registry_lock(root, CATEGORIES):
            return read_registry(root, CATEGORIES)

    def _load_category_localized_names(self) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        """Return (ids_by_folder, names_by_folder) built from the category registry.

        Folder keys are derived from each category's id (see _folder_name_from_category_id),
        matching the on-disk folder name — not from the mutable English display name, which
        can be updated independently of the id/folder (see update_category_id). Names cover
        the configured languages; a blank name is left out.
        """
        registry = self._read_category_registry()
        ids_by_folder: dict[str, str] = {}
        names_by_folder: dict[str, dict[str, str]] = {}
        for index, category_id in enumerate(registry.ids):
            folder = self._folder_name_from_category_id(category_id) if category_id else ""
            if folder:
                ids_by_folder[folder] = category_id
                names_by_folder[folder] = registry.row(index, settings.SUPPORTED_LANGUAGES)["names"]
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
        """Return the configured-language descriptions of *category_name* (a folder name); blanks are left out."""
        registry = self._read_category_registry()
        for index, category_id in enumerate(registry.ids):
            if category_id and self._folder_name_from_category_id(category_id) == category_name.lower():
                return registry.row(index, settings.SUPPORTED_LANGUAGES)["descriptions"]
        return {}

    def _read_item_csv_data(
            self,
            category_path: Path,
    ) -> tuple[list[str], dict[str, list[str]]]:
        """Read item IDs and per-language names from the category CSV files.

        The lists stay row-aligned: rows without an id are dropped from every list together,
        and a blank name stays in place as "". An unreadable file raises ContentOperationError
        rather than reading as empty, which would hide the item or misalign its names.
        """
        with registry_lock(category_path, ITEMS):
            registry = read_registry(category_path, ITEMS)
        rows = [index for index, item_id in enumerate(registry.ids) if item_id]
        item_ids = [registry.ids[index] for index in rows]
        items_by_lang = {
            lang: [names[index] for index in rows]
            for lang, names in registry.names.items() if lang in settings.SUPPORTED_LANGUAGES
        }
        return item_ids, items_by_lang

    def _registered_item_ids(self, category_path: Path) -> list[str]:
        """The category's registered item ids, in registry order."""
        return self._read_item_csv_data(category_path)[0]

    def _item_folder_for_id(self, category_path: Path, item_id: str) -> str | None:
        """Return the filesystem folder name for the given item_id, or None if it isn't registered.

        The folder comes from the id alone; the item's names are never consulted, so
        editing them can't orphan the folder.
        """
        if item_id not in self._registered_item_ids(category_path):
            return None
        return self._folder_name_from_item_id(item_id) or None

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
