"""Content API routes (public v1).

Exposes category and item CRUD endpoints. Delegates to ContentService
resolved from the ServiceRegistry.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from src.api.dependencies import get_current_user, get_registry
from src.api.service_resolver import get_typed_service
from src.core.config import settings
from src.services import content, text_to_speech
from src.services.content.models import (
    ArchiveUnavailableError,
    CategoryConflictError,
    CategoryNotFoundError,
    ContentError,
    CreateItem,
    InvalidCategoryNameError,
)
from src.core.localization import LocalizedString
from src.services.content.schemas import (
    ArchivedEntry,
    CategoryDetailsResponse,
    CategoryImagesResponse,
    CategoryListResponse,
    CategorySummary,
    CategoryVoicesResponse,
    CreateCategoryBody,
    CreateCategoryResponse,
    CreateItemResponse,
    DeleteArchivedCategoryResponse,
    ImageResponse,
    ItemImagesResponse,
    ItemListResponse,
    ItemSummary,
    ItemVoicesResponse,
    RemoveCategoryResponse,
    RenameCategoryBody,
    RenameCategoryResponse,
    RestoreCategoryBody,
    RestoreCategoryResponse,
    UpdateItemBody,
    VoiceResponse,
)
from src.services.content.service import ContentService
from src.services.service_registry import ServiceRegistry
from src.services.text_to_speech.service import TextToSpeechService

TAG_CATEGORIES = "Categories"
TAG_CATEGORIES_IMAGES = "Categories Images"
TAG_CATEGORIES_VOICES = "Categories Voices"
TAG_ITEMS = "Items"
TAG_ITEMS_IMAGES = "Items Images"
TAG_ITEMS_VOICES = "Items Voices"

CATEGORIES = "/categories"
CATEGORIES_RESTORE = "/categories/restore"
CATEGORIES_ARCHIVE = "/categories/archive/{archive_name}"
CATEGORY_DETAILS = "/categories/{category_id}"
CATEGORY_IMAGES = "/categories/{category_id}/images"
CATEGORY_IMAGE = "/categories/{category_id}/images/{image_id}"
CATEGORY_ICON = "/categories/{category_id}/icon"
CATEGORY_VOICES = "/categories/{category_id}/voices"
CATEGORY_VOICE = "/categories/{category_id}/voices/{voice_id}"
CATEGORY_ITEMS = "/categories/{category_id}/items"
CATEGORY_ITEM = "/categories/{category_id}/items/{item_id}"
CATEGORY_ITEM_IMAGES = "/categories/{category_id}/items/{item_id}/images"
CATEGORY_ITEM_IMAGE = "/categories/{category_id}/items/{item_id}/images/{image_id}"
CATEGORY_ITEM_VOICES = "/categories/{category_id}/items/{item_id}/voices"
CATEGORY_ITEM_VOICE = "/categories/{category_id}/items/{item_id}/voices/{voice_id}"

ITEMS = "/items"

router = APIRouter(prefix=f"{settings.API_V1_STR}/content")

NO_VOICE_GENERATORS_WARNING = "No active voice generators configured; voices were not generated."


def _read_image_upload(image: UploadFile | None, *, required: bool) -> tuple[bytes | None, str]:
    """Return (bytes, extension) for an uploaded image, or (None, ".png") when none was sent.

    An absent file may arrive as None or as an UploadFile with an empty filename, depending
    on the client. Raises 400 when the file is required but missing, or when it is empty.
    """
    if image is None or not image.filename:
        if required:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is required")
        return None, ".png"
    image_data = image.file.read()
    if not image_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is empty")
    ext = ""
    if "." in image.filename:
        ext = "." + image.filename.rsplit(".", 1)[-1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        ext = ".png"
    return image_data, ext


def _create_item_response(result: CreateItem, voice_generation_available: bool) -> CreateItemResponse:
    """Build the create-item response, reporting skipped voice generation as a warning."""
    warnings = list(result.voice_warnings)
    if not voice_generation_available:
        warnings.append(NO_VOICE_GENERATORS_WARNING)
    return CreateItemResponse(
        category=result.category,
        item=result.item,
        folder=result.folder,
        image_id=result.image_id,
        item_id=result.item_id,
        voices_generated=list(result.voices_generated),
        voice_warnings=warnings,
        message=result.message,
    )


# region Categories

@router.get(
    CATEGORIES,
    response_model=CategoryListResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES],
    summary="List all content categories",
    description=(
            "Retrieve all content categories. "
            "By default returns only active categories. "
            "Use 'include_archived=true' to append archived entries to the response, "
            "or 'archived_only=true' to return only archived categories. "
            "Requires authentication."
    ),
)
def categories_list(
        service: ContentServiceDep,
        include_archived: bool = False,
        archived_only: bool = False,
        _user: dict = Depends(get_current_user),
) -> CategoryListResponse:
    """List categories."""
    try:
        result = service.list_categories(
            include_archived=include_archived,
            archived_only=archived_only,
        )
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return CategoryListResponse(
        categories=[
            CategorySummary(
                id=c.id,
                name=c.name,
                items_count=c.items_count,
                thumbnail_uri=c.thumbnail_uri or None,
            )
            for c in result.categories
        ],
        archived=[
                     ArchivedEntry(base_name=e.base_name, archive_folder=e.archive_folder, archived_at=e.archived_at)
                     for e in result.archived
                 ] or None,
    )


@router.post(
    CATEGORIES,
    response_model=CreateCategoryResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_CATEGORIES],
    summary="Create a new content category",
    description=(
            "Create a new content category. "
            "The category name must be unique and follow naming conventions. "
            "Active categories are returned by default; use 'include_archived=true' to include archived categories "
            "in the response or 'archived_only=true' to return only archived categories. "
            "Requires authentication."
    ),
)
def categories_create(
        body: CreateCategoryBody,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> CreateCategoryResponse:
    """Create a new category."""
    try:
        result = service.create_category(body.name.strip())
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CreateCategoryResponse(category=result.category, message=result.message)


@router.post(
    CATEGORIES_RESTORE,
    response_model=RestoreCategoryResponse,
    tags=[TAG_CATEGORIES],
    summary="Restore a category from archive",
    description=(
            "Restore an archived category back into the content directory. "
            "Accepts either a base folder name ('foods'), which restores the most recent archive, "
            "or a full archive folder name ('foods_1738456789') to restore a specific snapshot. "
            "Fails with 409 when a live category already occupies that folder. "
            "Requires authentication."
    ),
)
def categories_restore(
        body: RestoreCategoryBody,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> RestoreCategoryResponse:
    """Restore a category from archive."""
    try:
        result = service.restore_category(body.name.strip())
    except ArchiveUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RestoreCategoryResponse(
        restored=result.restored,
        name=result.name,
        from_archive=result.from_archive,
        message=result.message,
    )


@router.delete(
    CATEGORIES_ARCHIVE,
    response_model=DeleteArchivedCategoryResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES],
    summary="Delete a category from archive",
    description=(
            "Permanently delete an archived category snapshot. "
            "Accepts either a base folder name ('foods') or a full archive folder name "
            "('foods_1738456789') to delete a specific snapshot. "
            "Requires authentication."
    ),
)
def categories_archive_delete(
        archive_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> DeleteArchivedCategoryResponse:
    """Permanently delete an archived category snapshot."""
    try:
        result = service.delete_archived_category(archive_name.strip())
    except ArchiveUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DeleteArchivedCategoryResponse(
        deleted=result.deleted,
        name=result.name,
        archive_folder=result.archive_folder,
        message=result.message,
    )


@router.get(
    CATEGORY_DETAILS,
    response_model=CategoryDetailsResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES],
    summary="Get details of a specific content category",
    description=(
            "Retrieve detailed information about a content category. "
            "Returns the category name, description, and associated metadata. "
            "Requires authentication."
    ),
)
def categories_details(
        category_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> CategoryDetailsResponse:
    """Get category details."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CategoryDetailsResponse(
        id=result.id,
        name=result.name,
        description=result.description,
        items=[
            ItemSummary(id=item.id, name=item.name, thumbnail_uri=item.thumbnail_uri, images_count=item.images_count)
            for item in result.items
        ],
    )


