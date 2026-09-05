"""Pydantic request/response schemas for content service routes."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from src.core.localization import LocalizedString


# region Command models

class FileAction(str, Enum):
    """Action performed on a category/item file (image or voice), echoed back in the response."""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"


class ItemSummary(BaseModel):
    """Item entry returned inside CategoryDetailsResponse."""

    id: str
    name: LocalizedString
    thumbnail_uri: str | None = None
    images_count: int = 0


# endregion

# region Categories Request bodies

class CreateCategoryBody(BaseModel):
    """Request body for creating a new category."""

    name: str


class RenameCategoryBody(BaseModel):
    """Request body for updating a category's localized name and description."""

    new_category_id: str | None = None
    category_name: LocalizedString
    description: LocalizedString


class RestoreCategoryBody(BaseModel):
    """Request body for restoring a category."""

    name: str


# endregion

# region Categories Response models

class ArchivedEntry(BaseModel):
    """Single archived category entry."""

    base_name: str
    archive_folder: str
    archived_at: int


class CategorySummary(BaseModel):
    """Summary of a single category returned in the list endpoint."""

    id: str
    name: LocalizedString
    items_count: int
    thumbnail_uri: str | None = None


class CategoryListResponse(BaseModel):
    """Response for GET /content/categories."""

    categories: list[CategorySummary]
    archived: list[ArchivedEntry] | None = None
    error: str | None = None


class CategoryDetailsResponse(BaseModel):
    """Response for GET /content/categories/{name}."""

    id: str
    name: LocalizedString
    description: LocalizedString = Field(default_factory=lambda: LocalizedString({}))
    items: list[ItemSummary] = []


class CreateCategoryResponse(BaseModel):
    """Response for POST /content/categories."""

    category: str
    message: str


class RenameCategoryResponse(BaseModel):
    """Response for PATCH /content/categories/{category_id}."""

    category: str
    message: str
    category_id: str
    old_category_id: str


class RestoreCategoryResponse(BaseModel):
    """Response for POST /content/categories/restore."""

    restored: bool
    name: str | None = None
    from_archive: str | None = None
    message: str


class DeleteArchivedCategoryResponse(BaseModel):
    """Response for DELETE /content/categories/archive/{name}."""

    deleted: bool
    name: str | None = None
    archive_folder: str | None = None
    message: str


class ArchiveCategoryResponse(BaseModel):
    """Response for archiving (or removing via archive) a category."""

    archived: bool | None = None
    removed: bool | None = None
    name: str
    archive_folder: str | None = None
    destination: str | None = None
    message: str


class RemoveCategoryResponse(BaseModel):
    """Response for DELETE /content/categories/{name}.

    Maybe an archive result or a permanent delete result, so both
    "archived" and "removed" are optional.
    """

    archived: bool | None = None
    removed: bool | None = None
    name: str
    archive_folder: str | None = None
    destination: str | None = None
    message: str


# endregion

# region Category Images Response models

class ImageResponse(BaseModel):
    """Response for a single item image entry."""

    id: str
    file_uri: str | None = None
    action: FileAction | None = None


class CategoryImagesResponse(BaseModel):
    """Response for GET /categories/{name}/images."""

    category_id: str
    name: LocalizedString
    images: list[ImageResponse] = []


# endregion

# region Category Voices Response models

class VoiceResponse(BaseModel):
    """A single voice entry."""

    id: str
    language: str
    type: str
    file_uri: str | None = None
    action: FileAction | None = None


class CategoryVoicesResponse(BaseModel):
    """Response for GET /categories/{name}/voices."""

    category_id: str
    name: LocalizedString
    voices: list[VoiceResponse] = []


# endregion

# region Items Request bodies


class UpdateItemBody(BaseModel):
    """Request body for updating an item's localized name."""

    item_name: LocalizedString


# endregion

# region Items Response models

class CreateItemResponse(BaseModel):
    """Response for POST /content/categories/{category_id}/items (and legacy POST /content/items)."""

    category: str
    item: str
    folder: str
    image_id: str  # Legacy alias of item_id.
    item_id: str
    voices_generated: list[str]
    voice_warnings: list[str]
    message: str


class ItemListResponse(BaseModel):
    """Response for GET /categories/{category_name}/items."""

    items: list[ItemSummary]


class ItemImagesResponse(BaseModel):
    """Response for GET /categories/{name}/items/{item_id}/images."""

    item_id: str
    name: LocalizedString
    images: list[ImageResponse] = []
    thumbnail_uri: str | None = None


class ItemVoicesResponse(BaseModel):
    """Response for GET /categories/{name}/items/{item_id}/voices."""

    item_id: str
    name: LocalizedString
    voices: list[VoiceResponse] = []

# endregion
