"""Pydantic request/response schemas for content service routes."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from src.core.localization import LocalizedString


# region Command models

class FileAction(str, Enum):
    """Action performed on a category/item file (image or voice), echoed back in the response."""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    REGENERATE = "regenerate"


class ItemSummary(BaseModel):
    """Item entry returned inside CategoryDetailsResponse."""

    id: str
    name: LocalizedString
    thumbnail_uri: str | None = None
    images_count: int = 0


class ImageResponse(BaseModel):
    """Response for a single item image entry."""

    id: str
    file_uri: str | None = None
    action: FileAction | None = None
    # How an item image was made, from its metadata sidecar: the text to image configuration
    # for a generated image ("custom" for an uploaded one, "unknown" when nothing is recorded)
    # and its generation settings, with the prompt as "_prompt".
    provider: str = "unknown"
    parameters: dict[str, Any] = Field(default_factory=dict)


class VoiceResponse(BaseModel):
    """A single voice entry."""

    id: str
    language: str
    type: str
    file_uri: str | None = None
    action: FileAction | None = None
    provider: str = "unknown"
    parameters: dict[str, Any] = Field(default_factory=dict)


# endregion

# region Categories Request bodies

class CreateCategoryBody(BaseModel):
    """Request body for creating a new category."""

    name: str


class UpdateCategoryBody(BaseModel):
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


class UpdateCategoryResponse(BaseModel):
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

class CategoryImagesResponse(BaseModel):
    """Response for GET /categories/{name}/images."""

    category_id: str
    name: LocalizedString
    images: list[ImageResponse] = []


# endregion

# region Category Voices Response models

class CategoryVoicesResponse(BaseModel):
    """Response for GET /categories/{name}/voices."""

    category_id: str
    name: LocalizedString
    voices: list[VoiceResponse] = []


# endregion

# region Items Request bodies


class UpdateItemBody(BaseModel):
    """Request body for updating an item's localized name, and optionally its id."""

    new_item_id: str | None = None
    item_name: LocalizedString


class GenerateItemImageBody(BaseModel):
    """Request body for generating a new image variant of an item from a text prompt."""

    prompt: str
    # Text to image configuration (config file name) to generate with; it must be active.
    # Omitted: the first active configuration that succeeds.
    configuration_name: str | None = None


class GenerateItemVoiceBody(BaseModel):
    """Request body for generating a new take of an item voice."""

    language: str
    text: str
    # Text-to-speech configuration (config file name) to synthesize with, enabled or not.
    configuration_name: str
    # Voice settings layered over the configuration's own; omitted ones keep the configured value.
    speed: float | None = Field(default=None, gt=0)
    stability: float | None = Field(default=None, ge=0, le=1)
    similarity: float | None = Field(default=None, ge=0, le=1)
    # Also create optimized versions of the new take with the active voice optimizers.
    optimize: bool = False


# endregion

# region Items Response models

class CreateItemResponse(BaseModel):
    """Response for POST /content/categories/{category_id}/items."""

    category: str
    item: str
    folder: str
    image_id: str  # Legacy alias of item_id.
    item_id: str
    voices_generated: list[str]
    voice_warnings: list[str]
    image_generated: bool
    image_warnings: list[str]
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
    # Id of the image in images the main image was copied from, i.e. the one currently
    # selected as main. None when the main image matches no image in the list.
    main_image_id: str | None = None


class VoiceCapabilities(BaseModel):
    """Which voice actions the API can perform right now, so a client only offers the working ones."""

    # POST /voices can add another recording for a language that already has one.
    multiple_voices: bool = True
    # POST /voices/generate (per-request text, configuration and settings) can run: at least
    # one text-to-speech configuration exists.
    generate: bool = False
    # POST /voices/{voice_id}/optimize can run: at least one voice optimizer is active.
    optimize: bool = False
    # PUT /voices/{voice_id}/main: any recording of a language can be made its main voice.
    select: bool = True
    # GET /voices/presets exists.
    presets: bool = False
    # Audio formats accepted by the upload endpoints.
    upload_formats: list[str] = Field(default_factory=lambda: ["mp3"])


class ItemVoicesResponse(BaseModel):
    """Response for GET /categories/{name}/items/{item_id}/voices."""

    item_id: str
    name: LocalizedString
    # One voice per supported language: the one currently used for that language.
    voices: list[VoiceResponse] = []
    # Alternative recordings per language, any of which can be selected as main.
    variants: list[VoiceResponse] = []
    # Language -> id of the variant the main voice was copied from, i.e. the one currently
    # selected as main. None when the main voice matches no variant of that language.
    main_voice_ids: dict[str, str | None] = {}
    # What a client can do with the voices; only the list endpoint fills it in.
    capabilities: VoiceCapabilities | None = None


class OptimizeItemVoiceResponse(ItemVoicesResponse):
    """Response for POST /categories/{category_id}/items/{item_id}/voices/{voice_id}/optimize."""

    # Id of the original recording (a variant) the optimized versions were made from.
    source_voice_id: str
    # Ids of the variants written by this run, one per optimizer that succeeded.
    optimized_voice_ids: list[str] = []
    # One message per active optimizer that failed; the others' results are kept.
    warnings: list[str] = []


class GenerateItemVoiceResponse(ItemVoicesResponse):
    """Response for POST /categories/{category_id}/items/{item_id}/voices/generate."""

    # Id of the new take.
    voice_id: str
    # Ids of the optimized versions made from the new take, when optimization was requested.
    optimized_voice_ids: list[str] = []
    # Optimization problems; the new take is kept regardless.
    warnings: list[str] = []

# endregion
