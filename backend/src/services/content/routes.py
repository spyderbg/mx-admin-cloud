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
from src.services.content.schemas import (
    ArchivedEntry,
    CategoryDetailResponse,
    CategoryImagesResponse,
    CategoryListResponse,
    CategorySummary,
    CategoryVoicesResponse,
    CreateCategoryBody,
    CreateCategoryResponse,
    CreateItemResponse,
    ItemDetailResponse,
    ItemImagesResponse,
    ItemListResponse,
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

router = APIRouter(prefix=f"{settings.API_V1_STR}/content")


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

@router.get(
    "/categories",
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
            CategorySummary(name=c.name, item_count=c.item_count)
            for c in result.categories
        ],
        archived=[
            ArchivedEntry(base_name=e.base_name, archive_folder=e.archive_folder, archived_at=e.archived_at)
            for e in result.archived
        ] or None,
    )


@router.post(
    "/categories",
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
    "/categories/restore",
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
    "/categories/{category_name}",
    response_model=CategoryDetailResponse,
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
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> CategoryDetailResponse:
    """Get category details."""
    try:
        result = service.category_details(category_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CategoryDetailResponse(
        id=result.id,
        name=result.name,
        items=result.items,
        images_ids=result.images_ids,
        images_by_lang=result.images_by_lang,
    )


@router.put(
    "/categories/{category_name}",
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
    "/categories/{category_name}",
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
    "/categories/{category_name}",
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


# ---------------------------------------------------------------------------
# Category Images
# ---------------------------------------------------------------------------

@router.get(
    "/categories/{category_name}/images",
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
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> CategoryImagesResponse:
    """List images for a category."""
    try:
        result = service.category_details(category_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    items = [
        ItemDetailResponse(
            id=f"{category_name}-{name}",
            name=name.replace("_", " ").title(),
            files=_mock_item_files(category_name, name),
            thumbnail_url=_mock_item_files(category_name, name)[0],
            voices=_mock_item_voices(category_name, name),
        )
        for name in (result.images_ids or [])
    ]
    return CategoryImagesResponse(category=category_name, items=items)


@router.post(
    "/categories/{category_name}/images",
    response_model=None,
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
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Add a new image to a category."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: add image to category '{category_name}'",
    )


@router.put(
    "/categories/{category_name}/images/{image_id}",
    response_model=None,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    tags=[TAG_CATEGORIES_IMAGES],
    summary="Rename a category image",
    description=(
            "Rename an image file associated with a category. "
            "The image must exist in the category and be accessible. "
            "Requires authentication."
    ),
)
def categories_images_update(
        category_name: str,
        image_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Rename a category image."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: update image '{image_id}' in category '{category_name}'",
    )


@router.delete(
    "/categories/{category_name}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=[TAG_CATEGORIES_IMAGES],
    summary="Delete category image",
    description=(
            "Delete or reset a category image."
            "Removes the specified image from the given category."
    )
)
def categories_images_delete(
        category_name: str,
        image_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete or reset a category image."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: delete image '{image_id}' in category '{category_name}'",
    )


# ---------------------------------------------------------------------------
# Category Voices
# ---------------------------------------------------------------------------

@router.get(
    "/categories/{category_name}/voices",
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
        service.category_details(category_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return CategoryVoicesResponse(
        category=category_name,
        voices=_mock_category_voices(category_name),
    )


@router.post(
    "/categories/{category_name}/voices",
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
    "/categories/{category_name}/voices/{voice_id}",
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
    "/categories/{category_name}/voices/{voice_id}",
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


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

@router.get(
    "/categories/{category_name}/items",
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
        result = service.category_details(category_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return ItemListResponse(items=result.images_ids or [])


@router.get(
    "/categories/{category_name}/items/{item_id}",
    response_model=ItemDetailResponse,
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
) -> ItemDetailResponse:
    """Get a single item by ID."""
    try:
        result = service.category_details(category_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if item_id not in (result.images_ids or []):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_name}'",
        )
    files = _mock_item_files(category_name, item_id)
    return ItemDetailResponse(
        id=f"{category_name}-{item_id}",
        name=item_id.replace("_", " ").title(),
        files=files,
        thumbnail_url=files[0],
        voices=_mock_item_voices(category_name, item_id),
    )


@router.put(
    "/categories/{category_name}/items/{item_id}",
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
    "/categories/{category_name}/items/{item_id}",
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


# ---------------------------------------------------------------------------
# Items Images
# ---------------------------------------------------------------------------

@router.get(
    "/categories/{category_name}/items/{item_id}/images",
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
        result = service.category_details(category_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if item_id not in (result.images_ids or []):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_name}'",
        )
    files = _mock_item_files(category_name, item_id)
    return ItemImagesResponse(item_id=item_id, files=files, thumbnail_url=files[0])


@router.post(
    "/categories/{category_name}/items/{item_id}/images",
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
    "/categories/{category_name}/items/{item_id}/images/{image_index}",
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
    "/categories/{category_name}/items/{item_id}/files/{file_index}",
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


# ---------------------------------------------------------------------------
# Items Voices
# ---------------------------------------------------------------------------

@router.get(
    "/categories/{category_name}/items/{item_id}/voices",
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
        result = service.category_details(category_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if item_id not in (result.images_ids or []):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_name}'",
        )
    return ItemVoicesResponse(item_id=item_id, voices=_mock_item_voices(category_name, item_id))


@router.put(
    "/categories/{category_name}/items/{item_id}/voices/{voice_id}",
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
    "/categories/{category_name}/items/{item_id}/voices/{voice_id}/audio",
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
    "/categories/{category_name}/items/{item_id}/voices/{voice_id}",
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


# ---------------------------------------------------------------------------
# Legacy item creation (not in frontend OpenAPI; kept for backward compat)
# ---------------------------------------------------------------------------

@router.post(
    "/items",
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


################################################################################

_MOCK_LANGS = [
    ("en", "male"),
    ("es", "female"),
    ("fr", "male"),
    ("bg", "female"),
]


def _mock_category_voices(category_name: str) -> list[VoiceResponse]:
    return [
        VoiceResponse(
            id=f"{category_name}-v-{lang}",
            name=category_name,
            language=lang,
            type=voice_type,
            audio_url=f"/content/{category_name}/{category_name}_{lang}.mp3",
        )
        for lang, voice_type in _MOCK_LANGS
    ]


def _mock_item_voices(category_name: str, item_id: str) -> list[VoiceResponse]:
    return [
        VoiceResponse(
            id=f"{item_id}-{lang}",
            name=item_id.replace("_", " "),
            language=lang,
            type=voice_type,
            audio_url=f"/content/{category_name}/images/{item_id}/{item_id}_{lang}.mp3",
        )
        for lang, voice_type in _MOCK_LANGS
    ]


def _mock_item_files(category_name: str, item_id: str) -> list[str]:
    return [f"/content/{category_name}/images/{item_id}/{item_id}_01.png"]


################################################################################


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
