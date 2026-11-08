"""Speech-to-text providers of the evaluation service: registry, the three backends, and their isolation."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from src.services.evaluation.models import STTProvider
from src.services.evaluation.providers.elevenlabs_stt import ElevenLabsSTTProvider
from src.services.evaluation.providers.openai_stt import OpenAISTTProvider
from src.services.evaluation.providers.speaktor_stt import SpeaktorSTTProvider
from src.services.evaluation.service import SpeechToTextProviderFactory

_SRC = Path(__file__).resolve().parents[3]
_PROVIDERS = {
    "openai": ("openai_stt", OpenAISTTProvider),
    "elevenlabs": ("elevenlabs_stt", ElevenLabsSTTProvider),
    "speaktor": ("speaktor_stt", SpeaktorSTTProvider),
}


class _Client:
    """Async HTTP client double: records calls and answers with canned JSON bodies."""

    def __init__(self, *bodies: dict[str, Any]) -> None:
        self.bodies = list(bodies)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> Mock:
        self.calls.append((method, url, kwargs))
        return Mock(json=Mock(return_value=self.bodies.pop(0) if self.bodies else {}))

    async def post(self, url: str, **kwargs: Any) -> Mock:
        return await self._answer("POST", url, kwargs)

    async def put(self, url: str, **kwargs: Any) -> Mock:
        return await self._answer("PUT", url, kwargs)

    async def get(self, url: str, **kwargs: Any) -> Mock:
        return await self._answer("GET", url, kwargs)


@pytest.fixture(name="audio")
def fixture_audio(tmp_path: Path) -> Path:
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF-audio")
    return path


@pytest.fixture(name="log_request")
def fixture_log_request(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    recorder = AsyncMock()
    monkeypatch.setattr("src.services.evaluation.models.log_provider_request", recorder)
    return recorder


@pytest.fixture(name="no_sleep")
def fixture_no_sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    sleep = AsyncMock()
    monkeypatch.setattr("src.services.evaluation.providers.speaktor_stt.provider.asyncio.sleep", sleep)
    return sleep


# --- registry ----------------------------------------------------------------------------------------------


def test_every_stt_provider_id_resolves_to_its_package() -> None:
    assert SpeechToTextProviderFactory.available_names() == sorted(_PROVIDERS)
    for provider_id, (package, cls) in _PROVIDERS.items():
        provider = SpeechToTextProviderFactory.create(provider_id, timeout_seconds=12.5)
        assert isinstance(provider, cls)
        assert provider.timeout_seconds == 12.5
        assert cls.__module__ == f"src.services.evaluation.providers.{package}.provider"


def test_unknown_stt_provider_id_is_rejected() -> None:
    assert not SpeechToTextProviderFactory.supports("whisper_local")
    with pytest.raises(ValueError, match="Unsupported speech-to-text provider: 'whisper_local'"):
        SpeechToTextProviderFactory.create("whisper_local", timeout_seconds=1)


def test_duplicate_stt_provider_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    builders = dict(SpeechToTextProviderFactory._builders)  # pylint: disable=protected-access
    monkeypatch.setattr(SpeechToTextProviderFactory, "_builders", builders)

    OpenAISTTProvider.register()  # the same class again (e.g. a re-import) is harmless
    with pytest.raises(ValueError, match="Duplicate speech-to-text provider 'openai'.*OpenAISTTProvider"):
        SpeechToTextProviderFactory.register(" OpenAI ", ElevenLabsSTTProvider)
    assert builders["openai"] is OpenAISTTProvider


def test_stt_provider_must_implement_transcribe() -> None:
    class Incomplete(STTProvider):  # pylint: disable=abstract-method,too-few-public-methods
        """Lacks transcribe()."""

    with pytest.raises(TypeError, match="transcribe"):
        Incomplete(timeout_seconds=1)  # pylint: disable=abstract-class-instantiated


# --- the backends ------------------------------------------------------------------------------------------


async def test_openai_sends_the_transcription_request(audio: Path, log_request: AsyncMock) -> None:
    client = _Client({"text": "  hello  "})
    provider = OpenAISTTProvider(timeout_seconds=9)

    text = await provider.transcribe(audio, "bg", {"model": "gpt-4o-transcribe", "prompt": " kids "}, "sk", client)

    assert text == "hello"
    assert len(client.calls) == 1
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("POST", "https://api.openai.com/v1/audio/transcriptions")
    assert kwargs["headers"] == {"Authorization": "Bearer sk"}
    assert kwargs["timeout"] == 9
    assert kwargs["files"] == {
        "file": ("clip.wav", b"RIFF-audio", "audio/wav"),
        "model": (None, "gpt-4o-transcribe"),
        "response_format": (None, "json"),
        "language": (None, "bg"),
        "prompt": (None, "kids"),
    }
    logged = log_request.await_args.args[0]
    assert (logged.provider, logged.tts_model, logged.language, logged.input_data) == (
        "openai", "gpt-4o-transcribe", "bg", "audio:clip.wav"
    )


async def test_openai_defaults_to_whisper_and_omits_an_empty_prompt(audio: Path) -> None:
    client = _Client({"text": "hi"})
    await OpenAISTTProvider(timeout_seconds=1).transcribe(audio, "en", {"prompt": "  "}, "sk", client)
    files = client.calls[0][2]["files"]
    assert files["model"] == (None, "whisper-1") and "prompt" not in files


async def test_elevenlabs_sends_the_transcription_request(audio: Path, log_request: AsyncMock) -> None:
    client = _Client({"text": " eleven "})

    text = await ElevenLabsSTTProvider(timeout_seconds=3).transcribe(audio, "de", {}, "xi", client)

    assert text == "eleven"
    assert len(client.calls) == 1
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("POST", "https://api.elevenlabs.io/v1/speech-to-text")
    assert kwargs["headers"] == {"xi-api-key": "xi"}
    assert kwargs["files"] == {
        "file": ("clip.wav", b"RIFF-audio", "audio/wav"),
        "model_id": (None, "scribe_v1"),
        "language_code": (None, "de"),
    }
    assert log_request.await_args.args[0].provider == "elevenlabs"


@pytest.mark.usefixtures("no_sleep")
async def test_speaktor_uploads_then_polls_until_completed(audio: Path, log_request: AsyncMock) -> None:
    base = "https://api.tor.app/developer"
    client = _Client(
        {"upload_url": "https://upload/x", "public_url": "https://public/x"},
        {},
        {"order_id": "ord-1"},
        {"body": {"status": "Processing"}},
        {"status": "Completed", "content": [{"text": " one "}, {"text": ""}, {"text": "two"}]},
    )

    text = await SpeaktorSTTProvider(timeout_seconds=4).transcribe(audio, "bg", {}, "tk", client)

    assert text == "one two"
    assert [(method, url) for method, url, _ in client.calls] == [
        ("POST", f"{base}/transcription/local_file/get_upload_url"),
        ("PUT", "https://upload/x"),
        ("POST", f"{base}/transcription/local_file/initiate_transcription"),
        ("GET", f"{base}/files/ord-1/content"),
        ("GET", f"{base}/files/ord-1/content"),
    ]
    assert client.calls[2][2]["json"] == {"url": "https://public/x", "language": "bg-BG", "service": "Standard"}
    logged = log_request.await_args.args[0]
    assert (logged.provider, logged.tts_model, logged.language) == ("speaktor", "transkriptor-stt", "bg-BG")


async def test_speaktor_gives_up_after_the_configured_polls(audio: Path, no_sleep: AsyncMock) -> None:
    client = _Client({"upload_url": "u", "public_url": "p"}, {}, {"order_id": "o"}, *[{"status": "Queued"}] * 2)
    config = {"poll_max_attempts": 2, "poll_interval_seconds": 1.5}

    with pytest.raises(RuntimeError, match=r"did not complete within 3s \(order_id=o\)"):
        await SpeaktorSTTProvider(timeout_seconds=1).transcribe(audio, "en", config, "tk", client)
    assert [call.args for call in no_sleep.await_args_list] == [(1.5,), (1.5,)]


# --- isolation ---------------------------------------------------------------------------------------------

_ISOLATED_SCRIPT = """
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import src.services.evaluation  # the facade imports the provider loader, which imports every package left
from src.services.evaluation.service import SpeechToTextProviderFactory

