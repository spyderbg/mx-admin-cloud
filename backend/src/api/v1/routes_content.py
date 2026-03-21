"""Content API routes."""

from fastapi import APIRouter, Depends, HTTPException, status  # pylint: disable=import-error
from fastapi import File, Form, UploadFile  # pylint: disable=import-error
from pydantic import BaseModel

from src.api.dependencies import get_current_user
from src.core.content_service import (
    list_categories as svc_list_categories,
    category_details as svc_category_details,
    create_category as svc_create_category,
    create_object as svc_create_object,
    remove_category as svc_remove_category,
    rename_category as svc_rename_category,
    restore_category as svc_restore_category,
)


class CreateCategoryBody(BaseModel):
    """Request body for creating a new category."""
    name: str


class RenameCategoryBody(BaseModel):
    """Request body for renaming a category."""
    name: str


class RestoreCategoryBody(BaseModel):
    """Request body for restoring a category."""
    name: str


router = APIRouter()


@router.get("/content/categories")
def list_categories(
        _user: dict = Depends(get_current_user),
        include_archived: bool = False,
        archived_only: bool = False,
):
    """List categories."""
    result = svc_list_categories(include_archived=include_archived, archived_only=archived_only)
    if "error" in result and "categories" not in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result.get("error"))
    return result


@router.post("/content/categories", status_code=status.HTTP_201_CREATED)
def create_category_endpoint(body: CreateCategoryBody, _user: dict = Depends(get_current_user)):
    """Create a new category."""
    result = svc_create_category(body.name.strip())
    if "error" in result:
        if result.get("error", "").startswith("Category already exists"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["error"])
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


@router.post("/content/categories/restore")
def restore_category_endpoint(body: RestoreCategoryBody, _user: dict = Depends(get_current_user)):
    """Restore a category."""
    result = svc_restore_category(body.name.strip())
    if "error" in result:
        if result.get("error") == "Archive directory does not exist":
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=result["error"])
        if "No archive found" in result.get("error", ""):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
        if "already exists" in result.get("error", ""):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["error"])
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


@router.patch("/content/categories/{category_name}")
def rename_category_endpoint(category_name: str, body: RenameCategoryBody, _user: dict = Depends(get_current_user)):
    """Rename a category."""
    result = svc_rename_category(category_name, body.name.strip())
    if "error" in result:
        if result.get("error") == "Category not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
        if "already exists" in result.get("error", ""):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["error"])
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


@router.delete("/content/categories/{category_name}")
def remove_category(category_name: str, _ser: dict = Depends(get_current_user)):
    """Remove a category."""
    result = svc_remove_category(category_name)
    if "error" in result:
        if result.get("error") == "Category not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
        if result.get("error") == "Invalid category name":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


@router.get("/content/categories/{category_name}")
def category_details(category_name: str, _user: dict = Depends(get_current_user)):
    """Get category details."""
    result = svc_category_details(category_name)
    if "error" in result and result.get("error") == "Category not found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
    if "error" in result and result.get("error") == "Invalid category name":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


@router.post("/content/objects", status_code=status.HTTP_201_CREATED)
def create_object(
        _user: dict = Depends(get_current_user),
        image: UploadFile = File(...),  # pylint: disable=invalid-name
        category: str = Form(...),
        object: str = Form(..., alias="object"),
):
    """Create a new object."""
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
    result = svc_create_object(
        category_name=category.strip(),
        english_word=object.strip(),
        image_data=image_data,
        image_extension=ext,
    )
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"],
        )
    return result
