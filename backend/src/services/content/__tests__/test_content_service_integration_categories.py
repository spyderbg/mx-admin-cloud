"""HTTP integration tests for the content category routes (ASGI + httpx): categories, and
their images and voices.

Tests exercise the full FastAPI request/response cycle with a stub
ContentProvider (content_http_helpers.py; fixtures in conftest.py), covering status codes,
response bodies, auth enforcement, and all implemented error branches.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,too-few-public-methods,too-many-public-methods

from __future__ import annotations

from pathlib import Path
from typing import Any
import pytest

from httpx import ASGITransport, AsyncClient

from src.services.content.models import (
    ArchiveCategory,
    ArchiveUnavailableError,
    CategoryConflictError,
    CategoryDetails,
    CategoryImage,
    CategoryNotFoundError,
    ContentOperationError,
    CreateCategory,
    DeleteArchivedCategory,
    InvalidCategoryNameError,
    ItemDetail,
    RenameCategory,
)
from src.services.content.__tests__.content_http_helpers import PNG_BYTES, StubContentProvider, make_app


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
    provider = StubContentProvider(tmp_resolved).returns(
        "list_categories",
        {
            "categories": ["foods"],
            "archived": [
                {"base_name": "drinks", "archive_folder": "drinks_1000", "archived_at": 1000}
            ],
        },
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories", params={"include_archived": "true"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["archived"]) == 1
    assert body["archived"][0]["base_name"] == "drinks"


@pytest.mark.asyncio
async def test_list_categories_archived_only(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).returns(
        "list_categories",
        {"categories": [], "archived": [{"base_name": "foods", "archive_folder": "foods_999", "archived_at": 999}]},
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories", params={"archived_only": "true"})
    assert response.status_code == 200
    assert response.json()["categories"] == []


@pytest.mark.asyncio
async def test_list_categories_provider_error_returns_500(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).returns(
        "list_categories", {"error": "Content root does not exist: /x"}
    )
    app = make_app(provider, tmp_resolved)
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

    class _TrackingProvider(StubContentProvider):
        def create_category(self, category_name: str) -> CreateCategory:
            seen.append(category_name)
            return CreateCategory(category=category_name, message="created")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/api/v1/content/categories", json={"name": "  animals  "})
    assert seen == ["animals"]


@pytest.mark.asyncio
async def test_create_category_already_exists_returns_409(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "create_category", CategoryConflictError("Category already exists: animals")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories", json={"name": "animals"})
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "create_category", ContentOperationError("TTS failed for en: connection refused")
    )
    app = make_app(provider, tmp_resolved)
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
    provider = StubContentProvider(tmp_resolved).raises(
        "restore_category", ArchiveUnavailableError("Archive directory does not exist")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_restore_category_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "restore_category", CategoryNotFoundError("No archive found for category 'foods'")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_restore_category_conflict_returns_409(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "restore_category", CategoryConflictError("Category already exists in content directory: foods")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_restore_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "restore_category", ContentOperationError("Failed to restore: permission denied")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/content/categories/restore", json={"name": "foods"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_restore_category_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post("/api/v1/content/categories/restore", json={"name": "x"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/content/categories/archive/{archive_name}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_archived_category_returns_200(tmp_resolved: Path) -> None:
    seen: list[str] = []

    class _TrackingProvider(StubContentProvider):
        def delete_archived_category(self, name: str) -> DeleteArchivedCategory:
            seen.append(name)
            return DeleteArchivedCategory(
                deleted=True,
                name="foods",
                archive_folder=name,
                message="deleted",
            )

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/archive/foods_1000")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["archive_folder"] == "foods_1000"
    assert seen == ["foods_1000"]


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

    class _TrackingProvider(StubContentProvider):
        def update_category_details(self, category_id: str, category_name: Any, description: Any) -> RenameCategory:
            seen.append((category_id, category_name.to_dict(), description.to_dict()))
            return RenameCategory(category=category_id, message="updated")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    assert seen == [("foods", {"en": "meals"}, {"en": "tasty meals"})]


@pytest.mark.asyncio
async def test_rename_category_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "update_category_details", CategoryNotFoundError("Category not found")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rename_category_conflict_returns_409(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "update_category_details", CategoryConflictError("Target category already exists: meals")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_rename_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "update_category_details", ContentOperationError("Failed to write CSV: disk full")
    )
    app = make_app(provider, tmp_resolved)
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
    app = make_app(StubContentProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY)
    body = response.json()
    assert body["category_id"] == "foods"
    assert body["old_category_id"] == "foods"


@pytest.mark.asyncio
async def test_rename_category_with_new_id_calls_rename_category(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str]] = []

    class _TrackingProvider(StubContentProvider):
        def rename_category(self, category_id: str, new_category_id: str) -> RenameCategory:
            seen.append((category_id, new_category_id))
            return RenameCategory(category="meals", message="renamed")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.patch("/api/v1/content/categories/foods", json=_RENAME_BODY_WITH_NEW_ID)
    assert seen == [("foods", "C_Meals")]
    body = response.json()
    assert body["category_id"] == "C_Meals"
    assert body["old_category_id"] == "foods"


@pytest.mark.asyncio
async def test_rename_category_new_id_conflict_returns_409(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "rename_category", CategoryConflictError("Category id already exists: C_Meals"),
    )
    app = make_app(provider, tmp_resolved)
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
    provider = StubContentProvider(tmp_resolved).returns(
        "remove_category",
        ArchiveCategory(
            archived=True,
            name="foods",
            archive_folder="foods_1000",
            destination="/archive/foods_1000",
            message="archived",
        ),
    )
    app = make_app(provider, tmp_resolved)
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

    class _TrackingProvider(StubContentProvider):
        def category_id_by_name(self, category_name: str) -> str:
            raise CategoryNotFoundError(category_name)

        def remove_category(self, category_id: str) -> ArchiveCategory:
            seen.append(category_id)
            return ArchiveCategory(archived=True, name=category_id, message="archived")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/C_Foods")
    assert response.status_code == 200
    assert seen == ["C_Foods"]


@pytest.mark.asyncio
async def test_remove_category_resolves_folder_name_to_id(tmp_resolved: Path) -> None:
    """A folder name in the path is resolved to its registry id, keeping the CLI working."""
    seen: list[str] = []

    class _TrackingProvider(StubContentProvider):
        def category_id_by_name(self, category_name: str) -> str:
            return f"C_{category_name.title()}"

        def remove_category(self, category_id: str) -> ArchiveCategory:
            seen.append(category_id)
            return ArchiveCategory(archived=True, name=category_id, message="archived")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.delete("/api/v1/content/categories/foods")
    assert seen == ["C_Foods"]


@pytest.mark.asyncio
async def test_remove_category_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "remove_category", CategoryNotFoundError("Category not found")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_category_generic_error_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "remove_category", ContentOperationError("Permission denied")
    )
    app = make_app(provider, tmp_resolved)
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
    provider = StubContentProvider(tmp_resolved).returns(
        "category_details",
        CategoryDetails(
            name={"en": "Foods", "fr": "Aliments"},
            items=[
                ItemDetail(id="I_Apple", name={"en": "apple", "fr": "pomme"}),
                ItemDetail(id="I_Banana", name={"en": "banana", "fr": "banane"}),
            ],
        ),
    )
    app = make_app(provider, tmp_resolved)
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
    provider = StubContentProvider(tmp_resolved).raises(
        "category_details", CategoryNotFoundError("Category not found")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories/foods")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_category_details_invalid_name_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "category_details", InvalidCategoryNameError("Invalid category name")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/v1/content/categories/bad!")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_category_details_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.get("/api/v1/content/categories/foods")
    assert response.status_code == 401


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Category images and voices
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
        files={"image": ("img.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "foods_1"
    assert body["file_uri"] == "/content/foods/foods_1.png"
    assert body["action"] == "add"


@pytest.mark.asyncio
async def test_add_image_to_category_forwards_bytes_and_extension(tmp_resolved: Path) -> None:
    seen: list[tuple[str, bytes, str]] = []

    class _TrackingProvider(StubContentProvider):
        def add_category_image(self, category_id, image_data, image_extension=".png"):
            seen.append((category_id, image_data, image_extension))
            return CategoryImage(id=f"{category_id}_1", file_uri="/content/foods/foods_1.jpg")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/v1/content/categories/foods/images",
            files={"image": ("photo.JPEG", PNG_BYTES, "image/jpeg")},
        )
    assert seen == [("foods", PNG_BYTES, ".jpeg")]


@pytest.mark.asyncio
async def test_update_category_image_returns_200(client: AsyncClient) -> None:
    response = await client.put(
        "/api/v1/content/categories/foods/images/I_Apple",
        files={"image": ("img.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "I_Apple"
    assert body["file_uri"] == "/content/foods/I_Apple.png"
    assert body["action"] == "update"


@pytest.mark.asyncio
async def test_update_category_image_forwards_bytes_and_extension(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, bytes, str]] = []

    class _TrackingProvider(StubContentProvider):
        def update_category_image(self, category_id, image_id, image_data, image_extension=".png"):
            seen.append((category_id, image_id, image_data, image_extension))
            return CategoryImage(id=image_id, file_uri=f"/content/foods/{image_id}.jpg")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/api/v1/content/categories/foods/images/I_Apple",
            files={"image": ("photo.bmp", PNG_BYTES, "image/bmp")},
        )
    assert seen == [("foods", "I_Apple", PNG_BYTES, ".png")]


@pytest.mark.asyncio
async def test_update_category_image_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "update_category_image", CategoryNotFoundError("Image 'I_Missing' not found in category 'foods'."),
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.put(
            "/api/v1/content/categories/foods/images/I_Missing",
            files={"image": ("img.png", PNG_BYTES, "image/png")},
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

    class _TrackingProvider(StubContentProvider):
        def delete_category_image(self, category_id, image_id):
            seen.append((category_id, image_id))
            return CategoryImage(id=image_id, file_uri=f"/content/{category_id}/{image_id}.png")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.delete("/api/v1/content/categories/foods/images/I_Apple")
    assert seen == [("foods", "I_Apple")]


@pytest.mark.asyncio
async def test_delete_category_image_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "delete_category_image", CategoryNotFoundError("Image 'I_Missing' not found in category 'foods'."),
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods/images/I_Missing")
    assert response.status_code == 404
