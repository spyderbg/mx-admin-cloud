"""Shared pieces of the content API routes: route paths and tags, request/response helpers,
and the FastAPI dependencies that resolve the content and text-to-speech services.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    Depends,
    HTTPException,
    UploadFile,
    status,
)

from src.api.dependencies import get_registry
from src.api.service_resolver import get_typed_service, require_active_providers
from src.services import content, text_to_speech, voice_optimization
from src.services.content.models import CategoryVoice, CreateItem
from src.services.content.schemas import CreateItemResponse, VoiceResponse
from src.services.content.service import ContentService
from src.services.service_registry import ServiceRegistry
from src.services.text_to_speech.service import TextToSpeechService
from src.services.voice_optimization.service import VoiceOptimizationService

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
CATEGORY_VOICE_REGENERATE = "/categories/{category_id}/voices/{voice_id}/regenerate"
CATEGORY_ITEMS = "/categories/{category_id}/items"
CATEGORY_ITEM = "/categories/{category_id}/items/{item_id}"
CATEGORY_ITEM_IMAGES = "/categories/{category_id}/items/{item_id}/images"
CATEGORY_ITEM_IMAGE = "/categories/{category_id}/items/{item_id}/images/{image_id}"
CATEGORY_ITEM_IMAGE_MAIN = "/categories/{category_id}/items/{item_id}/images/{image_id}/main"
CATEGORY_ITEM_VOICES = "/categories/{category_id}/items/{item_id}/voices"
CATEGORY_ITEM_VOICE = "/categories/{category_id}/items/{item_id}/voices/{voice_id}"
CATEGORY_ITEM_VOICE_MAIN = "/categories/{category_id}/items/{item_id}/voices/{voice_id}/main"
CATEGORY_ITEM_VOICE_OPTIMIZE = "/categories/{category_id}/items/{item_id}/voices/{voice_id}/optimize"
CATEGORY_ITEM_VOICE_REGENERATE = "/categories/{category_id}/items/{item_id}/voices/{voice_id}/regenerate"

NO_VOICE_GENERATORS_WARNING = "No active voice generators configured; voices were not generated."


def _read_image_upload(image: UploadFile | None) -> tuple[bytes | None, str]:
    """Return (bytes, extension) for an uploaded image, or (None, ".png") when none was sent.

    An absent file may arrive as None or as an UploadFile with an empty filename, depending
    on the client. Raises 400 when the file is empty.
    """
    if image is None or not image.filename:
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


def _voice_response(voice: CategoryVoice, action: str | None = None) -> VoiceResponse:
    """Build a VoiceResponse from a provider-returned CategoryVoice, echoing generation metadata."""
    return VoiceResponse(
        id=voice.id,
        language=voice.language,
        type=voice.type,
        file_uri=voice.file_uri,
        action=action,
        provider=voice.provider,
        parameters=voice.parameters,
    )


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


def get_content_service(
        registry: ServiceRegistry = Depends(get_registry),
) -> ContentService:
    """Resolve the ContentService from the service registry; 503 when no content provider is active."""
    service = get_typed_service(
        registry=registry,
        service_name=content.SERVICE_NAME,
        expected_type=ContentService,
    )
    require_active_providers(
        service,
        service_name=content.SERVICE_NAME,
        detail="No active content provider is configured.",
    )
    return service


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


def get_voice_optimization_available(
        registry: ServiceRegistry = Depends(get_registry),
) -> bool:
    """Return whether at least one voice optimizer is active.

    The voices list advertises this so clients only offer optimization when it can run.
    A missing or unconfigured voice optimization service just means it is unavailable.
    """
    try:
        service = get_typed_service(
            registry=registry,
            service_name=voice_optimization.SERVICE_NAME,
            expected_type=VoiceOptimizationService,
        )
    except HTTPException:
        return False
    return bool(service.get_active_providers())


VoiceOptimizationAvailableDep = Annotated[bool, Depends(get_voice_optimization_available)]
