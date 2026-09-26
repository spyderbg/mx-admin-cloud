"""Content API routes for items: the item itself, its images and its voices, plus the
legacy POST /items creation endpoint.
"""

from __future__ import annotations

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
    CategoryConflictError,
    CategoryNotFoundError,
    ContentError,
    InvalidCategoryNameError,
)
from src.core.localization import LocalizedString
from src.services.content.schemas import (
    CreateItemResponse,
    ImageResponse,
    ItemImagesResponse,
    ItemListResponse,
    ItemSummary,
    ItemVoicesResponse,
    UpdateItemBody,
    VoiceResponse,
)
from src.services.content.routes.common import (
    CATEGORY_ITEM,
    CATEGORY_ITEMS,
    CATEGORY_ITEM_IMAGE,
    CATEGORY_ITEM_IMAGES,
    CATEGORY_ITEM_IMAGE_MAIN,
    CATEGORY_ITEM_VOICE,
    CATEGORY_ITEM_VOICES,
    CATEGORY_ITEM_VOICE_REGENERATE,
    ContentServiceDep,
    ITEMS,
    TAG_ITEMS,
    TAG_ITEMS_IMAGES,
    TAG_ITEMS_VOICES,
    VoiceGenerationAvailableDep,
    _create_item_response,
    _read_image_upload,
    _voice_response,
)

router = APIRouter()


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
            ItemSummary(
                id=item.id, name=item.name, thumbnail_uri=item.thumbnail_uri or None, images_count=item.images_count,
            )
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
            "When 'new_item_id' is given and differs from item_id, the item's id is changed too; "
            "fails with 409 if another item in the category already uses that id. "
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
    """Update an item's localized name, and optionally its id."""
    try:
        result = service.update_item(category_id, item_id, body.item_name)
        if body.new_item_id and body.new_item_id != item_id:
            result = service.update_item_id(category_id, item_id, body.new_item_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
        main_image_id=images_result.main_image_id,
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


@router.put(
    CATEGORY_ITEM_IMAGE_MAIN,
    response_model=ItemImagesResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_IMAGES],
    summary="Set an image file variant as the item's main image",
    description=(
            "Make the image file variant identified by image_id the item's main image — the one "
            "returned first by the images list and used as the item's thumbnail. Requires authentication."
    ),
)
def items_images_set_main(
        category_id: str,
        item_id: str,
        image_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemImagesResponse:
    """Make the given image variant the item's main image."""
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
    try:
        images_result = service.set_main_item_image(category_id, item_id, image_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ItemImagesResponse(
        item_id=item_id,
        name=item_name,
        images=[ImageResponse(id=img.id, file_uri=img.file_uri) for img in images_result.images],
        thumbnail_uri=images_result.thumbnail_uri,
        main_image_id=images_result.main_image_id,
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
        voices=[_voice_response(v) for v in voices_result.voices],
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
    return _voice_response(result, "add")


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
    return _voice_response(result, "update")


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
    return _voice_response(result, "delete")


@router.post(
    CATEGORY_ITEM_VOICE_REGENERATE,
    response_model=VoiceResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_VOICES],
    summary="Regenerate a voice for an item",
    description=(
            "Re-synthesize the audio for an item voice from the item's current text in that "
            "language, using the active text-to-speech configuration. Overwrites any existing "
            "audio file, and creates one when the voice has none yet. Requires authentication."
    ),
)
def items_voices_regenerate(
        category_id: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> VoiceResponse:
    """Regenerate an item voice from its current text."""
    try:
        result = service.regenerate_item_voice(category_id, item_id, voice_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _voice_response(result, "regenerate")


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
