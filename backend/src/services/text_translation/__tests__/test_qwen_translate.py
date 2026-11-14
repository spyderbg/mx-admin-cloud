"""Qwen registration, shipped configurations, and SDK request isolation."""

# pylint: disable=missing-function-docstring

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.services.models import ServiceProvider
from src.services.text_translation.models import TranslateTextRequest
from src.services.text_translation.providers.qwen_translate import provider as qwen_translate
from src.services.text_translation.providers.qwen_translate import QwenTextTranslate
from src.services.text_translation.service import TextTranslationProviderFactory

_CONFIG_DIR = Path(__file__).resolve().parents[4] / "config" / "text_translation"
_MODEL = "Qwen3.8-27B-Q4_K_M.gguf"
_BASE_URL = "http://82.103.77.45:8080/v1/"


def _provider(config=None):
    parsed = ServiceProvider.from_dict(
        {"service_provider": {
            "service": "text_translation",
            "provider": "qwen_translate",
            "enabled": True,
            "config": config or {},
        }},
        Path("custom_language_pair.json"),
    )
    assert parsed is not None
    return TextTranslationProviderFactory.create(parsed, Path("."))


@pytest.mark.parametrize("language", ["bg", "es", "fr"])
def test_shipped_qwen_configs_load_and_only_support_their_language_pair(language):
    config_path = _CONFIG_DIR / f"qwen_en_{language}.json"
    parsed = ServiceProvider.from_dict(json.loads(config_path.read_text(encoding="utf-8")), config_path)
    assert parsed is not None
    provider = TextTranslationProviderFactory.create(parsed, _CONFIG_DIR.parent.parent)

    assert isinstance(provider, QwenTextTranslate)
    assert provider.is_active()
    assert provider.priority == 3
    assert provider.config["model"] == _MODEL
    assert provider.config["base_url"] == _BASE_URL
    assert provider.api_key_env_name() == "QWEN_API_KEY"
    assert provider.supports("en", language)
    assert not provider.supports(language, "en")
    assert not provider.supports("en", "de")


@pytest.mark.parametrize(
    "config, expected_model, expected_url",
    [
        ({}, _MODEL, _BASE_URL),
        ({"model": "custom-qwen", "base_url": "http://localhost:9000/v1/"},
         "custom-qwen", "http://localhost:9000/v1/"),
    ],
)
@pytest.mark.asyncio
async def test_translation_uses_provider_specific_endpoint_model_and_key(
    monkeypatch, config, expected_model, expected_url,
):
    monkeypatch.setenv("QWEN_API_KEY", "qwen-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="  котка  "))],
    ))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.chat.completions.create = create
    sdk = Mock(return_value=client)
    log_request = AsyncMock()
    monkeypatch.setattr(qwen_translate, "AsyncOpenAI", sdk)
    monkeypatch.setattr(qwen_translate, "log_provider_request", log_request)
    provider = _provider({
        **config,
        "prompt": "Translate {{ input_language }} to {{ output_language }}.\nCategory: {{ category }}",
        "temperature": 0.2,
    })

    result = await provider.translate_text(TranslateTextRequest(" cat ", "en", "bg", "animals"))

    assert result == "котка"
    sdk.assert_called_once_with(api_key="qwen-test-key", base_url=expected_url)
    create.assert_awaited_once_with(
        model=expected_model,
        messages=[
            {"role": "system", "content": "Translate English to Bulgarian.\nCategory: animals"},
            {"role": "user", "content": "cat"},
        ],
        temperature=0.2,
    )
    log_request.assert_awaited_once()
    client.__aexit__.assert_awaited_once()
    assert log_request.call_args.args[0].provider == "custom_language_pair"


@pytest.mark.asyncio
async def test_qwen_missing_key_does_not_use_openai_credentials(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    sdk = Mock()
    monkeypatch.setattr(qwen_translate, "AsyncOpenAI", sdk)

    with pytest.raises(RuntimeError, match="QWEN_API_KEY is not set"):
        await _provider().translate_text(TranslateTextRequest("cat", "en", "bg"))

    sdk.assert_not_called()


@pytest.mark.asyncio
async def test_qwen_blank_input_skips_client_and_credentials(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    sdk = Mock()
    monkeypatch.setattr(qwen_translate, "AsyncOpenAI", sdk)

    assert await _provider().translate_text(TranslateTextRequest(" \n ", "en", "bg")) is None
    sdk.assert_not_called()


@pytest.mark.parametrize("base_url", ["   ", 123])
def test_qwen_rejects_invalid_base_url(base_url):
    with pytest.raises(ValueError, match="base_url"):
        _provider({"base_url": base_url})


def test_qwen_works_with_openai_provider_file_removed(tmp_path):
    source_dir = _CONFIG_DIR.parent.parent / "src"
    isolated_src = tmp_path / "src"
    shutil.copytree(source_dir, isolated_src, ignore=shutil.ignore_patterns("__pycache__", "__tests__"))
    shutil.rmtree(isolated_src / "services/text_translation/providers/openai_translate")
    script = """
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.services.models import ServiceProvider
from src.services.text_translation.models import TranslateTextRequest
from src.services.text_translation.providers.qwen_translate import provider as qwen_translate
from src.services.text_translation.service import TextTranslationProviderFactory

assert Path(qwen_translate.__file__).resolve().is_relative_to(Path.cwd())
assert 'src.services.text_translation.providers.openai_translate' not in sys.modules
assert not TextTranslationProviderFactory.supports('openai_translate')
config = ServiceProvider.from_dict({'service_provider': {
    'service': 'text_translation', 'provider': 'qwen_translate', 'enabled': True,
}}, Path('qwen.json'))
provider = TextTranslationProviderFactory.create(config, Path('.'))
client = AsyncMock()
client.__aenter__.return_value = client
client.chat.completions.create.return_value = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content='котка'))],
)
with patch.object(provider, 'resolve_api_key', return_value='test-key'), \\
     patch.object(qwen_translate, 'AsyncOpenAI', Mock(return_value=client)), \\
     patch.object(qwen_translate, 'log_provider_request', AsyncMock()):
    result = asyncio.run(provider.translate_text(TranslateTextRequest('cat', 'en', 'bg')))
assert result == 'котка'
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
