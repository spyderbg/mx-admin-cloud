"""TTS service: delegates to the internal /internal/generators/generate-word endpoint.

Follows the same HTTP-boundary pattern as ``core/translation.py``.
Keeps the ``generate_audio()`` signature so ``content_service.py`` needs no changes.
"""

from datetime import timedelta

import httpx

from src.core.config import settings
from src.security.tokens import create_internal_token


def generate_audio(word: str, lang: str) -> bytes:
    """Generate TTS audio for one word using the first active provider.

    Returns raw audio bytes (format depends on provider config).
    """
    if not word or not word.strip():
        raise ValueError("Word must be non-empty for TTS.")

    token = create_internal_token("tts-service", timedelta(seconds=30))
    response = httpx.post(
        f"{settings.API_BASE_URL}/internal/generators/generate-word",
        json={"word": word.strip(), "lang": lang},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.content
