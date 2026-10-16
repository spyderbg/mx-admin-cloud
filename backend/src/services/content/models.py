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
    """A single archived category snapshot within an archive listing."""

    base_name: str
    archive_folder: str
    archived_at: int


@dataclass
class CategorySummary:
    """Lightweight category listing entry."""

    id: str = ""
    name: LocalizedString = field(default_factory=lambda: LocalizedString({}))
    items_count: int = 0
    thumbnail_uri: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.name, dict):
            self.name = LocalizedString(self.name)


@dataclass
class CategoryList:
    """Result of listing categories, optionally alongside archived ones."""

    categories: list[CategorySummary] = field(default_factory=list)
    archived: list[ArchivedEntry] = field(default_factory=list)


@dataclass
class ItemDetail:
    """Summary of an item within a category's details."""

    id: str = ""
    name: LocalizedString = field(default_factory=lambda: LocalizedString({}))
    thumbnail_uri: str = ""
    images_count: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.name, dict):
            self.name = LocalizedString(self.name)


@dataclass
class CategoryImage:
    """A single image file belonging to a category or item.

    *provider* and *parameters* tell how an item image was made, from its metadata sidecar:
    the text to image configuration and its settings (with the prompt as "_prompt") for a
    generated image, "custom" for an uploaded one, and "unknown" when nothing is recorded.
    """

    id: str = ""
    file_uri: str | None = None
    provider: str = "unknown"
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass
class CategoryVoice:
    """A single voice audio file belonging to a category or item."""

    id: str = ""
    language: str = ""
    type: str = ""
    file_uri: str | None = None
    provider: str = "unknown"
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass
class CategoryDetails:
    """Full category record: metadata plus its images, voices, and items."""

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
    """Image files for a specific item, with the selected main image."""

    item_id: str = ""
    images: list[CategoryImage] = field(default_factory=list)
    thumbnail_uri: str | None = None
    #: Id of the entry in *images* the main image was copied from, i.e. the one
    #: currently selected as main. None when it matches no entry in the list.
    main_image_id: str | None = None


@dataclass
class ItemVoices:
    """Voice audio files for a specific item, with the selected main voice per language."""

    item_id: str = ""
    #: One entry per supported language: the voice currently used for that language.
    voices: list[CategoryVoice] = field(default_factory=list)
    #: Alternative recordings per language, any of which can be selected as main.
    variants: list[CategoryVoice] = field(default_factory=list)
    #: Language -> id of the entry in *variants* the main voice was copied from, i.e. the
    #: one currently selected as main. None when it matches no variant of that language.
    main_voice_ids: dict[str, str | None] = field(default_factory=dict)


@dataclass
class OptimizedItemVoice(ItemVoices):
    """Result of optimizing an item voice: the refreshed voice listing plus what was produced."""

    #: Id of the original recording (a variant) the optimized versions were made from.
    source_voice_id: str = ""
    #: Ids of the variants written by this run, one per optimizer that succeeded.
    optimized_voice_ids: list[str] = field(default_factory=list)
    #: One message per active optimizer that failed; the others' results are kept.
    warnings: list[str] = field(default_factory=list)


@dataclass
class GenerateItemVoice:  # pylint: disable=too-many-instance-attributes
    """A request to synthesize a new take of an item voice with a chosen text-to-speech configuration."""

    language: str
    text: str
    #: Text-to-speech configuration to synthesize with, enabled or not.
    configuration_name: str
    #: Voice settings layered over the configuration's own; None keeps the configured value.
    speed: float | None = None
    stability: float | None = None
    similarity: float | None = None
    #: Also create optimized versions of the new take with the active voice optimizers.
    optimize: bool = False


@dataclass
class GeneratedItemVoice(ItemVoices):
    """Result of generating an item voice: the refreshed voice listing plus what was produced."""

    #: Id of the new take.
    voice_id: str = ""
    #: Ids of the optimized versions made from the new take, when optimization was requested.
    optimized_voice_ids: list[str] = field(default_factory=list)
    #: Optimization problems; the new take is kept regardless.
    warnings: list[str] = field(default_factory=list)


@dataclass
class CreateCategory:
    """Result of creating a new category."""

    category: str = ""
    message: str = ""


@dataclass
class GenerationOutcome:
    """What one optional generation step of item creation produced: its voices or its image.

    Generation is best effort: whatever could not be made is reported in *warnings* and never
    fails the creation of the item itself.
    """

    requested: bool = False
    generated: list[str] = field(default_factory=list)  # voice languages, or image ids
    warnings: list[str] = field(default_factory=list)

    @property
    def missing(self) -> bool:
        """Whether the step was asked for and produced nothing."""
        return self.requested and not self.generated


@dataclass
class CreateItem:
    """Result of creating a new item, including what its voice and image generation produced."""

    category: str = ""
    item: str = ""
    folder: str = ""
    image_id: str = ""  # Legacy alias of item_id, kept for CLI JSON compatibility.
    item_id: str = ""
    voices: GenerationOutcome = field(default_factory=GenerationOutcome)
    image: GenerationOutcome = field(default_factory=GenerationOutcome)
    message: str = ""


@dataclass
class RemoveItem:
    """Result of permanently deleting an item from a category."""

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
class UpdateCategory:
    """Result of updating a category's id or its localized details."""

    category: str = ""
    message: str = ""


