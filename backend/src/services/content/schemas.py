"""Pydantic request/response schemas for content service routes."""

from __future__ import annotations

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CreateCategoryBody(BaseModel):
    """Request body for creating a new category."""

    name: str


class RenameCategoryBody(BaseModel):
    """Request body for renaming a category."""

    name: str


class RestoreCategoryBody(BaseModel):
    """Request body for restoring a category."""

    name: str


# ---------------------------------------------------------------------------
# Archived entry sub-model
# ---------------------------------------------------------------------------

class ArchivedEntry(BaseModel):
    """Single archived category entry."""

    base_name: str
    archive_folder: str
    archived_at: int


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class CategoryListResponse(BaseModel):
    """Response for GET /content/categories."""

    categories: list[str]
    archived: list[ArchivedEntry] | None = None
    error: str | None = None


class CategoryDetailResponse(BaseModel):
    """Response for GET /content/categories/{name}.

    Language-specific CSV word lists are dynamic keys (images_en, images_fr, ...)
    so extra fields are allowed.
    """

    name: str
    objects: list[str] = []
    images_names: list[str] | None = None

    model_config = {"extra": "allow"}


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

    May be an archive result or a permanent delete result, so both
    ``archived`` and ``removed`` are optional.
    """

    archived: bool | None = None
    removed: bool | None = None
    name: str
    archive_folder: str | None = None
    destination: str | None = None
    message: str
