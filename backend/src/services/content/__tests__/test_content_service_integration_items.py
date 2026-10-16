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
    CategoryImage,
    CategoryNotFoundError,
    CategoryVoice,
    ContentOperationError,
    CreateItem,
    GenerateItemVoice,
    GeneratedItemVoice,
    GenerationOutcome,
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
    # Generating voices and the image are both opt-in.
    assert body["voices_generated"] == []
    assert body["voice_warnings"] == []
    assert body["image_generated"] is False
    assert body["image_warnings"] == []
    assert body["message"] == "created"


@pytest.mark.asyncio
async def test_items_create_without_image_returns_201(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, bytes | None, str, bool]] = []

    class _TrackingProvider(StubContentProvider):
        def create_item(
                self, category_id, english_word, image_data=None, image_extension=".png",
                generate_voices=False, generate_image=False,
        ):
            seen.append((category_id, english_word, image_data, image_extension, generate_voices))
            return CreateItem(category="foods", item=english_word, folder="apple", image_id="I_Apple",
                              item_id="I_Apple")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_ITEMS_URL, data={"item": "  apple  "})
    assert response.status_code == 201
    assert seen == [("C_Foods", "apple", None, ".png", False)]


@pytest.mark.asyncio
async def test_items_create_picks_image_extension(tmp_resolved: Path) -> None:
    seen: list[tuple[bytes | None, str]] = []

    class _TrackingProvider(StubContentProvider):
        def create_item(
                self, category_id, english_word, image_data=None, image_extension=".png",
                generate_voices=False, generate_image=False,
        ):
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
        def create_item(
                self, category_id, english_word, image_data=None, image_extension=".png",
                generate_voices=False, generate_image=False,
        ):
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
        def create_item(
                self, category_id, english_word, image_data=None, image_extension=".png",
                generate_voices=False, generate_image=False,
        ):
            seen_flags.append(generate_voices)
            return CreateItem(category="foods", item=english_word, folder="apple", image_id="I_Apple",
                              item_id="I_Apple")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved, voice_generation_available=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_ITEMS_URL, data={"item": "apple", "generate_voices": "true"})
    assert response.status_code == 201
    assert seen_flags == [False]
    body = response.json()
    assert body["voices_generated"] == []
    assert any("No active voice generators" in warning for warning in body["voice_warnings"])


@pytest.mark.asyncio
async def test_items_create_forwards_the_generation_choices(tmp_resolved: Path) -> None:
    seen: list[tuple[bool, bool]] = []

    class _TrackingProvider(StubContentProvider):
        def create_item(
                self, category_id, english_word, image_data=None, image_extension=".png",
                generate_voices=False, generate_image=False,
        ):
            seen.append((generate_voices, generate_image))
            return CreateItem(category="foods", item=english_word, folder="apple", image_id="I_Apple",
                              item_id="I_Apple", image=GenerationOutcome(requested=True, generated=["apple_01"]))

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(
            _ITEMS_URL, data={"item": "apple", "generate_voices": "false", "generate_image": "true"},
        )
    assert response.status_code == 201
    assert seen == [(False, True)]
    body = response.json()
    assert body["image_generated"] is True
    # Declining voices is not a missing generator, so no warning is raised for it.
    assert body["voice_warnings"] == []


