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
from src.security.tokens import create_internal_token
from src.services.content.models import (
    ArchiveCategory,
    ArchiveUnavailableError,
    ArchivedEntry,
    CategoryConflictError,
    CategoryDetail,
    CategoryList,
    CategoryNotFoundError,
    ContentOperationError,
    ContentProvider,
    CreateCategory,
    CreateItem,
    InvalidCategoryNameError,
    RenameCategory,
    RestoreCategory,
)
from src.services.content.service import ContentProviderFactory

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


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
                    for ext in (".png", ".jpg", ".jpeg")
            ):
                return i
        return 1

    def _get_category_id(self, category_name: str) -> str:
        """Validate the category exists and return its ID from categories_ids.csv.

        Raises CategoryNotFoundError with a specific message when either condition fails:
        - The category directory is absent
        - The category has no entry in categories_ids.csv
        """
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(f"Category '{category_name}': directory is missing.")

        root = self._content_root()
        ids_path = root / "categories_ids.csv"
        try:
            with open(ids_path, newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
            for row in rows:
                if not row or not row[0].strip():
                    continue
                entry = row[0].strip()
                if "_" in entry and entry.split("_", 1)[1].lower() == category_name.lower():
                    return entry
        except (OSError, csv.Error):
            pass
        raise CategoryNotFoundError(f"Category '{category_name}': ID is not found in categories_ids.csv.")

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
        names = [p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
        return CategoryList(
            categories=sorted(names),
            archived=archived if include_archived else [],
        )

    def category_details(self, category_name: str) -> CategoryDetail:
        if not self._is_safe_category_name(category_name):
            raise InvalidCategoryNameError(category_name)

        category_id = self._get_category_id(category_name)
        category_path = self._category_path(category_name)

        objects = [p.name for p in category_path.iterdir() if p.is_dir() and not p.name.startswith(".")]
        images_by_lang: dict[str, list[str]] = {}

        for lang in settings.SUPPORTED_LANGUAGES:
            csv_path = category_path / f"items_{lang}.csv"
            if csv_path.exists():
                try:
                    with open(csv_path, newline="", encoding="utf-8") as fh:
                        rows = list(csv.reader(fh))
                        images_by_lang[lang] = [r[0].strip() for r in rows if r and r[0].strip()]
                except (OSError, csv.Error):
                    images_by_lang[lang] = []

        images_ids: list[str] | None = None
        ids_path = category_path / "items_ids.csv"
        if ids_path.exists():
            try:
                with open(ids_path, newline="", encoding="utf-8") as fh:
                    rows = list(csv.reader(fh))
                    images_ids = [r[0].strip() for r in rows if r and r[0].strip()]
            except (OSError, csv.Error):
                images_ids = []

        return CategoryDetail(
            id=category_id,
            name=category_name,
            items=sorted(objects),
            images_ids=images_ids,
            images_by_lang=images_by_lang,
        )

    def create_category(self, category_name: str) -> CreateCategory:
        if not self._is_safe_category_name(category_name):
            raise InvalidCategoryNameError(category_name)
        category_path = self._category_path(category_name)
        if category_path.exists() and category_path.is_dir():
            raise CategoryConflictError(f"Category already exists: {category_name}")

        category_path.mkdir(parents=True, exist_ok=True)

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
                voice_path = category_path / f"{category_name}_{lang}.mp3"
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

        return CreateCategory(category=category_name, message=f"Category '{category_name}' created.")

    def create_item(  # pylint: disable=too-many-locals
            self,
            category_name: str,
            english_word: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CreateItem:
        if not self._is_safe_category_name(category_name):
            raise InvalidCategoryNameError(category_name)
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
        if ext.lower() not in (".png", ".jpg", ".jpeg"):
            ext = ".png"
        image_path = item_path / f"{idx:02d}{ext}"
        with open(image_path, "wb") as fh:
            fh.write(image_data)

        self._append_csv_line(category_path / "items_en.csv", english_word)
        self._append_csv_line(category_path / "items_ids.csv", image_id)

        words_by_lang: dict[str, str] = {"en": english_word}
        for lang in settings.SUPPORTED_LANGUAGES:
            if lang == "en":
                continue
            try:
                translated = self._translate_word(english_word, lang, category_name)
                words_by_lang[lang] = translated
                self._append_csv_line(category_path / f"items_{lang}.csv", translated)
            except (ValueError, RuntimeError, httpx.HTTPError, KeyError) as exc:
                raise ContentOperationError(f"Translation failed for {lang}: {exc}") from exc

        for lang in settings.SUPPORTED_LANGUAGES:
            word = words_by_lang.get(lang) or english_word
            try:
                audio_bytes = self._generate_audio(word, lang)
                voice_path = item_path / f"{folder_name}_{lang}_01.mp3"
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

    def archive_category(self, category_name: str) -> ArchiveCategory:
        if not self._is_safe_category_name(category_name):
            raise InvalidCategoryNameError(category_name)
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
            old_name: str,
            new_name: str,
    ) -> RenameCategory:
        if not self._is_safe_category_name(old_name):
            raise InvalidCategoryNameError(old_name)
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
            old_voice = category_path / f"{old_name}_{lang}.mp3"
            if old_voice.exists() and old_voice.is_file():
                old_voice.unlink()
        for lang in settings.SUPPORTED_LANGUAGES:
            legacy_voice = category_path / f"category_{lang}.mp3"
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
                voice_path = category_path / f"{category_name}_{lang}.mp3"
                with open(voice_path, "wb") as fh:
                    fh.write(audio_bytes)
            except (ValueError, RuntimeError) as exc:
                raise ContentOperationError(f"TTS failed for {lang}: {exc}") from exc
            except OSError as exc:
                raise ContentOperationError(f"File write failed for {lang}: {exc}") from exc

    def _update_category_icon(self, category_path: Path, old_name: str, new_name: str) -> None:
        """Rename the category icon to match the new category name."""
        new_icon_path = category_path / f"{new_name}.png"
        for old_icon_name in (f"{old_name}.png", "category_icon.png"):
            old_icon_path = category_path / old_icon_name
            if old_icon_path.exists() and old_icon_path.is_file():
                if old_icon_path != new_icon_path:
                    old_icon_path.rename(new_icon_path)
                break

    def remove_category(self, category_name: str) -> ArchiveCategory:
        if not self._is_safe_category_name(category_name):
            raise InvalidCategoryNameError(category_name)
        category_path = self._category_path(category_name)
        if not category_path.exists() or not category_path.is_dir():
            raise CategoryNotFoundError(category_name)
        if settings.ARCHIVE_DIR and settings.ARCHIVE_DIR.strip():
            return self.archive_category(category_name)
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
            category_name: str,
    ) -> RestoreCategory:
        name = (category_name or "").strip()
        if not name:
            raise InvalidCategoryNameError("No category name provided")

        content_root = self._content_root()
        archive_root = self._archive_root()
        if not archive_root.exists() or not archive_root.is_dir():
            raise ArchiveUnavailableError("Archive directory does not exist")

        if re.match(r"^.+_\d+$", name):
            base_name = re.sub(r"_\d+$", "", name)
            archive_folder = name
        else:
            base_name = name
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

    # ------------------------------------------------------------------
    # Factory registration
    # ------------------------------------------------------------------

    @classmethod
    def register(cls) -> None:
        """Register this provider in the content provider factory."""
        ContentProviderFactory.register("file_system", cls)


FileSystemContentProvider.register()
