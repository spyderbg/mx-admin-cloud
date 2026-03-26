"""OpenAI-powered translator using the Chat Completions API."""

from __future__ import annotations

import re
import time

from openai import AsyncOpenAI

from src.core.logging import log_translator_request
from src.services.translation.models import TextTranslate
from src.services.translation.service import TranslatorFactory

_CATEGORY_LINE_RE = re.compile(r"[^\n]*\{\{\s*category\s*\}\}[^\n]*\n?")


class OpenAITTSTranslate(TextTranslate):
    """OpenAI-powered translator using the Chat Completions API."""

    @classmethod
    def register(cls) -> None:
        """Register the OpenAI translator."""
        TranslatorFactory.register("openai_translate", cls)

    def _render_prompt(
        self,
        input_lang_code: str,
        target_lang_code: str,
        category: str | None,
    ) -> str:
        raw: str = str(self.config.get("prompt", ""))

        rendered = raw.replace("{{ input_language }}", self.language_name(input_lang_code))
        rendered = rendered.replace("{{ target_language }}", self.language_name(target_lang_code))

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

        cfg = self.config
        model = cfg.get("model", "gpt-4o")
        temperature = cfg.get("temperature", 0)

        system_content = self._render_prompt(input_lang_code, target_lang_code, category)

        client = AsyncOpenAI(api_key=api_key)
        started_at = time.perf_counter()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": text.strip()},
            ],
            temperature=temperature,
        )
        latency = time.perf_counter() - started_at
        log_translator_request(
            base_dir=self.base_dir,
            translator=self.name,
            model=str(model),
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
