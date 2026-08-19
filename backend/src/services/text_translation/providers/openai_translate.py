"""OpenAI-powered translator using the Chat Completions API."""

from __future__ import annotations

import re
import time
from typing import Any

from openai import AsyncOpenAI

from src.services.logging.schemas import LogProviderRequest
from src.core.log_client import log_provider_request
from src.services.text_translation.models import TextTranslationServiceProvider, TranslateTextRequest

_CATEGORY_LINE_RE = re.compile(r"[^\n]*\{\{\s*category\s*\}\}[^\n]*\n?")


class OpenAITextTranslate(TextTranslationServiceProvider):
    """OpenAI-powered translator using the Chat Completions API.

    Config keys (from "self.config"):
        "input_language"  — source language code (default ""en"")
        "output_language" — target language code (default ""en"")
        "model"           — OpenAI model ID (default ""gpt-4o"")
        "temperature"     — sampling temperature (default "0.0")
        "prompt"          — system-prompt template (supports "{{ input_language }}",
                              "{{ output_language }}", and "{{ category }}" placeholders)
    """

    @classmethod
    def register(cls) -> None:
        """Register this provider with the TranslatorFactory."""
        from src.services.text_translation.service import \
            TextTranslationProviderFactory  # pylint: disable=import-outside-toplevel
        TextTranslationProviderFactory.register("openai_translate", cls)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        temp = self.config.get("temperature", 0.0)
        try:
            self._temperature = float(temp)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.config_path}: 'temperature' must be a number, got {temp!r}"
            ) from exc

        model = str(self.config.get("model", "gpt-4o")).strip()
        if not model:
            raise ValueError(f"{self.config_path}: 'model' must be a non-empty string")
        self._model = model
        self._prompt = str(self.config.get("prompt", ""))

    def supports(self, input_lang_code: str, target_lang_code: str) -> bool:
        """Return whether this config's fixed language pair matches the request.

        One config file exists per language pair (openai_en_es.json, openai_en_fr.json,
        ...); without this check the service's provider chain would invoke every
        language's provider on every request, tripling API cost and latency.
        """
        expected_input = self.default_input_language()
        expected_output = self.default_output_language()
        return (
                (expected_input is None or expected_input == input_lang_code)
                and (expected_output is None or expected_output == target_lang_code)
        )

    def api_key_env_name(self) -> str:
        """Return "OPENAI_API_KEY".

        The default (config-filename-derived) lookup breaks here: this provider is
        configured via one file per language pair (openai_en_es.json, openai_en_fr.json,
        ...), so "self.name" is "openai_en_es" etc., not "openai" — every language file
        needs to resolve to the same actual OpenAI key.
        """
        return "OPENAI_API_KEY"

    def _render_prompt(
            self,
            input_lang_code: str,
            target_lang_code: str,
            category: str | None,
    ) -> str:
        rendered = self._prompt.replace("{{ input_language }}", self.language_name(input_lang_code))
        rendered = rendered.replace("{{ output_language }}", self.language_name(target_lang_code))

        if category:
            rendered = rendered.replace("{{ category }}", category)
        else:
            rendered = _CATEGORY_LINE_RE.sub("", rendered)

        return rendered.strip()

    async def translate_text(self, request: TranslateTextRequest) -> str | None:
        if not request.text.strip():
            return None

        api_key = self.resolve_api_key()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")

        system_content = self._render_prompt(
            request.input_lang_code, request.target_lang_code, request.category
        )

        client = AsyncOpenAI(api_key=api_key)
        started_at = time.perf_counter()
        response = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": request.text.strip()},
            ],
            temperature=self._temperature,
        )
        latency = time.perf_counter() - started_at
        await log_provider_request(
            LogProviderRequest(
                provider=self.name,
                tts_model=self._model,
                actor="translator",
                voice_id="",
                language=f"{request.input_lang_code}->{request.target_lang_code}",
                input_data={
                    "system": system_content,
                    "text": request.text.strip(),
                    "category": request.category,
                },
                response_duration=latency,
            )
        )

        result = (response.choices[0].message.content or "").strip()
        return result or None


OpenAITextTranslate.register()
