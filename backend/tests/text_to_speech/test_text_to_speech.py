"""Unit tests for TTS provider service business logic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.services.text_to_speech.models import ProviderConfig
from src.services.text_to_speech.base import TTSProvider, _boolish
from src.services.text_to_speech import (
    ProviderFactory,
    build_provider,
    build_active_providers,
)
from src.core.models import GeneratedAudio, TTSRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    name: str = "test_provider",
    generate: dict | None = None,
    evaluate: dict | None = None,
    config_path: Path | None = None,
    version: str = "1.0",
) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        generate=generate if generate is not None else {"enabled": True},
        evaluate=evaluate if evaluate is not None else {"enabled": False},
        config_path=config_path or Path("test.json"),
        version=version,
    )


def _make_tts_request(
    text: str = "hello",
    language_code: str = "en",
    language_name: str = "English",
) -> TTSRequest:
    return TTSRequest(
        request_id="test-001",
        text=text,
        language_code=language_code,
        language_name=language_name,
        filename_text=text,
        expected_text=text,
        source_file="test.txt",
        source_type="txt",
        row_index=0,
    )


class _StubProvider(TTSProvider):
    """Minimal concrete TTS provider for testing."""

    def __init__(self, cfg: ProviderConfig, base_dir: Path, success: bool = True) -> None:
        # ProviderConfig has no .config field; bypass ServiceProvider.__init__
        self.cfg = cfg
        self.config: dict[str, Any] = {}
        self.base_dir = base_dir
        self._success = success

    @classmethod
    def register(cls) -> None:
        pass

    async def generate(self, request: TTSRequest, output_format: str, client: Any) -> GeneratedAudio:
        return GeneratedAudio(
            provider=self.name,
            request_id=request.request_id,
            language_code=request.language_code,
            language_name=request.language_name,
            expected_text=request.expected_text,
            filename_text=request.filename_text,
            output_format=output_format,
            raw_path=Path("/tmp/test.mp3"),
            raw_extension=output_format,
            latency_seconds=0.1,
            success=self._success,
        )


# ---------------------------------------------------------------------------
# _boolish helper
# ---------------------------------------------------------------------------

class TestBoolish:
    def test_true_bool(self) -> None:
        assert _boolish(True) is True

    def test_false_bool(self) -> None:
        assert _boolish(False) is False

    def test_none_returns_false(self) -> None:
        assert _boolish(None) is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "Yes"])
    def test_truthy_strings(self, val: str) -> None:
        assert _boolish(val) is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "nope", ""])
    def test_falsy_strings(self, val: str) -> None:
        assert _boolish(val) is False


# ---------------------------------------------------------------------------
# TTSProvider base class
# ---------------------------------------------------------------------------

class TestTTSProvider:
    def test_name_property(self, tmp_path: Path) -> None:
        cfg = _make_config(name="my_provider")
        p = _StubProvider(cfg, tmp_path)
        assert p.name == "my_provider"

    def test_is_active_true(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={"enabled": True})
        assert _StubProvider(cfg, tmp_path).is_active() is True

    def test_is_active_false(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={"enabled": False})
        assert _StubProvider(cfg, tmp_path).is_active() is False

    def test_is_active_string_yes(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={"enabled": "yes"})
        assert _StubProvider(cfg, tmp_path).is_active() is True

    def test_is_active_defaults_to_false_when_missing(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={})
        assert _StubProvider(cfg, tmp_path).is_active() is False

    def test_resolve_language_generate_exact_match(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={
            "model": "tts-1",
            "languages": {
                "en": {"actor": "alloy"},
                "fr": {"actor": "nova"},
            },
        })
        p = _StubProvider(cfg, tmp_path)
        result = p.resolve_language_generate("fr")
        assert result["actor"] == "nova"
        assert result["model"] == "tts-1"

    def test_resolve_language_generate_en_fallback(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={
            "model": "tts-1",
            "languages": {
                "en": {"actor": "alloy"},
            },
        })
        p = _StubProvider(cfg, tmp_path)
        result = p.resolve_language_generate("es")
        assert result["actor"] == "alloy"

    def test_resolve_language_generate_first_entry_fallback(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={
            "languages": {
                "fr": {"actor": "nova"},
            },
        })
        p = _StubProvider(cfg, tmp_path)
        result = p.resolve_language_generate("bg")
        assert result["actor"] == "nova"

    def test_resolve_language_generate_no_languages_key(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={"model": "tts-1", "actor": "alloy"})
        p = _StubProvider(cfg, tmp_path)
        result = p.resolve_language_generate("en")
        assert result["model"] == "tts-1"
        assert result["actor"] == "alloy"

    def test_resolve_language_generate_empty_languages(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={"model": "tts-1", "languages": {}})
        p = _StubProvider(cfg, tmp_path)
        result = p.resolve_language_generate("en")
        assert result["model"] == "tts-1"
        assert "languages" not in result

    def test_resolve_language_generate_strips_languages_from_result(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={
            "model": "tts-1",
            "languages": {"en": {"actor": "alloy"}},
        })
        p = _StubProvider(cfg, tmp_path)
        result = p.resolve_language_generate("en")
        assert "languages" not in result

    def test_build_prompt_text_no_prompt(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={"enabled": True})
        p = _StubProvider(cfg, tmp_path)
        request = _make_tts_request(text="apple")
        assert p.build_prompt_text(request) == "apple"

    def test_build_prompt_text_with_prompt(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={"prompt": "Say clearly:"})
        p = _StubProvider(cfg, tmp_path)
        request = _make_tts_request(text="apple")
        result = p.build_prompt_text(request)
        assert result == "Say clearly:\napple"

    def test_build_prompt_text_use_space(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={"use_space": "yes"})
        p = _StubProvider(cfg, tmp_path)
        request = _make_tts_request(text="apple")
        assert p.build_prompt_text(request) == " apple"

    def test_build_prompt_text_use_space_false(self, tmp_path: Path) -> None:
        cfg = _make_config(generate={"use_space": "no"})
        p = _StubProvider(cfg, tmp_path)
        request = _make_tts_request(text="apple")
        assert p.build_prompt_text(request) == "apple"

    def test_build_output_path_creates_dir(self, tmp_path: Path) -> None:
        cfg = _make_config(name="openai")
        p = _StubProvider(cfg, tmp_path)
        request = _make_tts_request(text="apple", language_code="en")
        output_path = p.build_output_path(request, "mp3")
        assert output_path.suffix == ".mp3"
        assert output_path.parent.exists()

    def test_build_output_path_extension_normalised(self, tmp_path: Path) -> None:
        cfg = _make_config(name="openai")
        p = _StubProvider(cfg, tmp_path)
        request = _make_tts_request(text="apple", language_code="en")
        path_with_dot = p.build_output_path(request, ".mp3")
        path_no_dot = p.build_output_path(request, "mp3")
        assert path_with_dot == path_no_dot


# ---------------------------------------------------------------------------
# ProviderFactory
# ---------------------------------------------------------------------------

class TestProviderFactory:
    def setup_method(self) -> None:
        ProviderFactory._builders.clear()

    def test_register_and_create(self, tmp_path: Path) -> None:
        ProviderFactory.register("stub", _StubProvider)
        cfg = _make_config(name="stub")
        provider = ProviderFactory.create(cfg, tmp_path)
        assert isinstance(provider, _StubProvider)

    def test_register_normalises_name(self, tmp_path: Path) -> None:
        ProviderFactory.register("  STUB  ", _StubProvider)
        cfg = _make_config(name="stub")
        provider = ProviderFactory.create(cfg, tmp_path)
        assert isinstance(provider, _StubProvider)

    def test_create_unknown_name_raises(self, tmp_path: Path) -> None:
        cfg = _make_config(name="nonexistent")
        with pytest.raises(ValueError, match="Unsupported provider name"):
            ProviderFactory.create(cfg, tmp_path)

    def test_boolish_true_values(self) -> None:
        for val in (True, "yes", "1", "on", "true"):
            assert ProviderFactory._boolish(val) is True

    def test_boolish_false_values(self) -> None:
        for val in (False, None, "no", "0", "off", "false"):
            assert ProviderFactory._boolish(val) is False

    def test_parse_config_map_dict(self, tmp_path: Path) -> None:
        result = ProviderFactory._parse_config_map({"key": "val"}, "generate", tmp_path / "c.json")
        assert result == {"key": "val"}

    def test_parse_config_map_json_string(self, tmp_path: Path) -> None:
        result = ProviderFactory._parse_config_map('{"a": 1}', "generate", tmp_path / "c.json")
        assert result == {"a": 1}

    def test_parse_config_map_empty_string_returns_empty(self, tmp_path: Path) -> None:
        result = ProviderFactory._parse_config_map("  ", "generate", tmp_path / "c.json")
        assert result == {}

    def test_parse_config_map_invalid_json_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid JSON"):
            ProviderFactory._parse_config_map("not-json", "generate", tmp_path / "c.json")

    def test_parse_config_map_non_object_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            ProviderFactory._parse_config_map("[1, 2]", "generate", tmp_path / "c.json")

    def test_parse_config_map_non_string_non_dict_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            ProviderFactory._parse_config_map(42, "generate", tmp_path / "c.json")

    def test_create_normalises_enabled_flag(self, tmp_path: Path) -> None:
        ProviderFactory.register("stub", _StubProvider)
        cfg = _make_config(name="stub", generate={"enabled": "yes"})
        provider = ProviderFactory.create(cfg, tmp_path)
        assert provider.cfg.generate["enabled"] is True

    def test_create_defaults_enabled_to_false(self, tmp_path: Path) -> None:
        ProviderFactory.register("stub", _StubProvider)
        cfg = _make_config(name="stub", generate={})
        provider = ProviderFactory.create(cfg, tmp_path)
        assert provider.cfg.generate["enabled"] is False


# ---------------------------------------------------------------------------
# build_provider / build_active_providers
# ---------------------------------------------------------------------------

class TestBuildProvider:
    def setup_method(self) -> None:
        ProviderFactory._builders.clear()
        ProviderFactory.register("stub", _StubProvider)

    def test_build_provider_returns_instance(self, tmp_path: Path) -> None:
        cfg = _make_config(name="stub")
        p = build_provider(cfg, tmp_path)
        assert isinstance(p, _StubProvider)

    def test_build_active_providers_includes_only_active(self, tmp_path: Path) -> None:
        configs = [
            _make_config(name="stub", generate={"enabled": True}),
            _make_config(name="stub", generate={"enabled": False}),
            _make_config(name="stub", generate={"enabled": True}),
        ]
        result = build_active_providers(configs, tmp_path)
        assert len(result) == 2

    def test_build_active_providers_empty_when_all_inactive(self, tmp_path: Path) -> None:
        configs = [_make_config(name="stub", generate={"enabled": False})]
        assert build_active_providers(configs, tmp_path) == []

    def test_build_active_providers_empty_input(self, tmp_path: Path) -> None:
        assert build_active_providers([], tmp_path) == []


# ---------------------------------------------------------------------------
# load_provider_configs
# ---------------------------------------------------------------------------

class TestLoadProviderConfigs:
    def _write_generator_config(self, tmp_path: Path, filename: str, payload: dict) -> Path:
        generators_dir = tmp_path / "text_to_speech"
        generators_dir.mkdir(parents=True, exist_ok=True)
        config_file = generators_dir / filename
        config_file.write_text(json.dumps(payload), encoding="utf-8")
        return config_file

    def test_returns_empty_when_no_generators_dir(self, tmp_path: Path) -> None:
        from src.services.text_to_speech.config_loader import load_provider_configs
        result = load_provider_configs(base_dir=tmp_path, config_dir=tmp_path)
        assert result == []

    def test_loads_config_from_valid_json(self, tmp_path: Path) -> None:
        from src.services.text_to_speech.config_loader import load_provider_configs
        self._write_generator_config(tmp_path, "openai.json", {
            "service_provider": {
                "provider": "openai",
                "enabled": True,
                "version": "1.0",
                "config": {
                    "generate": {"model": "tts-1-hd"},
                    "evaluate": {"enabled": False},
                },
            }
        })
        result = load_provider_configs(base_dir=tmp_path, config_dir=tmp_path)
        assert len(result) == 1
        assert result[0].name == "openai"

    def test_enabled_flag_propagated_to_generate(self, tmp_path: Path) -> None:
        from src.services.text_to_speech.config_loader import load_provider_configs
        self._write_generator_config(tmp_path, "openai.json", {
            "service_provider": {
                "provider": "openai",
                "enabled": True,
                "config": {
                    "generate": {},
                    "evaluate": {},
                },
            }
        })
        result = load_provider_configs(base_dir=tmp_path, config_dir=tmp_path)
        assert result[0].generate["enabled"] is True

    def test_uses_filename_stem_when_provider_missing(self, tmp_path: Path) -> None:
        from src.services.text_to_speech.config_loader import load_provider_configs
        self._write_generator_config(tmp_path, "myprovider.json", {
            "service_provider": {
                "config": {
                    "generate": {},
                    "evaluate": {},
                },
            }
        })
        result = load_provider_configs(base_dir=tmp_path, config_dir=tmp_path)
        assert result[0].name == "myprovider"

    def test_skips_non_dict_top_level(self, tmp_path: Path) -> None:
        from src.services.text_to_speech.config_loader import load_provider_configs
        generators_dir = tmp_path / "text_to_speech"
        generators_dir.mkdir(parents=True)
        (generators_dir / "bad.json").write_text("[1, 2, 3]", encoding="utf-8")
        result = load_provider_configs(base_dir=tmp_path, config_dir=tmp_path)
        assert result == []

    def test_version_defaults_to_1_0(self, tmp_path: Path) -> None:
        from src.services.text_to_speech.config_loader import load_provider_configs
        self._write_generator_config(tmp_path, "openai.json", {
            "service_provider": {
                "provider": "openai",
                "config": {
                    "generate": {},
                    "evaluate": {},
                },
            }
        })
        result = load_provider_configs(base_dir=tmp_path, config_dir=tmp_path)
        assert result[0].version == "1.0"
