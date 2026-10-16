"""Content API routes for items: the item itself, its images and its voices."""

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
    GenerateItemVoice,
    InvalidCategoryNameError,
    ItemVoices,
)
from src.core.localization import LocalizedString
from src.services.content.schemas import (
    CreateItemResponse,
    GenerateItemImageBody,
    GenerateItemVoiceBody,
    GenerateItemVoiceResponse,
    ImageResponse,
    ItemImagesResponse,
    ItemListResponse,
    ItemSummary,
    ItemVoicesResponse,
    OptimizeItemVoiceResponse,
    UpdateItemBody,
    VoiceCapabilities,
    VoiceResponse,
)
from src.services.content.routes.common import (
    CATEGORY_ITEM,
    CATEGORY_ITEMS,
    CATEGORY_ITEM_IMAGE,
    CATEGORY_ITEM_IMAGES,
    CATEGORY_ITEM_IMAGES_GENERATE,
    CATEGORY_ITEM_IMAGE_MAIN,
    CATEGORY_ITEM_VOICE,
    CATEGORY_ITEM_VOICE_MAIN,
    CATEGORY_ITEM_VOICE_OPTIMIZE,
    CATEGORY_ITEM_VOICES,
    CATEGORY_ITEM_VOICES_GENERATE,
    CATEGORY_ITEM_VOICE_REGENERATE,
    ContentServiceDep,
    TAG_ITEMS,
    TAG_ITEMS_IMAGES,
    TAG_ITEMS_VOICES,
    VoiceConfigurationsAvailableDep,
    ImageGenerationAvailableDep,
    VoiceGenerationAvailableDep,
    VoiceOptimizationAvailableDep,
    _create_item_response,
    _image_response,
    _read_image_upload,
    _voice_response,
)

router = APIRouter()


