"""Self-hosted Qwen translator using the OpenAI-compatible Chat Completions API."""

from __future__ import annotations

import re
import time
from typing import Any

from openai import AsyncOpenAI

from src.core.log_client import log_provider_request
from src.services.logging.schemas import LogProviderRequest
from src.services.text_translation.models import TextTranslationServiceProvider, TranslateTextRequest
from src.services.text_translation.service import TextTranslationProviderFactory

_CATEGORY_LINE_RE = re.compile(r"[^\n]*\{\{\s*category\s*\}\}[^\n]*\n?")


class QwenTextTranslate(TextTranslationServiceProvider):
    """Translate with Qwen3.8 27B independently of other provider implementations.

    Config supports ``input_language``, ``output_language``, ``model``, ``base_url``,
    ``temperature``, and a ``prompt`` template with language/category placeholders.
    Credentials always come from ``QWEN_API_KEY``.
    """

    DEFAULT_MODEL = "Qwen3.8-27B-Q4_K_M.gguf"
    DEFAULT_BASE_URL = "http://82.103.77.45:8080/v1/"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        temperature = self.config.get("temperature", 0.0)
        try:
            self._temperature = float(temperature)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.config_path}: 'temperature' must be a number, got {temperature!r}"
            ) from exc

        model = self.config.get("model", self.DEFAULT_MODEL)
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"{self.config_path}: 'model' must be a non-empty string")
        self._model = model.strip()

        base_url = self.config.get("base_url") or self.DEFAULT_BASE_URL
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError(f"{self.config_path}: 'base_url' must be a non-empty string")
        self._base_url = base_url.strip()
        self._prompt = str(self.config.get("prompt", ""))

    @classmethod
    def register(cls) -> None:
        """Register the self-hosted translator as ``qwen_translate``."""
        TextTranslationProviderFactory.register("qwen_translate", cls)

    def api_key_env_name(self) -> str:
        """Use the Qwen credential for every language-pair configuration."""
        return "QWEN_API_KEY"

    def supports(self, input_lang_code: str, target_lang_code: str) -> bool:
        """Accept only this configuration's language pair, when specified."""
        source = self.default_input_language()
        target = self.default_output_language()
        return (source is None or source == input_lang_code) and (target is None or target == target_lang_code)

    def _render_prompt(self, request: TranslateTextRequest) -> str:
        """Fill language placeholders and omit category instructions when absent."""
        prompt = self._prompt.replace("{{ input_language }}", self.language_name(request.input_lang_code))
        prompt = prompt.replace("{{ output_language }}", self.language_name(request.target_lang_code))
        if request.category:
            prompt = prompt.replace("{{ category }}", request.category)
        else:
            prompt = _CATEGORY_LINE_RE.sub("", prompt)
        return prompt.strip()

    async def translate_text(self, request: TranslateTextRequest) -> str | None:
        """Request one translation and return ``None`` for empty input or output."""
        text = request.text.strip()
        if not text:
            return None

        api_key = self.resolve_api_key()
        if not api_key:
            raise RuntimeError("QWEN_API_KEY is not set.")

        system_content = self._render_prompt(request)
        started_at = time.perf_counter()
        async with AsyncOpenAI(api_key=api_key, base_url=self._base_url) as client:
            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": text},
                ],
                temperature=self._temperature,
            )
        await log_provider_request(
            LogProviderRequest(
                provider=self.name,
                tts_model=self._model,
                actor="translator",
                voice_id="",
                language=f"{request.input_lang_code}->{request.target_lang_code}",
                input_data={"system": system_content, "text": text, "category": request.category},
                response_duration=time.perf_counter() - started_at,
            )
        )

        result = (response.choices[0].message.content or "").strip()
        return result or None


QwenTextTranslate.register()
