"""Unit tests for the content service — models, service orchestration, and schemas
(no HTTP calls, no external APIs).

FileSystemContentProvider tests live in test_content_filesystem_provider.py.
"""

# pylint: disable=missing-function-docstring,too-few-public-methods,redefined-outer-name,protected-access

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

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
    ItemSummary,
    RemoveCategoryResponse,
    RenameCategoryBody,
    RenameCategoryResponse,
    RestoreCategoryBody,
    RestoreCategoryResponse,
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
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CreateItem:
        self._record("create_item", category_id, english_word, image_data, image_extension)
        return self._returns.get("create_item", CreateItem(
            category=category_id,
            item=english_word,
            folder=english_word.lower(),
            image_id=f"I_{english_word.title()}",
        ))

    def archive_category(self, category_id: str) -> dict[str, Any]:
        self._record("archive_category", category_id)
        return self._returns.get("archive_category", {
            "archived": True,
            "name": category_id,
            "archive_folder": f"{category_id}_1000",
            "destination": f"/archive/{category_id}_1000",
            "message": "archived",
        })

    def rename_category(self, category_id: str, new_name: str) -> dict[str, Any]:
        self._record("rename_category", category_id, new_name)
        return self._returns.get("rename_category", {
            "category": new_name,
            "message": f"renamed {category_id} to {new_name}",
        })

    def remove_category(self, category_id: str) -> dict[str, Any]:
        self._record("remove_category", category_id)
        return self._returns.get("remove_category", {
            "removed": True,
            "name": category_id,
            "message": "removed",
        })

    def restore_category(self, category_id: str) -> dict[str, Any]:
        self._record("restore_category", category_id)
        return self._returns.get("restore_category", {
            "restored": True,
            "name": category_id,
            "from_archive": f"{category_id}_1000",
            "message": "restored",
        })

    def item_images(self, category_id: str, item_id: str) -> dict[str, Any]:
        self._record("item_images", category_id, item_id)
        return self._returns.get("item_images", {"item_id": item_id, "images": [], "thumbnail_uri": None})

    def item_voices(self, category_id: str, item_id: str) -> dict[str, Any]:
        self._record("item_voices", category_id, item_id)
        return self._returns.get("item_voices", {"item_id": item_id, "voices": []})


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

    def create_item(self, category_id, english_word, image_data, image_extension=".png"):
        return {}

    def archive_category(self, category_id):
        return {}

    def rename_category(self, category_id, new_name):
        return {}

    def remove_category(self, category_id):
        return {}

    def restore_category(self, category_id):
        return {}

    def item_images(self, category_id, item_id):
        return {}

    def item_voices(self, category_id, item_id):
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


def test_create_item_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.create_item("foods", "apple", b"\x89PNG", ".png")
    assert result.item == "apple"
    assert stub.calls["create_item"] == [("foods", "apple", b"\x89PNG", ".png")]


def test_archive_category_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.archive_category("foods")
    assert result["archived"] is True
    assert stub.calls["archive_category"] == [("foods",)]


def test_rename_category_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.rename_category("foods", "meals")
    assert result["category"] == "meals"
    assert stub.calls["rename_category"] == [("foods", "meals")]


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


# ---------------------------------------------------------------------------
# Schemas — Pydantic validation
# ---------------------------------------------------------------------------

def test_create_category_body_requires_name() -> None:
    body = CreateCategoryBody(name="animals")
    assert body.name == "animals"
    with pytest.raises(ValidationError):
        CreateCategoryBody()  # type: ignore[call-arg]


def test_rename_category_body_requires_name() -> None:
    body = RenameCategoryBody(name="meals")
    assert body.name == "meals"


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
    assert dumped["items"] == [{"id": "I_Apple", "name": {"en": "apple", "fr": "pomme"}, "thumbnail_uri": None, "images_count": 0}]


def test_create_category_response() -> None:
    resp = CreateCategoryResponse(category="animals", message="Category 'animals' created.")
    assert resp.category == "animals"


def test_create_item_response() -> None:
    resp = CreateItemResponse(category="foods", item="apple", folder="apple", image_id="I_Apple")
    assert resp.image_id == "I_Apple"


def test_rename_category_response() -> None:
    resp = RenameCategoryResponse(category="meals", message="renamed")
    assert resp.category == "meals"


def test_remove_category_response_optional_fields() -> None:
    resp = RemoveCategoryResponse(name="foods", message="removed", removed=True)
    assert resp.archived is None
    assert resp.archive_folder is None


def test_restore_category_response() -> None:
    resp = RestoreCategoryResponse(restored=True, name="foods", from_archive="foods_1000", message="ok")
    assert resp.restored is True


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

def test_content_error_hierarchy() -> None:
    assert issubclass(ContentProviderUnavailableError, ContentError)
    assert issubclass(InvalidCategoryNameError, ContentError)
    assert issubclass(CategoryNotFoundError, ContentError)
    assert issubclass(CategoryConflictError, ContentError)
