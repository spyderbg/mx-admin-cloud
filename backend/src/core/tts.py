"""TTS service: delegates to multi-provider services.providers.

Keeps the ``generate_audio()`` signature so ``content_service.py`` needs no changes.
"""

import asyncio
import importlib

from src.core.config import settings
from src.services.config_loader import load_provider_configs
from src.services.providers import build_provider


def generate_audio(word: str, lang: str) -> bytes:
    """Generate TTS audio for one word using the first active provider.

    Returns raw audio bytes (format depends on provider config).
    """
    if not word or not word.strip():
        raise ValueError("Word must be non-empty for TTS.")

    base_dir = settings.BASE_DIR
    configs = load_provider_configs(base_dir=base_dir, config_dir=settings.config_path)
    active = [c for c in configs if c.generate.get("active")]
    if not active:
        raise ValueError("No active TTS providers configured.")

    provider = build_provider(active[0], settings.output_path)

    from src.services.models import TTSRequest
    from src.services.languages import language_name, normalize_language_code
    from src.services.output_paths import slugify_filename

    lang_code = normalize_language_code(lang)
    lang_full = language_name(lang_code)
    safe_text = slugify_filename(word.strip(), "item")
    request = TTSRequest(
        request_id=f"sync-{safe_text}-{lang_code}",
        text=word.strip(),
        language_code=lang_code,
        language_name=lang_full,
        filename_text=word.strip(),
        expected_text=word.strip(),
        source_file="content_service",
        source_type="inline",
        row_index=0,
    )

    async def _generate() -> bytes:
        httpx = importlib.import_module("httpx")
        async with httpx.AsyncClient(timeout=60.0) as client:
            result = await provider.generate(request, "mp3", client)
        if not result.success:
            raise RuntimeError(result.error or "TTS generation failed.")
        return result.raw_path.read_bytes()

    return asyncio.run(_generate())
