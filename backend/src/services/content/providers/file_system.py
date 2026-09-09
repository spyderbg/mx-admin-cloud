"""File-system backed content provider.

Migrated from src.core.content_service — manages categories and items on the
local filesystem, delegates translation and TTS to internal HTTP endpoints.
"""

from __future__ import annotations

import base64
import csv
import json
import re
import shutil
import time
from datetime import timedelta
from enum import Enum
from pathlib import Path

import httpx

from src.core.config import settings
from src.core.localization import LocalizedString
from src.security.tokens import create_internal_token
from src.services.content.models import (
    ArchiveCategory,
    ArchiveUnavailableError,
    ArchivedEntry,
    CategoryConflictError,
    CategoryDetails,
    CategoryImage,
    CategoryList,
    CategoryNotFoundError,
    CategorySummary,
    CategoryVoice,
    ContentError,
    ContentOperationError,
    ContentProvider,
    CreateCategory,
    CreateItem,
    DeleteArchivedCategory,
    InvalidCategoryNameError,
    ItemDetail,
    ItemImages,
    ItemVoices,
    RemoveItem,
    RenameCategory,
    RestoreCategory,
)
from src.services.content.service import ContentProviderFactory

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def _extension_for_content_type(content_type: str | None) -> str:
    """Map an image response content type onto a supported file extension."""
    if content_type and content_type.split(";", 1)[0].strip().lower() in ("image/jpeg", "image/jpg"):
        return ".jpg"
    return ".png"


# --- File name constants ---
_CATEGORIES_IDS = "categories_ids.csv"
_CATEGORIES_LANG_CSV = "categories_{}.csv"
_DESCRIPTIONS_LANG_CSV = "descriptions_{}.csv"
_ITEMS_IDS = "items_ids.csv"
_ITEMS_LANG_CSV = "items_{}.csv"
_CATEGORY_VOICE_MP3 = "{}_{}.mp3"
_VOICE_METADATA_COLUMNS = ("creation_method", "provider", "parameters")

# Written into a category folder when it is archived, so restore_category() can rebuild
# the root CSV registry rows that archive_category() deletes.
_ARCHIVE_MANIFEST = "_category.json"


class VoiceCreationMethod(str, Enum):
    """Supported voice provenance values stored in metadata sidecars."""

    UPLOADED = "uploaded"
    GENERATED = "generated"


class _GeneratedAudioBytes(bytes):
    """Audio payload carrying the provider reported by the internal TTS API."""

    provider: str
    parameters: dict[str, object]

    def __new__(cls, content: bytes, provider: str, parameters: dict[str, object]):
        instance = super().__new__(cls, content)
        instance.provider = provider
        instance.parameters = parameters
        return instance