def _item_voices_response(item_id: str, item_name: LocalizedString, voices: ItemVoices) -> ItemVoicesResponse:
    """Build an ItemVoicesResponse from a provider-returned ItemVoices."""
    return ItemVoicesResponse(
        item_id=item_id,
        name=item_name,
        voices=[_voice_response(v) for v in voices.voices],
        variants=[_voice_response(v) for v in voices.variants],
        main_voice_ids=voices.main_voice_ids,
    )


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
            "The image upload is optional. "
            "'generate_voices' (one voice per language) and 'generate_image' (an image from the name) "
            "are both off by default and work the same way: each runs after the item is created, "
            "only when an active generator exists, and its problems never block creation but are "
            "reported in 'voice_warnings' / 'image_warnings'. "
            "Uploading an image and asking to generate one is a 400. "
            "Requires authentication."
    ),
)
def items_create(
        category_id: str,
        service: ContentServiceDep,
        voice_generation_available: VoiceGenerationAvailableDep,
        image_generation_available: ImageGenerationAvailableDep,
        item: str = Form(...),
        image: UploadFile | None = File(None),
        generate_voices: bool = Form(False),
        generate_image: bool = Form(False),
        _user: dict = Depends(get_current_user),
) -> CreateItemResponse:
    """Create an item with translations, an optional image, and optionally generated voices and image."""
    word = item.strip()
    if not word:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Item name is required")
    image_data, ext = _read_image_upload(image)
    if image_data and generate_image:
        # Checked here too: with no active image generator the provider never sees generate_image.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Upload an image or generate one, not both.",
        )
    try:
        result = service.create_item_by_id(
            category_id,
            word,
            image_data,
            ext,
            generate_voices=generate_voices and voice_generation_available,
            generate_image=generate_image and image_generation_available,
        )
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _create_item_response(
        result,
        voices_unavailable=generate_voices and not voice_generation_available,
        image_unavailable=generate_image and not image_generation_available,
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
        images=[_image_response(img) for img in images_result.images],
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
    return _image_response(result, action="add")


@router.post(
    CATEGORY_ITEM_IMAGES_GENERATE,
    response_model=ImageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_ITEMS_IMAGES],
    summary="Generate an image variant for an item from a text prompt",
    description=(
            "Generate an image from 'prompt' with the text to image configuration named by "
            "'configuration_name' (which must be active) or, when omitted, the first active "
            "configuration that succeeds, and add it to the item as a new image variant stored "
            "at the next available index. Nothing is overwritten and the main image is not "
            "changed; the first image of an item becomes its main image. Responds with HTTP 400 "
            "when the prompt is empty or generation fails, including when no text to image "
            "configuration is active. Requires authentication."
    ),
)
def items_images_generate(
        category_id: str,
        item_id: str,
        body: GenerateItemImageBody,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ImageResponse:
    """Generate an image from a prompt and add it to an item as a new variant."""
    if not body.prompt.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Prompt is required")
    try:
        result = service.generate_item_image(category_id, item_id, body.prompt, body.configuration_name)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _image_response(result, action="add")


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
    return _image_response(result, action="update")


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
    return _image_response(result, action="delete")


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
        images=[_image_response(img) for img in images_result.images],
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
            "Returns one voice per language, the alternative recordings (variants) per language, "
            "and which variant is currently selected as each language's main voice. "
            "'capabilities' says which voice actions can run right now (for example, optimize "
            "needs an active voice optimizer and generate a text to speech configuration). "
            "Requires authentication."
    ),
)
def items_voices_list(
        category_id: str,
        item_id: str,
        service: ContentServiceDep,
        optimization_available: VoiceOptimizationAvailableDep,
        configurations_available: VoiceConfigurationsAvailableDep,
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
    response = _item_voices_response(item_id, item_name, voices_result)
    # POST /voices can add another recording for a language that already has one;
    # generation and optimization are on while a configuration / optimizer exists.
    response.capabilities = VoiceCapabilities(
        multiple_voices=True,
        optimize=optimization_available,
        generate=configurations_available,
    )
    return response


@router.post(
    CATEGORY_ITEM_VOICES,
    response_model=VoiceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_ITEMS_VOICES],
    summary="Add a voice to an item",
    description=(
            "Add a new voice file to the specified content item for the given language. "
            "If the item already has audio for that language, the new recording is added as a "
            "new variant and the current voice stays selected. Requires authentication."
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


@router.post(
    CATEGORY_ITEM_VOICES_GENERATE,
    response_model=GenerateItemVoiceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[TAG_ITEMS_VOICES],
    summary="Generate a new voice for an item",
    description=(
            "Synthesize 'text' in 'language' with the text-to-speech configuration named by "
            "'configuration_name' (enabled or not), layering 'speed', 'stability' and "
            "'similarity' over the configuration's own settings for this request only. "
            "Nothing is overwritten: the result is added as a new variant of that language and "
            "the main voice stays as it is, so it can be listened to and chosen later with the "
            "set-main endpoint. Only a language with no recording yet gets it as its main voice. "
            "With 'optimize', every active voice optimizer (or the first that succeeds, per the "
            "voice optimization strategy) then creates an optimized variant of it; optimization "
            "problems are reported in 'warnings' and never undo the new voice. The response is "
            "the refreshed voice listing plus the new voice's id. Requires authentication."
    ),
)
def items_voices_generate(
        category_id: str,
        item_id: str,
        body: GenerateItemVoiceBody,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> GenerateItemVoiceResponse:
    """Add a voice synthesized with a chosen configuration and settings, keeping the main voice."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    item = next((i for i in result.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_id}'",
        )
    try:
        generated = service.generate_item_voice(category_id, item_id, GenerateItemVoice(
            language=body.language.strip(),
            text=body.text,
            configuration_name=body.configuration_name,
            speed=body.speed,
            stability=body.stability,
            similarity=body.similarity,
            optimize=body.optimize,
        ))
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return GenerateItemVoiceResponse(
        **_item_voices_response(item_id, item.name, generated).model_dump(),
        voice_id=generated.voice_id,
        optimized_voice_ids=generated.optimized_voice_ids,
        warnings=generated.warnings,
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


@router.put(
    CATEGORY_ITEM_VOICE_MAIN,
    response_model=ItemVoicesResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_VOICES],
    summary="Set a voice variant as the item's main voice for its language",
    description=(
            "Make the voice variant identified by voice_id the item's main voice for its language "
            "(a per-language voice id or a variant id) — the one returned in 'voices' and played "
            "for that language. Requires authentication."
    ),
)
def items_voices_set_main(
        category_id: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> ItemVoicesResponse:
    """Make the given voice variant the item's main voice for its language."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    item = next((i for i in result.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_id}'",
        )
    try:
        voices_result = service.set_main_item_voice(category_id, item_id, voice_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _item_voices_response(item_id, item.name, voices_result)


@router.post(
    CATEGORY_ITEM_VOICE_OPTIMIZE,
    response_model=OptimizeItemVoiceResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_VOICES],
    summary="Create optimized versions of an item voice",
    description=(
            "Run every active voice optimizer on the original recording of the voice identified by "
            "voice_id (a per-language voice id or a variant id) and store each result as a new "
            "variant of that language. The original is kept as a variant too, and the main voice "
            "is not changed: select any of them with the set-main endpoint. Re-optimizing replaces "
            "an optimizer's previous output. Optimizers that fail are reported in 'warnings'; the "
            "request fails with 400 only when none is active or all of them fail. "
            "Requires authentication."
    ),
)
def items_voices_optimize(
        category_id: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> OptimizeItemVoiceResponse:
    """Create one optimized variant of an item voice per active optimizer."""
    try:
        result = service.category_details_by_id(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    item = next((i for i in result.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found in category '{category_id}'",
        )
    try:
        optimized = service.optimize_item_voice(category_id, item_id, voice_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidCategoryNameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OptimizeItemVoiceResponse(
        **_item_voices_response(item_id, item.name, optimized).model_dump(),
        source_voice_id=optimized.source_voice_id,
        optimized_voice_ids=optimized.optimized_voice_ids,
        warnings=optimized.warnings,
    )


@router.post(
    CATEGORY_ITEM_VOICE_REGENERATE,
    response_model=VoiceResponse,
    status_code=status.HTTP_200_OK,
    tags=[TAG_ITEMS_VOICES],
    summary="Generate a new take of an item voice",
    description=(
            "Synthesize a new take of an item's voice from the item's current text in the voice's "
            "language, using the active text-to-speech configuration. Nothing is overwritten: the "
            "take is added as a new variant of that language (the current main voice is first kept "
            "as a variant if it is not one already) and the main voice stays as it is, so takes can "
            "be compared and one chosen with the set-main endpoint. The response is the new variant. "
            "Only a language with no recording yet gets the take as its main voice. "
            "voice_id is a per-language voice id or a variant id. Requires authentication."
    ),
)
def items_voices_regenerate(
        category_id: str,
        item_id: str,
        voice_id: str,
        service: ContentServiceDep,
        _user: dict = Depends(get_current_user),
) -> VoiceResponse:
    """Add a newly synthesized take of an item voice, keeping the existing recordings."""
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
