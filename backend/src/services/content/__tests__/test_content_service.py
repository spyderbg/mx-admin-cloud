"""Unit tests for the content service — models, service orchestration, and schemas
(no HTTP calls, no external APIs).

FileSystemContentProvider tests live in test_content_filesystem_provider.py.
"""

# pylint: disable=missing-function-docstring,too-few-public-methods,too-many-public-methods,redefined-outer-name,protected-access

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.localization import LocalizedString
from src.services.content.models import (
    ContentProvider,
    ContentProviderUnavailableError,
    CategoryNotFoundError,
    CategoryConflictError,
    InvalidCategoryNameError,
    ContentError,
    CreateItem,
)
from src.services.content.schemas import (
    ArchivedEntry,
    CategoryDetailsResponse,
    CategoryListResponse,
    CategorySummary,
    CreateCategoryBody,
    CreateCategoryResponse,
    CreateItemResponse,
    DeleteArchivedCategoryResponse,
    ItemSummary,
    RemoveCategoryResponse,
    RestoreCategoryBody,
    RestoreCategoryResponse,
    UpdateCategoryBody,
    UpdateCategoryResponse,
)
from src.services.content.service import ContentService
from src.services.models import ServiceProvider


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

class _StubProvider(ContentProvider):
    """Minimal ContentProvider stub with recorded calls and configurable returns."""

    def __init__(
            self,
            base: Path,
            *,
            name: str = "stub",
            enabled: bool = True,
            return_values: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            service_name="content",
            provider_name="file_system",
            provider_category="",
            enabled=enabled,
            priority=1,
            config={},
            config_path=base / f"{name}.json",
            base_dir=base,
        )
        self.calls: dict[str, list[Any]] = {}
        self._returns: dict[str, Any] = return_values or {}

    def _record(self, method: str, *args: Any) -> None:
        self.calls.setdefault(method, []).append(args)

    def list_categories(
            self, include_archived: bool = False, archived_only: bool = False
    ) -> dict[str, Any]:
        self._record("list_categories", include_archived, archived_only)
        return self._returns.get("list_categories", {"categories": ["cats", "dogs"]})

    def category_id_by_name(self, category_name: str) -> str:
        self._record("category_id_by_name", category_name)
        return self._returns.get("category_id_by_name", category_name)

    def category_details(self, category_id: str) -> dict[str, Any]:
        self._record("category_details", category_id)
        return self._returns.get("category_details", {"name": category_id, "objects": []})

    def create_category(self, category_name: str) -> dict[str, Any]:
        self._record("create_category", category_name)
        return self._returns.get("create_category", {"category": category_name, "message": "created"})

    def create_item(
            self,
            category_id: str,
            english_word: str,
            image_data: bytes | None = None,
            image_extension: str = ".png",
            generate_voices: bool = True,
    ) -> CreateItem:
        self._record("create_item", category_id, english_word, image_data, image_extension, generate_voices)
        return self._returns.get("create_item", CreateItem(
            category=category_id,
            item=english_word,
            folder=english_word.lower(),
            image_id=f"I_{english_word.title()}",
        ))

    def update_item_name(self, category_id: str, item_id: str, item_name: Any) -> dict[str, Any]:
        self._record("update_item_name", category_id, item_id, item_name)
        return self._returns.get("update_item_name", {
            "id": item_id,
            "name": item_name,
        })

    def update_item_id(self, category_id: str, item_id: str, new_item_id: str) -> dict[str, Any]:
        self._record("update_item_id", category_id, item_id, new_item_id)
        return self._returns.get("update_item_id", {"id": new_item_id})

    def remove_item(self, category_id: str, item_id: str) -> dict[str, Any]:
        self._record("remove_item", category_id, item_id)
        return self._returns.get("remove_item", {
            "category": category_id,
            "item": item_id,
        })

    def add_category_image(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> dict[str, Any]:
        self._record("add_category_image", category_id, image_data, image_extension)
        return self._returns.get("add_category_image", {
            "id": f"{category_id}_1",
            "file_uri": f"/content/{category_id}/{category_id}_1{image_extension}",
        })

    def update_category_image(
            self,
            category_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> dict[str, Any]:
        self._record("update_category_image", category_id, image_id, image_data, image_extension)
        return self._returns.get("update_category_image", {
            "id": image_id,
            "file_uri": f"/content/{category_id}/{image_id}{image_extension}",
        })

    def delete_category_image(self, category_id: str, image_id: str) -> dict[str, Any]:
        self._record("delete_category_image", category_id, image_id)
        return self._returns.get("delete_category_image", {
            "id": image_id,
            "file_uri": f"/content/{category_id}/{image_id}.png",
        })

    def set_category_icon(
            self,
            category_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> dict[str, Any]:
        self._record("set_category_icon", category_id, image_data, image_extension)
        return self._returns.get("set_category_icon", {
            "id": category_id,
            "file_uri": f"/content/{category_id}/{category_id}{image_extension}",
        })

    def archive_category(self, category_id: str) -> dict[str, Any]:
        self._record("archive_category", category_id)
        return self._returns.get("archive_category", {
            "archived": True,
            "name": category_id,
            "archive_folder": f"{category_id}_1000",
            "destination": f"/archive/{category_id}_1000",
            "message": "archived",
        })

    def update_category_details(
            self, category_id: str, category_name: Any, description: Any,
    ) -> dict[str, Any]:
        self._record("update_category_details", category_id, category_name, description)
        return self._returns.get("update_category_details", {
            "category": category_id,
            "message": f"updated details for {category_id}",
        })

    def update_category_id(self, category_id: str, new_category_id: str) -> dict[str, Any]:
        self._record("update_category_id", category_id, new_category_id)
        return self._returns.get("update_category_id", {
            "category": category_id,
            "message": f"renamed {category_id} to {new_category_id}",
        })

    def remove_category(self, category_id: str) -> dict[str, Any]:
        self._record("remove_category", category_id)
        return self._returns.get("remove_category", {
            "removed": True,
            "name": category_id,
            "message": "removed",
        })

    def restore_category(self, name: str) -> dict[str, Any]:
        self._record("restore_category", name)
        return self._returns.get("restore_category", {
            "restored": True,
            "name": name,
            "from_archive": f"{name}_1000",
            "message": "restored",
        })

    def delete_archived_category(self, name: str) -> dict[str, Any]:
        self._record("delete_archived_category", name)
        return self._returns.get("delete_archived_category", {
            "deleted": True,
            "name": name,
            "archive_folder": f"{name}_1000",
            "message": "deleted",
        })

    def item_images(self, category_id: str, item_id: str) -> dict[str, Any]:
        self._record("item_images", category_id, item_id)
        return self._returns.get("item_images", {"item_id": item_id, "images": [], "thumbnail_uri": None})

    def item_voices(self, category_id: str, item_id: str) -> dict[str, Any]:
        self._record("item_voices", category_id, item_id)
        return self._returns.get("item_voices", {"item_id": item_id, "voices": []})

    def add_item_image(
            self,
            category_id: str,
            item_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> dict[str, Any]:
        self._record("add_item_image", category_id, item_id, image_data, image_extension)
        return self._returns.get("add_item_image", {
            "id": f"{item_id}_1",
            "file_uri": f"/content/{category_id}/{item_id}/{item_id}_1{image_extension}",
        })

    def update_item_image(
            self,
            category_id: str,
            item_id: str,
            image_id: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> dict[str, Any]:
        self._record("update_item_image", category_id, item_id, image_id, image_data, image_extension)
        return self._returns.get("update_item_image", {
            "id": image_id,
            "file_uri": f"/content/{category_id}/{item_id}/{image_id}{image_extension}",
        })

    def delete_item_image(self, category_id: str, item_id: str, image_id: str) -> dict[str, Any]:
        self._record("delete_item_image", category_id, item_id, image_id)
        return self._returns.get("delete_item_image", {
            "id": image_id,
            "file_uri": f"/content/{category_id}/{item_id}/{image_id}.png",
        })

    def set_main_item_image(self, category_id: str, item_id: str, image_id: str) -> dict[str, Any]:
        self._record("set_main_item_image", category_id, item_id, image_id)
        return self._returns.get("set_main_item_image", {"item_id": item_id, "images": [], "thumbnail_uri": None})

    def set_main_item_voice(self, category_id: str, item_id: str, voice_id: str, language: str) -> dict[str, Any]:
        self._record("set_main_item_voice", category_id, item_id, voice_id, language)
        return self._returns.get("set_main_item_voice", {"item_id": item_id, "voices": [], "variants": []})

    def optimize_item_voice(self, category_id: str, item_id: str, voice_id: str) -> dict[str, Any]:
        self._record("optimize_item_voice", category_id, item_id, voice_id)
        return self._returns.get("optimize_item_voice", {"item_id": item_id, "source_voice_id": voice_id})

    def add_item_voice(self, category_id: str, item_id: str, language: str, audio_data: bytes) -> dict[str, Any]:
        self._record("add_item_voice", category_id, item_id, language, audio_data)
        return self._returns.get("add_item_voice", {
            "id": f"{item_id}-v-{language}", "language": language, "type": "tts",
            "file_uri": f"/content/{category_id}/{item_id}/{item_id}-v-{language}.mp3",
        })

    def update_item_voice(self, category_id: str, item_id: str, voice_id: str, audio_data: bytes) -> dict[str, Any]:
        self._record("update_item_voice", category_id, item_id, voice_id, audio_data)
        return self._returns.get("update_item_voice", {
            "id": voice_id, "language": "en", "type": "tts",
            "file_uri": f"/content/{category_id}/{item_id}/{voice_id}.mp3",
        })

    def delete_item_voice(self, category_id: str, item_id: str, voice_id: str) -> dict[str, Any]:
        self._record("delete_item_voice", category_id, item_id, voice_id)
        return self._returns.get("delete_item_voice", {
            "id": voice_id, "language": "en", "type": "tts",
            "file_uri": f"/content/{category_id}/{item_id}/{voice_id}.mp3",
        })

    def regenerate_item_voice(self, category_id: str, item_id: str, voice_id: str) -> dict[str, Any]:
        self._record("regenerate_item_voice", category_id, item_id, voice_id)
        return self._returns.get("regenerate_item_voice", {
            "id": voice_id, "language": "en", "type": "tts",
            "file_uri": f"/content/{category_id}/{item_id}/{voice_id}.mp3",
        })

    def add_category_voice(self, category_id: str, language: str, audio_data: bytes) -> dict[str, Any]:
        self._record("add_category_voice", category_id, language, audio_data)
        return self._returns.get("add_category_voice", {
            "id": f"{category_id}-v-{language}", "language": language, "type": "tts",
            "file_uri": f"/content/{category_id}/{category_id}_{language}.mp3",
        })

    def update_category_voice(self, category_id: str, voice_id: str, audio_data: bytes) -> dict[str, Any]:
        self._record("update_category_voice", category_id, voice_id, audio_data)
        return self._returns.get("update_category_voice", {
            "id": voice_id, "language": "en", "type": "tts",
            "file_uri": f"/content/{category_id}/{voice_id}.mp3",
        })

    def delete_category_voice(self, category_id: str, voice_id: str) -> dict[str, Any]:
        self._record("delete_category_voice", category_id, voice_id)
        return self._returns.get("delete_category_voice", {
            "id": voice_id, "language": "en", "type": "tts",
            "file_uri": f"/content/{category_id}/{voice_id}.mp3",
        })

    def regenerate_category_voice(self, category_id: str, voice_id: str) -> dict[str, Any]:
        self._record("regenerate_category_voice", category_id, voice_id)
        return self._returns.get("regenerate_category_voice", {
            "id": voice_id, "language": "en", "type": "tts",
            "file_uri": f"/content/{category_id}/{voice_id}.mp3",
        })


def _make_service(tmp_path: Path, providers: list[Any] | None = None) -> ContentService:
    svc = ContentService(
        service_name="content",
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    if providers is not None:
        svc.replace_providers(providers)
    return svc


# ---------------------------------------------------------------------------
# ContentProvider — naming helpers (pure logic)
# ---------------------------------------------------------------------------

class _ConcreteProvider(ContentProvider):
    """Minimal concrete subclass for testing abstract base helpers."""

    def __init__(self) -> None:
        super().__init__(  # pylint: disable=too-many-arguments
            name="concrete",
            service_name="content",
            provider_name="test",
            provider_category="",
            enabled=True,
            priority=1,
            config={},
            config_path=Path("/tmp/concrete.json"),
            base_dir=Path("/tmp"),
        )

    def list_categories(self, include_archived=False, archived_only=False):
        return {}

    def category_id_by_name(self, category_name):
        return ""

    def category_details(self, category_id):
        return {}

    def create_category(self, category_name):
        return {}

    def create_item(self, category_id, english_word, image_data=None, image_extension=".png", generate_voices=True):
        return {}

    def update_item_name(self, category_id, item_id, item_name):
        return {}

    def update_item_id(self, category_id, item_id, new_item_id):
        return {}

    def remove_item(self, category_id, item_id):
        return {}

    def add_category_image(self, category_id, image_data, image_extension=".png"):
        return {}

    def update_category_image(self, category_id, image_id, image_data, image_extension=".png"):
        return {}

    def delete_category_image(self, category_id, image_id):
        return {}

    def set_category_icon(self, category_id, image_data, image_extension=".png"):
        return {}

    def archive_category(self, category_id):
        return {}

    def update_category_details(self, category_id, category_name, description):
        return {}

    def update_category_id(self, category_id, new_category_id):
        return {}

    def remove_category(self, category_id):
        return {}

    def restore_category(self, name):
        return {}

    def delete_archived_category(self, name):
        return {}

    def item_images(self, category_id, item_id):
        return {}

    def item_voices(self, category_id, item_id):
        return {}

    def add_item_image(self, category_id, item_id, image_data, image_extension=".png"):
        return {}

    def update_item_image(self, category_id, item_id, image_id, image_data, image_extension=".png"):
        return {}

    def delete_item_image(self, category_id, item_id, image_id):
        return {}

    def set_main_item_image(self, category_id, item_id, image_id):
        return {}

    def set_main_item_voice(self, category_id, item_id, voice_id, language):
        return {}

    def optimize_item_voice(self, category_id, item_id, voice_id):
        return {}

    def add_item_voice(self, category_id, item_id, language, audio_data):
        return {}

    def update_item_voice(self, category_id, item_id, voice_id, audio_data):
        return {}

    def delete_item_voice(self, category_id, item_id, voice_id):
        return {}

    def regenerate_item_voice(self, category_id, item_id, voice_id):
        return {}

    def add_category_voice(self, category_id, language, audio_data):
        return {}

    def update_category_voice(self, category_id, voice_id, audio_data):
        return {}

    def delete_category_voice(self, category_id, voice_id):
        return {}

    def regenerate_category_voice(self, category_id, voice_id):
        return {}


_provider = _ConcreteProvider()


@pytest.mark.parametrize("word,expected", [
    ("hello", "Hello"),
    ("hello world", "HelloWorld"),
    ("RED apple", "RedApple"),
    ("foo_bar", "FooBar"),
    ("foo-bar", "FooBar"),
    ("", ""),
    ("  ", ""),
    ("123abc", "123abc"),
])
def test_to_pascal(word: str, expected: str) -> None:
    assert ContentProvider._to_pascal(word) == expected


@pytest.mark.parametrize("word,expected", [
    ("Hello", "hello"),
    ("HelloWorld", "hello_world"),
    ("AtSchool", "at_school"),
    ("already_snake", "already_snake"),
    ("Foods", "foods"),
    ("", ""),
])
def test_to_snake(word: str, expected: str) -> None:
    assert ContentProvider._to_snake(word) == expected


@pytest.mark.parametrize("category_id,expected", [
    ("C_Foods", "foods"),
    ("C_AtSchool", "at_school"),
    ("C_AtHome", "at_home"),
    ("C_Vegetables", "vegetables"),
    ("C_already_snake", "already_snake"),
])
def test_folder_name_from_category_id(category_id: str, expected: str) -> None:
    assert ContentProvider._folder_name_from_category_id(category_id) == expected


@pytest.mark.parametrize("word,expected", [
    ("apple", "apple"),
    ("Red Apple", "red_apple"),
    ("red  apple", "red_apple"),
    ("  banana  ", "banana"),
    ("_leading", "leading"),
    ("trailing_", "trailing"),
    ("multi___under", "multi_under"),
    ("", ""),
    ("   ", ""),
])
def test_english_word_to_folder_name(word: str, expected: str) -> None:
    assert ContentProvider._english_word_to_folder_name(word) == expected


@pytest.mark.parametrize("word,expected", [
    ("apple", "I_Apple"),
    ("red apple", "I_RedApple"),
    ("  cherry  ", "I_Cherry"),
    ("", ""),
    ("   ", ""),
])
def test_english_word_to_image_id(word: str, expected: str) -> None:
    assert _provider._english_word_to_image_id(word) == expected


# ---------------------------------------------------------------------------
# ContentService — provider management
# ---------------------------------------------------------------------------

def test_replace_providers_keeps_content_providers(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    foreign = ServiceProvider(
        name="other", service_name="x", provider_name="x", provider_category="",
        enabled=True, priority=1, config={},
        config_path=tmp_path / "other.json", base_dir=tmp_path,
    )
    svc = _make_service(tmp_path, [stub, foreign])
    assert svc.providers_snapshot() == [stub]


def test_replace_providers_empty_list(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, [])
    assert not svc.providers_snapshot()


def test_providers_snapshot_returns_copy(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    snap1 = svc.providers_snapshot()
    snap2 = svc.providers_snapshot()
    assert snap1 == snap2
    assert snap1 is not snap2


def test_get_all_providers_includes_disabled(tmp_path: Path) -> None:
    active = _StubProvider(tmp_path, name="a", enabled=True)
    disabled = _StubProvider(tmp_path, name="b", enabled=False)
    svc = _make_service(tmp_path, [active, disabled])
    assert len(svc.get_all_providers()) == 2


def test_get_active_providers_excludes_disabled(tmp_path: Path) -> None:
    active = _StubProvider(tmp_path, name="a", enabled=True)
    disabled = _StubProvider(tmp_path, name="b", enabled=False)
    svc = _make_service(tmp_path, [active, disabled])
    assert svc.get_active_providers() == [active]


# ---------------------------------------------------------------------------
# ContentService — provider selection
# ---------------------------------------------------------------------------

def test_select_provider_raises_when_no_providers(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, [])
    with pytest.raises(ContentProviderUnavailableError):
        svc.list_categories()


def test_select_provider_prefers_active_provider(tmp_path: Path) -> None:
    active = _StubProvider(tmp_path, name="active", enabled=True)
    disabled = _StubProvider(tmp_path, name="disabled", enabled=False)
    svc = _make_service(tmp_path, [disabled, active])
    svc.list_categories()
    assert "list_categories" in active.calls
    assert "list_categories" not in disabled.calls


def test_select_provider_falls_back_to_first_when_all_disabled(tmp_path: Path) -> None:
    first = _StubProvider(tmp_path, name="first", enabled=False)
    second = _StubProvider(tmp_path, name="second", enabled=False)
    svc = _make_service(tmp_path, [first, second])
    svc.list_categories()
    assert "list_categories" in first.calls
    assert "list_categories" not in second.calls


# ---------------------------------------------------------------------------
# ContentService — delegation methods
# ---------------------------------------------------------------------------

def test_list_categories_delegates_to_provider(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.list_categories(include_archived=True, archived_only=False)
    assert result["categories"] == ["cats", "dogs"]
    assert stub.calls["list_categories"] == [(True, False)]


def test_list_categories_archived_only(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path, return_values={"list_categories": {"categories": [], "archived": []}})
    svc = _make_service(tmp_path, [stub])
    result = svc.list_categories(archived_only=True)
    assert stub.calls["list_categories"] == [(False, True)]
    assert result["categories"] == []


def test_category_details_by_name_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.category_details_by_name("foods")
    assert result["name"] == "foods"
    assert stub.calls["category_details"] == [("foods",)]


def test_category_details_by_id_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.category_details_by_id("C_Foods")
    assert result["name"] == "C_Foods"
    assert stub.calls["category_details"] == [("C_Foods",)]


def test_create_category_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.create_category("animals")
    assert result["category"] == "animals"
    assert stub.calls["create_category"] == [("animals",)]


def test_create_item_by_id_delegates_without_name_lookup(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.create_item_by_id("C_Foods", "apple", b"\x89PNG", ".png", generate_voices=False)
    assert result.item == "apple"
    assert stub.calls["create_item"] == [("C_Foods", "apple", b"\x89PNG", ".png", False)]
    assert "category_id_by_name" not in stub.calls


def test_archive_category_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.archive_category("foods")
    assert result["archived"] is True
    assert stub.calls["archive_category"] == [("foods",)]


def test_update_item_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    item_name = LocalizedString({"en": "banana"})
    result = svc.update_item("C_Foods", "I_Apple", item_name)
    assert result["id"] == "I_Apple"
    assert stub.calls["update_item_name"] == [("C_Foods", "I_Apple", item_name)]


def test_update_item_id_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.update_item_id("C_Foods", "I_Apple", "I_GreenApple")
    assert result["id"] == "I_GreenApple"
    assert stub.calls["update_item_id"] == [("C_Foods", "I_Apple", "I_GreenApple")]


def test_remove_item_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.remove_item("C_Foods", "I_Apple")
    assert result["item"] == "I_Apple"
    assert stub.calls["remove_item"] == [("C_Foods", "I_Apple")]


def test_set_main_item_voice_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.set_main_item_voice("C_Foods", "I_Apple", "apple_en_02", "en")
    assert result["item_id"] == "I_Apple"
    assert stub.calls["set_main_item_voice"] == [("C_Foods", "I_Apple", "apple_en_02", "en")]


def test_optimize_item_voice_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.optimize_item_voice("C_Foods", "I_Apple", "apple-v-en")
    assert result["source_voice_id"] == "apple-v-en"
    assert stub.calls["optimize_item_voice"] == [("C_Foods", "I_Apple", "apple-v-en")]


def test_update_category_details_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    name = LocalizedString({"en": "meals"})
    description = LocalizedString({"en": "tasty meals"})
    result = svc.update_category_details("C_Foods", name, description)
    assert result["category"] == "C_Foods"
    assert stub.calls["update_category_details"] == [("C_Foods", name, description)]


def test_update_category_id_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.update_category_id("C_Foods", "C_Meals")
    assert result["category"] == "C_Foods"
    assert stub.calls["update_category_id"] == [("C_Foods", "C_Meals")]


def test_remove_category_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.remove_category("foods")
    assert result["removed"] is True
    assert stub.calls["remove_category"] == [("foods",)]


def test_restore_category_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.restore_category("foods")
    assert result["restored"] is True
    assert stub.calls["restore_category"] == [("foods",)]


def test_delete_archived_category_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.delete_archived_category("foods_1000")
    assert result["deleted"] is True
    assert stub.calls["delete_archived_category"] == [("foods_1000",)]


# ---------------------------------------------------------------------------
# Schemas — Pydantic validation
# ---------------------------------------------------------------------------

def test_create_category_body_requires_name() -> None:
    body = CreateCategoryBody(name="animals")
    assert body.name == "animals"
    with pytest.raises(ValidationError):
        CreateCategoryBody()  # type: ignore[call-arg]


def test_update_category_body_requires_name_and_description() -> None:
    body = UpdateCategoryBody(category_name={"en": "meals"}, description={"en": "tasty meals"})
    assert body.category_name.en == "meals"
    assert body.description.en == "tasty meals"
    assert body.new_category_id is None
    with pytest.raises(ValidationError):
        UpdateCategoryBody(category_name={"en": "meals"})  # type: ignore[call-arg]


def test_update_category_body_accepts_new_category_id() -> None:
    body = UpdateCategoryBody(
        category_name={"en": "meals"}, description={"en": "tasty meals"}, new_category_id="C_Meals",
    )
    assert body.new_category_id == "C_Meals"


def test_restore_category_body_requires_name() -> None:
    body = RestoreCategoryBody(name="animals")
    assert body.name == "animals"


def test_archived_entry_model() -> None:
    entry = ArchivedEntry(base_name="foods", archive_folder="foods_1700000000", archived_at=1700000000)
    assert entry.archived_at == 1700000000


def test_category_list_response_defaults() -> None:
    resp = CategoryListResponse(categories=[
        CategorySummary(id="C_Foods", name={"en": "Foods"}, items_count=3),
        CategorySummary(id="C_Drinks", name={"en": "Drinks"}, items_count=1),
    ])
    assert resp.archived is None
    assert resp.error is None


def test_category_list_response_with_archived() -> None:
    entry = ArchivedEntry(base_name="toys", archive_folder="toys_1000", archived_at=1000)
    resp = CategoryListResponse(categories=[], archived=[entry])
    assert len(resp.archived) == 1  # type: ignore[arg-type]


def test_category_detail_response_fields() -> None:
    resp = CategoryDetailsResponse(
        id="C_Foods",
        name={"en": "Foods", "fr": "Aliments"},
        items=[ItemSummary(id="I_Apple", name={"en": "apple", "fr": "pomme"})],
    )
    dumped = resp.model_dump()
    assert dumped["name"] == {"en": "Foods", "fr": "Aliments"}
    assert dumped["items"] == [
        {"id": "I_Apple", "name": {"en": "apple", "fr": "pomme"}, "thumbnail_uri": None, "images_count": 0}]


def test_create_category_response() -> None:
    resp = CreateCategoryResponse(category="animals", message="Category 'animals' created.")
    assert resp.category == "animals"


def test_create_item_response() -> None:
    resp = CreateItemResponse(
        category="foods", item="apple", folder="apple", image_id="I_Apple", item_id="I_Apple",
        voices_generated=["en"], voice_warnings=[], message="created",
    )
    assert resp.image_id == "I_Apple"


def test_update_category_response() -> None:
    resp = UpdateCategoryResponse(
        category="meals", message="renamed", category_id="C_Foods", old_category_id="C_Foods",
    )
    assert resp.category == "meals"
    assert resp.category_id == resp.old_category_id == "C_Foods"


def test_remove_category_response_optional_fields() -> None:
    resp = RemoveCategoryResponse(name="foods", message="removed", removed=True)
    assert resp.archived is None
    assert resp.archive_folder is None


def test_restore_category_response() -> None:
    resp = RestoreCategoryResponse(restored=True, name="foods", from_archive="foods_1000", message="ok")
    assert resp.restored is True


def test_delete_archived_category_response() -> None:
    resp = DeleteArchivedCategoryResponse(
        deleted=True,
        name="foods",
        archive_folder="foods_1000",
        message="deleted",
    )
    assert resp.deleted is True
    assert resp.archive_folder == "foods_1000"


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

def test_content_error_hierarchy() -> None:
    assert issubclass(ContentProviderUnavailableError, ContentError)
    assert issubclass(InvalidCategoryNameError, ContentError)
    assert issubclass(CategoryNotFoundError, ContentError)
    assert issubclass(CategoryConflictError, ContentError)