@router.patch(
    CATEGORY_DETAILS,
    response_model=RenameCategoryResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES],
    summary="Update a content category's name and description",
    description=(
            "Update the localized name and description of a content category, identified by category_id. "
            "The category must exist to be updated. "
            "Requires authentication."
    ),
)
def categories_rename(
        category_id: str,
        body: RenameCategoryBody,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> RenameCategoryResponse:
    """Update a category's localized name and description, and optionally its id."""
    try:
        result = service.update_category_details(category_id, body.category_name, body.description)
        new_category_id = category_id
        category = result.category
        message = result.message
        if body.new_category_id:
            requested_id = body.new_category_id
            if requested_id != category_id:
                rename_result = service.rename_category(category_id, requested_id)
                category = rename_result.category
                message = f"{message} {rename_result.message}"
            new_category_id = requested_id
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RenameCategoryResponse(
        category=category,
        message=message,
        category_id=new_category_id,
        old_category_id=category_id,
    )


@router.delete(
    CATEGORY_DETAILS,
    response_model=RemoveCategoryResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES],
    summary="Remove a content category",
    description=(
            "Remove a content category and all of its items, images and audio. "
            "When ARCHIVE_DIR is configured the category is moved to the archive and can be restored "
            "via 'POST /categories/restore'; otherwise it is deleted permanently. "
            "The response 'archived' and 'removed' flags report which of the two happened. "
            "Accepts either a category id ('C_Foods') or a folder name ('foods'). "
            "Requires authentication."
    ),
)
def categories_remove(
        category_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> RemoveCategoryResponse:
    """Remove a category."""
    try:
        result = service.remove_category_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RemoveCategoryResponse(
        archived=result.archived,
        removed=result.removed,
        name=result.name,
        archive_folder=result.archive_folder,
        destination=result.destination,
        message=result.message,
    )


# endregion

# region Category Images

@router.get(
    CATEGORY_IMAGES,
    response_model=CategoryImagesResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES_IMAGES],
    summary="List all images in a category",
    description=(
            "Retrieve a list of all image files associated with a content category. "
            "Returns the image filenames and their metadata. "
            "Requires authentication."
    ),
)
def categories_images_list(
        category_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> CategoryImagesResponse:
    """List images for a category."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return CategoryImagesResponse(
        category_id=result.id,
        name=result.name,
        images=[
            ImageResponse(id=img.id, file_uri=img.file_uri)
            for img in result.images
        ],
    )


@router.post(
    CATEGORY_IMAGES,
    response_model=ImageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_CATEGORIES_IMAGES],
    summary="Add an image to a category",
    description=(
            "Add a new image file to the specified content category. "
            "This endpoint allows uploading image files and associating them with a category. "
            "The image file will be stored in the category's image directory. "
            "Requires authentication."
    ),
)
def categories_images_add(
        category_id: str,
        service: ContentServiceDep,
        image: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> ImageResponse:
    """Add a new image to a category."""
    if not image.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is required")
    image_data = image.file.read()
    if not image_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is empty")
    ext = ""
    if "." in image.filename:
        ext = "." + image.filename.rsplit(".", 1)[-1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        ext = ".png"
    try:
        result = service.add_category_image(category_id, image_data, ext)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ImageResponse(id=result.id, file_uri=result.file_uri, action="add")


@router.put(
    CATEGORY_IMAGE,
    response_model=ImageResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES_IMAGES],
    summary="Replace a category image file",
    description=(
            "Upload a new file to override an existing image associated with a category. "
            "The image must already exist in the category; its file contents (and extension, "
            "if the uploaded file differs) are replaced. "
            "Requires authentication."
    ),
)
def categories_images_update(
        category_id: str,
        image_id: str,
        service: ContentServiceDep,
        image: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> ImageResponse:
    """Override an existing category image with an uploaded file."""
    if not image.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is required")
    image_data = image.file.read()
    if not image_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is empty")
    ext = ""
    if "." in image.filename:
        ext = "." + image.filename.rsplit(".", 1)[-1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        ext = ".png"
    try:
        result = service.update_category_image(category_id, image_id, image_data, ext)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ImageResponse(id=result.id, file_uri=result.file_uri, action="update")


@router.delete(
    CATEGORY_IMAGE,
    response_model=ImageResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES_IMAGES],
    summary="Delete category image",
    description=(
            "Delete or reset a category image."
            "Removes the specified image from the given category."
    )
)
def categories_images_delete(
        category_id: str,
        image_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ImageResponse:
    """Delete or reset a category image."""
    try:
        result = service.delete_category_image(category_id, image_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ImageResponse(id=result.id, file_uri=result.file_uri, action="delete")


def _resolve_category_icon_image_id(service: ContentService, category_id: str) -> str:
    """Return the image_id of the category's icon file (stored as {category_name}/{category_name}.<ext>).

    Unlike regular category images (added via ``categories_images_add``, always named
    ``{category_name}_NN.<ext>``), the icon file has no numeric suffix, so it's the one
    entry in the category's images whose id doesn't end in ``_NN``.
    """
    result = service.category_details_by_id(category_id)
    icon = next((img for img in result.images if not re.search(r"_\d+$", img.id)), None)
    if icon is None:
        raise CategoryNotFoundError(f"Category '{category_id}' has no icon image.")
    return icon.id


@router.post(
    CATEGORY_ICON,
    response_model=ImageResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES_IMAGES],
    summary="Create or replace a category's icon/thumbnail image",
    description=(
            "Upload a file to set the category's icon image, creating it if it doesn't yet exist "
            "or overriding it otherwise. "
            "The icon is stored as '{category_name}/{category_name}.<ext>' and is used for "
            "both the category thumbnail and its app icon. "
            "Requires authentication."
    ),
)
def categories_icon_update(
        category_id: str,
        service: ContentServiceDep,
        image: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> ImageResponse:
    """Create or replace the category's icon/thumbnail image with an uploaded file."""
    if not image.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is required")
    image_data = image.file.read()
    if not image_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is empty")
    ext = ""
    if "." in image.filename:
        ext = "." + image.filename.rsplit(".", 1)[-1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        ext = ".png"
    try:
        result = service.set_category_icon(category_id, image_data, ext)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ImageResponse(id=result.id, file_uri=result.file_uri, action="update")


@router.delete(
    CATEGORY_ICON,
    response_model=ImageResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES_IMAGES],
    summary="Delete a category's icon/thumbnail image",
    description=(
            "Delete the category's icon image. "
            "The icon is stored as '{category_name}/{category_name}.<ext>' and is used for "
            "both the category thumbnail and its app icon. "
            "Requires authentication."
    ),
)
def categories_icon_delete(
        category_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ImageResponse:
    """Delete the category's icon/thumbnail image."""
    try:
        image_id = _resolve_category_icon_image_id(service, category_id)
        result = service.delete_category_image(category_id, image_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ImageResponse(id=result.id, file_uri=result.file_uri, action="delete")


# endregion

# region Category Voices

@router.get(
    CATEGORY_VOICES,
    response_model=CategoryVoicesResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES_VOICES],
    summary="List voices for a category",
    description=(
            "Retrieve a list of all voice files associated with a content category. "
            "Returns the voice filenames and their metadata. "
            "Requires authentication."
    ),
)
def categories_voices_list(
        category_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> CategoryVoicesResponse:
    """List voices for a category."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return CategoryVoicesResponse(
        category_id=result.id,
        name=result.name,
        voices=[
            VoiceResponse(
                id=v.id,
                language=v.language,
                type=v.type,
                file_uri=v.file_uri,
            )
            for v in result.voices
        ],
    )


@router.post(
    CATEGORY_VOICES,
    response_model=VoiceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_CATEGORIES_VOICES],
    summary="Add a voice to a category",
    description=(
            "Add a new voice file to the specified content category. "
            "This endpoint allows uploading voice files and associating them with a category. "
            "The voice file will be stored in the category's voice directory. "
            "Requires authentication."
    ),
)
def categories_voices_add(
        category_id: str,
        service: ContentServiceDep,
        language: str = Form(...),
        audio: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> VoiceResponse:
    """Add a voice to a category."""
    if not audio.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is required")
    audio_data = audio.file.read()
    if not audio_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is empty")
    try:
        result = service.add_category_voice(category_id, language.strip(), audio_data)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return VoiceResponse(
        id=result.id, language=result.language, type=result.type, file_uri=result.file_uri, action="add",
    )


@router.put(
    CATEGORY_VOICE,
    response_model=VoiceResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES_VOICES],
    summary="Replace audio for a category voice",
    description=(
            "Upload or replace the audio file for a specific voice in a content category. "
            "This endpoint allows replacing the audio file associated with a voice. "
            "Requires authentication."
    ),
)
def categories_voices_update(
        category_id: str,
        voice_id: str,
        service: ContentServiceDep,
        audio: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> VoiceResponse:
    """Upload or replace audio for a category voice."""
    if not audio.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is required")
    audio_data = audio.file.read()
    if not audio_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is empty")
    try:
        result = service.update_category_voice(category_id, voice_id, audio_data)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return VoiceResponse(
        id=result.id, language=result.language, type=result.type, file_uri=result.file_uri, action="update",
    )


@router.delete(
    CATEGORY_VOICE,
    response_model=VoiceResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES_VOICES],
    summary="Delete a voice from a category",
    description=(
            "Delete or reset a voice file associated with a content category. "
            "This operation removes the specified voice from the given category. "
            "Requires authentication."
    ),
)
def categories_voices_delete(
        category_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> VoiceResponse:
    """Delete or reset a category voice."""
    try:
        result = service.delete_category_voice(category_id, voice_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return VoiceResponse(
        id=result.id, language=result.language, type=result.type, file_uri=result.file_uri, action="delete",
    )


# endregion

# region Items

@router.get(
    CATEGORY_ITEMS,
    response_model=ItemListResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS],
    summary="List all items in a category",
    description=(
            "Retrieve a list of all content items within a specific category. "
            "Returns the item names and their metadata. "
            "Requires authentication."
    ),
)
def items_list(
        category_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemListResponse:
    """List all items in a category."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return ItemListResponse(
        items=[
            ItemSummary(id=item.id, name=item.name, thumbnail_uri=item.thumbnail_uri, images_count=item.images_count)
            for item in result.items
        ]
    )


@router.post(
    CATEGORY_ITEMS,
    response_model=CreateItemResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_ITEMS],
    summary="Create a new item in a category",
    description=(
            "Create a content item from its English name. "
            "The name is translated into every supported language first; if translation fails "
            "the item is not created. "
            "The image is optional. "
            "Voices are generated for every language when an active text to speech provider "
            "exists; voice problems never block creation and are reported in 'voice_warnings'. "
            "Requires authentication."
    ),
)
def items_create(
        category_id: str,
        service: ContentServiceDep,
        voice_generation_available: VoiceGenerationAvailableDep,
        item: str = Form(...),
        image: UploadFile | None = File(None),
        _user: dict = Depends(get_current_user),
) -> CreateItemResponse:
    """Create an item with translations, an optional image, and best-effort voices."""
    word = item.strip()
    if not word:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Item name is required")
    image_data, ext = _read_image_upload(image, required=False)
    try:
        result = service.create_item_by_id(
            category_id,
            word,
            image_data,
            ext,
            generate_voices=voice_generation_available,
        )
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _create_item_response(result, voice_generation_available)


@router.get(
    CATEGORY_ITEM,
    response_model=ItemSummary,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS],
    summary="Get a single item by ID",
    description=(
            "Retrieve detailed information about a single content item. "
            "Returns the item name, associated images, and metadata. "
            "Requires authentication."
    ),
)
def items_get(
        category_id: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemSummary:
    """Get a single item by ID."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    matched = next((item for item in result.items if item.id == item_id), None)
    if matched is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_id}'",
        )
    return ItemSummary(
        id=matched.id,
        name=matched.name,
        thumbnail_uri=matched.thumbnail_uri or None,
        images_count=matched.images_count,
    )


@router.put(
    CATEGORY_ITEM,
    response_model=ItemSummary,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS],
    summary="Update an item in a category",
    description=(
            "Update the localized name of a content item within a specific category. "
            "Requires authentication."
    ),
)
def items_update(
        category_id: str,
        item_id: str,
        body: UpdateItemBody,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemSummary:
    """Update an item's localized name."""
    try:
        result = service.update_item(category_id, item_id, body.item_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ItemSummary(
        id=result.id,
        name=result.name,
        thumbnail_uri=result.thumbnail_uri or None,
        images_count=result.images_count,
    )


@router.delete(
    CATEGORY_ITEM,
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    tags=[TAG_ITEMS],
    summary="Delete an item from a category",
    description=(
            "Permanently delete a content item from its category. "
            "This operation cannot be undone and will remove the item and all associated metadata. "
            "Requires authentication."
    ),
)
def items_delete(
        category_id: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete an item from a category."""
    try:
        service.remove_item(category_id, item_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# endregion

# region Items Images

@router.get(
    CATEGORY_ITEM_IMAGES,
    response_model=ItemImagesResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_IMAGES],
    summary="List image variants for an item",
    description=(
            "Retrieve a list of all image file variants associated with a content item. "
            "Returns the image filenames, URLs, and metadata for each variant. "
            "Requires authentication."
    ),
)
def items_images_list(
        category_id: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemImagesResponse:
    """List image file variants for an item."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if item_id not in [item.id for item in result.items]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_id}'",
        )
    item = next((i for i in result.items if i.id == item_id), None)
    item_name = item.name if item else LocalizedString({})
    images_result = service.item_images(category_id, item_id)
    return ItemImagesResponse(
        item_id=item_id,
        name=item_name,
        images=[ImageResponse(id=img.id, file_uri=img.file_uri) for img in images_result.images],
        thumbnail_uri=images_result.thumbnail_uri,
    )


@router.post(
    CATEGORY_ITEM_IMAGES,
    response_model=ImageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_ITEMS_IMAGES],
    summary="Add an image file variant to an item",
    description=(
            "Add an additional image file variant to the specified content item, stored at the next "
            "available index. This endpoint allows uploading multiple image files (e.g., different "
            "resolutions, formats) for a single item. Requires authentication."
    ),
)
def items_images_add(
        category_id: str,
        item_id: str,
        service: ContentServiceDep,
        image: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> ImageResponse:
    """Add a new image file variant to an item at the next available index."""
    if not image.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is required")
    image_data = image.file.read()
    if not image_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is empty")
    ext = ""
    if "." in image.filename:
        ext = "." + image.filename.rsplit(".", 1)[-1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        ext = ".png"
    try:
        result = service.add_item_image(category_id, item_id, image_data, ext)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ImageResponse(id=result.id, file_uri=result.file_uri, action="add")


@router.put(
    CATEGORY_ITEM_IMAGE,
    response_model=ImageResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_IMAGES],
    summary="Replace a specific image file variant",
    description=(
            "Replace the image file variant with the given image_id for the given item in the category. "
            "Uploads the new file and stores it in place of the existing variant. "
            "Requires authentication."
    ),
)
def items_images_update(
        category_id: str,
        item_id: str,
        image_id: str,
        service: ContentServiceDep,
        image: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> ImageResponse:
    """Replace a specific file variant."""
    if not image.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is required")
    image_data = image.file.read()
    if not image_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is empty")
    ext = ""
    if "." in image.filename:
        ext = "." + image.filename.rsplit(".", 1)[-1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        ext = ".png"
    try:
        result = service.update_item_image(category_id, item_id, image_id, image_data, ext)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ImageResponse(id=result.id, file_uri=result.file_uri, action="update")


@router.delete(
    CATEGORY_ITEM_IMAGE,
    response_model=ImageResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_IMAGES],
    summary="Delete a file variant from an item",
    description=(
            "Remove a specific image file variant, identified by image_id, from a content item. "
            "At least one file variant must remain after deletion. "
            "Requires authentication."
    ),
)
def items_images_delete(
        category_id: str,
        item_id: str,
        image_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ImageResponse:
    """Delete a file variant (at least one must remain)."""
    try:
        result = service.delete_item_image(category_id, item_id, image_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ImageResponse(id=result.id, file_uri=result.file_uri, action="delete")


# endregion

# region Items Voices

@router.get(
    CATEGORY_ITEM_VOICES,
    response_model=ItemVoicesResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_VOICES],
    summary="List voices for an item",
    description=(
            "Retrieve a list of all voice files associated with a content item. "
            "Returns the voice filenames and their metadata. "
            "Requires authentication."
    ),
)
def items_voices_list(
        category_id: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemVoicesResponse:
    """List voices for an item."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if item_id not in [item.id for item in result.items]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_id}'",
        )
    item = next((i for i in result.items if i.id == item_id), None)
    item_name = item.name if item else LocalizedString({})
    voices_result = service.item_voices(category_id, item_id)
    return ItemVoicesResponse(
        item_id=item_id,
        name=item_name,
        voices=[
            VoiceResponse(id=v.id, language=v.language, type=v.type, file_uri=v.file_uri)
            for v in voices_result.voices
        ],
    )


@router.post(
    CATEGORY_ITEM_VOICES,
    response_model=VoiceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_ITEMS_VOICES],
    summary="Add a voice to an item",
    description=(
            "Add a new voice file to the specified content item for the given language. "
            "Fails with 409 if the item already has a voice for that language; use the update endpoint "
            "to replace it. Requires authentication."
    ),
)
def items_voices_add(
        category_id: str,
        item_id: str,
        service: ContentServiceDep,
        language: str = Form(...),
        audio: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> VoiceResponse:
    """Add a voice to an item for the given language."""
    if not audio.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is required")
    audio_data = audio.file.read()
    if not audio_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is empty")
    try:
        result = service.add_item_voice(category_id, item_id, language.strip(), audio_data)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return VoiceResponse(
        id=result.id, language=result.language, type=result.type, file_uri=result.file_uri, action="add",
    )


@router.put(
    CATEGORY_ITEM_VOICE,
    response_model=VoiceResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_VOICES],
    summary="Update voice for an item",
    description=(
            "Replace the audio file for an item voice that already has one. "
            "Fails with 404 if the voice has no audio file yet; use the add endpoint to create it. "
            "Requires authentication."
    ),
)
def items_voices_update(
        category_id: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        audio: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> VoiceResponse:
    """Replace the audio file for an existing item voice."""
    if not audio.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is required")
    audio_data = audio.file.read()
    if not audio_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is empty")
    try:
        result = service.update_item_voice(category_id, item_id, voice_id, audio_data)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return VoiceResponse(
        id=result.id, language=result.language, type=result.type, file_uri=result.file_uri, action="update",
    )


@router.delete(
    CATEGORY_ITEM_VOICE,
    response_model=VoiceResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_VOICES],
    summary="Delete a voice from an item",
    description=(
            "Delete or reset a voice file associated with a content item. "
            "This operation removes the specified voice from the given item in a category. "
            "Requires authentication."
    ),
)
def items_voices_delete(
        category_id: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> VoiceResponse:
    """Delete or reset an item voice."""
    try:
        result = service.delete_item_voice(category_id, item_id, voice_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return VoiceResponse(
        id=result.id, language=result.language, type=result.type, file_uri=result.file_uri, action="delete",
    )


# endregion

# region Legacy item creation

@router.post(
    ITEMS,
    response_model=CreateItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new content item",
    description=(
            "Create a new content item with an image and associated voices, identified by "
            "category folder name. Prefer 'POST /categories/{category_id}/items', which makes "
            "the image optional. "
            "The image must be in PNG, JPG, or JPEG format. "
            "Voices are generated when an active text to speech provider exists; voice problems "
            "are reported in 'voice_warnings' and never block creation. "
            "Requires authentication."
    ),
)
def create_item_endpoint(
        service: ContentServiceDep,
        voice_generation_available: VoiceGenerationAvailableDep,
        image: UploadFile = File(...),  # pylint: disable=invalid-name
        category: str = Form(...),
        item: str = Form(...),
        _user: dict = Depends(get_current_user),
) -> CreateItemResponse:
    """Create a new content item (image + voices)."""
    image_data, ext = _read_image_upload(image, required=True)
    try:
        result = service.create_item(
            category_name=category.strip(),
            english_word=item.strip(),
            image_data=image_data,
            image_extension=ext,
            generate_voices=voice_generation_available,
        )
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _create_item_response(result, voice_generation_available)


# endregion

# region Dependencies


def get_content_service(
        registry: ServiceRegistry = Depends(get_registry),
) -> ContentService:
    return get_typed_service(
        registry=registry,
        service_name=content.SERVICE_NAME,
        expected_type=ContentService,
    )


ContentServiceDep = Annotated[ContentService, Depends(get_content_service)]


def get_voice_generation_available(
        registry: ServiceRegistry = Depends(get_registry),
) -> bool:
    """Return whether at least one text to speech provider is active.

    Item creation generates voices only when this is true. A missing or unconfigured
    text to speech service simply means no voices, never a failed request.
    """
    try:
        service = get_typed_service(
            registry=registry,
            service_name=text_to_speech.SERVICE_NAME,
            expected_type=TextToSpeechService,
        )
    except HTTPException:
        return False
    return bool(service.get_active_providers())


VoiceGenerationAvailableDep = Annotated[bool, Depends(get_voice_generation_available)]

# service: ContentService = Depends(_content_service),

# endregion
