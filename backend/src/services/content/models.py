"""Content service models and provider contract."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from src.services.models import ServiceProvider


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


class ContentProvider(ServiceProvider, ABC):
    """Provider contract for content CRUD operations."""

    @abstractmethod
    def list_categories(
            self,
            include_archived: bool = False,
            archived_only: bool = False,
    ) -> dict[str, Any]:
        """List content categories, optionally including archived ones."""

    @abstractmethod
    def category_details(self, category_name: str) -> dict[str, Any]:
        """Return object folders and CSV-derived data for a category."""

    @abstractmethod
    def create_category(self, category_name: str) -> dict[str, Any]:
        """Create a new category with voice files and icon."""

    @abstractmethod
    def create_item(
            self,
            category_name: str,
            english_word: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> dict[str, Any]:
        """Create an item folder with image, translations, and voice files."""

    @abstractmethod
    def archive_category(self, category_name: str) -> dict[str, Any]:
        """Move a category from the content directory to the archive."""

    @abstractmethod
    def rename_category(self, old_name: str, new_name: str) -> dict[str, Any]:
        """Rename a category and regenerate its voice files."""

    @abstractmethod
    def remove_category(self, category_name: str) -> dict[str, Any]:
        """Remove a category (archive if configured, else delete permanently)."""

    @abstractmethod
    def restore_category(self, category_name: str) -> dict[str, Any]:
        """Restore a category from the archive to the content directory."""

    # ------------------------------------------------------------------
    # Naming helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_pascal(s: str) -> str:
        parts = [p for p in re.split(r"[^A-Za-z0-9]+", s) if p]
        return "".join(p[:1].upper() + p[1:].lower() for p in parts)

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
