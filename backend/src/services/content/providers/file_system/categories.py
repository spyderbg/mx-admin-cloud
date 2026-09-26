"""Category methods for the file_system content provider: the category itself (listing, creation,
details, update, archive/restore, removal), its images, and its voices.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

import httpx

from src.core.config import settings
from src.core.localization import LocalizedString
from src.services.content.models import (
    ArchiveCategory,
    ArchivedEntry,
    ArchiveUnavailableError,
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
    DeleteArchivedCategory,
    InvalidCategoryNameError,
    RestoreCategory,
    UpdateCategory,
)
from src.services.content.providers.file_system.constants import (
    VoiceCreationMethod,
    _ARCHIVE_MANIFEST,
    _CATEGORIES_IDS,
    _CATEGORIES_LANG_CSV,
    _CATEGORY_VOICE_MP3,
    _DESCRIPTIONS_LANG_CSV,
    _IMAGE_EXTENSIONS,
)


class FileSystemCategoriesMixin(ContentProvider):  # pylint: disable=abstract-method
    """Category methods: the category itself, its images and its voices."""

    @staticmethod
    def _category_name_to_icon_prompt(category_name: str) -> str:
        """Derive a short, child-friendly icon prompt from a category name."""
        label = category_name.replace("_", " ").strip()
        if not label:
            return category_name
        label = label.title()
        return f"Simple, friendly icon for children: {label}"

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

    @classmethod
    def _find_category_image_file(cls, category_path: Path, image_id: str) -> Path | None:
        """Return the existing category image file whose stem matches *image_id*, if any."""
        if not cls._is_safe_category_name(image_id):
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
    def _language_for_category_voice_id(category_name: str, voice_id: str) -> str | None:
        """Return the language encoded in *voice_id* for this category, or None if unrecognized."""
        for lang in settings.SUPPORTED_LANGUAGES:
            if voice_id == f"{category_name}-v-{lang}":
                return lang
        return None

    @classmethod
    def _find_category_voice_file(
            cls,
            category_path: Path,
            category_name: str,
            voice_id: str,
    ) -> tuple[str, Path] | None:
        """Return the (language, path) for the existing voice file matching *voice_id*, if any."""
        lang = cls._language_for_category_voice_id(category_name, voice_id)
        if lang is None:
            return None
        voice_path = cls._category_voice_path(category_path, category_name, lang)
        return (lang, voice_path) if voice_path.is_file() else None

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
        category_name, category_path = self._validate_category_path(category_id)

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
            provider, parameters = self._read_voice_metadata(voice_file) if has_file else ("unknown", {})
            voices.append(CategoryVoice(
                id=f"{category_name}-v-{lang}",
                language=lang,
                type="tts",
                file_uri=f"/content/{category_name}/{voice_file.name}" if has_file else None,
                provider=provider,
                parameters=parameters,
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

    def add_category_image(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        category_name, category_path = self._validate_category_path(category_id)

        ext = self._normalize_image_extension(image_extension)

        idx = self._first_available_category_image_index(category_path, category_name)
        file_name = f"{category_name}_{idx:02d}{ext}"
        image_path = category_path / file_name
        self._write_image_bytes(image_path, image_data)

        return CategoryImage(id=image_path.stem, file_uri=f"/content/{category_name}/{file_name}")

    def update_category_image(
            self,
            category_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        category_name, category_path = self._validate_category_path(category_id)

        existing = self._find_category_image_file(category_path, image_id)
        if existing is None:
            raise CategoryNotFoundError(f"Image '{image_id}' not found in category '{category_name}'.")

        ext = self._normalize_image_extension(image_extension)

        new_path = category_path / f"{image_id}{ext}"
        if existing != new_path:
            try:
                existing.unlink()
            except OSError as exc:
                raise ContentOperationError(
                    f"Failed to remove previous category image '{existing}': {exc}",
                ) from exc
        self._write_image_bytes(new_path, image_data)

        return CategoryImage(id=new_path.stem, file_uri=f"/content/{category_name}/{new_path.name}")

    def delete_category_image(self, category_id: str, image_id: str) -> CategoryImage:
        category_name, category_path = self._validate_category_path(category_id)

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
        category_name, category_path = self._validate_category_path(category_id)

        ext = self._normalize_image_extension(image_extension)

        existing = self._find_category_image_file(category_path, category_name)
        new_path = category_path / f"{category_name}{ext}"
        if existing is not None and existing != new_path:
            try:
                existing.unlink()
            except OSError as exc:
                raise ContentOperationError(
                    f"Failed to remove previous category icon '{existing}': {exc}",
                ) from exc
        self._write_image_bytes(new_path, image_data)

        return CategoryImage(id=new_path.stem, file_uri=f"/content/{category_name}/{new_path.name}")

    def add_category_voice(self, category_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        category_name, category_path = self._validate_category_path(category_id)

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
            self._write_voice_metadata(voice_path, VoiceCreationMethod.UPLOADED, provider="custom")
        except OSError as exc:
            raise ContentOperationError(f"Failed to save category voice: {exc}") from exc

        provider, parameters = self._read_voice_metadata(voice_path)
        return CategoryVoice(
            id=f"{category_name}-v-{language}",
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{voice_path.name}",
            provider=provider,
            parameters=parameters,
        )

    def update_category_voice(self, category_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        category_name, category_path = self._validate_category_path(category_id)

        found = self._find_category_voice_file(category_path, category_name, voice_id)
        if found is None:
            raise CategoryNotFoundError(f"Voice '{voice_id}' not found in category '{category_name}'.")
        language, voice_path = found

        try:
            with open(voice_path, "wb") as fh:
                fh.write(audio_data)
            self._write_voice_metadata(voice_path, VoiceCreationMethod.UPLOADED, provider="custom")
        except OSError as exc:
            raise ContentOperationError(f"Failed to update category voice: {exc}") from exc

        provider, parameters = self._read_voice_metadata(voice_path)
        return CategoryVoice(
            id=voice_id,
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{voice_path.name}",
            provider=provider,
            parameters=parameters,
        )

    def delete_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        category_name, category_path = self._validate_category_path(category_id)

        found = self._find_category_voice_file(category_path, category_name, voice_id)
        if found is None:
            raise CategoryNotFoundError(f"Voice '{voice_id}' not found in category '{category_name}'.")
        language, voice_path = found
        file_uri = f"/content/{category_name}/{voice_path.name}"
        provider, parameters = self._read_voice_metadata(voice_path)

        try:
            self._delete_voice_with_metadata(voice_path)
        except OSError as exc:
            raise ContentOperationError(f"Failed to delete category voice: {exc}") from exc

        return CategoryVoice(
            id=voice_id, language=language, type="tts", file_uri=file_uri, provider=provider, parameters=parameters,
        )

    def regenerate_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        category_name, category_path = self._validate_category_path(category_id)

        language = self._language_for_category_voice_id(category_name, voice_id)
        if language is None:
            raise CategoryNotFoundError(f"Voice '{voice_id}' not found in category '{category_name}'.")

        label = self._category_label_for_language(category_name, language)
        if not label:
            raise ContentOperationError(
                f"Category '{category_name}' has no {language} name to synthesize; set the name first.",
            )

        try:
            audio_bytes = self._generate_audio(label, language)
        except (ValueError, RuntimeError, httpx.HTTPError) as exc:
            raise ContentOperationError(f"Voice generation failed for {language}: {exc}") from exc

        # Reuse the existing file (if any) so the voice keeps its URI.
        found = self._find_category_voice_file(category_path, category_name, voice_id)
        voice_path = found[1] if found else self._category_voice_path(category_path, category_name, language)
        try:
            voice_path.write_bytes(audio_bytes)
            self._write_generated_voice_metadata(voice_path, audio_bytes, label)
        except OSError as exc:
            raise ContentOperationError(f"Failed to save category voice: {exc}") from exc

        provider, parameters = self._read_voice_metadata(voice_path)
        return CategoryVoice(
            id=voice_id,
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{voice_path.name}",
            provider=provider,
            parameters=parameters,
        )

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
    ) -> UpdateCategory:
        folder_name = self._folder_name_for_id(category_id)
        index = self._category_row_index(category_id)
        root = self._content_root()

        for lang, value in category_name.to_dict().items():
            if lang in settings.SUPPORTED_LANGUAGES:
                self._write_csv_row_at_index(root / _CATEGORIES_LANG_CSV.format(lang), index, value)

        for lang, value in description.to_dict().items():
            if lang in settings.SUPPORTED_LANGUAGES:
                self._write_csv_row_at_index(root / _DESCRIPTIONS_LANG_CSV.format(lang), index, value)

        return UpdateCategory(
            category=folder_name,
            message=f"Category '{folder_name}' details updated.",
        )

    def update_category_id(self, category_id: str, new_category_id: str) -> UpdateCategory:
        if not self._is_safe_category_name(new_category_id):
            raise InvalidCategoryNameError(new_category_id)

        index = self._category_row_index(category_id)
        folder_name, category_path = self._validate_category_path(category_id)

        new_folder_name = self._folder_name_from_category_id(new_category_id)

        if new_category_id == category_id:
            return UpdateCategory(
                category=folder_name,
                message=f"Category id '{category_id}' unchanged.",
            )

        new_category_path = self._category_path(new_folder_name)
        if new_folder_name != folder_name:
            if new_category_path.exists():
                raise CategoryConflictError(f"Category already exists: {new_folder_name}")
            try:
                shutil.move(str(category_path), str(new_category_path))
                self._rename_category_content_files(new_category_path, folder_name, new_folder_name)
            except (OSError, shutil.Error) as exc:
                raise ContentOperationError(f"Failed to rename category folder: {exc}") from exc

        self._write_csv_row_at_index(self._content_root() / _CATEGORIES_IDS, index, new_category_id)

        return UpdateCategory(
            category=new_folder_name,
            message=f"Category id '{category_id}' renamed to '{new_category_id}'.",
        )

    @classmethod
    def _rename_category_content_files(cls, category_path: Path, old_name: str, new_name: str) -> None:
        """Rename category-prefixed files (voices, icon, extra images) after a folder move."""
        for lang in settings.SUPPORTED_LANGUAGES:
            old_voice = category_path / _CATEGORY_VOICE_MP3.format(old_name, lang)
            if old_voice.is_file():
                new_voice = category_path / _CATEGORY_VOICE_MP3.format(new_name, lang)
                old_voice.rename(new_voice)
                old_metadata = cls._voice_metadata_path(old_voice)
                if old_metadata.is_file():
                    old_metadata.rename(cls._voice_metadata_path(new_voice))

        image_pattern = re.compile(r"^" + re.escape(old_name) + r"(_\d+)?\.(png|jpg|jpeg)$", re.IGNORECASE)
        for f in list(category_path.iterdir()):
            if f.is_file():
                match = image_pattern.match(f.name)
                if match:
                    suffix = match.group(1) or ""
                    f.rename(category_path / f"{new_name}{suffix}.{match.group(2)}")

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
