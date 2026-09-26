"""HTTP integration tests for the content item routes (ASGI + httpx).

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
    CategoryConflictError,
    CategoryNotFoundError,
    ContentOperationError,
    CreateItem,
    ItemDetail,
    RemoveItem,
)
from src.services.content.__tests__.content_http_helpers import PNG_BYTES, StubContentProvider, make_app


# ---------------------------------------------------------------------------
# POST /api/v1/content/items  (multipart form)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_item_returns_201(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/content/items",
        files={"image": ("apple.png", PNG_BYTES, "image/png")},
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

    class _TrackingProvider(StubContentProvider):
        def create_item(self, category_id, english_word, image_data=None, image_extension=".png", generate_voices=True):
            seen.append((category_id, english_word))
            return CreateItem(
                category=category_id,
                item=english_word,
                folder=english_word.lower(),
                image_id=f"I_{english_word.title()}",
            )

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/v1/content/items",
            files={"image": ("a.png", PNG_BYTES, "image/png")},
            data={"category": "  foods  ", "item": "  apple  "},
        )
    assert seen == [("foods", "apple")]


@pytest.mark.asyncio
async def test_create_item_picks_correct_extension(tmp_resolved: Path) -> None:
    seen_ext: list[str] = []

    class _ExtProvider(StubContentProvider):
        def create_item(self, category_id, english_word, image_data=None, image_extension=".png", generate_voices=True):
            seen_ext.append(image_extension)
            return CreateItem(category=category_id, item=english_word, folder=english_word, image_id="X")

    app = make_app(_ExtProvider(tmp_resolved), tmp_resolved)
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

    class _ExtProvider(StubContentProvider):
        def create_item(self, category_id, english_word, image_data=None, image_extension=".png", generate_voices=True):
            seen_ext.append(image_extension)
            return CreateItem(category=category_id, item=english_word, folder=english_word, image_id="X")

    app = make_app(_ExtProvider(tmp_resolved), tmp_resolved)
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
        files={"image": ("", PNG_BYTES, "image/png")},
        data={"category": "foods", "item": "apple"},
    )
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_create_item_service_error_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "create_item", CategoryConflictError("Item folder already exists: apple")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(
            "/api/v1/content/items",
            files={"image": ("apple.png", PNG_BYTES, "image/png")},
            data={"category": "foods", "item": "apple"},
        )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_item_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post(
        "/api/v1/content/items",
        files={"image": ("a.png", PNG_BYTES, "image/png")},
        data={"category": "foods", "item": "apple"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/content/categories/{category_id}/items  (multipart form)
# ---------------------------------------------------------------------------

_ITEMS_URL = "/api/v1/content/categories/C_Foods/items"


@pytest.mark.asyncio
async def test_items_create_with_image_returns_201(client: AsyncClient) -> None:
    response = await client.post(
        _ITEMS_URL,
        files={"image": ("apple.png", PNG_BYTES, "image/png")},
        data={"item": "apple"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["item_id"] == "I_Apple"
    assert body["image_id"] == "I_Apple"
    assert body["item"] == "apple"
    assert body["voices_generated"] == ["en"]
    assert body["voice_warnings"] == []
    assert body["message"] == "created"


@pytest.mark.asyncio
async def test_items_create_without_image_returns_201(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, bytes | None, str, bool]] = []

    class _TrackingProvider(StubContentProvider):
        def create_item(self, category_id, english_word, image_data=None, image_extension=".png", generate_voices=True):
            seen.append((category_id, english_word, image_data, image_extension, generate_voices))
            return CreateItem(category="foods", item=english_word, folder="apple", image_id="I_Apple",
                              item_id="I_Apple")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_ITEMS_URL, data={"item": "  apple  "})
    assert response.status_code == 201
    assert seen == [("C_Foods", "apple", None, ".png", True)]


@pytest.mark.asyncio
async def test_items_create_picks_image_extension(tmp_resolved: Path) -> None:
    seen: list[tuple[bytes | None, str]] = []

    class _TrackingProvider(StubContentProvider):
        def create_item(self, category_id, english_word, image_data=None, image_extension=".png", generate_voices=True):
            seen.append((image_data, image_extension))
            return CreateItem(category="foods", item=english_word, folder="apple", image_id="I_Apple",
                              item_id="I_Apple")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(_ITEMS_URL, files={"image": ("photo.JPEG", b"data", "image/jpeg")}, data={"item": "apple"})
    assert seen == [(b"data", ".jpeg")]


@pytest.mark.asyncio
async def test_items_create_skips_voices_when_no_active_generators(tmp_resolved: Path) -> None:
    seen_flags: list[bool] = []

    class _TrackingProvider(StubContentProvider):
        def create_item(self, category_id, english_word, image_data=None, image_extension=".png", generate_voices=True):
            seen_flags.append(generate_voices)
            return CreateItem(category="foods", item=english_word, folder="apple", image_id="I_Apple",
                              item_id="I_Apple")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved, voice_generation_available=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_ITEMS_URL, data={"item": "apple"})
    assert response.status_code == 201
    assert seen_flags == [False]
    body = response.json()
    assert body["voices_generated"] == []
    assert any("No active voice generators" in warning for warning in body["voice_warnings"])


@pytest.mark.asyncio
async def test_items_create_reports_provider_voice_warnings(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).returns("create_item", CreateItem(
        category="foods", item="apple", folder="apple", image_id="I_Apple", item_id="I_Apple",
        voices_generated=["en"], voice_warnings=["Voice generation failed for fr: boom"], message="created",
    ))
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_ITEMS_URL, data={"item": "apple"})
    assert response.status_code == 201
    assert response.json()["voice_warnings"] == ["Voice generation failed for fr: boom"]


@pytest.mark.asyncio
async def test_items_create_blank_name_returns_400(client: AsyncClient) -> None:
    response = await client.post(_ITEMS_URL, data={"item": "   "})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_items_create_empty_image_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        _ITEMS_URL,
        files={"image": ("apple.png", b"", "image/png")},
        data={"item": "apple"},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


@pytest.mark.asyncio
async def test_items_create_conflict_returns_409(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "create_item", CategoryConflictError("Item folder already exists: apple")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_ITEMS_URL, data={"item": "apple"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_items_create_unknown_category_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises("create_item", CategoryNotFoundError("nope"))
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_ITEMS_URL, data={"item": "apple"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_items_create_translation_failure_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "create_item", ContentOperationError("Translation failed for fr: boom")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_ITEMS_URL, data={"item": "apple"})
    assert response.status_code == 400
    assert "Translation failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_items_create_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post(_ITEMS_URL, data={"item": "apple"})
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

    class _TrackingProvider(StubContentProvider):
        def update_item_name(self, category_id: str, item_id: str, item_name: Any) -> ItemDetail:
            seen.append((category_id, item_id, item_name.to_dict()))
            return ItemDetail(id=item_id, name=item_name)

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/api/v1/content/categories/foods/items/I_Apple",
            json={"item_name": {"en": "green apple"}},
        )
    assert seen == [("foods", "I_Apple", {"en": "green apple"})]


@pytest.mark.asyncio
async def test_update_item_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "update_item_name", CategoryNotFoundError("Item not found")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.put(
            "/api/v1/content/categories/foods/items/I_Missing",
            json={"item_name": {"en": "green apple"}},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_item_without_new_id_does_not_rename(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, str]] = []

    class _TrackingProvider(StubContentProvider):
        def update_item_id(self, category_id: str, item_id: str, new_item_id: str) -> ItemDetail:
            seen.append((category_id, item_id, new_item_id))
            return ItemDetail(id=new_item_id)

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        for body in (
                {"item_name": {"en": "apple"}},
                {"item_name": {"en": "apple"}, "new_item_id": "I_Apple"},
        ):
            response = await c.put("/api/v1/content/categories/foods/items/I_Apple", json=body)
            assert response.status_code == 200
            assert response.json()["id"] == "I_Apple"
    assert not seen


@pytest.mark.asyncio
async def test_update_item_with_new_id_renames_item(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, str]] = []

    class _TrackingProvider(StubContentProvider):
        def update_item_id(self, category_id: str, item_id: str, new_item_id: str) -> ItemDetail:
            seen.append((category_id, item_id, new_item_id))
            return ItemDetail(id=new_item_id)

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.put(
            "/api/v1/content/categories/foods/items/I_Apple",
            json={"item_name": {"en": "apple"}, "new_item_id": "I_GreenApple"},
        )
    assert response.status_code == 200
    assert response.json()["id"] == "I_GreenApple"
    assert seen == [("foods", "I_Apple", "I_GreenApple")]


@pytest.mark.asyncio
async def test_update_item_new_id_conflict_returns_409(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "update_item_id", CategoryConflictError("Item id already exists: I_Banana")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.put(
            "/api/v1/content/categories/foods/items/I_Apple",
            json={"item_name": {"en": "apple"}, "new_item_id": "I_Banana"},
        )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


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

    class _TrackingProvider(StubContentProvider):
        def remove_item(self, category_id: str, item_id: str) -> RemoveItem:
            seen.append((category_id, item_id))
            return RemoveItem(category=category_id, item=item_id)

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.delete("/api/v1/content/categories/foods/items/I_Apple")
    assert seen == [("foods", "I_Apple")]


@pytest.mark.asyncio
async def test_delete_item_not_found_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "remove_item", CategoryNotFoundError("Item not found")
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/api/v1/content/categories/foods/items/I_Missing")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_item_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.delete("/api/v1/content/categories/foods/items/I_Apple")
    assert response.status_code == 401