class FileSystemContentProvider(ContentProvider):
    """Manage content categories and items under the local content directory."""

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _content_root() -> Path:
        return settings.content_path

    def _category_path(self, category: str) -> Path:
        return self._content_root() / category

    @staticmethod
    def _archive_root() -> Path:
        return settings.archive_path

    # ------------------------------------------------------------------
    # Internal HTTP callers (respect HTTP boundary rule)
    # ------------------------------------------------------------------

    @staticmethod
    def _translate_word(
            text: str,
            target_lang_code: str | None = None,
            category: str | None = None,
            input_lang_code: str | None = None,
            *,
            require_success: bool = False,
    ) -> str:
        """Translate text via the internal /internal/translators/translate endpoint.

        The endpoint answers 200 with ``success: false`` and the untranslated input when
        no translator produced a result. With *require_success* that case raises instead
        of silently returning the source text.
        """
        if not text or not text.strip():
            return text.strip()

        token = create_internal_token("translation-service", timedelta(seconds=30))
        response = httpx.post(
            f"{settings.API_BASE_URL}/internal/translators/translate",
            json={
                "text": text.strip(),
                "target_lang_code": target_lang_code,
                "input_lang_code": input_lang_code,
                "category": category,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        if response.is_error:
            raise RuntimeError(FileSystemContentProvider._extract_error_detail(response))
        payload = response.json()
        if require_success and not payload.get("success", False):
            errors = payload.get("errors") or []
            if errors:
                raise RuntimeError("; ".join(errors))
            raise RuntimeError(f"No active translator produced a translation for '{target_lang_code}'.")
        translated_text = payload["lang_text"]["text"]
        return translated_text or text.strip()

    @staticmethod
    def _extract_error_detail(response: httpx.Response) -> str:
        """Return the internal endpoint's JSON 'detail' field, falling back to raw response text."""
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        return str(detail) if detail else response.text

    @staticmethod
    def _generate_audio(word: str, lang: str) -> bytes:
        """Generate TTS audio via the internal /internal/generators/generate-word endpoint."""
        if not word or not word.strip():
            raise ValueError("Word must be non-empty for TTS.")

        token = create_internal_token("tts-service", timedelta(seconds=30))
        response = httpx.post(
            f"{settings.API_BASE_URL}/internal/generators/generate-word",
            json={"word": word.strip(), "lang": lang},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )
        if response.is_error:
            raise RuntimeError(FileSystemContentProvider._extract_error_detail(response))
        encoded_parameters = response.headers.get("X-TTS-Parameters", "")
        try:
            parameters = json.loads(base64.urlsafe_b64decode(encoded_parameters).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parameters = {}
        if not isinstance(parameters, dict):
            parameters = {}
        return _GeneratedAudioBytes(
            response.content,
            response.headers.get("X-TTS-Provider", "unknown"),
            parameters,
        )

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
        """Persist how a voice was created in a one-row CSV next to its MP3."""
        metadata_path = cls._voice_metadata_path(voice_path)
        with metadata_path.open("w", encoding="utf-8", newline="") as file_handle:
            writer = csv.DictWriter(file_handle, fieldnames=_VOICE_METADATA_COLUMNS)
            writer.writeheader()
            writer.writerow({
                "creation_method": creation_method.value,
                "provider": provider,
                "parameters": json.dumps(parameters or {}, ensure_ascii=False, sort_keys=True),
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
    def _delete_voice_with_metadata(cls, voice_path: Path) -> None:
        voice_path.unlink()
        cls._voice_metadata_path(voice_path).unlink(missing_ok=True)

    @staticmethod
    def _generate_image(prompt: str, filename_text: str | None = None) -> tuple[bytes, str]:
        """Generate an image via the internal /internal/image-generators/generate-image endpoint.

        Returns the raw bytes plus the file extension implied by the response
        content type, so whichever image provider is active (and however it
        encodes the result) decides the format.
        """
        if not prompt or not prompt.strip():
            raise ValueError("Prompt must be non-empty for image generation.")

        token = create_internal_token("image-service", timedelta(seconds=30))
        response = httpx.post(
            f"{settings.API_BASE_URL}/internal/image-generators/generate-image",
            json={"prompt": prompt.strip(), "filename_text": filename_text},
            headers={"Authorization": f"Bearer {token}"},
            # Local render backends (e.g. ComfyUI) can legitimately take minutes.
            timeout=httpx.Timeout(600.0, connect=10.0),
        )
        if response.is_error:
            raise RuntimeError(FileSystemContentProvider._extract_error_detail(response))
        return response.content, _extension_for_content_type(response.headers.get("content-type"))

    # ------------------------------------------------------------------
    # Filesystem utilities
    # ------------------------------------------------------------------

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
    def _category_name_to_icon_prompt(category_name: str) -> str:
        """Derive a short, child-friendly icon prompt from a category name."""
        label = category_name.replace("_", " ").strip()
        if not label:
            return category_name
        label = label.title()
        return f"Simple, friendly icon for children: {label}"

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
    def _first_available_image_index(object_folder: Path) -> int:
        """First 1-based index where no image file exists yet."""
        for i in range(1, 1000):
            if not any(
                    (object_folder / f"{i:02d}{ext}").exists()
                    for ext in _IMAGE_EXTENSIONS
            ):
                return i
        return 1

    @staticmethod
    def _first_available_category_image_index(category_path: Path, category_name: str) -> int:
        """First 1-based index where no ``{category_name}_{index:02d}.{ext}`` file exists yet."""
        for i in range(1, 1000):
            if not any(
                    (category_path / f"{category_name}_{i:02d}{ext}").exists()
                    for ext in _IMAGE_EXTENSIONS
            ):
                return i
        return 1

    @staticmethod
    def _find_category_image_file(category_path: Path, image_id: str) -> Path | None:
        """Return the existing category image file whose stem matches *image_id*, if any."""
        if not FileSystemContentProvider._is_safe_category_name(image_id):
            return None
        for ext in _IMAGE_EXTENSIONS:
            candidate = category_path / f"{image_id}{ext}"
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _find_item_image_by_id(item_path: Path, image_id: str) -> Path | None:
        """Return the existing item image file whose stem matches *image_id*, if any."""
        if not FileSystemContentProvider._is_safe_category_name(image_id):
            return None
        for ext in _IMAGE_EXTENSIONS:
            candidate = item_path / f"{image_id}{ext}"
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _first_available_item_image_index(item_path: Path, item_folder_name: str) -> int:
        """First 1-based index where no ``{item_folder_name}_{index:02d}.{ext}`` file exists yet."""
        for i in range(1, 1000):
            if not any(
                    (item_path / f"{item_folder_name}_{i:02d}{ext}").exists()
                    for ext in _IMAGE_EXTENSIONS
            ):
                return i
        return 1

    @staticmethod
    def _category_voice_path(category_path: Path, category_name: str, language: str) -> Path:
        return category_path / _CATEGORY_VOICE_MP3.format(category_name, language)

    @staticmethod
    def _find_category_voice_file(
            category_path: Path,
            category_name: str,
            voice_id: str,
    ) -> tuple[str, Path] | None:
        """Return the (language, path) for the existing voice file matching *voice_id*, if any."""
        for lang in settings.SUPPORTED_LANGUAGES:
            if voice_id != f"{category_name}-v-{lang}":
                continue
            voice_path = FileSystemContentProvider._category_voice_path(category_path, category_name, lang)
            if voice_path.is_file():
                return lang, voice_path
        return None

    @staticmethod
    def _item_voice_path(item_path: Path, item_folder_name: str, language: str) -> Path:
        return item_path / _CATEGORY_VOICE_MP3.format(item_folder_name, language)

    @staticmethod
    def _language_for_item_voice_id(item_folder_name: str, voice_id: str) -> str | None:
        """Return the language encoded in *voice_id* for this item, or None if unrecognized."""
        for lang in settings.SUPPORTED_LANGUAGES:
            if voice_id == f"{item_folder_name}-v-{lang}":
                return lang
        return None

    @staticmethod
    def _find_item_voice_file(
            item_path: Path,
            item_folder_name: str,
            voice_id: str,
    ) -> tuple[str, Path] | None:
        """Return the (language, path) for the existing item voice file matching *voice_id*, if any.

        Tolerates an optional numeric suffix (e.g. ``{name}_en_01.mp3``) to match how
        ``item_voices`` locates files created by different content pipelines.
        """
        lang = FileSystemContentProvider._language_for_item_voice_id(item_folder_name, voice_id)
        if lang is None:
            return None
        pattern = re.compile(
            r"^" + re.escape(item_folder_name) + r"_" + re.escape(lang) + r"(_\d+)?\.mp3$",
            re.IGNORECASE,
        )
        match = next(
            (f for f in sorted(item_path.iterdir()) if f.is_file() and pattern.match(f.name)),
            None,
        )
        return (lang, match) if match else None

    def _folder_name_for_id(self, category_id: str) -> str:
        """Return the on-disk folder name registered for *category_id*.

        Pure registry lookup (categories_ids.csv / categories_en.csv) — does not
        check whether the folder currently exists under the content root, so it
        also resolves categories that have been moved to the archive.
        """
        ids_by_folder, _ = self._load_category_localized_names()
        for folder, cid in ids_by_folder.items():
            if cid == category_id:
                return folder
        raise CategoryNotFoundError(f"Category id '{category_id}' is not found in categories_ids.csv.")

    def _list_archived_entries(self) -> list[ArchivedEntry]:
        """Scan archive root for folders matching ``{base}_{timestamp}``."""
        if not settings.ARCHIVE_DIR or not settings.ARCHIVE_DIR.strip():
            return []
        archive_root = self._archive_root()
        if not archive_root.exists() or not archive_root.is_dir():
            return []
        entries: list[ArchivedEntry] = []
        for p in archive_root.iterdir():
            if p.is_dir() and not p.name.startswith("."):
                m = re.match(r"^(.+)_(\d+)$", p.name)
                if m:
                    entries.append(ArchivedEntry(
                        base_name=m.group(1),
                        archive_folder=p.name,
                        archived_at=int(m.group(2)),
                    ))
        return sorted(entries, key=lambda e: e.archived_at, reverse=True)

    @staticmethod
    def _archive_folders_for_base(archive_root: Path, base_name: str) -> list[tuple[str, int]]:
        """Return (folder_name, timestamp) pairs for matching archive folders."""
        candidates: list[tuple[str, int]] = []
        for p in archive_root.iterdir():
            if p.is_dir() and not p.name.startswith("."):
                m = re.match(r"^" + re.escape(base_name) + r"_(\d+)$", p.name)
                if m:
                    candidates.append((p.name, int(m.group(1))))
        return sorted(candidates, key=lambda x: x[1], reverse=True)

    def _load_category_localized_names(self) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        """Return (ids_by_folder, names_by_folder) built from root CSVs.

        Folder keys are derived from each category's id (see _folder_name_from_category_id),
        matching the on-disk folder name — not from the mutable English display name, which
        can be updated independently of the id/folder (see rename_category).
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

    def _register_category_rows(
            self,
            category_id: str,
            names: dict[str, str],
            descriptions: dict[str, str] | None = None,
    ) -> int:
        """Add *category_id* to the root CSV registry and return its row index.

        Reuses an existing row when the id is already registered, so re-creating a
        previously removed category cannot leave two rows with the same id — which
        _category_row_index() would resolve to the first of them.
        """
        root = self._content_root()
        ids = self._read_csv_rows_aligned(root / _CATEGORIES_IDS)
        if category_id in ids:
            index = ids.index(category_id)
        else:
            index = len(ids)
            self._set_csv_row_aligned(root / _CATEGORIES_IDS, index, category_id)

        for lang in settings.SUPPORTED_LANGUAGES:
            label = names.get(lang, "")
            if label:
                self._set_csv_row_aligned(root / _CATEGORIES_LANG_CSV.format(lang), index, label)
            description = (descriptions or {}).get(lang, "")
            if description:
                self._set_csv_row_aligned(root / _DESCRIPTIONS_LANG_CSV.format(lang), index, description)
        return index

    def _unregister_category_rows(self, category_id: str) -> None:
        """Remove *category_id*'s row from the root id, name and description CSVs."""
        root = self._content_root()
        ids = self._read_csv_rows_aligned(root / _CATEGORIES_IDS)
        if category_id not in ids:
            return
        index = ids.index(category_id)
        for lang in settings.SUPPORTED_LANGUAGES:
            self._remove_csv_row_aligned(root / _CATEGORIES_LANG_CSV.format(lang), index)
            self._remove_csv_row_aligned(root / _DESCRIPTIONS_LANG_CSV.format(lang), index)
        self._remove_csv_row_aligned(root / _CATEGORIES_IDS, index)

    def _write_archive_manifest(self, category_path: Path, category_id: str) -> None:
        """Persist the category's registry rows into the folder about to be archived."""
        folder_name = category_path.name
        _, names_by_folder = self._load_category_localized_names()
        manifest = {
            "id": category_id,
            "folder": folder_name,
            "names": names_by_folder.get(folder_name.lower(), {}),
            "descriptions": self._load_category_localized_descriptions(folder_name),
        }
        try:
            with open(category_path / _ARCHIVE_MANIFEST, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise ContentOperationError(f"Failed to write archive manifest: {exc}") from exc

    @staticmethod
    def _read_archive_manifest(category_path: Path, base_name: str) -> dict[str, dict[str, str] | str]:
        """Read a restored folder's manifest, deriving defaults for pre-manifest archives."""
        fallback: dict[str, dict[str, str] | str] = {
            "id": f"C_{base_name}",
            "folder": base_name,
            "names": {"en": base_name.replace("_", " ")},
            "descriptions": {},
        }
        manifest_path = category_path / _ARCHIVE_MANIFEST
        if not manifest_path.is_file():
            return fallback
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, ValueError):
            return fallback
        if not isinstance(loaded, dict) or not loaded.get("id"):
            return fallback
        return {
            "id": str(loaded["id"]),
            "folder": str(loaded.get("folder") or base_name),
            "names": loaded.get("names") or fallback["names"],
            "descriptions": loaded.get("descriptions") or {},
        }

    # ------------------------------------------------------------------
    # ContentProvider interface
    # ------------------------------------------------------------------

    def list_categories(
            self,
            include_archived: bool = False,
            archived_only: bool = False,
    ) -> CategoryList:
        archived = self._list_archived_entries() if (include_archived or archived_only) else []

        if archived_only:
            return CategoryList(categories=[], archived=archived)

        root = self._content_root()
        if not root.exists() or not root.is_dir():
            raise ContentOperationError(f"Content root does not exist: {root}")

        ids_by_folder, names_by_folder = self._load_category_localized_names()
        summaries = []
        for p in sorted(root.iterdir(), key=lambda x: x.name):
            if p.is_dir() and not p.name.startswith("."):
                items_count = sum(1 for c in p.iterdir() if c.is_dir() and not c.name.startswith("."))
                fn = p.name.lower()
                icon_path = next(
                    (p / f"{p.name}{ext}" for ext in _IMAGE_EXTENSIONS if (p / f"{p.name}{ext}").is_file()),
                    None,
                )
                summaries.append(CategorySummary(
                    id=ids_by_folder.get(fn, p.name),
                    name=LocalizedString(names_by_folder.get(fn, {})),
                    items_count=items_count,
                    thumbnail_uri=f"/content/{p.name}/{icon_path.name}" if icon_path else "",
                ))
        return CategoryList(
            categories=summaries,
            archived=archived if include_archived else [],
        )

    def category_id_by_name(self, category_name: str) -> str:
        if not self._is_safe_category_name(category_name):
            raise InvalidCategoryNameError(category_name)
        ids_by_folder, _ = self._load_category_localized_names()
        cid = ids_by_folder.get(category_name.lower())
        if cid:
            return cid
        raise CategoryNotFoundError(f"Category '{category_name}': ID is not found in categories_ids.csv.")

    def category_details(self, category_id: str) -> CategoryDetails:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")

        _, names_by_folder = self._load_category_localized_names()
        category_localized_name = LocalizedString(names_by_folder.get(category_name.lower(), {}))

        item_ids, items_by_lang = self._read_item_csv_data(category_path)
        description = LocalizedString(self._load_category_localized_descriptions(category_name))

        return CategoryDetails(
            id=category_id,
            name=category_localized_name,
            description=description,
            items=self._collect_items(category_path, category_name, item_ids, items_by_lang),
            images=self._collect_images(category_path, category_name),
            voices=self._collect_voices(category_path, category_name),
        )

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

    def _collect_items(
            self,
            category_path: Path,
            category_name: str,
            item_ids: list[str],
            items_by_lang: dict[str, list[str]],
    ) -> list[ItemDetail]:
        """Build ItemDetail list from CSV data and filesystem scan."""
        en_words = items_by_lang.get("en", [])
        items = []
        for i, iid in enumerate(item_ids):
            thumbnail_uri = ""
            images_count = 0
            if i < len(en_words):
                folder_name = self._english_word_to_folder_name(en_words[i])
                item_folder = category_path / folder_name
                if item_folder.is_dir():
                    image_files = sorted(
                        f for f in item_folder.iterdir()
                        if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS
                    )
                    images_count = len(image_files)
                    if image_files:
                        thumbnail_uri = f"/content/{category_name}/{folder_name}/{image_files[0].name}"
            items.append(ItemDetail(
                id=iid,
                name=LocalizedString({lang: names[i] for lang, names in items_by_lang.items() if i < len(names)}),
                thumbnail_uri=thumbnail_uri,
                images_count=images_count,
            ))
        return items

    def _collect_images(
            self,
            category_path: Path,
            category_name: str,
    ) -> list[CategoryImage]:
        """Collect category-level images named {category_name}[_{index}].{ext} from the category root."""
        pattern = re.compile(
            r"^" + re.escape(category_name) + r"(_\d+)?\.(png|jpg|jpeg)$",
            re.IGNORECASE,
        )
        images = []
        for f in sorted(category_path.iterdir()):
            if f.is_file() and pattern.match(f.name):
                images.append(CategoryImage(
                    id=f.stem,
                    file_uri=f"/content/{category_name}/{f.name}",
                ))
        return images

    def _collect_voices(
            self,
            category_path: Path,
            category_name: str,
    ) -> list[CategoryVoice]:
        """Build one CategoryVoice entry per supported language.

        A language with no TTS file yet is still included (file_uri=None) so
        callers can render a placeholder/upload slot for it.
        """
        voices = []
        for lang in settings.SUPPORTED_LANGUAGES:
            voice_file = category_path / _CATEGORY_VOICE_MP3.format(category_name, lang)
            has_file = voice_file.exists() and voice_file.is_file()
            voices.append(CategoryVoice(
                id=f"{category_name}-v-{lang}",
                language=lang,
                type="tts",
                file_uri=f"/content/{category_name}/{voice_file.name}" if has_file else None,
            ))
        return voices

    def create_category(self, category_name: str) -> CreateCategory:
        category_name = self._normalize_category_name(category_name)
        if not self._is_safe_category_name(category_name):
            raise InvalidCategoryNameError(category_name)
        category_path = self._category_path(category_name)
        if category_path.exists() and category_path.is_dir():
            raise CategoryConflictError(f"Category already exists: {category_name}")

        category_path.mkdir(parents=True, exist_ok=True)
        # PascalCase to match the registry convention ('at_school' -> 'C_AtSchool'), so the id
        # round-trips through _folder_name_from_category_id() and cannot collide with an
        # existing row for the same folder under different casing.
        category_id = f"C_{self._to_pascal(category_name)}"

        try:
            description_en = f"Explore fun items about {category_name.replace('_', ' ').strip() or category_name}."
            labels_by_lang: dict[str, str] = {}
            descriptions_by_lang: dict[str, str] = {"en": description_en}
            for lang in settings.SUPPORTED_LANGUAGES:
                if lang == "en":
                    label = category_name.replace("_", " ").strip() or category_name
                else:
                    try:
                        label = self._translate_word(
                            category_name.replace("_", " ").strip() or category_name, lang, category_name,
                        )
                        descriptions_by_lang[lang] = self._translate_word(description_en, lang, category_name)
                    except (ValueError, RuntimeError, httpx.HTTPError, KeyError) as exc:
                        raise ContentOperationError(f"Translation failed for {lang}: {exc}") from exc
                labels_by_lang[lang] = label
                try:
                    audio_bytes = self._generate_audio(label, lang)
                    voice_path = category_path / _CATEGORY_VOICE_MP3.format(category_name, lang)
                    with open(voice_path, "wb") as fh:
                        fh.write(audio_bytes)
                    self._write_generated_voice_metadata(voice_path, audio_bytes, label)
                except (ValueError, RuntimeError, httpx.HTTPError) as exc:
                    raise ContentOperationError(f"TTS failed for {lang}: {exc}") from exc
                except OSError as exc:
                    raise ContentOperationError(f"File write failed for {lang}: {exc}") from exc

            try:
                prompt = self._category_name_to_icon_prompt(category_name)
                icon_bytes, icon_extension = self._generate_image(prompt, category_name)
                with open(category_path / f"{category_name}{icon_extension}", "wb") as fh:
                    fh.write(icon_bytes)
            except (ValueError, RuntimeError, httpx.HTTPError) as exc:
                raise ContentOperationError(f"Icon generation failed: {exc}") from exc
            except OSError as exc:
                raise ContentOperationError(f"Icon file write failed: {exc}") from exc

            fallback_label = category_name.replace("_", " ").strip()
            self._register_category_rows(
                category_id,
                {lang: labels_by_lang.get(lang) or fallback_label for lang in settings.SUPPORTED_LANGUAGES},
                {lang: descriptions_by_lang.get(lang) or description_en for lang in settings.SUPPORTED_LANGUAGES},
            )
        except Exception:
            # Creation failed partway through: leave no trace (no orphaned folder, no
            # partial/registry row) rather than a half-created category.
            shutil.rmtree(category_path, ignore_errors=True)
            self._unregister_category_rows(category_id)
            raise

        return CreateCategory(category=category_name, message=f"Category '{category_name}' created.")

    def _translate_item_word(self, english_word: str, category_name: str) -> dict[str, str]:
        """Translate *english_word* into every non-English supported language.

        Runs before any disk write so a failing translator leaves no trace. Raises
        ContentOperationError on the first failure, including a translator that reports
        no successful translation.
        """
        words_by_lang: dict[str, str] = {"en": english_word}
        for lang in settings.SUPPORTED_LANGUAGES:
            if lang == "en":
                continue
            try:
                words_by_lang[lang] = self._translate_word(
                    english_word, lang, category_name, require_success=True,
                )
            except (ValueError, RuntimeError, httpx.HTTPError, KeyError) as exc:
                raise ContentOperationError(f"Translation failed for {lang}: {exc}") from exc
        return words_by_lang

    def _generate_item_voices(
            self,
            item_path: Path,
            folder_name: str,
            words_by_lang: dict[str, str],
    ) -> tuple[list[str], list[str]]:
        """Generate one voice file per supported language, best effort.

        Returns (languages generated, warnings). Never raises: a failing language is
        reported and the remaining languages are still attempted, except when the TTS
        service reports that no provider is active, which would fail for every language.
        """
        generated: list[str] = []
        warnings: list[str] = []
        for lang in settings.SUPPORTED_LANGUAGES:
            word = words_by_lang.get(lang) or words_by_lang["en"]
            try:
                audio_bytes = self._generate_audio(word, lang)
                voice_path = self._item_voice_path(item_path, folder_name, lang)
                voice_path.write_bytes(audio_bytes)
                self._write_generated_voice_metadata(voice_path, audio_bytes, word)
            except (ValueError, RuntimeError, httpx.HTTPError) as exc:
                warnings.append(f"Voice generation failed for {lang}: {exc}")
                if "no active text to speech" in str(exc).lower():
                    break
            except OSError as exc:
                warnings.append(f"Voice file write failed for {lang}: {exc}")
            else:
                generated.append(lang)
        return generated, warnings

    def create_item(  # pylint: disable=too-many-locals
            self,
            category_id: str,
            english_word: str,
            image_data: bytes | None = None,
            image_extension: str = ".png",
            generate_voices: bool = True,
    ) -> CreateItem:
        category_name = self._folder_name_for_id(category_id)
        if not english_word or not english_word.strip():
            raise ContentOperationError("Item word is required")
        english_word = english_word.strip()

        folder_name = self._english_word_to_folder_name(english_word)
        item_id = self._english_word_to_image_id(english_word)
        if not folder_name:
            raise ContentOperationError("Could not derive folder name from item word")

        category_path = self._category_path(category_name)
        if not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")
        item_path = category_path / folder_name
        if item_path.exists():
            raise CategoryConflictError(f"Item folder already exists: {folder_name}")
        if item_id in self._read_csv_column(category_path / _ITEMS_IDS):
            raise CategoryConflictError(f"Item id already exists: {item_id}")

        # Translate before touching the disk: a failing translator must not create the item.
        words_by_lang = self._translate_item_word(english_word, category_name)

        # (path, index of the appended row, whether the file existed before the append)
        appended: list[tuple[Path, int, bool]] = []
        try:
            item_path.mkdir(parents=True, exist_ok=False)
            if image_data:
                ext = image_extension if image_extension.startswith(".") else f".{image_extension}"
                if ext.lower() not in _IMAGE_EXTENSIONS:
                    ext = ".png"
                idx = self._first_available_item_image_index(item_path, folder_name)
                (item_path / f"{folder_name}_{idx:02d}{ext}").write_bytes(image_data)

            targets = [(category_path / _ITEMS_IDS, item_id)] + [
                (category_path / _ITEMS_LANG_CSV.format(lang), words_by_lang[lang])
                for lang in settings.SUPPORTED_LANGUAGES
            ]
            for path, value in targets:
                existed = path.exists()
                row_index = len(self._read_csv_column(path))
                self._append_csv_line(path, value)
                appended.append((path, row_index, existed))
        except Exception as exc:
            # Leave no trace: no orphaned folder and no partial registry rows, which would
            # misalign the per-language CSV files for every item after this one.
            shutil.rmtree(item_path, ignore_errors=True)
            for path, row_index, existed in appended:
                if existed:
                    self._delete_csv_row_at_index(path, row_index)
                else:
                    path.unlink(missing_ok=True)
            if isinstance(exc, ContentError):
                raise
            raise ContentOperationError(f"Failed to write item files: {exc}") from exc

        voices_generated: list[str] = []
        voice_warnings: list[str] = []
        if generate_voices:
            voices_generated, voice_warnings = self._generate_item_voices(item_path, folder_name, words_by_lang)

        message = f"Item '{english_word}' created."
        if generate_voices and not voices_generated:
            message += " Voices were not generated."

        return CreateItem(
            category=category_name,
            item=english_word,
            folder=folder_name,
            image_id=item_id,
            item_id=item_id,
            voices_generated=voices_generated,
            voice_warnings=voice_warnings,
            message=message,
        )

    def update_item_name(
            self,
            category_id: str,
            item_id: str,
            item_name: LocalizedString,
    ) -> ItemDetail:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        self._resolve_item_path(category_path, category_name, item_id)

        index = self._read_csv_column(category_path / _ITEMS_IDS).index(item_id)
        for lang, value in item_name.to_dict().items():
            if lang in settings.SUPPORTED_LANGUAGES:
                self._write_csv_row_at_index(category_path / _ITEMS_LANG_CSV.format(lang), index, value)

        item_ids, items_by_lang = self._read_item_csv_data(category_path)
        updated = next(
            (
                item for item in self._collect_items(category_path, category_name, item_ids, items_by_lang)
                if item.id == item_id
            ),
            None,
        )
        if updated is None:
            raise CategoryNotFoundError(f"Item '{item_id}' not found in category '{category_name}'.")
        return updated

    def remove_item(self, category_id: str, item_id: str) -> RemoveItem:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        _, item_path = self._resolve_item_path(category_path, category_name, item_id)

        index = self._read_csv_column(category_path / _ITEMS_IDS).index(item_id)
        try:
            shutil.rmtree(item_path)
        except (OSError, shutil.Error) as exc:
            raise ContentOperationError(f"Failed to delete item: {exc}") from exc

        self._delete_csv_row_at_index(category_path / _ITEMS_IDS, index)
        for lang in settings.SUPPORTED_LANGUAGES:
            self._delete_csv_row_at_index(category_path / _ITEMS_LANG_CSV.format(lang), index)

        return RemoveItem(category=category_name, item=item_id)

    def add_category_image(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")

        ext = image_extension if image_extension.startswith(".") else f".{image_extension}"
        if ext.lower() not in _IMAGE_EXTENSIONS:
            ext = ".png"

        idx = self._first_available_category_image_index(category_path, category_name)
        file_name = f"{category_name}_{idx:02d}{ext}"
        image_path = category_path / file_name
        try:
            with open(image_path, "wb") as fh:
                fh.write(image_data)
        except OSError as exc:
            raise ContentOperationError(f"Failed to save category image: {exc}") from exc

        return CategoryImage(id=image_path.stem, file_uri=f"/content/{category_name}/{file_name}")

    def update_category_image(
            self,
            category_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")

        existing = self._find_category_image_file(category_path, image_id)
        if existing is None:
            raise CategoryNotFoundError(f"Image '{image_id}' not found in category '{category_name}'.")

        ext = image_extension if image_extension.startswith(".") else f".{image_extension}"
        if ext.lower() not in _IMAGE_EXTENSIONS:
            ext = ".png"

        new_path = category_path / f"{image_id}{ext}"
        try:
            if existing != new_path:
                existing.unlink()
            with open(new_path, "wb") as fh:
                fh.write(image_data)
        except OSError as exc:
            raise ContentOperationError(f"Failed to update category image: {exc}") from exc

        return CategoryImage(id=new_path.stem, file_uri=f"/content/{category_name}/{new_path.name}")

    def delete_category_image(self, category_id: str, image_id: str) -> CategoryImage:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")

        existing = self._find_category_image_file(category_path, image_id)
        if existing is None:
            raise CategoryNotFoundError(f"Image '{image_id}' not found in category '{category_name}'.")

        file_uri = f"/content/{category_name}/{existing.name}"
        try:
            existing.unlink()
        except OSError as exc:
            raise ContentOperationError(f"Failed to delete category image: {exc}") from exc

        return CategoryImage(id=image_id, file_uri=file_uri)

    def set_category_icon(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Create or replace the category's icon file, regardless of whether one already exists."""
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")

        ext = image_extension if image_extension.startswith(".") else f".{image_extension}"
        if ext.lower() not in _IMAGE_EXTENSIONS:
            ext = ".png"

        existing = self._find_category_image_file(category_path, category_name)
        new_path = category_path / f"{category_name}{ext}"
        try:
            if existing is not None and existing != new_path:
                existing.unlink()
            with open(new_path, "wb") as fh:
                fh.write(image_data)
        except OSError as exc:
            raise ContentOperationError(f"Failed to save category icon: {exc}") from exc

        return CategoryImage(id=new_path.stem, file_uri=f"/content/{category_name}/{new_path.name}")

    def add_category_voice(self, category_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")

        if language not in settings.SUPPORTED_LANGUAGES:
            raise ContentOperationError(f"Unsupported language: {language}")

        voice_path = self._category_voice_path(category_path, category_name, language)
        if voice_path.exists():
            raise CategoryConflictError(
                f"Voice for language '{language}' already exists in category '{category_name}'.",
            )

        try:
            with open(voice_path, "wb") as fh:
                fh.write(audio_data)
            self._write_voice_metadata(voice_path, VoiceCreationMethod.UPLOADED)
        except OSError as exc:
            raise ContentOperationError(f"Failed to save category voice: {exc}") from exc

        return CategoryVoice(
            id=f"{category_name}-v-{language}",
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{voice_path.name}",
        )

    def update_category_voice(self, category_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")

        found = self._find_category_voice_file(category_path, category_name, voice_id)
        if found is None:
            raise CategoryNotFoundError(f"Voice '{voice_id}' not found in category '{category_name}'.")
        language, voice_path = found

        try:
            with open(voice_path, "wb") as fh:
                fh.write(audio_data)
            self._write_voice_metadata(voice_path, VoiceCreationMethod.UPLOADED)
        except OSError as exc:
            raise ContentOperationError(f"Failed to update category voice: {exc}") from exc

        return CategoryVoice(
            id=voice_id,
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{voice_path.name}",
        )

    def delete_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")

        found = self._find_category_voice_file(category_path, category_name, voice_id)
        if found is None:
            raise CategoryNotFoundError(f"Voice '{voice_id}' not found in category '{category_name}'.")
        language, voice_path = found
        file_uri = f"/content/{category_name}/{voice_path.name}"

        try:
            self._delete_voice_with_metadata(voice_path)
        except OSError as exc:
            raise ContentOperationError(f"Failed to delete category voice: {exc}") from exc

        return CategoryVoice(id=voice_id, language=language, type="tts", file_uri=file_uri)

    def archive_category(self, category_id: str) -> ArchiveCategory:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(category_name)
        archive_root = self._archive_root()
        archive_root.mkdir(parents=True, exist_ok=True)
        archive_folder_name = f"{category_name}_{int(time.time())}"
        dest = archive_root / archive_folder_name
        # Written before the move so the manifest travels with the folder.
        self._write_archive_manifest(category_path, category_id)
        try:
            shutil.move(str(category_path), str(dest))
        except (OSError, shutil.Error) as exc:
            (category_path / _ARCHIVE_MANIFEST).unlink(missing_ok=True)
            raise ContentOperationError(f"Failed to archive category: {exc}") from exc

        self._unregister_category_rows(category_id)
        return ArchiveCategory(
            archived=True,
            name=category_name,
            archive_folder=archive_folder_name,
            destination=str(dest),
            message=(
                f"Category '{category_name}' has been archived to "
                f"{archive_folder_name} and removed from content."
            ),
        )

    def update_category_details(
            self,
            category_id: str,
            category_name: LocalizedString,
            description: LocalizedString,
    ) -> RenameCategory:
        folder_name = self._folder_name_for_id(category_id)
        index = self._category_row_index(category_id)
        root = self._content_root()

        for lang, value in category_name.to_dict().items():
            if lang in settings.SUPPORTED_LANGUAGES:
                self._write_csv_row_at_index(root / _CATEGORIES_LANG_CSV.format(lang), index, value)

        for lang, value in description.to_dict().items():
            if lang in settings.SUPPORTED_LANGUAGES:
                self._write_csv_row_at_index(root / _DESCRIPTIONS_LANG_CSV.format(lang), index, value)

        return RenameCategory(
            category=folder_name,
            message=f"Category '{folder_name}' details updated.",
        )

    def rename_category(self, category_id: str, new_category_id: str) -> RenameCategory:
        if not self._is_safe_category_name(new_category_id):
            raise InvalidCategoryNameError(new_category_id)

        index = self._category_row_index(category_id)
        old_folder_name = self._folder_name_from_category_id(category_id)
        old_category_path = self._category_path(old_folder_name)
        if not old_category_path.exists() or not old_category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{old_folder_name}': directory is missing.")

        new_folder_name = self._folder_name_from_category_id(new_category_id)

        if new_category_id == category_id:
            return RenameCategory(
                category=old_folder_name,
                message=f"Category id '{category_id}' unchanged.",
            )

        new_category_path = self._category_path(new_folder_name)
        if new_folder_name != old_folder_name:
            if new_category_path.exists():
                raise CategoryConflictError(f"Category already exists: {new_folder_name}")
            try:
                shutil.move(str(old_category_path), str(new_category_path))
                self._rename_category_content_files(new_category_path, old_folder_name, new_folder_name)
            except (OSError, shutil.Error) as exc:
                raise ContentOperationError(f"Failed to rename category folder: {exc}") from exc

        self._write_csv_row_at_index(self._content_root() / _CATEGORIES_IDS, index, new_category_id)

        return RenameCategory(
            category=new_folder_name,
            message=f"Category id '{category_id}' renamed to '{new_category_id}'.",
        )

    @staticmethod
    def _rename_category_content_files(category_path: Path, old_name: str, new_name: str) -> None:
        """Rename category-prefixed files (voices, icon, extra images) after a folder move."""
        for lang in settings.SUPPORTED_LANGUAGES:
            old_voice = category_path / _CATEGORY_VOICE_MP3.format(old_name, lang)
            if old_voice.is_file():
                new_voice = category_path / _CATEGORY_VOICE_MP3.format(new_name, lang)
                old_voice.rename(new_voice)
                old_metadata = FileSystemContentProvider._voice_metadata_path(old_voice)
                if old_metadata.is_file():
                    old_metadata.rename(FileSystemContentProvider._voice_metadata_path(new_voice))

        image_pattern = re.compile(r"^" + re.escape(old_name) + r"(_\d+)?\.(png|jpg|jpeg)$", re.IGNORECASE)
        for f in list(category_path.iterdir()):
            if f.is_file():
                match = image_pattern.match(f.name)
                if match:
                    suffix = match.group(1) or ""
                    f.rename(category_path / f"{new_name}{suffix}.{match.group(2)}")

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

    @staticmethod
    def _write_csv_row_at_index(file_path: Path, index: int, value: str) -> None:
        """Overwrite the row at *index* in a single-column CSV file, padding with blanks as needed."""
        rows = FileSystemContentProvider._read_csv_column(file_path)
        while len(rows) <= index:
            rows.append("")
        rows[index] = value
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            for row in rows:
                writer.writerow([row])

    @staticmethod
    def _delete_csv_row_at_index(file_path: Path, index: int) -> None:
        """Remove the row at *index* from a single-column CSV file, shifting later rows up."""
        rows = FileSystemContentProvider._read_csv_column(file_path)
        if 0 <= index < len(rows):
            del rows[index]
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            for row in rows:
                writer.writerow([row])

    def remove_category(self, category_id: str) -> ArchiveCategory:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(category_name)
        if settings.ARCHIVE_DIR and settings.ARCHIVE_DIR.strip():
            return self.archive_category(category_id)
        try:
            shutil.rmtree(category_path)
            self._unregister_category_rows(category_id)
            return ArchiveCategory(
                removed=True,
                name=category_name,
                message=f"Category '{category_name}' was permanently deleted (ARCHIVE_DIR not set).",
            )
        except (OSError, shutil.Error) as exc:
            raise ContentOperationError(f"Failed to delete category: {exc}") from exc

    def restore_category(self, name: str) -> RestoreCategory:
        requested = (name or "").strip()
        if not requested:
            raise InvalidCategoryNameError("No category name provided")

        content_root = self._content_root()
        archive_root = self._archive_root()
        if not archive_root.exists() or not archive_root.is_dir():
            raise ArchiveUnavailableError("Archive directory does not exist")

        base_name, archive_folder = self._resolve_archive_folder(archive_root, requested)
        archive_path = archive_root / archive_folder
        if not archive_path.is_dir():
            raise CategoryNotFoundError(f"Archive folder not found: {archive_folder}")

        content_dest = content_root / base_name
        if content_dest.exists() and content_dest.is_dir():
            raise CategoryConflictError(f"Category already exists in content directory: {base_name}")

        try:
            # Move rather than copy, so the snapshot is consumed and a second restore of the
            # same archive cannot conflict with the category it just recreated.
            shutil.move(str(archive_path), str(content_dest))
        except (OSError, shutil.Error) as exc:
            raise ContentOperationError(f"Failed to restore category: {exc}") from exc

        manifest = self._read_archive_manifest(content_dest, base_name)
        names = manifest["names"] if isinstance(manifest["names"], dict) else {}
        descriptions = manifest["descriptions"] if isinstance(manifest["descriptions"], dict) else {}
        self._register_category_rows(str(manifest["id"]), names, descriptions)
        (content_dest / _ARCHIVE_MANIFEST).unlink(missing_ok=True)

        return RestoreCategory(
            name=base_name,
            from_archive=archive_folder,
            message=f"Restored category '{base_name}' from archive '{archive_folder}'.",
        )

    def delete_archived_category(self, name: str) -> DeleteArchivedCategory:
        requested = (name or "").strip()
        if not requested:
            raise InvalidCategoryNameError("No archive name provided")

        archive_root = self._archive_root()
        if not archive_root.exists() or not archive_root.is_dir():
            raise ArchiveUnavailableError("Archive directory does not exist")

        base_name, archive_folder = self._resolve_archive_folder(archive_root, requested)
        archive_path = archive_root / archive_folder
        if not archive_path.is_dir():
            raise CategoryNotFoundError(f"Archive folder not found: {archive_folder}")

        try:
            shutil.rmtree(archive_path)
        except (OSError, shutil.Error) as exc:
            raise ContentOperationError(f"Failed to delete archived category: {exc}") from exc

        return DeleteArchivedCategory(
            name=base_name,
            archive_folder=archive_folder,
            message=f"Deleted archived category '{archive_folder}'.",
        )

    def _resolve_archive_folder(self, archive_root: Path, requested: str) -> tuple[str, str]:
        """Return (base_name, archive_folder) for a base name or a full archive folder name."""
        match = re.match(r"^(.+)_(\d+)$", requested)
        if match and (archive_root / requested).is_dir():
            return match.group(1), requested

        base_name = self._normalize_category_name(requested)
        candidates = self._archive_folders_for_base(archive_root, base_name)
        if not candidates:
            raise CategoryNotFoundError(f"No archive found for category '{base_name}'")
        return base_name, candidates[0][0]

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

    def item_images(self, category_id: str, item_id: str) -> ItemImages:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name = self._item_folder_for_id(category_path, item_id)
        images: list[CategoryImage] = []
        if item_folder_name:
            item_folder = category_path / item_folder_name
            if item_folder.is_dir():
                for f in sorted(item_folder.iterdir()):
                    if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS:
                        images.append(CategoryImage(
                            id=f.stem,
                            file_uri=f"/content/{category_name}/{item_folder_name}/{f.name}",
                        ))
        return ItemImages(
            item_id=item_id,
            images=images,
            thumbnail_uri=images[0].file_uri if images else None,
        )

    def item_voices(self, category_id: str, item_id: str) -> ItemVoices:
        """Build one CategoryVoice entry per supported language for the item.

        A language with no TTS file yet is still included (file_uri=None) so
        callers can render a placeholder/upload slot for it.
        """
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name = self._item_folder_for_id(category_path, item_id)
        item_folder = category_path / item_folder_name if item_folder_name else None
        files = sorted(item_folder.iterdir()) if item_folder and item_folder.is_dir() else []
        voices: list[CategoryVoice] = []
        for lang in settings.SUPPORTED_LANGUAGES:
            pattern = re.compile(
                r"^" + re.escape(item_folder_name or "") + r"_" + re.escape(lang) + r"(_\d+)?\.mp3$",
                re.IGNORECASE,
            )
            match = next((f for f in files if f.is_file() and pattern.match(f.name)), None)
            voices.append(CategoryVoice(
                id=f"{item_folder_name}-v-{lang}",
                language=lang,
                type="tts",
                file_uri=f"/content/{category_name}/{item_folder_name}/{match.name}" if match else None,
            ))
        return ItemVoices(item_id=item_id, voices=voices)

    def add_item_image(
            self,
            category_id: str,
            item_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        ext = image_extension if image_extension.startswith(".") else f".{image_extension}"
        if ext.lower() not in _IMAGE_EXTENSIONS:
            ext = ".png"

        idx = self._first_available_item_image_index(item_path, item_folder_name)
        file_name = f"{item_folder_name}_{idx:02d}{ext}"
        image_path = item_path / file_name
        try:
            with open(image_path, "wb") as fh:
                fh.write(image_data)
        except OSError as exc:
            raise ContentOperationError(f"Failed to save item image: {exc}") from exc

        return CategoryImage(
            id=image_path.stem,
            file_uri=f"/content/{category_name}/{item_folder_name}/{file_name}",
        )

    def update_item_image(
            self,
            category_id: str,
            item_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        existing = self._find_item_image_by_id(item_path, image_id)
        if existing is None:
            raise CategoryNotFoundError(
                f"Image '{image_id}' not found for item '{item_id}' in category '{category_name}'.",
            )

        ext = image_extension if image_extension.startswith(".") else f".{image_extension}"
        if ext.lower() not in _IMAGE_EXTENSIONS:
            ext = ".png"

        new_path = item_path / f"{image_id}{ext}"
        try:
            if existing != new_path:
                existing.unlink()
            with open(new_path, "wb") as fh:
                fh.write(image_data)
        except OSError as exc:
            raise ContentOperationError(f"Failed to update item image: {exc}") from exc

        return CategoryImage(
            id=new_path.stem,
            file_uri=f"/content/{category_name}/{item_folder_name}/{new_path.name}",
        )

    def delete_item_image(self, category_id: str, item_id: str, image_id: str) -> CategoryImage:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        existing = self._find_item_image_by_id(item_path, image_id)
        if existing is None:
            raise CategoryNotFoundError(
                f"Image '{image_id}' not found for item '{item_id}' in category '{category_name}'.",
            )

        remaining = [
            f for f in item_path.iterdir()
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS
        ]
        if len(remaining) <= 1:
            raise ContentOperationError(
                f"Cannot delete the last remaining image variant for item '{item_id}'.",
            )

        file_uri = f"/content/{category_name}/{item_folder_name}/{existing.name}"
        try:
            existing.unlink()
        except OSError as exc:
            raise ContentOperationError(f"Failed to delete item image: {exc}") from exc

        return CategoryImage(id=image_id, file_uri=file_uri)

    def add_item_voice(self, category_id: str, item_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        if language not in settings.SUPPORTED_LANGUAGES:
            raise ContentOperationError(f"Unsupported language: {language}")

        voice_id = f"{item_folder_name}-v-{language}"
        if self._find_item_voice_file(item_path, item_folder_name, voice_id) is not None:
            raise CategoryConflictError(f"Voice for language '{language}' already exists for item '{item_id}'.")

        voice_path = self._item_voice_path(item_path, item_folder_name, language)
        try:
            with open(voice_path, "wb") as fh:
                fh.write(audio_data)
            self._write_voice_metadata(voice_path, VoiceCreationMethod.UPLOADED)
        except OSError as exc:
            raise ContentOperationError(f"Failed to save item voice: {exc}") from exc

        return CategoryVoice(
            id=voice_id,
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{item_folder_name}/{voice_path.name}",
        )

    def update_item_voice(self, category_id: str, item_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        found = self._find_item_voice_file(item_path, item_folder_name, voice_id)
        if found is None:
            raise CategoryNotFoundError(
                f"Voice '{voice_id}' not found for item '{item_id}' in category '{category_name}'.",
            )
        language, voice_path = found

        try:
            with open(voice_path, "wb") as fh:
                fh.write(audio_data)
            self._write_voice_metadata(voice_path, VoiceCreationMethod.UPLOADED)
        except OSError as exc:
            raise ContentOperationError(f"Failed to update item voice: {exc}") from exc

        return CategoryVoice(
            id=voice_id,
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{item_folder_name}/{voice_path.name}",
        )

    def delete_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        found = self._find_item_voice_file(item_path, item_folder_name, voice_id)
        if found is None:
            raise CategoryNotFoundError(
                f"Voice '{voice_id}' not found for item '{item_id}' in category '{category_name}'.",
            )
        language, voice_path = found
        file_uri = f"/content/{category_name}/{item_folder_name}/{voice_path.name}"

        try:
            self._delete_voice_with_metadata(voice_path)
        except OSError as exc:
            raise ContentOperationError(f"Failed to delete item voice: {exc}") from exc

        return CategoryVoice(id=voice_id, language=language, type="tts", file_uri=file_uri)

    def regenerate_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        language = self._language_for_item_voice_id(item_folder_name, voice_id)
        if language is None:
            raise CategoryNotFoundError(
                f"Voice '{voice_id}' not found for item '{item_id}' in category '{category_name}'.",
            )

        word = self._item_word_for_language(category_path, item_id, language)
        if not word:
            raise ContentOperationError(
                f"Item '{item_id}' has no {language} text to synthesize; set the word first.",
            )

        try:
            audio_bytes = self._generate_audio(word, language)
        except (ValueError, RuntimeError, httpx.HTTPError) as exc:
            raise ContentOperationError(f"Voice generation failed for {language}: {exc}") from exc

        # Reuse the existing file (it may carry a numeric suffix) so the voice keeps its URI.
        found = self._find_item_voice_file(item_path, item_folder_name, voice_id)
        voice_path = found[1] if found else self._item_voice_path(item_path, item_folder_name, language)
        try:
            voice_path.write_bytes(audio_bytes)
            self._write_generated_voice_metadata(voice_path, audio_bytes, word)
        except OSError as exc:
            raise ContentOperationError(f"Failed to save item voice: {exc}") from exc

        return CategoryVoice(
            id=voice_id,
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{item_folder_name}/{voice_path.name}",
        )

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

    # ------------------------------------------------------------------
    # Factory registration
    # ------------------------------------------------------------------

    @classmethod
    def register(cls) -> None:
        """Register this provider in the content provider factory."""
        ContentProviderFactory.register("file_system", cls)


FileSystemContentProvider.register()
