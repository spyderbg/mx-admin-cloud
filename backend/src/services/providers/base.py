"""Base provider abstractions and provider factory."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..models import GeneratedAudio, ProviderConfig, TTSRequest
from ..output_paths import build_output_stem, raw_output_dir

logger = logging.getLogger(__name__)


class TTSProvider(ABC):
    def __init__(self, config: ProviderConfig, base_dir: Path) -> None:
        self.config = config
        self.base_dir = base_dir

    @property
    def name(self) -> str:
        return self.config.name

    def is_active(self) -> bool:
        return bool(self.config.generate.get("active"))

    def api_key_env_name(self) -> str:
        return f"{self.config.name.upper()}_API_KEY"

    def resolve_api_key(self) -> str:
        return os.getenv(self.api_key_env_name(), "")

    def resolve_language_generate(self, language_code: str) -> dict[str, Any]:
        """Return a merged generate dict with language-specific overrides applied.

        Lookup order for the ``languages`` map:
        1. Exact ``language_code`` key.
        2. ``"en"`` fallback (if present).
        3. First entry in the map (arbitrary but deterministic in Python 3.7+).
        4. No overrides -- return the base generate dict as-is.
        """
        base: dict[str, Any] = dict(self.config.generate)
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
        prompt = str(self.config.generate.get("prompt", "")).strip()
        use_space = str(self.config.generate.get("use_space", "no")).strip().lower() in {
            "yes", "true", "1", "on",
        }
        text = request.text
        if use_space:
            text = f" {text}"
        if prompt:
            return f"{prompt}\n{text}"
        return text

    def build_output_path(self, request: TTSRequest, extension: str) -> Path:
        output_dir = raw_output_dir(self.base_dir, request.language_code, self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_stem = build_output_stem(request)
        return output_dir / f"{output_stem}.{extension.lstrip('.')}"

    @abstractmethod
    async def generate(self, request: TTSRequest, output_format: str, client: Any) -> GeneratedAudio:
        """Generate raw audio for a single request."""

    async def evaluate(self, *_: Any, **__: Any) -> None:
        return None


def build_provider(config: ProviderConfig, base_dir: Path) -> TTSProvider:
    name = config.name.lower()
    if name == "openai":
        from .openai_tts import OpenAITTSProvider
        return OpenAITTSProvider(config, base_dir)
    if name == "elevenlabs":
        from .elevenlabs_tts import ElevenLabsTTSProvider
        return ElevenLabsTTSProvider(config, base_dir)
    if name == "speaktor":
        from .speaktor_tts import SpeaktorTTSProvider
        return SpeaktorTTSProvider(config, base_dir)
    raise ValueError(f"Unsupported provider config: {config.name}")