provider_id, package = sys.argv[1:3]
assert Path(src.services.evaluation.__file__).resolve().is_relative_to(Path.cwd())
assert SpeechToTextProviderFactory.available_names() == [provider_id]
loaded = {m.split('.')[4] for m in sys.modules if m.startswith('src.services.evaluation.providers.')}
assert loaded == {package}, loaded

body = {'text': 'hi', 'upload_url': 'u', 'public_url': 'p', 'order_id': 'o',
        'status': 'Completed', 'content': [{'text': 'hi'}]}
client = Mock(**{name: AsyncMock(return_value=Mock(json=Mock(return_value=body))) for name in ('post', 'put', 'get')})
provider = SpeechToTextProviderFactory.create(provider_id, timeout_seconds=5)
with patch('asyncio.sleep', AsyncMock()), patch('src.services.evaluation.models.log_provider_request', AsyncMock()):
    assert asyncio.run(provider.transcribe(Path('a.wav'), 'en', {}, 'key', client)) == 'hi'
"""


@pytest.mark.parametrize("provider_id", sorted(_PROVIDERS))
def test_stt_provider_works_with_its_sibling_packages_removed(provider_id: str, tmp_path: Path) -> None:
    package = _PROVIDERS[provider_id][0]
    isolated_src = tmp_path / "src"
    shutil.copytree(_SRC, isolated_src, ignore=shutil.ignore_patterns("__pycache__", "__tests__"))
    for sibling, _cls in _PROVIDERS.values():
        if sibling != package:
            shutil.rmtree(isolated_src / "services/evaluation/providers" / sibling)
    (tmp_path / "a.wav").write_bytes(b"RIFF")

    result = subprocess.run(
        [sys.executable, "-c", _ISOLATED_SCRIPT, provider_id, package],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
