"""Pydantic request/response schemas for content service routes."""

from __future__ import annotations

from pydantic import BaseModel

from src.core.localization import LocalizedString


# region Request bodies

class CreateCategoryBody(BaseModel):
    """Request body for creating a new category."""

    name: str


class RenameCategoryBody(BaseModel):
    """Request body for renaming a category."""

    name: str


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


class ItemSummary(BaseModel):
    """Item entry returned inside CategoryDetailsResponse."""

    id: str
    name: LocalizedString
    thumbnail_uri: str | None = None
    images_count: int = 0


class CategoryDetailsResponse(BaseModel):
    """Response for GET /content/categories/{name}."""

    id: str
    name: LocalizedString
    description: str = ""
    items: list[ItemSummary] = []


class CreateCategoryResponse(BaseModel):
    """Response for POST /content/categories."""

    category: str
    message: str


class CreateItemResponse(BaseModel):
    """Response for POST /content/items."""

    category: str
    item: str
    folder: str
    image_id: str


class RenameCategoryResponse(BaseModel):
    """Response for PATCH /content/categories/{name}."""

    category: str
    message: str


class ArchiveCategoryResponse(BaseModel):
    """Response for archiving (or removing via archive) a category."""

    archived: bool | None = None
    removed: bool | None = None
    name: str
    archive_folder: str | None = None
    destination: str | None = None
    message: str


class RestoreCategoryResponse(BaseModel):
    """Response for POST /content/categories/restore."""

    restored: bool
    name: str | None = None
    from_archive: str | None = None
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


class CategoryVoicesResponse(BaseModel):
    """Response for GET /categories/{name}/voices."""

    category_id: str
    name: LocalizedString
    voices: list[VoiceResponse] = []


# endregion

# region Items Response models

class ItemListResponse(BaseModel):
    """Response for GET /categories/{category_name}/items."""

    items: list[str]


# endregion

# region Mock-backed response models

class ItemImagesResponse(BaseModel):
    """Response for GET /categories/{name}/items/{item_id}/images."""

    item_id: str
    files: list[str] = []
    thumbnail_uri: str | None = None


class ItemVoicesResponse(BaseModel):
    """Response for GET /categories/{name}/items/{item_id}/voices."""

    item_id: str
    name: LocalizedString
    voices: list[VoiceResponse] = []

# endregion