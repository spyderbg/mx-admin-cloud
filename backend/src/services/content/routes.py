"""Content API routes (public v1).

Exposes category and item CRUD endpoints. Delegates to ContentService
resolved from the ServiceRegistry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from src.api.dependencies import get_current_user, get_registry
from src.api.service_resolver import get_typed_service
from src.core.config import settings
from src.services.content.schemas import (
    CategoryDetailResponse,
    CategoryListResponse,
    CreateCategoryBody,
    CreateCategoryResponse,
    CreateItemResponse,
    RemoveCategoryResponse,
    RenameCategoryBody,
    RenameCategoryResponse,
    RestoreCategoryBody,
    RestoreCategoryResponse,
)
from src.services.content.service import ContentService
from src.services.service_registry import ServiceRegistry

router = APIRouter(
    prefix=f"{settings.API_V1_STR}/content",
    tags=["content"],
)


def _content_service(registry: ServiceRegistry = Depends(get_registry)) -> ContentService:
    return get_typed_service(registry, "content", ContentService)


@router.get("/categories", response_model=CategoryListResponse)
def list_categories(
        service: ContentService = Depends(_content_service),
        _user: dict = Depends(get_current_user),
        include_archived: bool = False,
        archived_only: bool = False,
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
    status_code=status.HTTP_201_CREATED,
    response_model=CreateCategoryResponse,
)
def create_category_endpoint(
        body: CreateCategoryBody,
        service: ContentService = Depends(_content_service),
        _user: dict = Depends(get_current_user),
) -> CreateCategoryResponse:
    """Create a new category."""
    result = service.create_category(body.name.strip())
    if "error" in result:
        if result.get("error", "").startswith("Category already exists"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["error"])
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return CreateCategoryResponse(**result)


@router.post("/categories/restore", response_model=RestoreCategoryResponse)
def restore_category_endpoint(
        body: RestoreCategoryBody,
        service: ContentService = Depends(_content_service),
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


@router.patch("/categories/{category_name}", response_model=RenameCategoryResponse)
def rename_category_endpoint(
        category_name: str,
        body: RenameCategoryBody,
        service: ContentService = Depends(_content_service),
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


@router.delete("/categories/{category_name}", response_model=RemoveCategoryResponse)
def remove_category(
        category_name: str,
        service: ContentService = Depends(_content_service),
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


@router.get("/categories/{category_name}", response_model=CategoryDetailResponse)
def category_details(
        category_name: str,
        service: ContentService = Depends(_content_service),
        _user: dict = Depends(get_current_user),
) -> CategoryDetailResponse:
    """Get category details."""
    result = service.category_details(category_name)
    if "error" in result and result.get("error") == "Category not found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
    if "error" in result and result.get("error") == "Invalid category name":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return CategoryDetailResponse(**result)


@router.put("/categories/{category_name}")
def update_category(
        category_name: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """Update category name or description."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: update category '{category_name}'",
    )


# ---------------------------------------------------------------------------
# Category Voices
# ---------------------------------------------------------------------------

@router.get("/categories/{category_name}/voices")
def list_category_voices(
        category_name: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """List voices for a category."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: list voices for category '{category_name}'",
    )


@router.put("/categories/{category_name}/voices/{voice_id}")
def update_category_voice(
        category_name: str,
        voice_id: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """Rename a category voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: update voice '{voice_id}' in category '{category_name}'",
    )


@router.delete("/categories/{category_name}/voices/{voice_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def delete_category_voice(
        category_name: str,
        voice_id: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete or reset a category voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: delete voice '{voice_id}' in category '{category_name}'",
    )


@router.put("/categories/{category_name}/voices/{voice_id}/audio")
def upload_category_voice_audio(
        category_name: str,
        voice_id: str,
        _file: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> None:
    """Upload or replace audio for a category voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: upload audio for voice '{voice_id}' in category '{category_name}'",
    )


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

@router.get("/categories/{category_name}/images")
def list_category_images(
        category_name: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """List images for a category."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: list images for category '{category_name}'",
    )


@router.post("/categories/{category_name}/images",
             status_code=status.HTTP_201_CREATED)
def add_image_to_category(
        category_name: str,
        _file: UploadFile = File(...),
        _name: str = Form(...),
        _user: dict = Depends(get_current_user),
) -> None:
    """Add a new image to a category."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: add image to category '{category_name}'",
    )


@router.get("/categories/{category_name}/images/{image_id}")
def get_image(
        category_name: str,
        image_id: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """Get a single image by ID."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: get image '{image_id}' in category '{category_name}'",
    )


@router.put("/categories/{category_name}/images/{image_id}")
def update_image(
        category_name: str,
        image_id: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """Update image name."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: update image '{image_id}' in category '{category_name}'",
    )


@router.delete("/categories/{category_name}/images/{image_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
        category_name: str,
        image_id: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete an image from a category."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: delete image '{image_id}' in category '{category_name}'",
    )


# ---------------------------------------------------------------------------
# Image Files
# ---------------------------------------------------------------------------

@router.post("/categories/{category_name}/images/{image_id}/files",
             status_code=status.HTTP_201_CREATED)
def add_image_file(
        category_name: str,
        image_id: str,
        _file: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> None:
    """Upload an additional file variant for an image."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: add file to image '{image_id}' in category '{category_name}'",
    )


@router.put("/categories/{category_name}/images/{image_id}/files/{file_index}")
def replace_image_file(
        category_name: str,
        image_id: str,
        file_index: int,
        _file: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> None:
    """Replace a specific file variant."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: replace file {file_index} of image '{image_id}' "
               f"in category '{category_name}'",
    )


@router.delete("/categories/{category_name}/images/{image_id}/files/{file_index}",
               status_code=status.HTTP_204_NO_CONTENT)
def delete_image_file(
        category_name: str,
        image_id: str,
        file_index: int,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete a file variant (at least one must remain)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: delete file {file_index} of image '{image_id}' "
               f"in category '{category_name}'",
    )


# ---------------------------------------------------------------------------
# Image Voices
# ---------------------------------------------------------------------------

@router.get("/categories/{category_name}/images/{image_id}/voices")
def list_image_voices(
        category_name: str,
        image_id: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """List voices for an image."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: list voices for image '{image_id}' "
               f"in category '{category_name}'",
    )


@router.put("/categories/{category_name}/images/{image_id}/voices/{voice_id}")
def update_image_voice(
        category_name: str,
        image_id: str,
        voice_id: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """Rename an image voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: update voice '{voice_id}' of image '{image_id}' "
               f"in category '{category_name}'",
    )


@router.delete("/categories/{category_name}/images/{image_id}/voices/{voice_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def delete_image_voice(
        category_name: str,
        image_id: str,
        voice_id: str,
        _user: dict = Depends(get_current_user),
) -> None:
    """Delete or reset an image voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: delete voice '{voice_id}' of image '{image_id}' "
               f"in category '{category_name}'",
    )


@router.put("/categories/{category_name}/images/{image_id}/voices/{voice_id}/audio")
def upload_image_voice_audio(
        category_name: str,
        image_id: str,
        voice_id: str,
        _file: UploadFile = File(...),
        _user: dict = Depends(get_current_user),
) -> None:
    """Upload or replace audio for an image voice."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Not implemented: upload audio for voice '{voice_id}' of image '{image_id}' "
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
        service: ContentService = Depends(_content_service),
        _user: dict = Depends(get_current_user),
        image: UploadFile = File(...),  # pylint: disable=invalid-name
        category: str = Form(...),
        item: str = Form(...),
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