@dataclass
class RestoreCategory:
    """Result of restoring a category from the archive."""

    restored: bool = True
    name: str | None = None
    from_archive: str | None = None
    message: str = ""


@dataclass
class DeleteArchivedCategory:
    """Result of permanently deleting a category snapshot from the archive."""

    deleted: bool = True
    name: str | None = None
    archive_folder: str | None = None
    message: str = ""


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------


class ContentProvider(ServiceProvider, ABC):  # pylint: disable=too-many-public-methods
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
    def regenerate_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        """Re-synthesize a category voice from its current localized name via the text-to-speech service."""

    @abstractmethod
    def archive_category(self, category_id: str) -> ArchiveCategory:
        """Move a category from the content directory to the archive.

        Implementations must leave the archive self-describing, so that
        restore_category() can rebuild the category's root CSV registry rows.
        """

    @abstractmethod
    def update_category_details(
            self,
            category_id: str,
            category_name: LocalizedString,
            description: LocalizedString,
    ) -> UpdateCategory:
        """Update a category's localized name and description, keyed by category_id."""

    @abstractmethod
    def update_category_id(self, category_id: str, new_category_id: str) -> UpdateCategory:
        """Change a category's id from category_id to new_category_id."""

    @abstractmethod
    def remove_category(self, category_id: str) -> ArchiveCategory:
        """Remove a category (archive if configured, else delete permanently)."""

    @abstractmethod
    def restore_category(self, name: str) -> RestoreCategory:
        """Restore a category from the archive to the content directory.

        Args:
            name (str): Base folder name ('foods'), restoring the most recent archive,
                or a full archive folder name ('foods_1738456789'). Not a registry id —
                archive_category() removes the category's rows from the root CSVs.
        """

    @abstractmethod
    def delete_archived_category(self, name: str) -> DeleteArchivedCategory:
        """Permanently delete a category snapshot from the archive."""

    @abstractmethod
    def create_item(
            self,
            category_id: str,
            english_word: str,
            image_data: bytes | None = None,
            image_extension: str = ".png",
            generate_voices: bool = False,
            generate_image: bool = False,
    ) -> CreateItem:
        """Create an item with translations, an optional image, and optionally generated voices and image.

        A translation failure must abort creation without leaving files or registry rows
        behind. Voices (*generate_voices*) and the image (*generate_image*) are each generated
        only when asked for, after the item exists, and best effort: problems never fail the
        operation and are reported in ``CreateItem.voices`` / ``CreateItem.image``. Asking to
        generate an image while uploading one (*image_data*) is a ContentOperationError.
        """

    @abstractmethod
    def update_item_name(
            self,
            category_id: str,
            item_id: str,
            item_name: LocalizedString,
    ) -> ItemDetail:
        """Update an item's localized name, keyed by category_id and item_id."""

    @abstractmethod
    def update_item_id(self, category_id: str, item_id: str, new_item_id: str) -> ItemDetail:
        """Change an item's id from item_id to new_item_id within its category."""

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
    def generate_item_image(
            self,
            category_id: str,
            item_id: str,
            prompt: str,
            configuration_name: str | None = None,
    ) -> CategoryImage:
        """Generate an image from *prompt* with a text to image configuration and add it as a new variant."""

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
    def set_main_item_image(self, category_id: str, item_id: str, image_id: str) -> ItemImages:
        """Make the image file variant with the given image_id the item's main image."""

    @abstractmethod
    def set_main_item_voice(self, category_id: str, item_id: str, voice_id: str) -> ItemVoices:
        """Make the voice variant with the given voice_id the item's main voice for its language."""

    @abstractmethod
    def optimize_item_voice(self, category_id: str, item_id: str, voice_id: str) -> OptimizedItemVoice:
        """Create one optimized variant of an item voice per active voice optimizer, keeping the original."""

    @abstractmethod
    def add_item_voice(self, category_id: str, item_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        """Add an audio file for an item voice in the given language as a new take or variant."""

    @abstractmethod
    def update_item_voice(self, category_id: str, item_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        """Replace the audio file for an existing item voice."""

    @abstractmethod
    def delete_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        """Delete the audio file for an existing item voice."""

    @abstractmethod
    def regenerate_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        """Re-synthesize an item voice from its current text via the text-to-speech service."""

    @abstractmethod
    def generate_item_voice(self, category_id: str, item_id: str, request: GenerateItemVoice) -> GeneratedItemVoice:
        """Synthesize a new take of an item voice from the request's text, configuration and settings.

        The take is added next to the existing recordings and the language's main voice is
        kept. Requested optimization runs on the new take; its failures are only warnings.
        """

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

    @classmethod
    def _folder_name_from_item_id(cls, item_id: str) -> str:
        """Derive the on-disk folder name from an item id, e.g. 'I_WaterBottle' -> 'water_bottle'.

        The folder follows the id and nothing else: editing an item's names never moves it,
        and only an id change does.
        """
        _, _, remainder = item_id.partition("_")
        folder = cls._to_snake(remainder or item_id)
        return re.sub(r"_+", "_", folder).strip("_")

    def _english_word_to_image_id(self, word: str) -> str:
        """Return the item id for *word* (e.g. 'red apple' -> 'I_RedApple'), or "" if it has no letters or digits."""
        pascal = self._to_pascal(word.strip()) if word else ""
        return f"I_{pascal}" if pascal else ""
