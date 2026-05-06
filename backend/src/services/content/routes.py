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
from src.services.content.schemas import (
    CategoryDetailResponse,
    CategoryListResponse,
    CreateCategoryBody,
    CreateCategoryResponse,
    CreateItemResponse,
    ItemListResponse,
    RemoveCategoryResponse,
    RenameCategoryBody,
    RenameCategoryResponse,
    RestoreCategoryBody,
    RestoreCategoryResponse,
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
    result = service.list_categories(
        include_archived=include_archived,
        archived_only=archived_only,
    )
    if "error" in result and "categories" not in result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error"),
        )
    return CategoryListResponse(**result)


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
    result = service.create_category(body.name.strip())
    if "error" in result:
        if result.get("error", "").startswith("Category already exists"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["error"])
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return CreateCategoryResponse(**result)


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
    result = service.restore_category(body.name.strip())
    if "error" in result:
        error_msg = result.get("error", "")
        if error_msg == "Archive directory does not exist":
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error_msg)
        if "No archive found" in error_msg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_msg)
        if "already exists" in error_msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error_msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)
    return RestoreCategoryResponse(**result)


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
    result = service.category_details(category_name)
    if "error" in result and result.get("error") == "Category not found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
    if "error" in result and result.get("error") == "Invalid category name":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return CategoryDetailResponse(**result)


@router.put(
    "/categories/{category_name}",
    tags=[TAG_CATEGORIES]
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
    tags=[TAG_CATEGORIES]
)
def categories_rename(
        category_name: str,
        body: RenameCategoryBody,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> RenameCategoryResponse:
    """Rename a category."""
    result = service.rename_category(category_name, body.name.strip())
    if "error" in result:
        error_msg = result.get("error", "")
        if error_msg == "Category not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_msg)
        if "already exists" in error_msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error_msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)
    return RenameCategoryResponse(**result)


@router.delete(
    "/categories/{category_name}",
    response_model=RemoveCategoryResponse,
    tags=[TAG_CATEGORIES]
)
def categories_remove(
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> RemoveCategoryResponse:
    """Remove a category."""
    result = service.remove_category(category_name)
    if "error" in result:
        error_msg = result.get("error", "")
        if error_msg == "Category not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)
    return RemoveCategoryResponse(**result)


# ---------------------------------------------------------------------------
# Category Images
# ---------------------------------------------------------------------------

@router.get(
    "/categories/{category_name}/images",
    tags=[TAG_CATEGORIES_IMAGES]
)
def categories_images_list(
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """List images for a category."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: list images for category '{category_name}'",
    )


@router.post(
    "/categories/{category_name}/images",
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_CATEGORIES_IMAGES],
)
def categories_images_add(
        category_name: str,
        # file: UploadFile = File(...),
        # name: str = Form(...),
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
    tags=[TAG_CATEGORIES_IMAGES]
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
    tags=[TAG_CATEGORIES_IMAGES]
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
    tags=[TAG_CATEGORIES_VOICES]
)
def categories_voices_list(
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """List voices for a category."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: list voices for category '{category_name}'",
    )


@router.post(
    "/categories/{category_name}/voices",
    tags=[TAG_CATEGORIES_VOICES]
)
def categories_voices_add(
        category_name: str,
        # file: UploadFile = File(...),
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Rename a category voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: add voice to category '{category_name}'",
    )


@router.put(
    "/categories/{category_name}/voices/{voice_id}",
    tags=[TAG_CATEGORIES_VOICES]
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
    status_code=status.HTTP_204_NO_CONTENT,
    tags=[TAG_CATEGORIES_VOICES]
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
    tags=[TAG_ITEMS]
)
def items_list(
        category_name: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemListResponse:
    """List all items in a category."""
    result = service.category_details(category_name)
    if "error" in result:
        error_msg = result.get("error", "")
        if error_msg == "Category not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_msg)
        if error_msg == "Invalid category name":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_msg)

    items = result.get("images_names", [])
    return ItemListResponse(items=items, category=category_name)


@router.get(
    "/categories/{category_name}/items/{item_id}",
    tags=[TAG_ITEMS]
)
def items_get(
        category_name: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Get a single item by ID."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: get item '{item_id}' in category '{category_name}'",
    )


@router.put(
    "/categories/{category_name}/items/{item_id}",
    tags=[TAG_ITEMS]
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
    status_code=status.HTTP_204_NO_CONTENT,
    tags=[TAG_ITEMS]
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
    tags=[TAG_ITEMS_IMAGES]
)
def items_images_list(
        category_name: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """List image file variants for an item."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: list images for item '{item_id}' in category '{category_name}'",
    )


@router.post(
    "/categories/{category_name}/items/{item_id}/images",
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_ITEMS_IMAGES]
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
    tags=[TAG_ITEMS_IMAGES]
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
    status_code=status.HTTP_204_NO_CONTENT,
    tags=[TAG_ITEMS_IMAGES]
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
    tags=[TAG_ITEMS_VOICES]
)
def items_voices_list(
        category_name: str,
        item_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """List voices for an image."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: list voices for image '{item_id}' "
               f"in category '{category_name}'",
    )


@router.put(
    "/categories/{category_name}/items/{item_id}/voices/{voice_id}",
    tags=[TAG_ITEMS_VOICES]
)
def items_voices_update(
        category_name: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Rename an image voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: update voice '{voice_id}' of image '{item_id}' "
               f"in category '{category_name}'",
    )


@router.put(
    "/categories/{category_name}/items/{item_id}/voices/{voice_id}/audio",
    tags=[TAG_ITEMS_VOICES]
)
def items_voices_upload(
        category_name: str,
        item_id: str,
        voice_id: str,
        # file: UploadFile = File(...),
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Upload or replace audio for an image voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: upload audio for voice '{voice_id}' of image '{item_id}' "
               f"in category '{category_name}'",
    )


@router.delete(
    "/categories/{category_name}/items/{item_id}/voices/{voice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=[TAG_ITEMS_VOICES]
)
def items_voices_delete(
        category_name: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete or reset an image voice."""
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
    status_code=status.HTTP_201_CREATED,
    response_model=CreateItemResponse,
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
    result = service.create_item(
        category_name=category.strip(),
        english_word=item.strip(),
        image_data=image_data,
        image_extension=ext,
    )
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"],
        )
    return CreateItemResponse(**result)


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
