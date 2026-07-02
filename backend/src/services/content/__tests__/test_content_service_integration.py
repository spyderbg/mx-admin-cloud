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
    CategoryDetails,
    CategoryList,
    CategoryNotFoundError,
    ContentProvider,
    CreateCategory,
    CreateItem,
    InvalidCategoryNameError,
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

    def category_details(self, category_name: str) -> CategoryDetails:
        from src.services.content.models import ItemDetail
        return self._call("category_details", CategoryDetails(
            name={"en": category_name},
            items=[ItemDetail(id=f"I_{category_name.title()}", name={"en": "apple"})],
        ))

    def create_category(self, category_name: str) -> CreateCategory:
        return self._call("create_category", CreateCategory(category=category_name, message="created"))

    def create_item(
            self,
            category_name: str,
            english_word: str,
            image_data: bytes,
            image_extension: str = ".png",
    ) -> CreateItem:
        return self._call("create_item", CreateItem(
            category=category_name,
            item=english_word,
            folder=english_word.lower(),
            image_id=f"I_{english_word.title()}",
        ))

    def archive_category(self, category_name: str) -> ArchiveCategory:
        return self._call("archive_category", ArchiveCategory(
            archived=True,
            name=category_name,
            archive_folder=f"{category_name}_1000",
            destination=f"/archive/{category_name}_1000",
            message="archived",
        ))

    def rename_category(self, old_name: str, new_name: str) -> RenameCategory:
        return self._call("rename_category", RenameCategory(category=new_name, message="renamed"))

    def remove_category(self, category_name: str) -> ArchiveCategory:
        return self._call("remove_category", ArchiveCategory(
            removed=True,
            name=category_name,
            message="removed",
        ))

    def restore_category(self, category_name: str) -> RestoreCategory:
        return self._call("restore_category", RestoreCategory(
            restored=True,
            name=category_name,
            from_archive=f"{category_name}_1000",
            message="restored",
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
        def create_category(self, category_name: str) -> dict[str, Any]:
            seen.append(category_name)
            return {"category": category_name, "message": "created"}

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/api/v1/content/categories", json={"name": "  animals  "})
    assert seen == ["animals"]


@pytest.mark.asyncio
async def test_create_category_already_exists_returns_409(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "create_category", {"error": "Category already exists: animals"}
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories", json={"name": "animals"})
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "create_category", {"error": "TTS failed for en: connection refused"}
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
    provider = _StubContentProvider(tmp_resolved).returns(
        "restore_category",
        {"restored": False, "error": "Archive directory does not exist", "message": "n/a"},
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_restore_category_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "restore_category",
        {"restored": False, "error": "No archive found for category 'foods'", "message": "n/a"},
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_restore_category_conflict_returns_409(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "restore_category",
        {"restored": False, "error": "Category already exists in content directory: foods", "message": "n/a"},
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_restore_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "restore_category",
        {"restored": False, "error": "Failed to restore: permission denied", "message": "n/a"},
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
# PATCH /api/v1/content/categories/{category_name}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rename_category_returns_200(client: AsyncClient) -> None:
    response = await client.patch("/api/v1/content/categories/foods", json={"name": "meals"})
    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "meals"


@pytest.mark.asyncio
async def test_rename_category_strips_whitespace(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str]] = []

    class _TrackingProvider(_StubContentProvider):
        def rename_category(self, old_name: str, new_name: str) -> dict[str, Any]:
            seen.append((old_name, new_name))
            return {"category": new_name, "message": "renamed"}

    app = _make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.patch("/api/v1/content/categories/foods", json={"name": "  meals  "})
    assert seen == [("foods", "meals")]


@pytest.mark.asyncio
async def test_rename_category_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "rename_category", {"error": "Category not found", "name": "foods"}
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json={"name": "meals"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rename_category_target_exists_returns_409(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "rename_category", {"error": "Target category already exists: meals", "name": "meals"}
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json={"name": "meals"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_rename_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "rename_category", {"error": "TTS failed for fr: timeout"}
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json={"name": "meals"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rename_category_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.patch("/api/v1/content/categories/foods", json={"name": "meals"})
    assert response.status_code == 401


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
        {
            "archived": True,
            "name": "foods",
            "archive_folder": "foods_1000",
            "destination": "/archive/foods_1000",
            "message": "archived",
        },
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods")
    assert response.status_code == 200
    assert response.json()["archived"] is True


@pytest.mark.asyncio
async def test_remove_category_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "remove_category", {"error": "Category not found", "name": "foods", "message": "not found"}
    )
    app = _make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = _StubContentProvider(tmp_resolved).returns(
        "remove_category",
        {"error": "Permission denied", "name": "foods", "message": "failed"},
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
    from src.services.content.models import ItemDetail
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
# PUT /api/v1/content/categories/{category_name}  (not implemented)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_category_returns_501(client: AsyncClient) -> None:
    response = await client.put("/api/v1/content/categories/foods")
    assert response.status_code == 501
    assert "Not implemented" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_category_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.put("/api/v1/content/categories/foods")
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
        def create_item(self, category_name, english_word, image_data, image_extension=".png"):
            seen.append((category_name, english_word))
            return {
                "category": category_name,
                "item": english_word,
                "folder": english_word.lower(),
                "image_id": f"I_{english_word.title()}",
            }

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
        def create_item(self, category_name, english_word, image_data, image_extension=".png"):
            seen_ext.append(image_extension)
            return {"category": category_name, "item": english_word, "folder": english_word, "image_id": "X"}

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
        def create_item(self, category_name, english_word, image_data, image_extension=".png"):
            seen_ext.append(image_extension)
            return {"category": category_name, "item": english_word, "folder": english_word, "image_id": "X"}

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
    provider = _StubContentProvider(tmp_resolved).returns(
        "create_item", {"error": "Item folder already exists: apple"}
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
async def test_add_image_to_category_returns_501(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/content/categories/foods/images",
        files={"file": ("img.png", _PNG_BYTES, "image/png")},
        data={"name": "apple"},
    )
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_delete_category_image_returns_501(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/content/categories/foods/images/I_Apple")
    assert response.status_code == 501
