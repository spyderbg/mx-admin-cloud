"""OpenAI-powered translator using the Chat Completions API."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from src.core.logging import log_translator_request
from src.services.text_translation.models import BaseTranslatorProviderConfig, TextTranslate
from src.services.text_translation.service import TranslatorFactory

_CATEGORY_LINE_RE = re.compile(r"[^\n]*\{\{\s*category\s*\}\}[^\n]*\n?")


@dataclass(slots=True)
class OpenAITranslatorConfig(BaseTranslatorProviderConfig):
    """Typed and validated config for the OpenAI translator."""
    model: str = "gpt-4o"
    temperature: float = 0.0
    prompt: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], config_path: Path) -> "OpenAITranslatorConfig":
        """Parse and validate *data* from the ``service_provider.config`` JSON block.

        Raises: exc:`ValueError` with a file-path context if a field is invalid,
        so errors surface at application startup rather than at request time.
        """
        temp = data.get("temperature", 0.0)
        try:
            temperature = float(temp)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{config_path}: 'temperature' must be a number, got {temp!r}"
            ) from exc

        model = str(data.get("model", "gpt-4o")).strip()
        if not model:
            raise ValueError(f"{config_path}: 'model' must be a non-empty string")

        return cls(
            input_language=str(data.get("input_language", "en")).strip(),
            output_language=str(data.get("output_language", "en")).strip(),
            model=model,
            temperature=temperature,
            prompt=str(data.get("prompt", "")),
        )


class OpenAITTSTranslate(TextTranslate):
    """OpenAI-powered translator using the Chat Completions API."""

    @classmethod
    def register(cls) -> None:
        """Register the OpenAI translator."""
        TranslatorFactory.register("openai_translate", cls, OpenAITranslatorConfig.from_dict)

    @property
    def openai_cfg(self) -> OpenAITranslatorConfig:
        """Return the typed OpenAI provider config."""
        assert isinstance(self.provider_config, OpenAITranslatorConfig)
        return self.provider_config

    def _render_prompt(
        self,
        input_lang_code: str,
        target_lang_code: str,
        category: str | None,
    ) -> str:
        raw: str = self.openai_cfg.prompt

        rendered = raw.replace("{{ input_language }}", self.language_name(input_lang_code))
        rendered = rendered.replace("{{ output_language }}", self.language_name(target_lang_code))

        if category:
            rendered = rendered.replace("{{ category }}", category)
        else:
            rendered = _CATEGORY_LINE_RE.sub("", rendered)

        return rendered.strip()

    async def translate_text(
        self,
        text: str,
        input_lang_code: str,
        target_lang_code: str,
        category: str | None = None,
    ) -> str | None:
        if not text.strip():
            return None

        api_key = self.resolve_api_key()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")

        cfg = self.openai_cfg
        system_content = self._render_prompt(input_lang_code, target_lang_code, category)

        client = AsyncOpenAI(api_key=api_key)
        started_at = time.perf_counter()
        response = await client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": text.strip()},
            ],
            temperature=cfg.temperature,
        )
        latency = time.perf_counter() - started_at
        log_translator_request(
            base_dir=self.base_dir,
            translator=self.name,
            model=cfg.model,
            actor="translator",
            voice_id="",
            language=f"{input_lang_code}->{target_lang_code}",
            input={
                "system": system_content,
                "text": text.strip(),
                "category": category,
            },
            response_duration=latency,
        )

        result = (response.choices[0].message.content or "").strip()
        return result or None


OpenAITTSTranslate.register()
