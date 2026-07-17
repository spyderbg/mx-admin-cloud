"""File-system backed content provider.

Migrated from src.core.content_service — manages categories and items on the
local filesystem, delegates translation and TTS to internal HTTP endpoints.
"""

from __future__ import annotations

import base64
import csv
import re
import shutil
import time
from datetime import timedelta
from pathlib import Path

import httpx
from openai import OpenAI

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
    ContentOperationError,
    ContentProvider,
    CreateCategory,
    CreateItem,
    InvalidCategoryNameError,
    ItemDetail,
    ItemImages,
    ItemVoices,
    RenameCategory,
    RestoreCategory,
)
from src.services.content.service import ContentProviderFactory

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")

# --- File name constants ---
_CATEGORIES_IDS = "categories_ids.csv"
_CATEGORIES_LANG_CSV = "categories_{}.csv"
_DESCRIPTIONS_LANG_CSV = "descriptions_{}.csv"
_ITEMS_IDS = "items_ids.csv"
_ITEMS_LANG_CSV = "items_{}.csv"
_CATEGORY_ICON = "category_icon.png"
_LEGACY_CATEGORY_VOICE_MP3 = "category_{}.mp3"
_CATEGORY_VOICE_MP3 = "{}_{}.mp3"
_ITEM_VOICE_MP3 = "{}_{}_01.mp3"


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
    ) -> str:
        """Translate text via the internal /internal/translate endpoint."""
        if not text or not text.strip():
            return text.strip()

        token = create_internal_token("translation-service", timedelta(seconds=30))
        response = httpx.post(
            f"{settings.API_BASE_URL}/internal/translate",
            json={
                "text": text.strip(),
                "target_lang_code": target_lang_code,
                "input_lang_code": input_lang_code,
                "category": category,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()["translated_text"] or text.strip()

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
        response.raise_for_status()
        return response.content

    @staticmethod
    def _generate_icon(prompt: str) -> bytes:
        """Generate a child-friendly icon via the OpenAI Images API."""
        if not prompt or not prompt.strip():
            raise ValueError("Prompt must be non-empty for icon generation.")

        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set; cannot call Images API.")

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.images.generate(
            model="dall-e-2",
            prompt=prompt.strip(),
            size="256x256",
            response_format="b64_json",
            n=1,
        )
        b64_data = response.data[0].b64_json
        if not b64_data:
            raise ValueError("No image data returned from Images API.")
        return base64.b64decode(b64_data)

    # ------------------------------------------------------------------
    # Filesystem utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _is_safe_category_name(name: str) -> bool:
        """Allow alphanumeric and underscore only."""
        return bool(name) and bool(_SAFE_NAME_RE.match(name))

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
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "a", newline="", encoding="utf-8") as fh:
            fh.write(line.strip() + "\n")

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

        Folder keys are derived from the English name at the same line position as the ID,
        not from the ID string itself (IDs are opaque identifiers).
        """
        root = self._content_root()
        try:
            with open(root / _CATEGORIES_IDS, newline="", encoding="utf-8") as fh:
                id_entries = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
        except (OSError, csv.Error):
            return {}, {}

        try:
            with open(root / _CATEGORIES_LANG_CSV.format("en"), newline="", encoding="utf-8") as fh:
                en_names = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
        except (OSError, csv.Error):
            en_names = []

        folder_keys: list[str] = [
            self._english_word_to_folder_name(en_names[i]) if i < len(en_names) else ""
            for i in range(len(id_entries))
        ]

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
        """Return per-language descriptions for *category_name* from descriptions_{lang}.csv.

        Descriptions share row position with the category's entry in categories_en.csv,
        the same convention used by _load_category_localized_names.
        """
        root = self._content_root()
        try:
            with open(root / _CATEGORIES_LANG_CSV.format("en"), newline="", encoding="utf-8") as fh:
                en_names = [r[0].strip() for r in csv.reader(fh) if r and r[0].strip()]
        except (OSError, csv.Error):
            return {}

        index = next(
            (
                i for i, name in enumerate(en_names)
                if self._english_word_to_folder_name(name) == category_name.lower()
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
        if not self._is_safe_category_name(category_name):
            raise InvalidCategoryNameError(category_name)
        category_path = self._category_path(category_name)
        if category_path.exists() and category_path.is_dir():
            raise CategoryConflictError(f"Category already exists: {category_name}")

        category_path.mkdir(parents=True, exist_ok=True)

        labels_by_lang: dict[str, str] = {}
        for lang in settings.SUPPORTED_LANGUAGES:
            if lang == "en":
                label = category_name.replace("_", " ").strip() or category_name
            else:
                try:
                    label = self._translate_word(
                        category_name.replace("_", " ").strip() or category_name, lang, category_name,
                    )
                except (ValueError, RuntimeError, httpx.HTTPError, KeyError) as exc:
                    raise ContentOperationError(f"Translation failed for {lang}: {exc}") from exc
            labels_by_lang[lang] = label
            try:
                audio_bytes = self._generate_audio(label, lang)
                voice_path = category_path / _CATEGORY_VOICE_MP3.format(category_name, lang)
                with open(voice_path, "wb") as fh:
                    fh.write(audio_bytes)
            except (ValueError, RuntimeError, httpx.HTTPError) as exc:
                raise ContentOperationError(f"TTS failed for {lang}: {exc}") from exc
            except OSError as exc:
                raise ContentOperationError(f"File write failed for {lang}: {exc}") from exc

        try:
            prompt = self._category_name_to_icon_prompt(category_name)
            icon_bytes = self._generate_icon(prompt)
            icon_path = category_path / f"{category_name}.png"
            with open(icon_path, "wb") as fh:
                fh.write(icon_bytes)
        except (ValueError, RuntimeError) as exc:
            raise ContentOperationError(f"Icon generation failed: {exc}") from exc
        except OSError as exc:
            raise ContentOperationError(f"Icon file write failed: {exc}") from exc

        root = self._content_root()
        category_id = f"C_{category_name}"
        self._append_csv_line(root / _CATEGORIES_IDS, category_id)
        for lang in settings.SUPPORTED_LANGUAGES:
            self._append_csv_line(
                root / _CATEGORIES_LANG_CSV.format(lang),
                labels_by_lang.get(lang, category_name.replace("_", " ").strip()),
            )

        return CreateCategory(category=category_name, message=f"Category '{category_name}' created.")

    def create_item(  # pylint: disable=too-many-locals
            self,
            category_id: str,
            english_word: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CreateItem:
        category_name = self._folder_name_for_id(category_id)
        if not english_word or not english_word.strip():
            raise ContentOperationError("Item word is required")
        english_word = english_word.strip()

        folder_name = self._english_word_to_folder_name(english_word)
        image_id = self._english_word_to_image_id(english_word)
        if not folder_name:
            raise ContentOperationError("Could not derive folder name from item word")

        root = self._content_root()
        category_path = root / category_name
        item_path = category_path / folder_name

        if item_path.exists():
            raise CategoryConflictError(f"Item folder already exists: {folder_name}")

        category_path.mkdir(parents=True, exist_ok=True)
        item_path.mkdir(parents=True, exist_ok=True)

        idx = self._first_available_image_index(item_path)
        ext = image_extension if image_extension.startswith(".") else f".{image_extension}"
        if ext.lower() not in _IMAGE_EXTENSIONS:
            ext = ".png"
        image_path = item_path / f"{idx:02d}{ext}"
        with open(image_path, "wb") as fh:
            fh.write(image_data)

        self._append_csv_line(category_path / _ITEMS_LANG_CSV.format("en"), english_word)
        self._append_csv_line(category_path / _ITEMS_IDS, image_id)

        words_by_lang: dict[str, str] = {"en": english_word}
        for lang in settings.SUPPORTED_LANGUAGES:
            if lang == "en":
                continue
            try:
                translated = self._translate_word(english_word, lang, category_name)
                words_by_lang[lang] = translated
                self._append_csv_line(category_path / _ITEMS_LANG_CSV.format(lang), translated)
            except (ValueError, RuntimeError, httpx.HTTPError, KeyError) as exc:
                raise ContentOperationError(f"Translation failed for {lang}: {exc}") from exc

        for lang in settings.SUPPORTED_LANGUAGES:
            word = words_by_lang.get(lang) or english_word
            try:
                audio_bytes = self._generate_audio(word, lang)
                voice_path = item_path / _ITEM_VOICE_MP3.format(folder_name, lang)
                with open(voice_path, "wb") as fh:
                    fh.write(audio_bytes)
            except (ValueError, RuntimeError, httpx.HTTPError) as exc:
                raise ContentOperationError(f"TTS failed for {lang}: {exc}") from exc
            except OSError as exc:
                raise ContentOperationError(f"File write failed for {lang}: {exc}") from exc

        return CreateItem(
            category=category_name,
            item=english_word,
            folder=folder_name,
            image_id=image_id,
        )

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
            voice_path.unlink()
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
        try:
            shutil.move(str(category_path), str(dest))
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
        except (OSError, shutil.Error) as exc:
            raise ContentOperationError(f"Failed to archive category: {exc}") from exc

    def rename_category(
            self,
            category_id: str,
            new_name: str,
    ) -> RenameCategory:
        old_name = self._folder_name_for_id(category_id)
        if not self._is_safe_category_name(new_name):
            raise InvalidCategoryNameError(new_name)
        old_path = self._category_path(old_name)
        new_path = self._category_path(new_name)
        if not old_path.exists() or not old_path.is_dir():
            raise CategoryNotFoundError(old_name)
        if new_path.exists() and new_path.is_dir():
            raise CategoryConflictError(f"Target category already exists: {new_name}")
        try:
            shutil.move(str(old_path), str(new_path))
        except (OSError, shutil.Error) as exc:
            raise ContentOperationError(f"Failed to rename: {exc}") from exc

        self._cleanup_old_voices(new_path, old_name)
        self._recreate_voices(new_path, new_name)
        self._update_category_icon(new_path, old_name, new_name)

        return RenameCategory(
            category=new_name,
            message=f"Category renamed from '{old_name}' to '{new_name}'.",
        )

    def _cleanup_old_voices(self, category_path: Path, old_name: str) -> None:
        """Remove audio files associated with the old category name."""
        for lang in settings.SUPPORTED_LANGUAGES:
            old_voice = category_path / _CATEGORY_VOICE_MP3.format(old_name, lang)
            if old_voice.exists() and old_voice.is_file():
                old_voice.unlink()
        for lang in settings.SUPPORTED_LANGUAGES:
            legacy_voice = category_path / _LEGACY_CATEGORY_VOICE_MP3.format(lang)
            if legacy_voice.exists() and legacy_voice.is_file():
                legacy_voice.unlink()

    def _recreate_voices(self, category_path: Path, category_name: str) -> None:
        """Translate category name and generate audio files for all languages."""
        for lang in settings.SUPPORTED_LANGUAGES:
            if lang == "en":
                label = category_name.replace("_", " ").strip() or category_name
            else:
                try:
                    label = self._translate_word(
                        category_name.replace("_", " ").strip() or category_name, lang, category_name,
                    )
                except (ValueError, RuntimeError, httpx.HTTPError, KeyError) as exc:
                    raise ContentOperationError(f"Translation failed for {lang}: {exc}") from exc
            try:
                audio_bytes = self._generate_audio(label, lang)
                voice_path = category_path / _CATEGORY_VOICE_MP3.format(category_name, lang)
                with open(voice_path, "wb") as fh:
                    fh.write(audio_bytes)
            except (ValueError, RuntimeError) as exc:
                raise ContentOperationError(f"TTS failed for {lang}: {exc}") from exc
            except OSError as exc:
                raise ContentOperationError(f"File write failed for {lang}: {exc}") from exc

    def _update_category_icon(self, category_path: Path, old_name: str, new_name: str) -> None:
        """Rename the category icon to match the new category name."""
        new_icon_path = category_path / f"{new_name}.png"
        for old_icon_name in (f"{old_name}.png", _CATEGORY_ICON):
            old_icon_path = category_path / old_icon_name
            if old_icon_path.exists() and old_icon_path.is_file():
                if old_icon_path != new_icon_path:
                    old_icon_path.rename(new_icon_path)
                break

    def remove_category(self, category_id: str) -> ArchiveCategory:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(category_name)
        if settings.ARCHIVE_DIR and settings.ARCHIVE_DIR.strip():
            return self.archive_category(category_id)
        try:
            shutil.rmtree(category_path)
            return ArchiveCategory(
                removed=True,
                name=category_name,
                message=f"Category '{category_name}' was permanently deleted (ARCHIVE_DIR not set).",
            )
        except (OSError, shutil.Error) as exc:
            raise ContentOperationError(f"Failed to delete category: {exc}") from exc

    def restore_category(
            self,
            category_id: str,
    ) -> RestoreCategory:
        cid = (category_id or "").strip()
        if not cid:
            raise InvalidCategoryNameError("No category id provided")

        content_root = self._content_root()
        archive_root = self._archive_root()
        if not archive_root.exists() or not archive_root.is_dir():
            raise ArchiveUnavailableError("Archive directory does not exist")

        base_name = self._folder_name_for_id(cid)
        candidates = self._archive_folders_for_base(archive_root, base_name)
        if not candidates:
            raise CategoryNotFoundError(f"No archive found for category '{base_name}'")
        archive_folder = candidates[0][0]

        archive_path = archive_root / archive_folder
        if not archive_path.exists() or not archive_path.is_dir():
            raise CategoryNotFoundError(f"Archive folder not found: {archive_folder}")

        content_dest = content_root / base_name
        if content_dest.exists() and content_dest.is_dir():
            raise CategoryConflictError(f"Category already exists in content directory: {base_name}")

        try:
            shutil.copytree(archive_path, content_dest)
            return RestoreCategory(
                name=base_name,
                from_archive=archive_folder,
                message=f"Restored category '{base_name}' from archive '{archive_folder}'.",
            )
        except (OSError, shutil.Error) as exc:
            raise ContentOperationError(f"Failed to restore category: {exc}") from exc

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

    def item_images(self, category_id: str, item_id: str) -> ItemImages:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        folder_name = self._item_folder_for_id(category_path, item_id)
        images: list[CategoryImage] = []
        if folder_name:
            item_folder = category_path / folder_name
            if item_folder.is_dir():
                for f in sorted(item_folder.iterdir()):
                    if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS:
                        images.append(CategoryImage(
                            id=f.stem,
                            file_uri=f"/content/{category_name}/{folder_name}/{f.name}",
                        ))
        return ItemImages(
            item_id=item_id,
            images=images,
            thumbnail_uri=images[0].file_uri if images else None,
        )

    def item_voices(self, category_id: str, item_id: str) -> ItemVoices:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        folder_name = self._item_folder_for_id(category_path, item_id)
        voices: list[CategoryVoice] = []
        if folder_name:
            item_folder = category_path / folder_name
            if item_folder.is_dir():
                for lang in settings.SUPPORTED_LANGUAGES:
                    pattern = re.compile(
                        r"^" + re.escape(folder_name) + r"_" + re.escape(lang) + r"(_\d+)?\.mp3$",
                        re.IGNORECASE,
                    )
                    for f in sorted(item_folder.iterdir()):
                        if f.is_file() and pattern.match(f.name):
                            voices.append(CategoryVoice(
                                id=f"{item_id}-{lang}",
                                language=lang,
                                type="tts",
                                file_uri=f"/content/{category_name}/{folder_name}/{f.name}",
                            ))
                            break
        return ItemVoices(item_id=item_id, voices=voices)

    # ------------------------------------------------------------------
    # Factory registration
    # ------------------------------------------------------------------

    @classmethod
    def register(cls) -> None:
        """Register this provider in the content provider factory."""
        ContentProviderFactory.register("file_system", cls)


FileSystemContentProvider.register()
