"""Content API routes for categories: the category itself, its images and its voices."""

from __future__ import annotations

import re

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from src.api.dependencies import get_current_user
from src.services.content.models import (
    ArchiveUnavailableError,
    CategoryConflictError,
    CategoryNotFoundError,
    ContentError,
    InvalidCategoryNameError,
)
from src.services.content.schemas import (
    ArchivedEntry,
    CategoryDetailsResponse,
    CategoryImagesResponse,
    CategoryListResponse,
    CategorySummary,
    CategoryVoicesResponse,
    CreateCategoryBody,
    CreateCategoryResponse,
    DeleteArchivedCategoryResponse,
    ImageResponse,
    ItemSummary,
    RemoveCategoryResponse,
    RenameCategoryBody,
    RenameCategoryResponse,
    RestoreCategoryBody,
    RestoreCategoryResponse,
    VoiceResponse,
)
from src.services.content.service import ContentService
from src.services.content.routes.common import (
    CATEGORIES,
    CATEGORIES_ARCHIVE,
    CATEGORIES_RESTORE,
    CATEGORY_DETAILS,
    CATEGORY_ICON,
    CATEGORY_IMAGE,
    CATEGORY_IMAGES,
    CATEGORY_VOICE,
    CATEGORY_VOICES,
    CATEGORY_VOICE_REGENERATE,
    ContentServiceDep,
    TAG_CATEGORIES,
    TAG_CATEGORIES_IMAGES,
    TAG_CATEGORIES_VOICES,
    _voice_response,
)

router = APIRouter()


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
        voices=[_voice_response(v) for v in result.voices],
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
    return _voice_response(result, "add")


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
    return _voice_response(result, "update")


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
    return _voice_response(result, "delete")


@router.post(
    CATEGORY_VOICE_REGENERATE,
    response_model=VoiceResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_CATEGORIES_VOICES],
    summary="Regenerate a voice for a category",
    description=(
            "Re-synthesize the audio for a category voice from the category's current localized "
            "name in that language, using the active text-to-speech configuration. Overwrites any "
            "existing audio file, and creates one when the voice has none yet. Requires authentication."
    ),
)
def categories_voices_regenerate(
        category_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> VoiceResponse:
    """Regenerate a category voice from its current localized name."""
    try:
        result = service.regenerate_category_voice(category_id, voice_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _voice_response(result, "regenerate")


# endregion
