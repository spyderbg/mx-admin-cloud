"""Tests for the ComfyUI provider (workflow rendering, polling, download)."""

# pylint: disable=missing-function-docstring,protected-access,too-few-public-methods
# pylint: disable=use-implicit-booleaness-not-comparison

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImageRequest
from src.services.text_to_image.providers.comfyui_image import ComfyUIImageProvider

_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": "{seed}", "steps": "{steps}", "cfg": "{cfg}", "model": ["4", 0]},
    },
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "{checkpoint}"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": "{width}", "height": "{height}"}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{prompt}"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{negative_prompt}"}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "{filename_prefix}"}},
}

_BASE_GENERATE: dict[str, Any] = {
    "base_url": "http://comfy.test:8188",
    "workflow": _WORKFLOW,
    "checkpoint": "sd15.safetensors",
    "width": 768,
    "height": 512,
    "steps": 25,
    "cfg": 6.5,
    "seed": 4242,
    "negative_prompt": "blurry",
    "filename_prefix": "kiddo",
    "poll_interval_seconds": 0,
    "poll_max_attempts": 5,
}


class _ScriptedClient:
    """Async HTTP client stand-in returning canned ComfyUI responses."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self._next("POST", url)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._next("GET", url)

    def _next(self, method: str, url: str) -> httpx.Response:
        response = self._responses.pop(0)
        response.request = httpx.Request(method, url)
        return response


def _make_provider(tmp_path: Path, generate: dict[str, Any] | None = None) -> ComfyUIImageProvider:
    provider = ComfyUIImageProvider(
        name="comfyui",
        service_name="text_to_image",
        provider_name="comfyui",
        provider_category="",
        enabled=True,
        priority=1,
        config={"generate": generate if generate is not None else dict(_BASE_GENERATE)},
        config_path=tmp_path / "comfyui.json",
        base_dir=tmp_path,
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    provider._sleep = _no_sleep
    return provider


def _request(**overrides: Any) -> ImageRequest:
    payload: dict[str, Any] = {
        "request_id": "img-1",
        "prompt": "a friendly cat",
        "filename_text": "cat",
    }
    payload.update(overrides)
    return ImageRequest(**payload)


def _history(images: list[dict[str, Any]], status_str: str = "success") -> httpx.Response:
    return httpx.Response(
        200,
        json={"pid-1": {"status": {"status_str": status_str}, "outputs": {"9": {"images": images}}}},
    )


def _success_responses() -> list[httpx.Response]:
    return [
        httpx.Response(200, json={"prompt_id": "pid-1"}),
        _history([{"filename": "kiddo_0001.png", "subfolder": "", "type": "output"}]),
        httpx.Response(200, content=b"PNGDATA", headers={"content-type": "image/png"}),
    ]


# ---------------------------------------------------------------------------
# Workflow loading and validation
# ---------------------------------------------------------------------------

def test_workflow_file_resolves_next_to_the_config(tmp_path: Path) -> None:
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "wf.json").write_text(json.dumps(_WORKFLOW), encoding="utf-8")
    generate = {**_BASE_GENERATE}
    generate.pop("workflow")
    generate["workflow_file"] = "workflows/wf.json"

    provider = _make_provider(tmp_path, generate)

    assert provider._workflow["6"]["inputs"]["text"] == "{prompt}"


def test_missing_base_url_is_rejected(tmp_path: Path) -> None:
    generate = {**_BASE_GENERATE, "base_url": ""}
    with pytest.raises(ValueError, match="base_url"):
        _make_provider(tmp_path, generate)


def test_missing_workflow_is_rejected(tmp_path: Path) -> None:
    generate = {**_BASE_GENERATE}
    generate.pop("workflow")
    with pytest.raises(ValueError, match="workflow"):
        _make_provider(tmp_path, generate)


def test_both_workflow_sources_is_rejected(tmp_path: Path) -> None:
    generate = {**_BASE_GENERATE, "workflow_file": "workflows/wf.json"}
    with pytest.raises(ValueError, match="both"):
        _make_provider(tmp_path, generate)


def test_missing_workflow_file_is_rejected(tmp_path: Path) -> None:
    generate = {**_BASE_GENERATE}
    generate.pop("workflow")
    generate["workflow_file"] = "workflows/absent.json"
    with pytest.raises(ValueError, match="not found"):
        _make_provider(tmp_path, generate)


def test_workflow_without_prompt_placeholder_is_rejected(tmp_path: Path) -> None:
    generate = {**_BASE_GENERATE, "workflow": {"6": {"inputs": {"text": "hardcoded"}}}}
    with pytest.raises(ValueError, match="prompt"):
        _make_provider(tmp_path, generate)


def test_shipped_default_workflow_loads(tmp_path: Path) -> None:
    shipped = Path(__file__).resolve().parents[4] / "config" / "text_to_image" / "workflows"
    generate = {**_BASE_GENERATE}
    generate.pop("workflow")
    generate["workflow_file"] = str(shipped / "default_txt2img.json")

    provider = _make_provider(tmp_path, generate)

    assert provider._workflow["9"]["class_type"] == "SaveImage"


# ---------------------------------------------------------------------------
# Rendering and seeds
# ---------------------------------------------------------------------------

def test_context_uses_typed_values(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)

    context = provider._build_context(_request())

    assert context["seed"] == 4242
    assert context["steps"] == 25
    assert context["cfg"] == 6.5
    assert context["width"] == 768
    assert context["height"] == 512
    assert context["prompt"] == "a friendly cat"
    assert context["negative_prompt"] == "blurry"


def test_request_seed_overrides_config(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)
    assert provider._build_context(_request(seed=99))["seed"] == 99


def test_null_config_seed_is_randomised(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path, {**_BASE_GENERATE, "seed": None})

    seeds = {provider._build_context(_request())["seed"] for _ in range(5)}

    assert all(isinstance(seed, int) for seed in seeds)
    assert len(seeds) > 1


@pytest.mark.asyncio
async def test_submitted_graph_carries_typed_numbers(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)
    client = _ScriptedClient(_success_responses())

    await provider.fetch_image(_request(), client)

    graph = client.calls[0]["json"]["prompt"]
    assert graph["3"]["inputs"]["seed"] == 4242
    assert isinstance(graph["3"]["inputs"]["seed"], int)
    assert graph["3"]["inputs"]["steps"] == 25
    assert graph["3"]["inputs"]["cfg"] == 6.5
    assert isinstance(graph["3"]["inputs"]["cfg"], float)
    assert graph["5"]["inputs"]["width"] == 768
    assert graph["3"]["inputs"]["model"] == ["4", 0]
    assert graph["4"]["inputs"]["ckpt_name"] == "sd15.safetensors"
    assert graph["6"]["inputs"]["text"] == "a friendly cat"
    assert graph["7"]["inputs"]["text"] == "blurry"
    assert client.calls[0]["json"]["client_id"]


@pytest.mark.asyncio
async def test_header_template_receives_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMFYUI_API_KEY", "proxy-token")
    provider = _make_provider(
        tmp_path,
        {**_BASE_GENERATE, "headers": {"Authorization": "Bearer {api_key}"}},
    )
    client = _ScriptedClient(_success_responses())

    await provider.fetch_image(_request(), client)

    assert client.calls[0]["headers"]["Authorization"] == "Bearer proxy-token"


# ---------------------------------------------------------------------------
# Submit / poll / download
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_downloads_image(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)
    client = _ScriptedClient(_success_responses())

    payload = await provider.fetch_image(_request(), client)

    assert payload.data == b"PNGDATA"
    assert payload.mime_type == "image/png"
    assert payload.width == 768
    assert payload.height == 512
    assert [call["url"] for call in client.calls] == [
        "http://comfy.test:8188/prompt",
        "http://comfy.test:8188/history/pid-1",
        "http://comfy.test:8188/view",
    ]
    assert client.calls[2]["params"] == {
        "filename": "kiddo_0001.png",
        "subfolder": "",
        "type": "output",
    }


@pytest.mark.asyncio
async def test_jpeg_content_type_is_passed_through(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)
    responses = _success_responses()
    responses[2] = httpx.Response(200, content=b"JPEG", headers={"content-type": "image/jpeg"})
    client = _ScriptedClient(responses)

    payload = await provider.fetch_image(_request(), client)

    assert payload.mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_polls_until_the_job_appears(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)
    client = _ScriptedClient([
        httpx.Response(200, json={"prompt_id": "pid-1"}),
        httpx.Response(200, json={}),                       # queued, not yet recorded
        _history([]),                                       # running, no outputs yet
        _history([{"filename": "kiddo_0001.png", "subfolder": "", "type": "output"}]),
        httpx.Response(200, content=b"PNGDATA", headers={"content-type": "image/png"}),
    ])

    payload = await provider.fetch_image(_request(), client)

    assert payload.data == b"PNGDATA"
    assert sum(1 for call in client.calls if "history" in call["url"]) == 3


@pytest.mark.asyncio
async def test_job_error_is_non_retryable(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)
    client = _ScriptedClient([
        httpx.Response(200, json={"prompt_id": "pid-1"}),
        httpx.Response(
            200,
            json={
                "pid-1": {
                    "status": {
                        "status_str": "error",
                        "messages": [["execution_error", "CheckpointLoaderSimple: file not found"]],
                    },
                    "outputs": {},
                },
            },
        ),
    ])

    with pytest.raises(NonRetryableError, match="file not found"):
        await provider.fetch_image(_request(), client)


@pytest.mark.asyncio
async def test_exhausted_polls_do_not_resubmit(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path, {**_BASE_GENERATE, "poll_max_attempts": 3})
    client = _ScriptedClient([
        httpx.Response(200, json={"prompt_id": "pid-1"}),
        _history([]),
        _history([]),
        _history([]),
    ])

    with pytest.raises(NonRetryableError, match="did not produce an image"):
        await provider.fetch_image(_request(), client)

    assert sum(1 for call in client.calls if call["url"].endswith("/prompt")) == 1


@pytest.mark.asyncio
async def test_missing_prompt_id_is_non_retryable(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)
    client = _ScriptedClient([httpx.Response(200, json={"node_errors": {}})])

    with pytest.raises(NonRetryableError, match="prompt_id"):
        await provider.fetch_image(_request(), client)


@pytest.mark.asyncio
async def test_submit_error_status_propagates(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)
    client = _ScriptedClient([httpx.Response(400, json={"error": "bad workflow"})])

    with pytest.raises(httpx.HTTPStatusError):
        await provider.fetch_image(_request(), client)


def test_output_images_prefer_saved_outputs() -> None:
    entry = {
        "outputs": {
            "8": {"images": [{"filename": "preview.png", "type": "temp"}]},
            "9": {"images": [{"filename": "final.png", "type": "output"}]},
        },
    }

    images = ComfyUIImageProvider._extract_output_images(entry)

    assert images[0]["filename"] == "final.png"


def test_output_images_fall_back_to_temp() -> None:
    entry = {"outputs": {"8": {"images": [{"filename": "preview.png", "type": "temp"}]}}}

    images = ComfyUIImageProvider._extract_output_images(entry)

    assert images[0]["filename"] == "preview.png"


def test_output_images_ignore_malformed_entries() -> None:
    entry = {"outputs": {"8": {"images": [{"subfolder": "x"}, "not-a-dict"]}}}

    assert ComfyUIImageProvider._extract_output_images(entry) == []
