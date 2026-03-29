"""Unit tests for translation service business logic."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.text_translation.models import TranslatorConfig
from src.services.text_translation.models import TextTranslate
from src.services.text_translation.service import (
    TranslatorFactory,
    build_translator,
    build_translators,
    translate_with_all_active,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    name: str = "test_translator",
    category: str = "translator",
    enabled: bool = True,
    priority: int = 1,
    config: dict | None = None,
    config_path: Path | None = None,
) -> TranslatorConfig:
    return TranslatorConfig(
        name=name,
        category=category,
        enabled=enabled,
        priority=priority,
        config=config if config is not None else {"input_language": "en", "output_language": "fr"},
        config_path=config_path or Path("test.json"),
    )


class _StubTranslator(TextTranslate):
    """Minimal concrete translator for testing."""

    def __init__(self, cfg: TranslatorConfig, base_dir: Path, result: str | None = "translated") -> None:
        super().__init__(cfg, base_dir)
        self._result = result

    @classmethod
    def register(cls) -> None:
        pass

    async def translate_text(
        self,
        text: str,
        input_lang_code: str,
        target_lang_code: str,
        category: str | None = None,
    ) -> str | None:
        return self._result


# ---------------------------------------------------------------------------
# TextTranslate base class
# ---------------------------------------------------------------------------

class TestTextTranslate:
    def test_name_property(self, tmp_path: Path) -> None:
        cfg = _make_config(name="my_translator")
        t = _StubTranslator(cfg, tmp_path)
        assert t.name == "my_translator"

    def test_is_active_true(self, tmp_path: Path) -> None:
        cfg = _make_config(enabled=True)
        assert _StubTranslator(cfg, tmp_path).is_active() is True

    def test_is_active_false(self, tmp_path: Path) -> None:
        cfg = _make_config(enabled=False)
        assert _StubTranslator(cfg, tmp_path).is_active() is False

    def test_api_key_env_name(self, tmp_path: Path) -> None:
        cfg = _make_config(name="openai_translate")
        t = _StubTranslator(cfg, tmp_path)
        assert t.api_key_env_name() == "OPENAI_TRANSLATE_API_KEY"

    def test_language_name_known(self, tmp_path: Path) -> None:
        cfg = _make_config()
        t = _StubTranslator(cfg, tmp_path)
        assert t.language_name("en") == "English"

    def test_language_name_unknown_returns_code(self, tmp_path: Path) -> None:
        cfg = _make_config()
        t = _StubTranslator(cfg, tmp_path)
        assert t.language_name("xx") == "xx"

    def test_default_input_language(self, tmp_path: Path) -> None:
        cfg = _make_config(config={"input_language": "en"})
        assert _StubTranslator(cfg, tmp_path).default_input_language() == "en"

    def test_default_output_language(self, tmp_path: Path) -> None:
        cfg = _make_config(config={"output_language": "fr"})
        assert _StubTranslator(cfg, tmp_path).default_output_language() == "fr"

    def test_default_languages_none_when_missing(self, tmp_path: Path) -> None:
        cfg = _make_config(config={})
        t = _StubTranslator(cfg, tmp_path)
        assert t.default_input_language() is None
        assert t.default_output_language() is None

    def test_default_language_empty_string_returns_none(self, tmp_path: Path) -> None:
        cfg = _make_config(config={"input_language": "  ", "output_language": ""})
        t = _StubTranslator(cfg, tmp_path)
        assert t.default_input_language() is None
        assert t.default_output_language() is None


# ---------------------------------------------------------------------------
# TranslatorFactory
# ---------------------------------------------------------------------------

class TestTranslatorFactory:
    def setup_method(self) -> None:
        # Clear any previously registered builders to isolate tests
        TranslatorFactory._builders.clear()

    def test_register_and_create(self, tmp_path: Path) -> None:
        TranslatorFactory.register("stub", _StubTranslator)
        cfg = _make_config(name="stub")
        translator = TranslatorFactory.create(cfg, tmp_path)
        assert isinstance(translator, _StubTranslator)

    def test_register_normalises_name(self, tmp_path: Path) -> None:
        TranslatorFactory.register("  STUB  ", _StubTranslator)
        cfg = _make_config(name="stub")
        translator = TranslatorFactory.create(cfg, tmp_path)
        assert isinstance(translator, _StubTranslator)

    def test_create_unknown_name_raises(self, tmp_path: Path) -> None:
        cfg = _make_config(name="nonexistent")
        with pytest.raises(ValueError, match="Unsupported translator service name"):
            TranslatorFactory.create(cfg, tmp_path)

    def test_parse_config_dict(self, tmp_path: Path) -> None:
        result = TranslatorFactory._parse_config({"key": "val"}, tmp_path / "c.json")
        assert result == {"key": "val"}

    def test_parse_config_json_string(self, tmp_path: Path) -> None:
        result = TranslatorFactory._parse_config('{"a": 1}', tmp_path / "c.json")
        assert result == {"a": 1}

    def test_parse_config_empty_string_returns_empty(self, tmp_path: Path) -> None:
        result = TranslatorFactory._parse_config("  ", tmp_path / "c.json")
        assert result == {}

    def test_parse_config_invalid_json_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid translator config JSON"):
            TranslatorFactory._parse_config("not-json", tmp_path / "c.json")

    def test_parse_config_non_object_json_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            TranslatorFactory._parse_config("[1, 2, 3]", tmp_path / "c.json")

    def test_parse_config_non_string_non_dict_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            TranslatorFactory._parse_config(42, tmp_path / "c.json")


# ---------------------------------------------------------------------------
# build_translator / build_translators
# ---------------------------------------------------------------------------

class TestBuildTranslator:
    def setup_method(self) -> None:
        TranslatorFactory._builders.clear()
        TranslatorFactory.register("stub", _StubTranslator)

    def test_build_translator_returns_instance(self, tmp_path: Path) -> None:
        cfg = _make_config(name="stub")
        t = build_translator(cfg, tmp_path)
        assert isinstance(t, _StubTranslator)

    def test_build_translators_filters_inactive(self, tmp_path: Path) -> None:
        configs = [
            _make_config(name="stub", enabled=True),
            _make_config(name="stub", enabled=False),
            _make_config(name="stub", enabled=True),
        ]
        result = build_translators(configs, tmp_path)
        assert len(result) == 2

    def test_build_translators_empty_when_all_inactive(self, tmp_path: Path) -> None:
        configs = [_make_config(name="stub", enabled=False)]
        assert build_translators(configs, tmp_path) == []

    def test_build_translators_empty_input(self, tmp_path: Path) -> None:
        assert build_translators([], tmp_path) == []


# ---------------------------------------------------------------------------
# translate_with_all_active
# ---------------------------------------------------------------------------

class TestTranslateWithAllActive:
    @pytest.mark.asyncio
    async def test_returns_original_when_no_translators(self) -> None:
        result = await translate_with_all_active([], "hello", "en", "fr")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_returns_translation_from_single_translator(self, tmp_path: Path) -> None:
        cfg = _make_config(config={"input_language": "en", "output_language": "fr"})
        t = _StubTranslator(cfg, tmp_path, result="bonjour")
        result = await translate_with_all_active([t], "hello", "en", "fr")
        assert result == "bonjour"

    @pytest.mark.asyncio
    async def test_chains_multiple_translators(self, tmp_path: Path) -> None:
        cfg = _make_config(config={"input_language": "en", "output_language": "fr"})
        t1 = _StubTranslator(cfg, tmp_path, result="intermediate")
        t2 = _StubTranslator(cfg, tmp_path, result="final")
        result = await translate_with_all_active([t1, t2], "hello", "en", "fr")
        assert result == "final"

    @pytest.mark.asyncio
    async def test_skips_translator_returning_none(self, tmp_path: Path) -> None:
        cfg = _make_config(config={"input_language": "en", "output_language": "fr"})
        t_none = _StubTranslator(cfg, tmp_path, result=None)
        t_good = _StubTranslator(cfg, tmp_path, result="bonjour")
        result = await translate_with_all_active([t_none, t_good], "hello", "en", "fr")
        assert result == "bonjour"

    @pytest.mark.asyncio
    async def test_continues_after_exception(self, tmp_path: Path) -> None:
        cfg = _make_config(config={"input_language": "en", "output_language": "fr"})

        bad = _StubTranslator(cfg, tmp_path)
        bad.translate_text = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

        good = _StubTranslator(cfg, tmp_path, result="bonjour")
        result = await translate_with_all_active([bad, good], "hello", "en", "fr")
        assert result == "bonjour"

    @pytest.mark.asyncio
    async def test_skips_when_no_resolved_languages(self, tmp_path: Path) -> None:
        cfg = _make_config(config={})  # no default languages
        t = _StubTranslator(cfg, tmp_path, result="should_not_appear")
        # No explicit lang codes, no defaults → translator is skipped
        result = await translate_with_all_active([t], "hello")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_uses_translator_default_languages(self, tmp_path: Path) -> None:
        cfg = _make_config(config={"input_language": "en", "output_language": "es"})
        t = _StubTranslator(cfg, tmp_path, result="hola")
        # No explicit lang codes → falls back to translator defaults
        result = await translate_with_all_active([t], "hello")
        assert result == "hola"

    @pytest.mark.asyncio
    async def test_all_translators_return_none_returns_original(self, tmp_path: Path) -> None:
        cfg = _make_config(config={"input_language": "en", "output_language": "fr"})
        translators = [_StubTranslator(cfg, tmp_path, result=None) for _ in range(3)]
        result = await translate_with_all_active(translators, "hello", "en", "fr")
        assert result == "hello"


# ---------------------------------------------------------------------------
# StaticTTSTranslate
# ---------------------------------------------------------------------------

class TestStaticTTSTranslate:
    def _make_content_dir(self, tmp_path: Path) -> Path:
        """Create a minimal content directory with CSV translation files."""
        category = tmp_path / "foods"
        category.mkdir()
        (category / "images_en.csv").write_text("apple\nbanana\n", encoding="utf-8")
        (category / "images_fr.csv").write_text("pomme\nbanane\n", encoding="utf-8")
        return tmp_path

    @pytest.mark.asyncio
    async def test_translates_known_word(self, tmp_path: Path) -> None:
        from src.services.text_translation.providers.static_translate import StaticTTSTranslate

        content_dir = self._make_content_dir(tmp_path)
        cfg = _make_config(name="static_translate", config={"content_dir": str(content_dir)})
        t = StaticTTSTranslate(cfg, tmp_path)
        result = await t.translate_text("apple", "en", "fr")
        assert result == "pomme"

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_word(self, tmp_path: Path) -> None:
        from src.services.text_translation.providers.static_translate import StaticTTSTranslate

        content_dir = self._make_content_dir(tmp_path)
        cfg = _make_config(name="static_translate", config={"content_dir": str(content_dir)})
        t = StaticTTSTranslate(cfg, tmp_path)
        result = await t.translate_text("cherry", "en", "fr")
        assert result is None

    @pytest.mark.asyncio
    async def test_case_insensitive_lookup(self, tmp_path: Path) -> None:
        from src.services.text_translation.providers.static_translate import StaticTTSTranslate

        content_dir = self._make_content_dir(tmp_path)
        cfg = _make_config(name="static_translate", config={"content_dir": str(content_dir)})
        t = StaticTTSTranslate(cfg, tmp_path)
        result = await t.translate_text("APPLE", "en", "fr")
        assert result == "pomme"

    @pytest.mark.asyncio
    async def test_reverse_direction(self, tmp_path: Path) -> None:
        from src.services.text_translation.providers.static_translate import StaticTTSTranslate

        content_dir = self._make_content_dir(tmp_path)
        cfg = _make_config(name="static_translate", config={"content_dir": str(content_dir)})
        t = StaticTTSTranslate(cfg, tmp_path)
        result = await t.translate_text("pomme", "fr", "en")
        assert result == "apple"

    def test_missing_content_dir_loads_empty(self, tmp_path: Path) -> None:
        from unittest.mock import patch
        from src.services.text_translation.providers.static_translate import StaticTTSTranslate

        cfg = _make_config(name="static_translate", config={"content_dir": str(tmp_path / "nonexistent")})
        # Patch settings.content_path so the fallback also points to a nonexistent dir
        with patch("src.services.text_translation.providers.static_translate.settings") as mock_settings:
            mock_settings.content_path = tmp_path / "also_nonexistent"
            t = StaticTTSTranslate(cfg, tmp_path)
        assert t._lookup == {}
