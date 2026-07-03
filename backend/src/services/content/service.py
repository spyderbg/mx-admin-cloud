"""Content service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path

from typing_extensions import override

from src.services.content.models import (
    ArchiveCategory,
    CategoryDetails,
    CategoryList,
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
    # Delegation methods
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

    def category_details(self, category_name: str) -> CategoryDetails:
        """Return category details via the active provider."""
        return self._select_provider().category_details(category_name)

    def create_category(self, category_name: str) -> CreateCategory:
        """Create a new category via the active provider."""
        return self._select_provider().create_category(category_name)

    def create_item(
            self,
            category_name: str,
            english_word: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CreateItem:
        """Create a content item via the active provider."""
        return self._select_provider().create_item(
            category_name=category_name,
            english_word=english_word,
            image_data=image_data,
            image_extension=image_extension,
        )

    def archive_category(self, category_name: str) -> ArchiveCategory:
        """Archive a category via the active provider."""
        return self._select_provider().archive_category(category_name)

    def rename_category(self, old_name: str, new_name: str) -> RenameCategory:
        """Rename a category via the active provider."""
        return self._select_provider().rename_category(old_name, new_name)

    def remove_category(self, category_name: str) -> ArchiveCategory:
        """Remove a category via the active provider."""
        return self._select_provider().remove_category(category_name)

    def restore_category(self, category_name: str) -> RestoreCategory:
        """Restore a category via the active provider."""
        return self._select_provider().restore_category(category_name)

    def item_images(self, category_name: str, item_id: str) -> ItemImages:
        """Return image files for a specific item via the active provider."""
        return self._select_provider().item_images(category_name, item_id)

    def item_voices(self, category_name: str, item_id: str) -> ItemVoices:
        """Return voice files for a specific item via the active provider."""
        return self._select_provider().item_voices(category_name, item_id)

    # ------------------------------------------------------------------

    def _select_provider(self) -> ContentProvider:
        """Return the first active content provider or raise."""
        active = self.get_active_providers()
        if active:
            return active[0]
        if self._providers:
            return self._providers[0]
        raise ContentProviderUnavailableError("No content provider is available.")
