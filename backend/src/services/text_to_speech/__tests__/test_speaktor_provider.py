"""Unit tests for the Speaktor provider's credential handling.

An empty ``SPEAKTOR_API_KEY`` used to render as the header ``"Bearer "``, which
the HTTP layer rejects with "Illegal header value" — a protocol error that hid
the real cause (a missing key) from anyone reading the message.
"""

# pylint: disable=missing-function-docstring,too-few-public-methods,redefined-outer-name
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.services.text_to_speech.models import TTSRequest
from src.services.text_to_speech.providers.speaktor_tts import SpeaktorTTSProvider

_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "text_to_speech" / "speaktor.json"


class _RecordingClient:
    """Async HTTP client stand-in that records the request it is handed."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self._responses.pop(0)
        response.request = httpx.Request(method, url)
        return response

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        response = self._responses.pop(0)
        response.request = httpx.Request("GET", url)
        return response


def _shipped_generate_config() -> dict[str, Any]:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))["service_provider"]["config"]


def _make_provider(tmp_path: Path, config: dict[str, Any] | None = None) -> SpeaktorTTSProvider:
    return SpeaktorTTSProvider(
        name="speaktor",
        service_name="text_to_speech",
        provider_name="speaktor",
        provider_category="",
        enabled=True,
        priority=2,
        config=config if config is not None else _shipped_generate_config(),
        config_path=tmp_path / "speaktor.json",
        base_dir=tmp_path,
    )


@pytest.fixture(autouse=True)
def _isolate_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep generated audio inside the test's tmp dir, not the real output tree."""
    from src.core.config import settings  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path / "output"))


def _request() -> TTSRequest:
    return TTSRequest(
        request_id="req-1",
        text="animals",
        language_code="en",
        language_name="English",
        filename_text="animals",
        expected_text="animals",
        source_file="unit-test",
        source_type="inline",
        row_index=0,
    )


@pytest.mark.asyncio
async def test_missing_key_reports_the_configuration_cause(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped config sends 'Bearer {api_key}', so an empty key must be named."""
    monkeypatch.delenv("SPEAKTOR_API_KEY", raising=False)
    provider = _make_provider(tmp_path)

    with pytest.raises(RuntimeError, match="SPEAKTOR_API_KEY is not set."):
        await provider.generate(_request(), _RecordingClient([]))


@pytest.mark.asyncio
async def test_missing_key_never_builds_an_illegal_header(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the old behaviour raised httpx.LocalProtocolError instead."""
    monkeypatch.delenv("SPEAKTOR_API_KEY", raising=False)
    provider = _make_provider(tmp_path)

    with pytest.raises(RuntimeError) as excinfo:
        await provider.generate(_request(), _RecordingClient([]))

    assert not isinstance(excinfo.value, httpx.HTTPError)
    assert "Illegal header value" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_error_names_the_env_var_for_a_renamed_config(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key env var follows the config file name, so the message must too."""
    monkeypatch.delenv("SPEAKTOR_BG_API_KEY", raising=False)
    provider = SpeaktorTTSProvider(
        name="speaktor_bg",
        service_name="text_to_speech",
        provider_name="speaktor",
        provider_category="",
        enabled=True,
        priority=2,
        config=_shipped_generate_config(),
        config_path=tmp_path / "speaktor_bg.json",
        base_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="SPEAKTOR_BG_API_KEY is not set."):
        await provider.generate(_request(), _RecordingClient([]))


@pytest.mark.asyncio
async def test_config_without_the_placeholder_needs_no_key(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hosted endpoint may need no credentials at all."""
    monkeypatch.delenv("SPEAKTOR_API_KEY", raising=False)
    config = {
        "generate": {
            "endpoint": "http://127.0.0.1:9999/tts",
            "method": "POST",
            "response_mode": "raw",
            "headers": {"Content-Type": "application/json"},
            "payload_template": {"text": "{text}", "voice_name": "{voice_name}"},
            "languages": {"en": {"voice_name": "Sveta"}},
        },
    }
    provider = _make_provider(tmp_path, config)
    client = _RecordingClient([httpx.Response(200, content=b"AUDIO")])

    result = await provider.generate(_request(), client)

    assert result.success is True
    assert "Authorization" not in client.calls[0]["headers"]
    assert result.raw_path.read_bytes() == b"AUDIO"


@pytest.mark.asyncio
async def test_present_key_reaches_the_authorization_header(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPEAKTOR_API_KEY", "spk-test")
    config = _shipped_generate_config()
    config["generate"]["response_mode"] = "raw"
    provider = _make_provider(tmp_path, config)
    client = _RecordingClient([httpx.Response(200, content=b"AUDIO")])

    result = await provider.generate(_request(), client)

    assert result.success is True
    assert client.calls[0]["headers"]["Authorization"] == "Bearer spk-test"
