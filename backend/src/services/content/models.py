"""Content service models and provider contract."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.core.localization import LocalizedString
from src.services.models import ServiceProvider


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ContentError(Exception):
    """Base exception for content service errors."""


class ContentProviderUnavailableError(ContentError):
    """Raised when no content provider is loaded or active."""


class InvalidCategoryNameError(ContentError):
    """Raised when a category name fails validation."""


class CategoryNotFoundError(ContentError):
    """Raised when the requested category does not exist."""


class CategoryConflictError(ContentError):
    """Raised when a category already exists and cannot be overwritten."""


class ArchiveUnavailableError(ContentError):
    """Raised when the archive directory does not exist or is not accessible."""


class ContentOperationError(ContentError):
    """Raised when a content operation fails (TTS, translation, file I/O, icon generation)."""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ArchivedEntry:
    base_name: str
    archive_folder: str
    archived_at: int


@dataclass
class CategorySummary:
    id: str = ""
    name: LocalizedString = field(default_factory=lambda: LocalizedString({}))
    items_count: int = 0
    thumbnail_uri: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.name, dict):
            self.name = LocalizedString(self.name)


@dataclass
class CategoryList:
    categories: list[CategorySummary] = field(default_factory=list)
    archived: list[ArchivedEntry] = field(default_factory=list)


@dataclass
class ItemDetail:
    id: str = ""
    name: LocalizedString = field(default_factory=lambda: LocalizedString({}))
    thumbnail_uri: str = ""
    images_count: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.name, dict):
            self.name = LocalizedString(self.name)


@dataclass
class CategoryImage:
    id: str = ""
    file_uri: str | None = None


@dataclass
class CategoryVoice:
    id: str = ""
    language: str = ""
    type: str = ""
    file_uri: str | None = None


@dataclass
class CategoryDetails:
    id: str = ""
    name: LocalizedString = field(default_factory=lambda: LocalizedString({}))
    description: LocalizedString = field(default_factory=lambda: LocalizedString({}))
    images: list[CategoryImage] = field(default_factory=list)
    voices: list[CategoryVoice] = field(default_factory=list)
    items: list[ItemDetail] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.name, dict):
            self.name = LocalizedString(self.name)
        if isinstance(self.description, dict):
            self.description = LocalizedString(self.description)


@dataclass
class ItemImages:
    item_id: str = ""
    images: list[CategoryImage] = field(default_factory=list)
    thumbnail_uri: str | None = None


@dataclass
class ItemVoices:
    item_id: str = ""
    voices: list[CategoryVoice] = field(default_factory=list)


@dataclass
class CreateCategory:
    category: str = ""
    message: str = ""


@dataclass
class CreateItem:
    category: str = ""
    item: str = ""
    folder: str = ""
    image_id: str = ""


@dataclass
class RemoveItem:
    category: str = ""
    item: str = ""


@dataclass
class ArchiveCategory:
    """Used for both archive_category and remove_category results."""
    name: str = ""
    message: str = ""
    archived: bool | None = None
    removed: bool | None = None
    archive_folder: str | None = None
    destination: str | None = None


@dataclass
class RenameCategory:
    category: str = ""
    message: str = ""


@dataclass
class RestoreCategory:
    restored: bool = True
    name: str | None = None
    from_archive: str | None = None
    message: str = ""


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------


class ContentProvider(ServiceProvider, ABC):
    """Provider contract for content CRUD operations."""

    @abstractmethod
    def list_categories(
            self,
            include_archived: bool = False,
            archived_only: bool = False,
    ) -> CategoryList:
        """List content categories, optionally including archived ones."""

    @abstractmethod
    def category_details(self, category_id: str) -> CategoryDetails:
        """Return object folders and CSV-derived data for a category."""

    @abstractmethod
    def create_category(self, category_name: str) -> CreateCategory:
        """Create a new category with voice files and icon."""

    @abstractmethod
    def add_category_image(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Add a new image file to a category."""

    @abstractmethod
    def update_category_image(
            self,
            category_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Replace the file contents of an existing category image."""

    @abstractmethod
    def delete_category_image(self, category_id: str, image_id: str) -> CategoryImage:
        """Delete an existing image file from a category."""

    @abstractmethod
    def set_category_icon(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Create or replace the category's icon file ({category_name}/{category_name}.<ext>)."""

    @abstractmethod
    def add_category_voice(self, category_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        """Add a new voice audio file to a category for the given language."""

    @abstractmethod
    def update_category_voice(self, category_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        """Replace the audio file contents of an existing category voice."""

    @abstractmethod
    def delete_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        """Delete an existing voice audio file from a category."""

    @abstractmethod
    def archive_category(self, category_id: str) -> ArchiveCategory:
        """Move a category from the content directory to the archive."""

    @abstractmethod
    def update_category_details(
            self,
            category_id: str,
            category_name: LocalizedString,
            description: LocalizedString,
    ) -> RenameCategory:
        """Update a category's localized name and description, keyed by category_id."""

    @abstractmethod
    def rename_category(self, category_id: str, new_category_id: str) -> RenameCategory:
        """Change a category's id from category_id to new_category_id."""

    @abstractmethod
    def remove_category(self, category_id: str) -> ArchiveCategory:
        """Remove a category (archive if configured, else delete permanently)."""

    @abstractmethod
    def restore_category(self, category_id: str) -> RestoreCategory:
        """Restore a category from the archive to the content directory."""

    @abstractmethod
    def create_item(
            self,
            category_id: str,
            english_word: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CreateItem:
        """Create an item folder with image, translations, and voice files."""

    @abstractmethod
    def update_item_name(
            self,
            category_id: str,
            item_id: str,
            item_name: LocalizedString,
    ) -> ItemDetail:
        """Update an item's localized name, keyed by category_id and item_id."""

    @abstractmethod
    def remove_item(self, category_id: str, item_id: str) -> RemoveItem:
        """Permanently delete an item folder from its category."""

    @abstractmethod
    def item_images(self, category_id: str, item_id: str) -> ItemImages:
        """Return image files for a specific item."""

    @abstractmethod
    def item_voices(self, category_id: str, item_id: str) -> ItemVoices:
        """Return voice files for a specific item."""

    @abstractmethod
    def add_item_image(
            self,
            category_id: str,
            item_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Add a new image file variant to an item at the next available index."""

    @abstractmethod
    def update_item_image(
            self,
            category_id: str,
            item_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Replace the image file variant with the given image_id for an item."""

    @abstractmethod
    def delete_item_image(self, category_id: str, item_id: str, image_id: str) -> CategoryImage:
        """Delete the image file variant with the given image_id from an item (at least one must remain)."""

    @abstractmethod
    def add_item_voice(self, category_id: str, item_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        """Add the audio file for an item voice in the given language, if it doesn't have one yet."""

    @abstractmethod
    def update_item_voice(self, category_id: str, item_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        """Replace the audio file for an existing item voice."""

    @abstractmethod
    def delete_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        """Delete the audio file for an existing item voice."""

    @abstractmethod
    def category_id_by_name(self, category_name: str) -> str:
        """Return the category_id registered for the given category_name."""

    # ------------------------------------------------------------------
    # Naming helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_pascal(s: str) -> str:
        parts = [p for p in re.split(r"[^A-Za-z0-9]+", s) if p]
        return "".join(p[:1].upper() + p[1:].lower() for p in parts)

    @staticmethod
    def _to_snake(s: str) -> str:
        """Convert a PascalCase (or already-snake) string to snake_case."""
        return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()

    @classmethod
    def _folder_name_from_category_id(cls, category_id: str) -> str:
        """Derive the on-disk folder name from a category id, e.g. 'C_AtSchool' -> 'at_school'."""
        _, _, remainder = category_id.partition("_")
        return cls._to_snake(remainder or category_id)

    @staticmethod
    def _english_word_to_folder_name(word: str) -> str:
        if not word or not word.strip():
            return ""
        normalized = word.strip().replace(" ", "_")
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized.lower()

    def _english_word_to_image_id(self, word: str) -> str:
        if not word or not word.strip():
            return ""
        return "I_" + self._to_pascal(word.strip())
