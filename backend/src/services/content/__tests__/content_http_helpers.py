"""Shared helpers for the content API integration tests (test_content_service_integration_*.py):
a controllable stub ContentProvider and a bare FastAPI app wired to it.
"""

# pylint: disable=missing-function-docstring,too-many-public-methods

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI

from src.api.dependencies import get_current_user
from src.services.content import routes as content_routes
from src.services.content.models import (
    ArchiveCategory,
    CategoryDetails,
    CategoryImage,
    CategoryList,
    CategorySummary,
    CategoryVoice,
    ContentProvider,
    CreateCategory,
    CreateItem,
    DeleteArchivedCategory,
    GenerateItemVoice,
    GeneratedItemVoice,
    ItemDetail,
    ItemImages,
    ItemVoices,
    OptimizedItemVoice,
    RemoveItem,
    RestoreCategory,
    UpdateCategory,
)
from src.services.content.routes import (
    get_content_service,
    get_voice_configurations_available,
    get_voice_generation_available,
    get_voice_optimization_available,
)
from src.services.content.service import ContentService


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8  # minimal valid-looking PNG header


class StubContentProvider(ContentProvider):
    """Controllable stub — returns configured model instances or raises configured exceptions."""

    def __init__(self, base: Path) -> None:
        super().__init__(
            name="stub",
            service_name="content",
            provider_name="file_system",
            provider_category="",
            enabled=True,
            priority=1,
            config={},
            config_path=base / "stub.json",
            base_dir=base,
        )
        self._returns: dict[str, Any] = {}
        self._raises: dict[str, Exception] = {}

    # ---- builder API ----

    def returns(self, method: str, value: Any) -> "StubContentProvider":
        self._returns[method] = value
        return self

    def raises(self, method: str, exc: Exception) -> "StubContentProvider":
        self._raises[method] = exc
        return self

    # ---- ContentProvider interface ----

    def _call(self, method: str, default: Any) -> Any:
        if method in self._raises:
            raise self._raises[method]
        return self._returns.get(method, default)

    def list_categories(
            self, include_archived: bool = False, archived_only: bool = False
    ) -> CategoryList:
        return self._call("list_categories", CategoryList(categories=[
            CategorySummary(
                id="C_Foods", name={"en": "Foods"}, items_count=2, thumbnail_uri="/content/foods/foods.png",
            ),
            CategorySummary(id="C_Drinks", name={"en": "Drinks"}),
        ]))

    def category_id_by_name(self, category_name: str) -> str:
        return self._call("category_id_by_name", category_name)

    def category_details(self, category_id: str) -> CategoryDetails:
        return self._call("category_details", CategoryDetails(
            name={"en": category_id},
            items=[ItemDetail(id=f"I_{category_id.title()}", name={"en": "apple"})],
        ))

    def create_category(self, category_name: str) -> CreateCategory:
        return self._call("create_category", CreateCategory(category=category_name, message="created"))

    def create_item(
            self,
            category_id: str,
            english_word: str,
            image_data: bytes | None = None,
            image_extension: str = ".png",
            generate_voices: bool = True,
            generate_image: bool = False,
    ) -> CreateItem:
        return self._call("create_item", CreateItem(
            category=category_id,
            item=english_word,
            folder=english_word.lower(),
            image_id=f"I_{english_word.title()}",
            item_id=f"I_{english_word.title()}",
            voices_generated=["en"] if generate_voices else [],
            message="created",
        ))

    def update_item_name(self, category_id: str, item_id: str, item_name: Any) -> ItemDetail:
        return self._call("update_item_name", ItemDetail(id=item_id, name=item_name))

    def update_item_id(self, category_id: str, item_id: str, new_item_id: str) -> ItemDetail:
        return self._call("update_item_id", ItemDetail(id=new_item_id))

    def remove_item(self, category_id: str, item_id: str) -> RemoveItem:
        return self._call("remove_item", RemoveItem(category=category_id, item=item_id))

    def add_category_image(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        return self._call("add_category_image", CategoryImage(
            id=f"{category_id}_1",
            file_uri=f"/content/{category_id}/{category_id}_1{image_extension}",
        ))

    def update_category_image(
            self,
            category_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        return self._call("update_category_image", CategoryImage(
            id=image_id,
            file_uri=f"/content/{category_id}/{image_id}{image_extension}",
        ))

    def delete_category_image(self, category_id: str, image_id: str) -> CategoryImage:
        return self._call("delete_category_image", CategoryImage(
            id=image_id,
            file_uri=f"/content/{category_id}/{image_id}.png",
        ))

    def set_category_icon(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        return self._call("set_category_icon", CategoryImage(
            id=category_id,
            file_uri=f"/content/{category_id}/{category_id}{image_extension}",
        ))

    def archive_category(self, category_id: str) -> ArchiveCategory:
        return self._call("archive_category", ArchiveCategory(
            archived=True,
            name=category_id,
            archive_folder=f"{category_id}_1000",
            destination=f"/archive/{category_id}_1000",
            message="archived",
        ))

    def update_category_details(
            self, category_id: str, category_name: Any, description: Any,
    ) -> UpdateCategory:
        return self._call(
            "update_category_details", UpdateCategory(category=category_id, message="updated"),
        )

    def update_category_id(self, category_id: str, new_category_id: str) -> UpdateCategory:
        return self._call(
            "update_category_id", UpdateCategory(category=new_category_id, message="renamed"),
        )

    def remove_category(self, category_id: str) -> ArchiveCategory:
        return self._call("remove_category", ArchiveCategory(
            removed=True,
            name=category_id,
            message="removed",
        ))

    def restore_category(self, name: str) -> RestoreCategory:
        return self._call("restore_category", RestoreCategory(
            restored=True,
            name=name,
            from_archive=f"{name}_1000",
            message="restored",
        ))

    def delete_archived_category(self, name: str) -> DeleteArchivedCategory:
        return self._call("delete_archived_category", DeleteArchivedCategory(
            deleted=True,
            name=name,
            archive_folder=f"{name}_1000",
            message="deleted",
        ))

    def item_images(self, category_id: str, item_id: str) -> ItemImages:
        return self._call("item_images", ItemImages(item_id=item_id))

    def item_voices(self, category_id: str, item_id: str) -> ItemVoices:
        return self._call("item_voices", ItemVoices(item_id=item_id))

    def add_item_image(
            self,
            category_id: str,
            item_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        return self._call("add_item_image", CategoryImage(
            id=f"{item_id}_1",
            file_uri=f"/content/{category_id}/{item_id}/{item_id}_1{image_extension}",
        ))

    def generate_item_image(
            self,
            category_id: str,
            item_id: str,
            prompt: str,
            configuration_name: str | None = None,
    ) -> CategoryImage:
        return self._call("generate_item_image", CategoryImage(
            id=f"{item_id}_1",
            file_uri=f"/content/{category_id}/{item_id}/{item_id}_1.png",
        ))

    def update_item_image(
            self,
            category_id: str,
            item_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CategoryImage:
        return self._call("update_item_image", CategoryImage(
            id=image_id,
            file_uri=f"/content/{category_id}/{item_id}/{image_id}{image_extension}",
        ))

    def delete_item_image(self, category_id: str, item_id: str, image_id: str) -> CategoryImage:
        return self._call("delete_item_image", CategoryImage(
            id=image_id,
            file_uri=f"/content/{category_id}/{item_id}/{image_id}.png",
        ))

    def set_main_item_image(self, category_id: str, item_id: str, image_id: str) -> ItemImages:
        return self._call("set_main_item_image", ItemImages(item_id=item_id))

    def set_main_item_voice(self, category_id: str, item_id: str, voice_id: str) -> ItemVoices:
        return self._call("set_main_item_voice", ItemVoices(item_id=item_id))

    def optimize_item_voice(self, category_id: str, item_id: str, voice_id: str) -> OptimizedItemVoice:
        return self._call("optimize_item_voice", OptimizedItemVoice(item_id=item_id, source_voice_id=voice_id))

    def add_item_voice(self, category_id: str, item_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        return self._call("add_item_voice", CategoryVoice(
            id=f"{item_id}-v-{language}",
            language=language,
            type="tts",
            file_uri=f"/content/{category_id}/{item_id}/{item_id}-v-{language}.mp3",
        ))

    def update_item_voice(self, category_id: str, item_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        return self._call("update_item_voice", CategoryVoice(
            id=voice_id,
            language="en",
            type="tts",
            file_uri=f"/content/{category_id}/{item_id}/{voice_id}.mp3",
        ))

    def delete_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        return self._call("delete_item_voice", CategoryVoice(
            id=voice_id,
            language="en",
            type="tts",
            file_uri=f"/content/{category_id}/{item_id}/{voice_id}.mp3",
        ))

    def regenerate_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
        return self._call("regenerate_item_voice", CategoryVoice(
            id=voice_id,
            language="en",
            type="tts",
            file_uri=f"/content/{category_id}/{item_id}/{voice_id}.mp3",
        ))

    def generate_item_voice(self, category_id: str, item_id: str, request: GenerateItemVoice) -> GeneratedItemVoice:
        return self._call("generate_item_voice", GeneratedItemVoice(item_id=item_id, voice_id=f"{item_id}_01"))

    def add_category_voice(self, category_id: str, language: str, audio_data: bytes) -> CategoryVoice:
        return self._call("add_category_voice", CategoryVoice(
            id=f"{category_id}-v-{language}",
            language=language,
            type="tts",
            file_uri=f"/content/{category_id}/{category_id}_{language}.mp3",
        ))

    def update_category_voice(self, category_id: str, voice_id: str, audio_data: bytes) -> CategoryVoice:
        return self._call("update_category_voice", CategoryVoice(
            id=voice_id,
            language="en",
            type="tts",
            file_uri=f"/content/{category_id}/{voice_id}.mp3",
        ))

    def delete_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        return self._call("delete_category_voice", CategoryVoice(
            id=voice_id,
            language="en",
            type="tts",
            file_uri=f"/content/{category_id}/{voice_id}.mp3",
        ))

    def regenerate_category_voice(self, category_id: str, voice_id: str) -> CategoryVoice:
        return self._call("regenerate_category_voice", CategoryVoice(
            id=voice_id,
            language="en",
            type="tts",
            file_uri=f"/content/{category_id}/{voice_id}.mp3",
        ))


def make_content_service(
        tmp_path: Path,
        provider: StubContentProvider,
) -> ContentService:
    """A ContentService whose only provider is *provider*."""
    svc = ContentService(
        service_name="content",
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    svc.replace_providers([provider])
    return svc


def make_app(
        provider: StubContentProvider | None = None,
        tmp_path: Path | None = None,
        *,
        auth: bool = True,
        voice_generation_available: bool = True,
        voice_optimization_available: bool = True,
        voice_configurations_available: bool = True,
) -> FastAPI:
    """A bare app with just the content router, its auth and service dependencies overridden."""
    app = FastAPI()
    app.include_router(content_routes.router)
    # The real dependency reads app.state.registry, which this bare app never sets.
    app.dependency_overrides[get_voice_generation_available] = lambda: voice_generation_available
    app.dependency_overrides[get_voice_optimization_available] = lambda: voice_optimization_available
    app.dependency_overrides[get_voice_configurations_available] = lambda: voice_configurations_available
    if auth:
        app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "role": "admin"}
    if provider is not None and tmp_path is not None:
        svc = make_content_service(tmp_path, provider)
        app.dependency_overrides[get_content_service] = lambda: svc
    elif not auth:
        # Prevent get_registry AttributeError from racing with the 401 raised by
        # get_current_user. The dummy value is never used because auth blocks first.
        app.dependency_overrides[get_content_service] = lambda: None
    return app
