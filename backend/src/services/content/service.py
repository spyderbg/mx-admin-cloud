"""Content service business logic and orchestration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from typing_extensions import override

from src.core.localization import LocalizedString
from src.services.content.models import (
    ArchiveCategory,
    CategoryDetails,
    CategoryImage,
    CategoryList,
    CategoryVoice,
    ContentError,
    ContentProvider,
    ContentProviderUnavailableError,
    CreateCategory,
    CreateItem,
    DeleteArchivedCategory,
    GenerateItemVoice,
    GeneratedItemVoice,
    ItemDetail,
    ItemImages,
    ItemVoices,
    OptimizedItemVoice,
    RemoveItem,
    RestoreCategory,
    UpdateCategory,
)
from src.services.models import Service, ServiceProvider
from src.services.provider_factory import ServiceProviderFactory
from src.services.provider_strategy import ProviderSelectionStrategy

ResultT = TypeVar("ResultT")


class ContentProviderFactory(ServiceProviderFactory[ContentProvider]):
    """Content-specific provider registry."""

    unsupported_label = "content provider"


class ContentService(Service):  # pylint: disable=too-many-public-methods
    """Orchestrator for content provider operations.

    Runs every content operation on the active ContentProviders in priority order until
    one succeeds (FIRST_SUCCESS); disabled providers never run.
    """

    SUPPORTED_STRATEGIES = (ProviderSelectionStrategy.FIRST_SUCCESS,)
    DEFAULT_STRATEGY = ProviderSelectionStrategy.FIRST_SUCCESS

    def __init__(
            self,
            service_name: str,
            package_name: str,
            service_dir: Path,
            base_dir: Path,
    ) -> None:
        super().__init__(service_name, package_name, service_dir, base_dir)
        self._providers: list[ContentProvider] = []

    @override
    def replace_providers(self, providers: list[ServiceProvider]) -> None:
        self._providers = [p for p in providers if isinstance(p, ContentProvider)]

    @override
    def providers_snapshot(self) -> list[ServiceProvider]:
        return list(self._providers)

    @override
    def get_all_providers(self) -> list[ContentProvider]:
        return list(self._providers)

    @override
    def get_active_providers(self) -> list[ContentProvider]:
        return [p for p in self._providers if p.is_active()]

    # ------------------------------------------------------------------
    # Category methods
    # ------------------------------------------------------------------

    def list_categories(
            self,
            include_archived: bool = False,
            archived_only: bool = False,
    ) -> CategoryList:
        """List content categories via the active provider."""
        return self._first_success(lambda provider: provider.list_categories(
            include_archived=include_archived,
            archived_only=archived_only,
        ))

    def category_details_by_name(self, category_name: str) -> CategoryDetails:
        """Return category details via the active provider, looked up by category_name."""
        return self._first_success(
            lambda provider: provider.category_details(provider.category_id_by_name(category_name)),
        )

    def category_details_by_id(self, category_id: str) -> CategoryDetails:
        """Return category details via the active provider."""
        return self._first_success(lambda provider: provider.category_details(category_id))

    def create_category(self, category_name: str) -> CreateCategory:
        """Create a new category via the active provider."""
        return self._first_success(lambda provider: provider.create_category(category_name))

    def archive_category(self, category_name: str) -> ArchiveCategory:
        """Archive a category via the active provider."""
        return self._first_success(
            lambda provider: provider.archive_category(provider.category_id_by_name(category_name)),
        )

    def update_category_details(
            self,
            category_id: str,
            category_name: LocalizedString,
            description: LocalizedString,
    ) -> UpdateCategory:
        """Update a category's localized name and description via the active provider."""
        return self._first_success(
            lambda provider: provider.update_category_details(category_id, category_name, description),
        )

    def update_category_id(self, category_id: str, new_category_id: str) -> UpdateCategory:
        """Rename a category's id via the active provider."""
        return self._first_success(lambda provider: provider.update_category_id(category_id, new_category_id))

    def remove_category(self, category_name: str) -> ArchiveCategory:
        """Remove a category via the active provider, looked up by folder name."""
        return self._first_success(
            lambda provider: provider.remove_category(provider.category_id_by_name(category_name)),
        )

    def remove_category_by_id(self, category_id: str) -> ArchiveCategory:
        """Remove a category via the active provider.

        Accepts either a registry id ('C_Foods') or a folder name ('foods') so the
        HTTP route and the CLI can both pass whatever identifier they hold.
        """
        return self._first_success(
            lambda provider: provider.remove_category(self._resolve_category_id(provider, category_id)),
        )

    def restore_category(self, category_name: str) -> RestoreCategory:
        """Restore a category via the active provider.

        Takes a base folder name or a full archive folder name — not a registry id,
        because archiving removes the category's rows from the root CSV registry.
        """
        return self._first_success(lambda provider: provider.restore_category(category_name))

    def delete_archived_category(self, archive_name: str) -> DeleteArchivedCategory:
        """Permanently delete an archived category snapshot via the active provider."""
        return self._first_success(lambda provider: provider.delete_archived_category(archive_name))

    @staticmethod
    def _resolve_category_id(provider: ContentProvider, value: str) -> str:
        """Return the registry id for *value*, which may be an id or a folder name."""
        try:
            return provider.category_id_by_name(value)
        except ContentError:
            return value

    # ------------------------------------------------------------------
    # Category images methods
    # ------------------------------------------------------------------

    def add_category_image(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Add a new image to a category via the active provider."""
        return self._first_success(
            lambda provider: provider.add_category_image(category_id, image_data, image_extension),
        )

    def update_category_image(
            self,
            category_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Replace an existing category image via the active provider."""
        return self._first_success(
            lambda provider: provider.update_category_image(category_id, image_id, image_data, image_extension),
        )

    def delete_category_image(self, category_id: str, image_id: str) -> CategoryImage:
        """Delete an existing category image via the active provider."""
        return self._first_success(lambda provider: provider.delete_category_image(category_id, image_id))

    def set_category_icon(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Create or replace the category's icon via the active provider."""
        return self._first_success(
            lambda provider: provider.set_category_icon(category_id, image_data, image_extension),
        )

    # ------------------------------------------------------------------
    # Category voices methods
    # ------------------------------------------------------------------

    def add_category_voice(self, category_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        """Add a new voice to a category via the active provider."""
        return self._first_success(lambda provider: provider.add_category_voice(category_id, language, audio_data))

    def update_category_voice(self, category_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        """Replace an existing category voice via the active provider."""
        return self._first_success(lambda provider: provider.update_category_voice(category_id, voice_id, audio_data))

    def delete_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        """Delete an existing category voice via the active provider."""
        return self._first_success(lambda provider: provider.delete_category_voice(category_id, voice_id))

    def regenerate_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        """Re-synthesize a category voice from its current localized name via the active provider."""
        return self._first_success(lambda provider: provider.regenerate_category_voice(category_id, voice_id))

    # ------------------------------------------------------------------
    # Item methods
    # ------------------------------------------------------------------

    def item_images(self, category_id: str, item_id: str) -> ItemImages:
        """Return image files for a specific item via the active provider."""
        return self._first_success(lambda provider: provider.item_images(category_id, item_id))

    def item_voices(self, category_id: str, item_id: str) -> ItemVoices:
        """Return voice files for a specific item via the active provider."""
        return self._first_success(lambda provider: provider.item_voices(category_id, item_id))

    def create_item_by_id(
            self,
            category_id: str,
            english_word: str,
            image_data: bytes | None = None,
            image_extension: str = ".png",
            generate_voices: bool = False,
            generate_image: bool = False,
    ) -> CreateItem:
        """Create a content item via the active provider, keyed by category id."""
        return self._first_success(lambda provider: provider.create_item(
            category_id,
            english_word,
            image_data,
            image_extension,
            generate_voices=generate_voices,
            generate_image=generate_image,
        ))

    def update_item(self, category_id: str, item_id: str, item_name: LocalizedString) -> ItemDetail:
        """Update an item's localized name via the active provider."""
        return self._first_success(lambda provider: provider.update_item_name(category_id, item_id, item_name))

    def update_item_id(self, category_id: str, item_id: str, new_item_id: str) -> ItemDetail:
        """Rename an item's id via the active provider."""
        return self._first_success(lambda provider: provider.update_item_id(category_id, item_id, new_item_id))

    def remove_item(self, category_id: str, item_id: str) -> RemoveItem:
        """Remove an item via the active provider."""
        return self._first_success(lambda provider: provider.remove_item(category_id, item_id))

    # ------------------------------------------------------------------
    # Item images methods
    # ------------------------------------------------------------------

    def add_item_image(
            self,
            category_id: str,
            item_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Add a new image file variant to an item via the active provider."""
        return self._first_success(
            lambda provider: provider.add_item_image(category_id, item_id, image_data, image_extension),
        )

    def generate_item_image(
            self,
            category_id: str,
            item_id: str,
            prompt: str,
            configuration_name: str | None = None,
    ) -> CategoryImage:
        """Generate an image from a prompt and add it to an item via the active provider."""
        return self._first_success(
            lambda provider: provider.generate_item_image(category_id, item_id, prompt, configuration_name),
        )

    def update_item_image(
            self,
            category_id: str,
            item_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Replace an item's image file variant via the active provider."""
        return self._first_success(lambda provider: provider.update_item_image(
            category_id, item_id, image_id, image_data, image_extension,
        ))

    def delete_item_image(self, category_id: str, item_id: str, image_id: str) -> CategoryImage:
        """Delete an item's image file variant via the active provider."""
        return self._first_success(lambda provider: provider.delete_item_image(category_id, item_id, image_id))

    def set_main_item_image(self, category_id: str, item_id: str, image_id: str) -> ItemImages:
        """Make an item's image file variant the main image via the active provider."""
        return self._first_success(lambda provider: provider.set_main_item_image(category_id, item_id, image_id))

    def set_main_item_voice(self, category_id: str, item_id: str, voice_id: str) -> ItemVoices:
        """Make an item's voice variant the main voice for its language via the active provider."""
        return self._first_success(lambda provider: provider.set_main_item_voice(category_id, item_id, voice_id))

    def optimize_item_voice(self, category_id: str, item_id: str, voice_id: str) -> OptimizedItemVoice:
        """Create optimized variants of an item voice via the active provider."""
        return self._first_success(lambda provider: provider.optimize_item_voice(category_id, item_id, voice_id))

    # ------------------------------------------------------------------
    # Item voices methods
    # ------------------------------------------------------------------

    def add_item_voice(self, category_id: str, item_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        """Add audio for an item voice in the given language, via the active provider."""
        return self._first_success(lambda provider: provider.add_item_voice(category_id, item_id, language, audio_data))

    def update_item_voice(self, category_id: str, item_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        """Replace audio for an existing item voice via the active provider."""
        return self._first_success(
            lambda provider: provider.update_item_voice(category_id, item_id, voice_id, audio_data),
        )

    def delete_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        """Delete audio for an existing item voice via the active provider."""
        return self._first_success(lambda provider: provider.delete_item_voice(category_id, item_id, voice_id))

    def regenerate_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        """Re-synthesize an item voice from its current text via the active provider."""
        return self._first_success(lambda provider: provider.regenerate_item_voice(category_id, item_id, voice_id))

    def generate_item_voice(self, category_id: str, item_id: str, request: GenerateItemVoice) -> GeneratedItemVoice:
        """Synthesize a new take of an item voice with a chosen configuration via the active provider."""
        return self._first_success(lambda provider: provider.generate_item_voice(category_id, item_id, request))

    # ------------------------------------------------------------------

    def _first_success(self, operation: Callable[[ContentProvider], ResultT]) -> ResultT:
        """Run *operation* on the active providers in priority order and return the first success.

        Disabled providers never run, so with none active the content is unavailable. When
        every provider fails, the highest-priority provider's error is raised, keeping the
        type the routes map onto a status (e.g. CategoryNotFoundError -> 404); with several
        providers its message lists each provider's error.
        """
        providers = self.get_active_providers()
        if not providers:
            raise ContentProviderUnavailableError("No active content provider is configured.")
        failures: list[tuple[ContentProvider, Exception]] = []
        for provider in providers:
            try:
                return operation(provider)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                failures.append((provider, exc))
        first_error = failures[0][1]
        if len(failures) > 1 and isinstance(first_error, ContentError):
            message = "; ".join(f"{provider.name}: {exc}" for provider, exc in failures)
            raise type(first_error)(message) from first_error
        raise first_error
