"""Content API routes (public v1).

Exposes category and item CRUD endpoints. Delegates to ContentService
resolved from the ServiceRegistry.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from src.api.dependencies import get_current_user, get_registry
from src.api.service_resolver import get_typed_service
from src.core.config import settings
from src.services import content
from src.services.content.models import (
    ArchiveUnavailableError,
    CategoryConflictError,
    CategoryNotFoundError,
    ContentError,
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
    VoiceResponse,
)
from src.services.content.service import ContentService
from src.services.service_registry import ServiceRegistry

TAG_CATEGORIES = "Categories"
TAG_CATEGORIES_IMAGES = "Categories Images"
TAG_CATEGORIES_VOICES = "Categories Voices"
TAG_ITEMS = "Items"
TAG_ITEMS_IMAGES = "Items Images"
TAG_ITEMS_VOICES = "Items Voices"

CATEGORIES = "/categories"
CATEGORIES_RESTORE = "/categories/restore"
CATEGORY = "/categories/{category_name}"
CATEGORY_DETAILS = "/categories/{category_id}"
CATEGORY_IMAGES = "/categories/{category_id}/images"
CATEGORY_IMAGE = "/categories/{category_id}/images/{image_id}"
CATEGORY_VOICES = "/categories/{category_name}/voices"
CATEGORY_VOICE = "/categories/{category_name}/voices/{voice_id}"
CATEGORY_ITEMS = "/categories/{category_name}/items"
CATEGORY_ITEM = "/categories/{category_name}/items/{item_id}"
CATEGORY_ITEM_IMAGES = "/categories/{category_name}/items/{item_id}/images"
CATEGORY_ITEM_IMAGE = "/categories/{category_name}/items/{item_id}/images/{image_index}"
CATEGORY_ITEM_FILES = "/categories/{category_name}/items/{item_id}/files/{file_index}"
CATEGORY_ITEM_VOICES = "/categories/{category_name}/items/{item_id}/voices"
CATEGORY_ITEM_VOICE = "/categories/{category_name}/items/{item_id}/voices/{voice_id}"
CATEGORY_ITEM_VOICE_AUDIO = "/categories/{category_name}/items/{item_id}/voices/{voice_id}/audio"
ITEMS = "/items"

router = APIRouter(prefix=f"{settings.API_V1_STR}/content")


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
            "Restores a category from the archive directory."
            "The category must exist in the archive with a .category extension."
            "If a category with the same name already exists, it will be restored to its original state."
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


@router.put(
    CATEGORY,
    response_model=None,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES],
    summary="Update category name or description",
    description=(
            "Update the name or description of a content category. "
            "The category must exist and be active to be updated. "
            "Requires authentication."
    ),
)
def categories_update(
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Update category name or description."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: update category '{category_name}'",
    )


@router.patch(
    CATEGORY,
    response_model=RenameCategoryResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES],
    summary="Rename a content category",
    description=(
            "Renames an existing content category. "
            "The new name must be unique and follow naming conventions. "
            "Requires authentication."
    ),
)
def categories_rename(
        category_name: str,
        body: RenameCategoryBody,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> RenameCategoryResponse:
    """Rename a category."""
    try:
        result = service.rename_category(category_name, body.name.strip())
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RenameCategoryResponse(category=result.category, message=result.message)


@router.delete(
    CATEGORY,
    response_model=RemoveCategoryResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES],
    summary="Remove a content category",
    description=(
            "Delete a content category permanently. "
            "This operation cannot be undone and will remove the category and all associated content. "
            "Requires authentication."
    ),
)
def categories_remove(
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> RemoveCategoryResponse:
    """Remove a category."""
    try:
        result = service.remove_category(category_name)
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
    return ImageResponse(id=result.id, file_uri=result.file_uri)


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
    return ImageResponse(id=result.id, file_uri=result.file_uri)


@router.delete(
    CATEGORY_IMAGE,
    status_code=status.HTTP_204_NO_CONTENT,
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
) -> None:
    """Delete or reset a category image."""
    try:
        service.delete_category_image(category_id, image_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> CategoryVoicesResponse:
    """List voices for a category."""
    try:
        result = service.category_details_by_name(category_name)
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
    response_model=None,
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
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Add a voice to a category."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: add voice to category '{category_name}'",
    )


@router.put(
    CATEGORY_VOICE,
    response_model=None,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    tags=[TAG_CATEGORIES_VOICES],
    summary="Replace audio for a category voice",
    description=(
            "Upload or replace the audio file for a specific voice in a content category. "
            "This endpoint allows replacing the audio file associated with a voice. "
            "Requires authentication."
    ),
)
def categories_voices_update(
        category_name: str,
        voice_id: str,
        # file: UploadFile = File(...),
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Upload or replace audio for a category voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: upload audio for voice '{voice_id}' in category '{category_name}'",
    )


@router.delete(
    CATEGORY_VOICE,
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    tags=[TAG_CATEGORIES_VOICES],
    summary="Delete a voice from a category",
    description=(
            "Delete or reset a voice file associated with a content category. "
            "This operation removes the specified voice from the given category. "
            "Requires authentication."
    ),
)
def categories_voices_delete(
        category_name: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete or reset a category voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: delete voice '{voice_id}' in category '{category_name}'",
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
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemListResponse:
    """List all items in a category."""
    try:
        result = service.category_details_by_name(category_name)
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
        category_name: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemSummary:
    """Get a single item by ID."""
    try:
        result = service.category_details_by_name(category_name)
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
            detail=f"Item '{item_id}' not found in category '{category_name}'",
        )
    return ItemSummary(
        id=matched.id,
        name=matched.name,
        thumbnail_uri=matched.thumbnail_uri or None,
        images_count=matched.images_count,
    )


@router.put(
    CATEGORY_ITEM,
    response_model=None,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS],
    summary="Update an item in a category",
    description=(
            "Update the name of a content item within a specific category. "
            "This endpoint allows modifying the item name for a given item ID. "
            "Requires authentication."
    ),
)
def items_update(
        category_name: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Update item name."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: update item '{item_id}' in category '{category_name}'",
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
        category_name: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete an item from a category."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: delete item '{item_id}' in category '{category_name}'",
    )


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
        category_name: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemImagesResponse:
    """List image file variants for an item."""
    try:
        result = service.category_details_by_name(category_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if item_id not in [item.id for item in result.items]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_name}'",
        )
    item = next((i for i in result.items if i.id == item_id), None)
    item_name = item.name if item else LocalizedString({})
    images_result = service.item_images(category_name, item_id)
    return ItemImagesResponse(
        item_id=item_id,
        name=item_name,
        images=[ImageResponse(id=img.id, file_uri=img.file_uri) for img in images_result.images],
        thumbnail_uri=images_result.thumbnail_uri,
    )


@router.post(
    CATEGORY_ITEM_IMAGES,
    response_model=None,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_ITEMS_IMAGES],
    summary="Add an image file variant to an item",
    description=(
            "Add an additional image file variant to the specified content item. "
            "This endpoint allows uploading multiple image files (e.g., different resolutions, formats) "
            "for a single item. Requires authentication."
    ),
)
def items_images_add(
        category_name: str,
        item_id: str,
        # file: UploadFile = File(...),
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Upload an additional file variant for an image."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: add file to image '{item_id}' in category '{category_name}'",
    )


@router.put(
    CATEGORY_ITEM_IMAGE,
    response_model=None,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    tags=[TAG_ITEMS_IMAGES],
    summary="Replace a specific image file variant",
    description=(
            "Replace the image file variant at the specified index for the given item in the category. "
            "Uploads the new file and stores it in place of the existing variant at this index. "
            "Requires authentication."
    ),
)
def items_images_replace(
        category_name: str,
        item_id: str,
        image_index: int,
        # file: UploadFile = File(...),
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Replace a specific file variant."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: replace file {image_index} of image '{item_id}' "
               f"in category '{category_name}'",
    )


@router.delete(
    CATEGORY_ITEM_FILES,
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    tags=[TAG_ITEMS_IMAGES],
    summary="Delete a file variant from an item",
    description=(
            "Remove a specific file variant (e.g., image at a given index) from a content item. "
            "This endpoint allows deleting a file variant from an item in a category. "
            "At least one file variant must remain after deletion. "
            "Requires authentication."
    ),
)
def items_images_delete(
        category_name: str,
        item_id: str,
        file_index: int,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete a file variant (at least one must remain)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: delete file {file_index} of image '{item_id}' "
               f"in category '{category_name}'",
    )


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
        category_name: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemVoicesResponse:
    """List voices for an item."""
    try:
        result = service.category_details_by_name(category_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if item_id not in [item.id for item in result.items]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_name}'",
        )
    item = next((i for i in result.items if i.id == item_id), None)
    item_name = item.name if item else LocalizedString({})
    voices_result = service.item_voices(category_name, item_id)
    return ItemVoicesResponse(
        item_id=item_id,
        name=item_name,
        voices=[
            VoiceResponse(id=v.id, language=v.language, type=v.type, file_uri=v.file_uri)
            for v in voices_result.voices
        ],
    )


@router.put(
    CATEGORY_ITEM_VOICE,
    response_model=None,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    tags=[TAG_ITEMS_VOICES],
    summary="Update voice for an item",
    description=(
            "Update or replace a voice file associated with a content item. "
            "This endpoint allows modifying the voice file for a specific item in a category. "
            "Requires authentication."
    ),
)
def items_voices_update(
        category_name: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Update voice for an item."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: update voice '{voice_id}' of image '{item_id}' "
               f"in category '{category_name}'",
    )


@router.put(
    CATEGORY_ITEM_VOICE_AUDIO,
    response_model=None,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    tags=[TAG_ITEMS_VOICES],
    summary="Upload audio for an item voice",
    description=(
            "Upload or replace the audio file for a specific voice associated with a content item. "
            "This endpoint allows uploading audio files for voices linked to items in a category. "
            "Requires authentication."
    ),
)
def items_voices_upload(
        category_name: str,
        item_id: str,
        voice_id: str,
        # file: UploadFile = File(...),
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Upload or replace audio for an item voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: upload audio for voice '{voice_id}' of image '{item_id}' "
               f"in category '{category_name}'",
    )


@router.delete(
    CATEGORY_ITEM_VOICE,
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    tags=[TAG_ITEMS_VOICES],
    summary="Delete a voice from an item",
    description=(
            "Delete or reset a voice file associated with a content item. "
            "This operation removes the specified voice from the given item in a category. "
            "Requires authentication."
    ),
)
def items_voices_delete(
        category_name: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete or reset an item voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: delete voice '{voice_id}' of image '{item_id}' "
               f"in category '{category_name}'",
    )


# endregion

# region Legacy item creation

@router.post(
    ITEMS,
    response_model=CreateItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new content item",
    description=(
            "Create a new content item with an image and associated voices. "
            "This endpoint allows uploading an image file and creating a new item entry "
            "with the specified category and English word. "
            "The image must be in PNG, JPG, or JPEG format. "
            "Requires authentication."
    ),
)
def create_item_endpoint(
        service: ContentServiceDep,
        image: UploadFile = File(...),  # pylint: disable=invalid-name
        category: str = Form(...),
        item: str = Form(...),
        _user: dict = Depends(get_current_user),
) -> CreateItemResponse:
    """Create a new content item (image + voices)."""
    if not image.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is required")
    image_data = image.read()
    if not image_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is empty")
    ext = ""
    if "." in image.filename:
        ext = "." + image.filename.rsplit(".", 1)[-1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        ext = ".png"
    try:
        result = service.create_item(
            category_name=category.strip(),
            english_word=item.strip(),
            image_data=image_data,
            image_extension=ext,
        )
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CreateItemResponse(
        category=result.category,
        item=result.item,
        folder=result.folder,
        image_id=result.image_id,
    )


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

# service: ContentService = Depends(_content_service),

# endregion
