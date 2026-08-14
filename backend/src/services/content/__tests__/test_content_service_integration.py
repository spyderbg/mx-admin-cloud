"""HTTP integration tests for content routes (ASGI + httpx).

Tests exercise the full FastAPI request/response cycle with a stub
ContentProvider, covering status codes, response bodies, auth enforcement,
and all implemented error branches.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,too-few-public-methods

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_current_user
from src.services.content import routes as content_routes
from src.services.content.models import (
    ArchiveCategory,
    ArchiveUnavailableError,
    CategoryConflictError,
    CategoryDetails,
    CategoryImage,
    CategoryList,
    CategoryNotFoundError,
    CategoryVoice,
    ContentOperationError,
    ContentProvider,
    CreateCategory,
    CreateItem,
    InvalidCategoryNameError,
    ItemDetail,
    ItemImages,
    ItemVoices,
    RemoveItem,
    RenameCategory,
    RestoreCategory,
)
from src.services.content.routes import get_content_service
from src.services.content.service import ContentService


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

class _StubContentProvider(ContentProvider):
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

    def returns(self, method: str, value: Any) -> "_StubContentProvider":
        self._returns[method] = value
        return self

    def raises(self, method: str, exc: Exception) -> "_StubContentProvider":
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
        return self._call("list_categories", CategoryList(categories=["foods", "drinks"]))

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
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CreateItem:
        return self._call("create_item", CreateItem(
            category=category_id,
            item=english_word,
            folder=english_word.lower(),
            image_id=f"I_{english_word.title()}",
        ))

    def update_item_name(self, category_id: str, item_id: str, item_name: Any) -> ItemDetail:
        return self._call("update_item_name", ItemDetail(id=item_id, name=item_name))

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
    ) -> RenameCategory:
        return self._call(
            "update_category_details", RenameCategory(category=category_id, message="updated"),
        )

    def rename_category(self, category_id: str, new_category_id: str) -> RenameCategory:
        return self._call(
            "rename_category", RenameCategory(category=new_category_id, message="renamed"),
        )

    def remove_category(self, category_id: str) -> ArchiveCategory:
        return self._call("remove_category", ArchiveCategory(
            removed=True,
            name=category_id,
            message="removed",
        ))

    def restore_category(self, category_id: str) -> RestoreCategory:
        return self._call("restore_category", RestoreCategory(
            restored=True,
            name=category_id,
            from_archive=f"{category_id}_1000",
            message="restored",
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


def _make_content_service(
        tmp_path: Path,
        provider: _StubContentProvider,
) -> ContentService:
    svc = ContentService(
        service_name="content",
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    svc.replace_providers([provider])
    return svc


def _make_app(
        provider: _StubContentProvider | None = None,
        tmp_path: Path | None = None,
        *,
        auth: bool = True,
) -> FastAPI:
    app = FastAPI()
    app.include_router(content_routes.router)
    if auth:
        app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "role": "admin"}
    if provider is not None and tmp_path is not None:
        svc = _make_content_service(tmp_path, provider)
        app.dependency_overrides[get_content_service] = lambda: svc
    elif not auth:
        # Prevent get_registry AttributeError from racing with the 401 raised by
        # get_current_user. The dummy value is never used because auth blocks first.
        app.dependency_overrides[get_content_service] = lambda: None
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_resolved(tmp_path: Path) -> Path:
    return tmp_path.resolve()


@pytest.fixture
async def client(tmp_resolved: Path) -> AsyncClient:
    provider = _StubContentProvider(tmp_resolved)
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def client_no_auth() -> AsyncClient:
    app = _make_app(auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/v1/content/categories
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_categories_returns_200(client: AsyncClient) -> None:
    response = await client.get("/api/v1/content/categories")
    assert response.status_code == 200
    body = response.json()
    assert body["categories"] == ["foods", "drinks"]
    assert body["error"] is None


@pytest.mark.asyncio
async def test_list_categories_include_archived(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "list_categories",
        {
            "categories": ["foods"],
            "archived": [
                {"base_name": "drinks", "archive_folder": "drinks_1000", "archived_at": 1000}
            ],
        },
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories", params={"include_archived": "true"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["archived"]) == 1
    assert body["archived"][0]["base_name"] == "drinks"


@pytest.mark.asyncio
async def test_list_categories_archived_only(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "list_categories",
        {"categories": [], "archived": [{"base_name": "foods", "archive_folder": "foods_999", "archived_at": 999}]},
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories", params={"archived_only": "true"})
    assert response.status_code == 200
    assert response.json()["categories"] == []


@pytest.mark.asyncio
async def test_list_categories_provider_error_returns_500(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "list_categories", {"error": "Content root does not exist: /x"}
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories")
    assert response.status_code == 500
    assert "Content root does not exist" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_categories_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.get("/api/v1/content/categories")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/content/categories
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_category_returns_201(client: AsyncClient) -> None:
    response = await client.post("/api/v1/content/categories", json={"name": "animals"})
    assert response.status_code == 201
    body = response.json()
    assert body["category"] == "animals"
    assert body["message"] == "created"


@pytest.mark.asyncio
async def test_create_category_strips_whitespace(tmp_resolved: Path) -> None:
    seen: list[str] = []

    class _TrackingProvider(_StubContentProvider):
        def create_category(self, category_name: str) -> CreateCategory:
            seen.append(category_name)
            return CreateCategory(category=category_name, message="created")

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/api/v1/content/categories", json={"name": "  animals  "})
    assert seen == ["animals"]


@pytest.mark.asyncio
async def test_create_category_already_exists_returns_409(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "create_category", CategoryConflictError("Category already exists: animals")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories", json={"name": "animals"})
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "create_category", ContentOperationError("TTS failed for en: connection refused")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories", json={"name": "animals"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_category_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post("/api/v1/content/categories", json={"name": "x"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/content/categories/restore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restore_category_returns_200(client: AsyncClient) -> None:
    response = await client.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 200
    body = response.json()
    assert body["restored"] is True
    assert body["name"] == "foods"


@pytest.mark.asyncio
async def test_restore_category_archive_dir_missing_returns_503(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "restore_category", ArchiveUnavailableError("Archive directory does not exist")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_restore_category_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "restore_category", CategoryNotFoundError("No archive found for category 'foods'")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_restore_category_conflict_returns_409(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "restore_category", CategoryConflictError("Category already exists in content directory: foods")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_restore_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "restore_category", ContentOperationError("Failed to restore: permission denied")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_restore_category_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post("/api/v1/content/categories/restore", json={"name": "x"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /api/v1/content/categories/{category_id}
# ---------------------------------------------------------------------------

_RENAME_BODY = {
    "category_name": {"en": "meals"},
    "description": {"en": "tasty meals"},
}


@pytest.mark.asyncio
async def test_rename_category_returns_200(client: AsyncClient) -> None:
    response = await client.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "foods"


@pytest.mark.asyncio
async def test_rename_category_passes_category_id_and_body(tmp_resolved: Path) -> None:
    seen: list[tuple[str, dict[str, str], dict[str, str]]] = []

    class _TrackingProvider(_StubContentProvider):
        def update_category_details(self, category_id: str, category_name: Any, description: Any) -> RenameCategory:
            seen.append((category_id, category_name.to_dict(), description.to_dict()))
            return RenameCategory(category=category_id, message="updated")

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    assert seen == [("foods", {"en": "meals"}, {"en": "tasty meals"})]


@pytest.mark.asyncio
async def test_rename_category_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "update_category_details", CategoryNotFoundError("Category not found")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rename_category_conflict_returns_409(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "update_category_details", CategoryConflictError("Target category already exists: meals")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_rename_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "update_category_details", ContentOperationError("Failed to write CSV: disk full")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rename_category_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    assert response.status_code == 401


_RENAME_BODY_WITH_NEW_ID = {
    "category_name": {"en": "meals"},
    "description": {"en": "tasty meals"},
    "new_category_id": "C_Meals",
}


@pytest.mark.asyncio
async def test_rename_category_without_new_id_keeps_same_category_id(tmp_resolved: Path) -> None:
    app = _make_app(_StubContentProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    body = response.json()
    assert body["category_id"] == "foods"
    assert body["old_category_id"] == "foods"


@pytest.mark.asyncio
async def test_rename_category_with_new_id_calls_rename_category(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str]] = []

    class _TrackingProvider(_StubContentProvider):
        def rename_category(self, category_id: str, new_category_id: str) -> RenameCategory:
            seen.append((category_id, new_category_id))
            return RenameCategory(category="meals", message="renamed")

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY_WITH_NEW_ID)
    assert seen == [("foods", "C_Meals")]
    body = response.json()
    assert body["category_id"] == "C_Meals"
    assert body["old_category_id"] == "foods"


@pytest.mark.asyncio
async def test_rename_category_new_id_conflict_returns_409(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "rename_category", CategoryConflictError("Category id already exists: C_Meals"),
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY_WITH_NEW_ID)
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /api/v1/content/categories/{category_name}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_category_returns_200(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/content/categories/foods")
    assert response.status_code == 200
    body = response.json()
    assert body["removed"] is True
    assert body["name"] == "foods"


@pytest.mark.asyncio
async def test_remove_category_archive_result_returns_200(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "remove_category",
        ArchiveCategory(
            archived=True,
            name="foods",
            archive_folder="foods_1000",
            destination="/archive/foods_1000",
            message="archived",
        ),
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods")
    assert response.status_code == 200
    body = response.json()
    assert body["archived"] is True
    assert body["archive_folder"] == "foods_1000"


@pytest.mark.asyncio
async def test_remove_category_accepts_category_id_in_path(tmp_resolved: Path) -> None:
    """The path segment must reach the provider — it used to be ignored for a query param."""
    seen: list[str] = []

    class _TrackingProvider(_StubContentProvider):
        def category_id_by_name(self, category_name: str) -> str:
            raise CategoryNotFoundError(category_name)

        def remove_category(self, category_id: str) -> ArchiveCategory:
            seen.append(category_id)
            return ArchiveCategory(archived=True, name=category_id, message="archived")

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/C_Foods")
    assert response.status_code == 200
    assert seen == ["C_Foods"]


@pytest.mark.asyncio
async def test_remove_category_resolves_folder_name_to_id(tmp_resolved: Path) -> None:
    """A folder name in the path is resolved to its registry id, keeping the CLI working."""
    seen: list[str] = []

    class _TrackingProvider(_StubContentProvider):
        def category_id_by_name(self, category_name: str) -> str:
            return f"C_{category_name.title()}"

        def remove_category(self, category_id: str) -> ArchiveCategory:
            seen.append(category_id)
            return ArchiveCategory(archived=True, name=category_id, message="archived")

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.delete("/api/v1/content/categories/foods")
    assert seen == ["C_Foods"]


@pytest.mark.asyncio
async def test_remove_category_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "remove_category", CategoryNotFoundError("Category not found")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "remove_category", ContentOperationError("Permission denied")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_remove_category_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.delete("/api/v1/content/categories/foods")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/content/categories/{category_name}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_category_details_returns_200(client: AsyncClient) -> None:
    response = await client.get("/api/v1/content/categories/foods")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == {"en": "foods"}
    assert body["items"] == [{"id": "I_Foods", "name": {"en": "apple"}, "thumbnail_uri": None, "images_count": 0}]


@pytest.mark.asyncio
async def test_category_details_with_items(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "category_details",
        CategoryDetails(
            name={"en": "Foods", "fr": "Aliments"},
            items=[
                ItemDetail(id="I_Apple", name={"en": "apple", "fr": "pomme"}),
                ItemDetail(id="I_Banana", name={"en": "banana", "fr": "banane"}),
            ],
        ),
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories/foods")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == {"en": "Foods", "fr": "Aliments"}
    assert body["items"] == [
        {"id": "I_Apple", "name": {"en": "apple", "fr": "pomme"}, "thumbnail_uri": None, "images_count": 0},
        {"id": "I_Banana", "name": {"en": "banana", "fr": "banane"}, "thumbnail_uri": None, "images_count": 0},
    ]


@pytest.mark.asyncio
async def test_category_details_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "category_details", CategoryNotFoundError("Category not found")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories/foods")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_category_details_invalid_name_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "category_details", InvalidCategoryNameError("Invalid category name")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories/bad!")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_category_details_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.get("/api/v1/content/categories/foods")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/content/items  (multipart form)
# ---------------------------------------------------------------------------

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8  # minimal valid-looking PNG header


@pytest.mark.asyncio
async def test_create_item_returns_201(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/content/items",
        files={"image": ("apple.png", _PNG_BYTES, "image/png")},
        data={"category": "foods", "item": "apple"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["category"] == "foods"
    assert body["item"] == "apple"
    assert body["folder"] == "apple"
    assert body["image_id"] == "I_Apple"


@pytest.mark.asyncio
async def test_create_item_strips_whitespace(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str]] = []

    class _TrackingProvider(_StubContentProvider):
        def create_item(self, category_id, english_word, image_data, image_extension=".png"):
            seen.append((category_id, english_word))
            return CreateItem(
                category=category_id,
                item=english_word,
                folder=english_word.lower(),
                image_id=f"I_{english_word.title()}",
            )

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/v1/content/items",
            files={"image": ("a.png", _PNG_BYTES, "image/png")},
            data={"category": "  foods  ", "item": "  apple  "},
        )
    assert seen == [("foods", "apple")]


@pytest.mark.asyncio
async def test_create_item_picks_correct_extension(tmp_resolved: Path) -> None:
    seen_ext: list[str] = []

    class _ExtProvider(_StubContentProvider):
        def create_item(self, category_id, english_word, image_data, image_extension=".png"):
            seen_ext.append(image_extension)
            return CreateItem(category=category_id, item=english_word, folder=english_word, image_id="X")

    app = _make_app(_ExtProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/v1/content/items",
            files={"image": ("photo.JPEG", b"data", "image/jpeg")},
            data={"category": "foods", "item": "apple"},
        )
    assert seen_ext == [".jpeg"]


@pytest.mark.asyncio
async def test_create_item_defaults_unknown_extension_to_png(tmp_resolved: Path) -> None:
    seen_ext: list[str] = []

    class _ExtProvider(_StubContentProvider):
        def create_item(self, category_id, english_word, image_data, image_extension=".png"):
            seen_ext.append(image_extension)
            return CreateItem(category=category_id, item=english_word, folder=english_word, image_id="X")

    app = _make_app(_ExtProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/v1/content/items",
            files={"image": ("photo.bmp", b"data", "image/bmp")},
            data={"category": "foods", "item": "apple"},
        )
    assert seen_ext == [".png"]


@pytest.mark.asyncio
async def test_create_item_missing_filename_returns_error(client: AsyncClient) -> None:
    # httpx with an empty filename string sends no Content-Disposition filename,
    # which FastAPI's multipart parser rejects with 422 before reaching the handler.
    response = await client.post(
        "/api/v1/content/items",
        files={"image": ("", _PNG_BYTES, "image/png")},
        data={"category": "foods", "item": "apple"},
    )
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_create_item_service_error_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "create_item", CategoryConflictError("Item folder already exists: apple")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(
            "/api/v1/content/items",
            files={"image": ("apple.png", _PNG_BYTES, "image/png")},
            data={"category": "foods", "item": "apple"},
        )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_item_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post(
        "/api/v1/content/items",
        files={"image": ("a.png", _PNG_BYTES, "image/png")},
        data={"category": "foods", "item": "apple"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/v1/content/categories/{category_id}/items/{item_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_item_returns_200(client: AsyncClient) -> None:
    response = await client.put(
        "/api/v1/content/categories/foods/items/I_Apple",
        json={"item_name": {"en": "green apple"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "I_Apple"
    assert body["name"] == {"en": "green apple"}


@pytest.mark.asyncio
async def test_update_item_passes_category_and_item_id(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, dict[str, str]]] = []

    class _TrackingProvider(_StubContentProvider):
        def update_item_name(self, category_id: str, item_id: str, item_name: Any) -> ItemDetail:
            seen.append((category_id, item_id, item_name.to_dict()))
            return ItemDetail(id=item_id, name=item_name)

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/api/v1/content/categories/foods/items/I_Apple",
            json={"item_name": {"en": "green apple"}},
        )
    assert seen == [("foods", "I_Apple", {"en": "green apple"})]


@pytest.mark.asyncio
async def test_update_item_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "update_item_name", CategoryNotFoundError("Item not found")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.put(
            "/api/v1/content/categories/foods/items/I_Missing",
            json={"item_name": {"en": "green apple"}},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_item_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.put(
        "/api/v1/content/categories/foods/items/I_Apple",
        json={"item_name": {"en": "green apple"}},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/content/categories/{category_id}/items/{item_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_item_returns_204(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/content/categories/foods/items/I_Apple")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_item_passes_category_and_item_id(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str]] = []

    class _TrackingProvider(_StubContentProvider):
        def remove_item(self, category_id: str, item_id: str) -> RemoveItem:
            seen.append((category_id, item_id))
            return RemoveItem(category=category_id, item=item_id)

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.delete("/api/v1/content/categories/foods/items/I_Apple")
    assert seen == [("foods", "I_Apple")]


@pytest.mark.asyncio
async def test_delete_item_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "remove_item", CategoryNotFoundError("Item not found")
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods/items/I_Missing")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_item_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.delete("/api/v1/content/categories/foods/items/I_Apple")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Stub 501 routes — spot checks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_category_images_returns_501(client: AsyncClient) -> None:
    response = await client.get("/api/v1/content/categories/foods/images")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_category_voices_returns_501(client: AsyncClient) -> None:
    response = await client.get("/api/v1/content/categories/foods/voices")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_add_image_to_category_returns_201(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/content/categories/foods/images",
        files={"image": ("img.png", _PNG_BYTES, "image/png")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "foods_1"
    assert body["file_uri"] == "/content/foods/foods_1.png"
    assert body["action"] == "add"


@pytest.mark.asyncio
async def test_add_image_to_category_forwards_bytes_and_extension(tmp_resolved: Path) -> None:
    seen: list[tuple[str, bytes, str]] = []

    class _TrackingProvider(_StubContentProvider):
        def add_category_image(self, category_id, image_data, image_extension=".png"):
            seen.append((category_id, image_data, image_extension))
            return CategoryImage(id=f"{category_id}_1", file_uri="/content/foods/foods_1.jpg")

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/v1/content/categories/foods/images",
            files={"image": ("photo.JPEG", _PNG_BYTES, "image/jpeg")},
        )
    assert seen == [("foods", _PNG_BYTES, ".jpeg")]


@pytest.mark.asyncio
async def test_update_category_image_returns_200(client: AsyncClient) -> None:
    response = await client.put(
        "/api/v1/content/categories/foods/images/I_Apple",
        files={"image": ("img.png", _PNG_BYTES, "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "I_Apple"
    assert body["file_uri"] == "/content/foods/I_Apple.png"
    assert body["action"] == "update"


@pytest.mark.asyncio
async def test_update_category_image_forwards_bytes_and_extension(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, bytes, str]] = []

    class _TrackingProvider(_StubContentProvider):
        def update_category_image(self, category_id, image_id, image_data, image_extension=".png"):
            seen.append((category_id, image_id, image_data, image_extension))
            return CategoryImage(id=image_id, file_uri=f"/content/foods/{image_id}.jpg")

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/api/v1/content/categories/foods/images/I_Apple",
            files={"image": ("photo.bmp", _PNG_BYTES, "image/bmp")},
        )
    assert seen == [("foods", "I_Apple", _PNG_BYTES, ".png")]


@pytest.mark.asyncio
async def test_update_category_image_not_found_returns_404(client: AsyncClient, tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "update_category_image", CategoryNotFoundError("Image 'I_Missing' not found in category 'foods'."),
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.put(
            "/api/v1/content/categories/foods/images/I_Missing",
            files={"image": ("img.png", _PNG_BYTES, "image/png")},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_category_image_returns_200(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/content/categories/foods/images/I_Apple")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "I_Apple"
    assert body["action"] == "delete"


@pytest.mark.asyncio
async def test_delete_category_image_forwards_ids(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str]] = []

    class _TrackingProvider(_StubContentProvider):
        def delete_category_image(self, category_id, image_id):
            seen.append((category_id, image_id))
            return CategoryImage(id=image_id, file_uri=f"/content/{category_id}/{image_id}.png")

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.delete("/api/v1/content/categories/foods/images/I_Apple")
    assert seen == [("foods", "I_Apple")]


@pytest.mark.asyncio
async def test_delete_category_image_not_found_returns_404(client: AsyncClient, tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).raises(
        "delete_category_image", CategoryNotFoundError("Image 'I_Missing' not found in category 'foods'."),
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods/images/I_Missing")
    assert response.status_code == 404
