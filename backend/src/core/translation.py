"""Translation service: delegates to the internal /internal/translate endpoint.

Keeps the ``translate_word()`` signature so ``content_service.py`` needs no changes.
"""

from datetime import timedelta

import httpx

from src.core.config import settings
from src.security.tokens import create_internal_token


def translate_word(
    text: str,
    target_lang_code: str | None = None,
    category: str | None = None,
    input_lang_code: str | None = None,
) -> str:
    """Translate text by calling the internal /internal/translate endpoint."""
    if not text or not text.strip():
        return text.strip()

    token = create_internal_token("translation-service", timedelta(seconds=30))
    response = httpx.post(
        f"{settings.API_BASE_URL}/internal/translate",
        json={
            "text": text.strip(),
            "target_lang_code": target_lang_code,
            "input_lang_code": input_lang_code,
            "category": category,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["translated_text"] or text.strip()
