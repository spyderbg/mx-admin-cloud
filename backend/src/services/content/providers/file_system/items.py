"""Item methods for the file_system content provider: the item itself (creation, updating,
removal, summaries), its images, and its voices.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import httpx

from src.core.config import settings
from src.core.localization import LocalizedString
from src.services.content.models import (
    CategoryConflictError,
    CategoryImage,
    CategoryNotFoundError,
    CategoryVoice,
    ContentError,
    ContentOperationError,
    ContentProvider,
    CreateItem,
    InvalidCategoryNameError,
    ItemDetail,
    ItemImages,
    ItemVoices,
    RemoveItem,
)
from src.services.content.providers.file_system.constants import (
    VoiceCreationMethod,
    _CATEGORY_VOICE_MP3,
    _IMAGE_EXTENSIONS,
    _ITEMS_IDS,
    _ITEMS_LANG_CSV,
)


# Read size for content hashing (identifying which variant a main image/voice was copied from).
_DIGEST_CHUNK_SIZE = 64 * 1024


class FileSystemItemsMixin(ContentProvider):  # pylint: disable=abstract-method
    """Item methods: the item itself, its images and its voices."""

    @classmethod
    def _find_item_image_by_id(cls, item_path: Path, image_id: str) -> Path | None:
        """Return the existing item image file whose stem matches *image_id*, if any."""
        if not cls._is_safe_category_name(image_id):
            return None
        for ext in _IMAGE_EXTENSIONS:
            candidate = item_path / f"{image_id}{ext}"
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _file_digest(path: Path) -> str:
        """SHA-256 of *path*, read in chunks so large images don't land in memory whole."""
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_DIGEST_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _main_copy_source_id(cls, main_path: Path, variant_paths: list[Path]) -> str | None:
        """Return the id of the variant *main_path* was copied from, or None if it matches none.

        A main image or main voice is a byte-for-byte copy of one variant (see
        set_main_item_image, add_item_image and set_main_item_voice), so which one is
        selected is recoverable from the files themselves — no sidecar to write, and
        existing content works unchanged. Sizes are compared first, so normally only the
        single same-sized candidate is ever read.
        """
        try:
            main_size = main_path.stat().st_size
            candidates = [v for v in variant_paths if v.stat().st_size == main_size]
            if not candidates:
                return None
            main_digest = cls._file_digest(main_path)
            for candidate in candidates:
                if cls._file_digest(candidate) == main_digest:
                    return candidate.stem
        except OSError:
            # A file-vanishing mid-scan only costs the selection highlight, not the listing.
            return None
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
    def _split_item_voice_files(
            files: list[Path],
            item_folder_name: str,
            language: str,
    ) -> tuple[Path | None, list[Path]]:
        """Return (main, variants) among *files* for one language of an item.

        The main voice is stored with no index suffix (``{name}_en.mp3``) and each variant
        with one (``{name}_en_01.mp3``), mirroring how item images are laid out.
        """
        pattern = re.compile(
            r"^" + re.escape(item_folder_name) + r"_" + re.escape(language) + r"(_\d+)?\.mp3$",
            re.IGNORECASE,
        )
        main: Path | None = None
        variants: list[Path] = []
        for f in sorted(files):
            match = pattern.match(f.name)
            if not f.is_file() or not match:
                continue
            if match.group(1):
                variants.append(f)
            elif main is None:
                main = f
        return main, variants

    @classmethod
    def _find_item_voice_file(
            cls,
            item_path: Path,
            item_folder_name: str,
            voice_id: str,
    ) -> tuple[str, Path] | None:
        """Return the (language, path) for the existing item voice file matching *voice_id*, if any.

        Tolerates an optional numeric suffix (e.g. ``{name}_en_01.mp3``) to match how
        ``item_voices`` locates files created by different content pipelines.
        """
        lang = cls._language_for_item_voice_id(item_folder_name, voice_id)
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

    def _item_thumbnail_and_count(
            self,
            category_path: Path,
            category_name: str,
            english_word: str,
    ) -> tuple[str, int]:
        """Return (thumbnail_uri, images_count) for one item, scanning its image folder."""
        folder_name = self._english_word_to_folder_name(english_word)
        item_folder = category_path / folder_name
        if not item_folder.is_dir():
            return "", 0

        image_files = sorted(
            f for f in item_folder.iterdir()
            if f.is_file()
            and f.suffix.lower() in _IMAGE_EXTENSIONS
            and self._is_safe_category_name(f.stem)
        )
        # The unindexed file (for example, ``calculator.png``) is the
        # dedicated main-image copy.  It is not an image variant and is
        # deliberately excluded from ``item_images().images`` as well,
        # so the category summary must exclude it from the displayed
        # count.  Older item folders have no dedicated main file; their
        # first variant remains both the thumbnail and part of the count.
        main_image = next((f for f in image_files if f.stem == folder_name), None)
        variant_files = [f for f in image_files if f != main_image]
        thumbnail = main_image or (variant_files[0] if variant_files else None)
        thumbnail_uri = f"/content/{category_name}/{folder_name}/{thumbnail.name}" if thumbnail else ""
        return thumbnail_uri, len(variant_files)

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
            thumbnail_uri, images_count = "", 0
            if i < len(en_words):
                thumbnail_uri, images_count = self._item_thumbnail_and_count(
                    category_path, category_name, en_words[i]
                )
            items.append(ItemDetail(
                id=iid,
                name=LocalizedString({lang: names[i] for lang, names in items_by_lang.items() if i < len(names)}),
                thumbnail_uri=thumbnail_uri,
                images_count=images_count,
            ))
        return items

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
        if not english_word or not english_word.strip():
            raise ContentOperationError("Item word is required")
        english_word = english_word.strip()

        folder_name = self._english_word_to_folder_name(english_word)
        item_id = self._english_word_to_image_id(english_word)
        if not folder_name:
            raise ContentOperationError("Could not derive folder name from item word")

        category_name, category_path = self._validate_category_path(category_id)
        item_path = self._validate_item_path(category_path, folder_name, item_id)

        # Translate before touching the disk: a failing translator must not create the item.
        words_by_lang = self._translate_item_word(english_word, category_name)

        # (path, index of the appended row, whether the file existed before the append)
        appended: list[tuple[Path, int, bool]] = []
        try:
            item_path.mkdir(parents=True, exist_ok=False)
            if image_data:
                ext = self._normalize_image_extension(image_extension)
                # The item's first image is its main image, stored with no index suffix
                # (e.g. "apple.png"), copied into "_01" so the item starts with a
                # variant to edit/replace without touching the main image (see add_item_image).
                self._write_image_bytes(item_path / f"{folder_name}{ext}", image_data)
                self._write_image_bytes(item_path / f"{folder_name}_01{ext}", image_data)

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

        return self._item_detail(category_path, category_name, item_id)

    def update_item_id(self, category_id: str, item_id: str, new_item_id: str) -> ItemDetail:
        if not self._is_safe_category_name(new_item_id):
            raise InvalidCategoryNameError(
                f"Invalid item id '{new_item_id}': use only letters, digits and underscores.",
            )

        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        self._resolve_item_path(category_path, category_name, item_id)

        # The item folder and its files are named after the English word, not the id, so
        # only the id row in the registry changes.
        ids_path = category_path / _ITEMS_IDS
        item_ids = self._read_csv_column(ids_path)
        if new_item_id != item_id:
            if new_item_id in item_ids:
                raise CategoryConflictError(f"Item id already exists: {new_item_id}")
            self._write_csv_row_at_index(ids_path, item_ids.index(item_id), new_item_id)

        return self._item_detail(category_path, category_name, new_item_id)

    def _item_detail(self, category_path: Path, category_name: str, item_id: str) -> ItemDetail:
        """Return the ItemDetail for *item_id* as currently registered, or raise CategoryNotFoundError."""
        item_ids, items_by_lang = self._read_item_csv_data(category_path)
        found = next(
            (
                item for item in self._collect_items(category_path, category_name, item_ids, items_by_lang)
                if item.id == item_id
            ),
            None,
        )
        if found is None:
            raise CategoryNotFoundError(f"Item '{item_id}' not found in category '{category_name}'.")
        return found

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

    def item_images(self, category_id: str, item_id: str) -> ItemImages:
        """Return the item's image variants, its main image's URI, and the variant it came from.

        The main image (stored with no index suffix, e.g. "apple.png") is a distinct
        concept from the indexed variants and is deliberately excluded from *images* —
        it is only exposed via *thumbnail_uri*. Because it is a copy of one of the
        variants (see set_main_item_image), *main_image_id* names the variant it was
        copied from, so callers can show which one is currently selected. If no dedicated
        main image exists yet (legacy data), the first variant is used as the thumbnail
        and stays listed.
        """
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name = self._item_folder_for_id(category_path, item_id)
        images: list[CategoryImage] = []
        variant_paths: list[Path] = []
        main_path: Path | None = None
        main_uri: str | None = None
        if item_folder_name:
            item_folder = category_path / item_folder_name
            if item_folder.is_dir():
                for f in sorted(item_folder.iterdir()):
                    if (
                            f.is_file()
                            and f.suffix.lower() in _IMAGE_EXTENSIONS
                            and self._is_safe_category_name(f.stem)
                    ):
                        file_uri = f"/content/{category_name}/{item_folder_name}/{f.name}"
                        if f.stem == item_folder_name:
                            main_path = f
                            main_uri = file_uri
                            continue
                        images.append(CategoryImage(id=f.stem, file_uri=file_uri))
                        variant_paths.append(f)
        if main_path is None:
            return ItemImages(
                item_id=item_id,
                images=images,
                thumbnail_uri=images[0].file_uri if images else None,
                main_image_id=images[0].id if images else None,
            )
        return ItemImages(
            item_id=item_id,
            images=images,
            thumbnail_uri=main_uri,
            main_image_id=self._main_copy_source_id(main_path, variant_paths),
        )

    def item_voices(self, category_id: str, item_id: str) -> ItemVoices:
        """Build one CategoryVoice entry per supported language, plus each language's variants.

        A language with no TTS file yet is still included in *voices* (file_uri=None) so
        callers can render a placeholder/upload slot for it. That entry is the main voice
        (``{name}_en.mp3``), or the first variant when no main file exists yet (legacy
        data). Numbered recordings (``{name}_en_01.mp3``) are also listed in *variants*,
        and *main_voice_ids* names the variant each language's main voice was copied from
        (see set_main_item_voice).
        """
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name = self._item_folder_for_id(category_path, item_id)
        item_folder = category_path / item_folder_name if item_folder_name else None
        files = sorted(item_folder.iterdir()) if item_folder and item_folder.is_dir() else []

        def to_voice(voice_id: str, lang: str, path: Path | None) -> CategoryVoice:
            provider, parameters = self._read_voice_metadata(path) if path else ("unknown", {})
            return CategoryVoice(
                id=voice_id,
                language=lang,
                type="tts",
                file_uri=f"/content/{category_name}/{item_folder_name}/{path.name}" if path else None,
                provider=provider,
                parameters=parameters,
            )

        voices: list[CategoryVoice] = []
        variants: list[CategoryVoice] = []
        main_voice_ids: dict[str, str | None] = {}
        for lang in settings.SUPPORTED_LANGUAGES:
            main, variant_paths = self._split_item_voice_files(files, item_folder_name or "", lang)
            voices.append(to_voice(f"{item_folder_name}-v-{lang}", lang, main or next(iter(variant_paths), None)))
            variants.extend(to_voice(path.stem, lang, path) for path in variant_paths)
            if main is not None:
                main_voice_ids[lang] = self._main_copy_source_id(main, variant_paths)
            else:
                main_voice_ids[lang] = variant_paths[0].stem if variant_paths else None
        return ItemVoices(item_id=item_id, voices=voices, variants=variants, main_voice_ids=main_voice_ids)

    def add_item_image(
            self,
            category_id: str,
            item_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        category_name, category_path = self._validate_category_path(category_id)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        ext = self._normalize_image_extension(image_extension)

        has_existing_image = any(
            f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS
            for f in item_path.iterdir()
        )
        if has_existing_image:
            idx = self._first_available_item_image_index(item_path, item_folder_name)
            file_name = f"{item_folder_name}_{idx:02d}{ext}"
            image_path = item_path / file_name
            self._write_image_bytes(image_path, image_data)
        else:
            # No image yet: this one becomes the main image, stored with no index suffix,
            # copied into "_01" so the item starts with a variant to edit/replace
            # without touching the main image.
            file_name = f"{item_folder_name}{ext}"
            image_path = item_path / file_name
            self._write_image_bytes(image_path, image_data)
            self._write_image_bytes(item_path / f"{item_folder_name}_01{ext}", image_data)

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
        category_name, category_path = self._validate_category_path(category_id)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        existing = self._find_item_image_by_id(item_path, image_id)
        if existing is None:
            raise CategoryNotFoundError(
                f"Image '{image_id}' not found for item '{item_id}' in category '{category_name}'.",
            )

        ext = self._normalize_image_extension(image_extension)

        new_path = item_path / f"{image_id}{ext}"
        if existing != new_path:
            try:
                existing.unlink()
            except OSError as exc:
                raise ContentOperationError(
                    f"Failed to remove previous item image '{existing}': {exc}",
                ) from exc
        self._write_image_bytes(new_path, image_data)

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
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS and f != existing
        ]

        file_uri = f"/content/{category_name}/{item_folder_name}/{existing.name}"
        was_main = existing.stem == item_folder_name
        try:
            existing.unlink()
            if was_main and remaining:
                # The main image (no index suffix) was removed and other variants remain:
                # re-point it at the lowest-indexed one by copying it, the same way
                # set_main_item_image does, so that variant stays in the item. If nothing
                # remains, the item is simply left with no images.
                promoted = min(remaining, key=lambda f: f.name)
                shutil.copyfile(promoted, item_path / f"{item_folder_name}{promoted.suffix}")
        except OSError as exc:
            raise ContentOperationError(f"Failed to delete item image: {exc}") from exc

        return CategoryImage(id=image_id, file_uri=file_uri)

    def set_main_item_image(self, category_id: str, item_id: str, image_id: str) -> ItemImages:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        target = self._find_item_image_by_id(item_path, image_id)
        if target is None:
            raise CategoryNotFoundError(
                f"Image '{image_id}' not found for item '{item_id}' in category '{category_name}'.",
            )

        if target.stem == item_folder_name:
            return self.item_images(category_id, item_id)  # Already the main image.

        # The main image is the one stored with no index suffix (e.g. "apple.png"), while
        # every other variant is indexed ("apple_01.png", "apple_02.png", ...). Making
        # *target* the main image COPIES it over the main file: the variant itself stays
        # exactly where it is, so the item keeps every image it had.
        main_path = self._find_item_image_by_id(item_path, item_folder_name)
        try:
            # The copy carries the source's extension, so a main image stored under a
            # different one (apple.png ← apple_01.jpg) must not be left behind as a second
            # unindexed file — item_images would then pick whichever sorted first.
            if main_path is not None and main_path.suffix != target.suffix:
                main_path.unlink()
            shutil.copyfile(target, item_path / f"{item_folder_name}{target.suffix}")
        except OSError as exc:
            raise ContentOperationError(f"Failed to set main image for item '{item_id}': {exc}") from exc

        return self.item_images(category_id, item_id)

    def set_main_item_voice(self, category_id: str, item_id: str, voice_id: str, language: str) -> ItemVoices:
        category_name = self._folder_name_for_id(category_id)
        category_path = self._category_path(category_name)
        item_folder_name, item_path = self._resolve_item_path(category_path, category_name, item_id)

        if language not in settings.SUPPORTED_LANGUAGES:
            raise ContentOperationError(f"Unsupported language: {language}")

        main_path, variant_paths = self._split_item_voice_files(
            list(item_path.iterdir()), item_folder_name, language,
        )
        if voice_id in (f"{item_folder_name}-v-{language}", f"{item_folder_name}_{language}") and (
                main_path or variant_paths
        ):
            return self.item_voices(category_id, item_id)  # Already the voice in use.

        target = next((p for p in variant_paths if p.stem == voice_id), None)
        if target is None:
            raise CategoryNotFoundError(
                f"Voice '{voice_id}' not found for language '{language}' of item '{item_id}' "
                f"in category '{category_name}'.",
            )

        # Like the main image, the main voice is the file with no index suffix
        # ("apple_en.mp3"). Selecting a variant COPIES it, with its metadata sidecar, over
        # the main file: the variant stays where it is, so the item keeps every recording.
        main_path = main_path or self._item_voice_path(item_path, item_folder_name, language)
        target_metadata = self._voice_metadata_path(target)
        main_metadata = self._voice_metadata_path(main_path)
        try:
            shutil.copyfile(target, main_path)
            if target_metadata.is_file():
                shutil.copyfile(target_metadata, main_metadata)
            else:
                main_metadata.unlink(missing_ok=True)
        except OSError as exc:
            raise ContentOperationError(f"Failed to set main voice for item '{item_id}': {exc}") from exc

        return self.item_voices(category_id, item_id)

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
            self._write_voice_metadata(voice_path, VoiceCreationMethod.UPLOADED, provider="custom")
        except OSError as exc:
            raise ContentOperationError(f"Failed to save item voice: {exc}") from exc

        provider, parameters = self._read_voice_metadata(voice_path)
        return CategoryVoice(
            id=voice_id,
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{item_folder_name}/{voice_path.name}",
            provider=provider,
            parameters=parameters,
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
            self._write_voice_metadata(voice_path, VoiceCreationMethod.UPLOADED, provider="custom")
        except OSError as exc:
            raise ContentOperationError(f"Failed to update item voice: {exc}") from exc

        provider, parameters = self._read_voice_metadata(voice_path)
        return CategoryVoice(
            id=voice_id,
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{item_folder_name}/{voice_path.name}",
            provider=provider,
            parameters=parameters,
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
        provider, parameters = self._read_voice_metadata(voice_path)

        try:
            self._delete_voice_with_metadata(voice_path)
        except OSError as exc:
            raise ContentOperationError(f"Failed to delete item voice: {exc}") from exc

        return CategoryVoice(
            id=voice_id, language=language, type="tts", file_uri=file_uri, provider=provider, parameters=parameters,
        )

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

        provider, parameters = self._read_voice_metadata(voice_path)
        return CategoryVoice(
            id=voice_id,
            language=language,
            type="tts",
            file_uri=f"/content/{category_name}/{item_folder_name}/{voice_path.name}",
            provider=provider,
            parameters=parameters,
        )
