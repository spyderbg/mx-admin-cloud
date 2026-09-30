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
    CategoryVoice,
    ContentOperationError,
    CreateItem,
    ItemDetail,
    ItemVoices,
    OptimizedItemVoice,
    RemoveItem,
)
from src.services.content.__tests__.content_http_helpers import PNG_BYTES, StubContentProvider, make_app


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
async def test_items_create_defaults_unknown_extension_to_png(tmp_resolved: Path) -> None:
    seen: list[str] = []

    class _TrackingProvider(StubContentProvider):
        def create_item(self, category_id, english_word, image_data=None, image_extension=".png", generate_voices=True):
            seen.append(image_extension)
            return CreateItem(category="foods", item=english_word, folder="apple", image_id="I_Apple",
                              item_id="I_Apple")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(_ITEMS_URL, files={"image": ("photo.bmp", b"data", "image/bmp")}, data={"item": "apple"})
    assert seen == [".png"]


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


# ---------------------------------------------------------------------------
# GET /api/v1/content/categories/{category_id}/items/{item_id}/voices  (capabilities)
# ---------------------------------------------------------------------------

_VOICES_URL = "/api/v1/content/categories/foods/items/I_Foods/voices"


@pytest.mark.asyncio
@pytest.mark.parametrize("optimizers_active", [True, False])
async def test_list_item_voices_advertises_what_can_run(tmp_resolved: Path, optimizers_active: bool) -> None:
    app = make_app(StubContentProvider(tmp_resolved), tmp_resolved, voice_optimization_available=optimizers_active)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(_VOICES_URL)
    assert response.status_code == 200
    # Only selection and (with an active optimizer) optimization exist: POST /voices still refuses a
    # second recording, and there are no generate/presets endpoints.
    assert response.json()["capabilities"] == {
        "multiple_voices": False,
        "generate": False,
        "optimize": optimizers_active,
        "select": True,
        "presets": False,
        "upload_formats": ["mp3"],
    }


@pytest.mark.asyncio
async def test_set_main_response_carries_no_capabilities(tmp_resolved: Path) -> None:
    app = make_app(StubContentProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        set_main = await c.put(_VOICE_MAIN_URL)
    assert set_main.status_code == 200
    assert set_main.json()["capabilities"] is None


# ---------------------------------------------------------------------------
# POST /api/v1/content/categories/{category_id}/items/{item_id}/voices/{voice_id}/regenerate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_item_voice_returns_the_new_take(tmp_resolved: Path) -> None:
    class _TrackingProvider(StubContentProvider):
        def regenerate_item_voice(self, category_id: str, item_id: str, voice_id: str) -> CategoryVoice:
            return CategoryVoice(id="apple_en_02", language="en", type="tts",
                                 file_uri="/content/foods/apple/apple_en_02.mp3", provider="openai")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(f"{_VOICES_URL}/apple-v-en/regenerate")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "apple_en_02"
    assert body["file_uri"] == "/content/foods/apple/apple_en_02.mp3"
    assert body["action"] == "regenerate"


# ---------------------------------------------------------------------------
# PUT /api/v1/content/categories/{category_id}/items/{item_id}/voices/{voice_id}/main
# ---------------------------------------------------------------------------

_VOICE_MAIN_URL = "/api/v1/content/categories/foods/items/I_Foods/voices/apple_en_02/main"


@pytest.mark.asyncio
async def test_set_main_item_voice_returns_voices_listing(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, str]] = []

    class _TrackingProvider(StubContentProvider):
        def set_main_item_voice(self, category_id: str, item_id: str, voice_id: str) -> ItemVoices:
            seen.append((category_id, item_id, voice_id))
            return ItemVoices(
                item_id=item_id,
                voices=[CategoryVoice(id="apple-v-en", language="en", type="tts",
                                      file_uri="/content/foods/apple/apple_en.mp3")],
                variants=[CategoryVoice(id="apple_en_02", language="en", type="tts",
                                        file_uri="/content/foods/apple/apple_en_02.mp3")],
                main_voice_ids={"en": "apple_en_02"},
            )

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.put(_VOICE_MAIN_URL)
    assert response.status_code == 200
    assert seen == [("foods", "I_Foods", "apple_en_02")]
    body = response.json()
    assert body["item_id"] == "I_Foods"
    assert body["name"] == {"en": "apple"}
    assert [v["id"] for v in body["voices"]] == ["apple-v-en"]
    assert [v["id"] for v in body["variants"]] == ["apple_en_02"]
    assert body["main_voice_ids"] == {"en": "apple_en_02"}