@pytest.mark.asyncio
async def test_items_create_skips_the_image_when_no_active_generators(tmp_resolved: Path) -> None:
    seen_flags: list[bool] = []

    class _TrackingProvider(StubContentProvider):
        def create_item(
                self, category_id, english_word, image_data=None, image_extension=".png",
                generate_voices=False, generate_image=False,
        ):
            seen_flags.append(generate_image)
            return CreateItem(category="foods", item=english_word, folder="apple", image_id="I_Apple",
                              item_id="I_Apple")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved, image_generation_available=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_ITEMS_URL, data={"item": "apple", "generate_image": "true"})
    assert response.status_code == 201
    assert seen_flags == [False]
    body = response.json()
    assert body["image_generated"] is False
    assert any("No active image generators" in warning for warning in body["image_warnings"])
    assert body["voice_warnings"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("image_generation_available", [True, False])
async def test_items_create_rejects_uploading_and_generating_an_image(
        tmp_resolved: Path, image_generation_available: bool,
) -> None:
    # The request is refused before the provider is reached.
    provider = StubContentProvider(tmp_resolved).raises("create_item", AssertionError("create_item was called"))
    app = make_app(provider, tmp_resolved, image_generation_available=image_generation_available)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(
            _ITEMS_URL,
            files={"image": ("apple.png", PNG_BYTES, "image/png")},
            data={"item": "apple", "generate_image": "true"},
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "Upload an image or generate one, not both."


@pytest.mark.asyncio
async def test_items_create_reports_provider_voice_warnings(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).returns("create_item", CreateItem(
        category="foods", item="apple", folder="apple", image_id="I_Apple", item_id="I_Apple",
        voices=GenerationOutcome(requested=True, generated=["en"], warnings=["Voice generation failed for fr: boom"]),
        message="created",
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
@pytest.mark.parametrize("available", [True, False])
async def test_list_item_voices_advertises_what_can_run(tmp_resolved: Path, available: bool) -> None:
    app = make_app(
        StubContentProvider(tmp_resolved), tmp_resolved,
        voice_optimization_available=available, voice_configurations_available=available,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(_VOICES_URL)
    assert response.status_code == 200
    # Selection always, generation with a TTS configuration and optimization with an active
    # optimizer: POST /voices can add multiple recordings, and there is no presets endpoint.
    assert response.json()["capabilities"] == {
        "multiple_voices": True,
        "generate": available,
        "optimize": available,
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
# POST /api/v1/content/categories/{category_id}/items/{item_id}/voices
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_item_voice_uploads_audio(tmp_resolved: Path) -> None:
    calls = []

    class _AddVoiceProvider(StubContentProvider):
        def add_item_voice(self, category_id: str, item_id: str, language: str, audio_data: bytes) -> CategoryVoice:
            calls.append((category_id, item_id, language, audio_data))
            return CategoryVoice(
                id="apple_en_02",
                language=language,
                type="tts",
                file_uri="/content/foods/apple/apple_en_02.mp3",
                provider="custom",
            )

    app = make_app(_AddVoiceProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(
            _VOICES_URL,
            data={"language": "en"},
            files={"audio": ("voice.mp3", b"audio-bytes", "audio/mpeg")},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "apple_en_02"
    assert body["language"] == "en"
    assert body["action"] == "add"
    assert calls == [("foods", "I_Foods", "en", b"audio-bytes")]


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
# POST /api/v1/content/categories/{category_id}/items/{item_id}/voices/generate
# ---------------------------------------------------------------------------

_VOICE_GENERATE_URL = "/api/v1/content/categories/foods/items/I_Foods/voices/generate"
_GENERATE_BODY = {
    "language": "en",
    "text": "apple",
    "configuration_name": "elevenlabs",
    "speed": 0.9,
    "stability": 0.4,
    "similarity": 0.8,
    "optimize": True,
}


@pytest.mark.asyncio
async def test_generate_item_voice_returns_listing_and_the_new_voice(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, GenerateItemVoice]] = []

    class _TrackingProvider(StubContentProvider):
        def generate_item_voice(self, category_id: str, item_id: str, request: GenerateItemVoice) -> GeneratedItemVoice:
            seen.append((category_id, item_id, request))
            return GeneratedItemVoice(
                item_id=item_id,
                voices=[CategoryVoice(id="apple-v-en", language="en", type="tts",
                                      file_uri="/content/foods/apple/apple_en.mp3")],
                variants=[
                    CategoryVoice(id="apple_en_01", language="en", type="tts",
                                  file_uri="/content/foods/apple/apple_en_01.mp3"),
                    CategoryVoice(id="apple_en_02", language="en", type="tts", provider="elevenlabs",
                                  file_uri="/content/foods/apple/apple_en_02.mp3",
                                  parameters={"configuration_name": "elevenlabs", "speed": 0.9}),
                    CategoryVoice(id="apple_en_03", language="en", type="tts",
                                  file_uri="/content/foods/apple/apple_en_03.mp3",
                                  parameters={"_optimizer": "ffmpeg_low", "_source": "apple_en_02"}),
                ],
                main_voice_ids={"en": "apple_en_01"},
                voice_id="apple_en_02",
                optimized_voice_ids=["apple_en_03"],
                warnings=["Voice optimization failed for pydub_optimized: boom"],
            )

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_VOICE_GENERATE_URL, json=_GENERATE_BODY)
    assert response.status_code == 201
    assert seen == [("foods", "I_Foods", GenerateItemVoice(
        language="en", text="apple", configuration_name="elevenlabs",
        speed=0.9, stability=0.4, similarity=0.8, optimize=True,
    ))]
    body = response.json()
    assert body["item_id"] == "I_Foods"
    assert body["name"] == {"en": "apple"}
    assert [v["id"] for v in body["variants"]] == ["apple_en_01", "apple_en_02", "apple_en_03"]
    # The main voice is left as it was: the new voice waits to be chosen.
    assert body["main_voice_ids"] == {"en": "apple_en_01"}
    assert body["voice_id"] == "apple_en_02"
    assert body["optimized_voice_ids"] == ["apple_en_03"]
    assert body["warnings"] == ["Voice optimization failed for pydub_optimized: boom"]


@pytest.mark.asyncio
async def test_generate_item_voice_settings_are_optional(tmp_resolved: Path) -> None:
    seen: list[GenerateItemVoice] = []

    class _TrackingProvider(StubContentProvider):
        def generate_item_voice(self, category_id: str, item_id: str, request: GenerateItemVoice) -> GeneratedItemVoice:
            seen.append(request)
            return GeneratedItemVoice(item_id=item_id, voice_id="apple_en_02")

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_VOICE_GENERATE_URL, json={
            "language": " en ", "text": "apple", "configuration_name": "openai",
        })
    assert response.status_code == 201
    assert seen == [GenerateItemVoice(language="en", text="apple", configuration_name="openai")]
    assert response.json()["optimized_voice_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"stability": 1.5}, {"similarity": -0.1}, {"speed": 0}, {"configuration_name": None}, {"text": None},
])
async def test_generate_item_voice_rejects_invalid_bodies(client: AsyncClient, override: dict[str, Any]) -> None:
    body = {k: v for k, v in {**_GENERATE_BODY, **override}.items() if v is not None}
    response = await client.post(_VOICE_GENERATE_URL, json=body)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_generate_item_voice_unknown_item_returns_404(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/content/categories/foods/items/I_Missing/voices/generate", json=_GENERATE_BODY,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_generate_item_voice_generation_failure_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "generate_item_voice",
        ContentOperationError("Voice generation failed for en: Text to speech configuration 'nope' not found."),
    )
    app = make_app(provider, tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_VOICE_GENERATE_URL, json=_GENERATE_BODY)
    assert response.status_code == 400
    assert "configuration 'nope' not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_generate_item_voice_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.post(_VOICE_GENERATE_URL, json=_GENERATE_BODY)
    assert response.status_code == 401


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


# ---------------------------------------------------------------------------
# POST /api/v1/content/categories/{category_id}/items/{item_id}/images/generate
# ---------------------------------------------------------------------------

_IMAGE_GENERATE_URL = "/api/v1/content/categories/C_Foods/items/I_Apple/images/generate"


@pytest.mark.asyncio
async def test_generate_item_image_returns_the_new_variant(tmp_resolved: Path) -> None:
    seen: list[tuple[str, str, str, str | None]] = []

    class _TrackingProvider(StubContentProvider):
        def generate_item_image(self, category_id: str, item_id: str, prompt: str,
                                configuration_name: str | None = None) -> CategoryImage:
            seen.append((category_id, item_id, prompt, configuration_name))
            return CategoryImage(
                id="apple_02",
                file_uri="/content/foods/apple/apple_02.png",
                provider="comfyui",
                parameters={"_prompt": prompt, "steps": 20},
            )

    app = make_app(_TrackingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_IMAGE_GENERATE_URL, json={"prompt": "a red apple", "configuration_name": "comfyui"})
        without_configuration = await c.post(_IMAGE_GENERATE_URL, json={"prompt": "a green pear"})

    assert response.status_code == 201
    assert response.json() == {
        "id": "apple_02",
        "file_uri": "/content/foods/apple/apple_02.png",
        "action": "add",
        "provider": "comfyui",
        "parameters": {"_prompt": "a red apple", "steps": 20},
    }
    assert without_configuration.status_code == 201
    assert seen == [
        ("C_Foods", "I_Apple", "a red apple", "comfyui"),
        ("C_Foods", "I_Apple", "a green pear", None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"prompt": "   "}, {"prompt": ""}])
async def test_generate_item_image_rejects_an_empty_prompt(client: AsyncClient, body: dict[str, Any]) -> None:
    response = await client.post(_IMAGE_GENERATE_URL, json=body)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_generate_item_image_requires_a_prompt_field(client: AsyncClient) -> None:
    response = await client.post(_IMAGE_GENERATE_URL, json={})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "status_code"), [
    (CategoryNotFoundError("Item 'I_Apple' not found"), 404),
    (ContentOperationError("Image generation failed: No active image providers configured."), 400),
])
async def test_generate_item_image_maps_errors(tmp_resolved: Path, error: Exception, status_code: int) -> None:
    class _FailingProvider(StubContentProvider):
        def generate_item_image(self, category_id: str, item_id: str, prompt: str,
                                configuration_name: str | None = None) -> CategoryImage:
            raise error

    app = make_app(_FailingProvider(tmp_resolved), tmp_resolved)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(_IMAGE_GENERATE_URL, json={"prompt": "apple"})
    assert response.status_code == status_code
