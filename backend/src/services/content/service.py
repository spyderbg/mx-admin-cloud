"""Content service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path

from typing_extensions import override

from src.services.content.models import (
    ArchiveCategory,
    CategoryDetails,
    CategoryImage,
    CategoryList,
    CategoryVoice,
    ContentProvider,
    ContentProviderUnavailableError,
    CreateCategory,
    CreateItem,
    ItemImages,
    ItemVoices,
    RenameCategory,
    RestoreCategory,
)
from src.services.models import Service, ServiceProvider
from src.services.provider_factory import ServiceProviderFactory


class ContentProviderFactory(ServiceProviderFactory[ContentProvider]):
    """Content-specific provider registry."""

    unsupported_label = "content provider"


class ContentService(Service):
    """Orchestrator for content provider operations.

    Delegates every content operation to the first active ContentProvider.
    """

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
        return self._select_provider().list_categories(
            include_archived=include_archived,
            archived_only=archived_only,
        )

    def category_details_by_name(self, category_name: str) -> CategoryDetails:
        """Return category details via the active provider, looked up by category_name."""
        provider = self._select_provider()
        return provider.category_details(provider.category_id_by_name(category_name))

    def category_details_by_id(self, category_id: str) -> CategoryDetails:
        """Return category details via the active provider."""
        return self._select_provider().category_details(category_id)

    def create_category(self, category_name: str) -> CreateCategory:
        """Create a new category via the active provider."""
        return self._select_provider().create_category(category_name)

    def archive_category(self, category_name: str) -> ArchiveCategory:
        """Archive a category via the active provider."""
        provider = self._select_provider()
        return provider.archive_category(provider.category_id_by_name(category_name))

    def rename_category(self, old_name: str, new_name: str) -> RenameCategory:
        """Rename a category via the active provider."""
        provider = self._select_provider()
        return provider.rename_category(provider.category_id_by_name(old_name), new_name)

    def remove_category(self, category_name: str) -> ArchiveCategory:
        """Remove a category via the active provider."""
        provider = self._select_provider()
        return provider.remove_category(provider.category_id_by_name(category_name))

    def restore_category(self, category_name: str) -> RestoreCategory:
        """Restore a category via the active provider."""
        provider = self._select_provider()
        return provider.restore_category(provider.category_id_by_name(category_name))

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
        return self._select_provider().add_category_image(category_id, image_data, image_extension)

    def update_category_image(
            self,
            category_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Replace an existing category image via the active provider."""
        return self._select_provider().update_category_image(category_id, image_id, image_data, image_extension)

    def delete_category_image(self, category_id: str, image_id: str) -> CategoryImage:
        """Delete an existing category image via the active provider."""
        return self._select_provider().delete_category_image(category_id, image_id)

    def set_category_icon(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Create or replace the category's icon via the active provider."""
        return self._select_provider().set_category_icon(category_id, image_data, image_extension)

    # ------------------------------------------------------------------
    # Category voices methods
    # ------------------------------------------------------------------

    def add_category_voice(self, category_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        """Add a new voice to a category via the active provider."""
        return self._select_provider().add_category_voice(category_id, language, audio_data)

    def update_category_voice(self, category_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        """Replace an existing category voice via the active provider."""
        return self._select_provider().update_category_voice(category_id, voice_id, audio_data)

    def delete_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        """Delete an existing category voice via the active provider."""
        return self._select_provider().delete_category_voice(category_id, voice_id)

    # ------------------------------------------------------------------
    # Item methods
    # ------------------------------------------------------------------

    def item_images(self, category_id: str, item_id: str) -> ItemImages:
        """Return image files for a specific item via the active provider."""
        return self._select_provider().item_images(category_id, item_id)

    def item_voices(self, category_id: str, item_id: str) -> ItemVoices:
        """Return voice files for a specific item via the active provider."""
        return self._select_provider().item_voices(category_id, item_id)

    def create_item(
            self,
            category_name: str,
            english_word: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CreateItem:
        """Create a content item via the active provider."""
        provider = self._select_provider()
        return provider.create_item(
            provider.category_id_by_name(category_name),
            english_word,
            image_data,
            image_extension,
        )

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
        return self._select_provider().add_item_image(category_id, item_id, image_data, image_extension)

    def update_item_image(
            self,
            category_id: str,
            item_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        """Replace an item's image file variant via the active provider."""
        return self._select_provider().update_item_image(
            category_id, item_id, image_id, image_data, image_extension,
        )

    def delete_item_image(self, category_id: str, item_id: str, image_id: str) -> CategoryImage:
        """Delete an item's image file variant via the active provider."""
        return self._select_provider().delete_item_image(category_id, item_id, image_id)

    # ------------------------------------------------------------------
    # Item voices methods
    # ------------------------------------------------------------------

    def add_item_voice(self, category_id: str, item_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        """Add audio for an item voice in the given language, via the active provider."""
        return self._select_provider().add_item_voice(category_id, item_id, language, audio_data)

    def update_item_voice(self, category_id: str, item_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        """Replace audio for an existing item voice via the active provider."""
        return self._select_provider().update_item_voice(category_id, item_id, voice_id, audio_data)

    def delete_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        """Delete audio for an existing item voice via the active provider."""
        return self._select_provider().delete_item_voice(category_id, item_id, voice_id)

    # ------------------------------------------------------------------

    def _select_provider(self) -> ContentProvider:
        """Return the first active content provider or raise."""
        active = self.get_active_providers()
        if active:
            return active[0]
        if self._providers:
            return self._providers[0]
        raise ContentProviderUnavailableError("No content provider is available.")
