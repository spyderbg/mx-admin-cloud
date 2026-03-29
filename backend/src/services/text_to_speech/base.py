"""Base TTS provider abstraction."""

from __future__ import annotations

import logging
from abc import abstractmethod
from pathlib import Path
from typing import Any

from src.core.config import settings
from src.core.models import GeneratedAudio, TTSRequest
from src.services.base import ServiceProvider
from src.services.text_to_speech.models import ProviderConfig

logger = logging.getLogger(__name__)


def _boolish(value: object) -> bool:
    """Coerce a config value to bool; accepts True/False, None, and common string truths."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class TTSProvider(ServiceProvider):
    """Common interface for all TTS provider implementations."""

    def __init__(self, cfg: ProviderConfig, base_dir: Path) -> None:
        super().__init__(cfg, base_dir)

    def is_active(self) -> bool:
        """Return True when the provider's generate section is marked active."""
        return _boolish(self.cfg.generate.get("enabled", False))

    def resolve_language_generate(self, language_code: str) -> dict[str, Any]:
        """Return a merged generate dict with language-specific overrides applied.

        Lookup order for the ``languages`` map:
        1. Exact ``language_code`` key.
        2. ``"en"`` fallback (if present).
        3. First entry in the map (arbitrary but deterministic in Python 3.7+).
        4. No overrides -- return the base generate dict as-is.
        """
        base: dict[str, Any] = dict(self.cfg.generate)
        languages: dict[str, Any] | None = base.pop("languages", None)
        if not isinstance(languages, dict) or not languages:
            return base

        overrides = languages.get(language_code)
        if overrides is None:
            overrides = languages.get("en")
        if overrides is None:
            overrides = next(iter(languages.values()))
        if isinstance(overrides, dict):
            base.update(overrides)
        return base

    def build_prompt_text(self, request: TTSRequest) -> str:
        """Combine the configured prompt prefix with the request text."""
        gen = self.cfg.generate
        prompt = str(gen.get("prompt", "")).strip()
        use_space = str(gen.get("use_space", "no")).strip().lower() in {
            "yes", "true", "1", "on",
        }
        text = request.text
        if use_space:
            text = f" {text}"
        if prompt:
            return f"{prompt}\n{text}"
        return text

    def build_output_path(self, request: TTSRequest, extension: str) -> Path:
        """Resolve the full output file path for the generated audio."""
        output_dir = settings.raw_output_dir(request.language_code, self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_stem = settings.build_output_stem(request)
        return output_dir / f"{output_stem}.{extension.lstrip('.')}"

    @abstractmethod
    async def generate(self, request: TTSRequest, output_format: str, client: Any) -> GeneratedAudio:
        """Generate raw audio for a single request."""

    async def evaluate(self, *_: Any, **__: Any) -> None:
        """Optional STT evaluation hook; providers that support it override this."""
        return None