@pytest.mark.asyncio
async def test_set_main_item_voice_unknown_item_returns_404(client: AsyncClient) -> None:
    response = await client.put("/api/v1/content/categories/foods/items/I_Missing/voices/apple_en_02/main")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_set_main_item_voice_unknown_voice_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises("set_main_item_voice", CategoryNotFoundError("nope"))
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.put(_VOICE_MAIN_URL)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_set_main_item_voice_provider_error_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "set_main_item_voice", ContentOperationError("Failed to set main voice for item 'I_Foods': disk full"),
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.put(_VOICE_MAIN_URL)
    assert response.status_code == 400
    assert "Failed to set main voice" in response.json()["detail"]


@pytest.mark.asyncio
async def test_set_main_item_voice_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.put(_VOICE_MAIN_URL)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/content/categories/{category_id}/items/{item_id}/voices/{voice_id}/optimize
# ---------------------------------------------------------------------------

_VOICE_OPTIMIZE_URL = "/api/v1/content/categories/foods/items/I_Foods/voices/apple-v-en/optimize"


@pytest.mark.asyncio
async def test_optimize_item_voice_returns_listing_and_produced_variants(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, str]] = []

    class _TrackingProvider(StubContentProvider):
        def optimize_item_voice(self, category_id: str, item_id: str, voice_id: str) -> OptimizedItemVoice:
            seen.append((category_id, item_id, voice_id))
            return OptimizedItemVoice(
                item_id=item_id,
                voices=[CategoryVoice(id="apple-v-en", language="en", type="tts",
                                      file_uri="/content/foods/apple/apple_en.mp3")],
                variants=[
                    CategoryVoice(id="apple_en_01", language="en", type="tts",
                                  file_uri="/content/foods/apple/apple_en_01.mp3"),
                    CategoryVoice(id="apple_en_02", language="en", type="tts",
                                  file_uri="/content/foods/apple/apple_en_02.mp3",
                                  parameters={"_optimizer": "ffmpeg_low", "_source": "apple_en_01"}),
                ],
                main_voice_ids={"en": "apple_en_01"},
                source_voice_id="apple_en_01",
                optimized_voice_ids=["apple_en_02"],
                warnings=["Voice optimization failed for pydub_optimized: boom"],
            )

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_VOICE_OPTIMIZE_URL)
    assert response.status_code == 200
    assert seen == [("foods", "I_Foods", "apple-v-en")]
    body = response.json()
    assert body["item_id"] == "I_Foods"
    assert body["name"] == {"en": "apple"}
    assert [v["id"] for v in body["variants"]] == ["apple_en_01", "apple_en_02"]
    assert body["variants"][1]["parameters"]["_optimizer"] == "ffmpeg_low"
    assert body["main_voice_ids"] == {"en": "apple_en_01"}
    assert body["source_voice_id"] == "apple_en_01"
    assert body["optimized_voice_ids"] == ["apple_en_02"]
    assert body["warnings"] == ["Voice optimization failed for pydub_optimized: boom"]


@pytest.mark.asyncio
async def test_optimize_item_voice_unknown_item_returns_404(client: AsyncClient) -> None:
    response = await client.post("/api/v1/content/categories/foods/items/I_Missing/voices/apple-v-en/optimize")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_optimize_item_voice_unknown_voice_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises("optimize_item_voice", CategoryNotFoundError("nope"))
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_VOICE_OPTIMIZE_URL)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_optimize_item_voice_without_optimizers_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "optimize_item_voice", ContentOperationError("No active voice optimizers configured."),
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_VOICE_OPTIMIZE_URL)
    assert response.status_code == 400
    assert "No active voice optimizers" in response.json()["detail"]


@pytest.mark.asyncio
async def test_optimize_item_voice_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post(_VOICE_OPTIMIZE_URL)
    assert response.status_code == 401
