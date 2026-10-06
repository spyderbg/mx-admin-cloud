"""Base TTS provider abstraction."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from abc import abstractmethod, ABC
from pathlib import Path
from typing import Any

from src.core.models import GeneratedAudio
from src.services.models import ServiceProvider

logger = logging.getLogger(__name__)


@dataclass(slots=True)
# pylint: disable=too-many-instance-attributes
class TTSRequest:
    """Single text item to be synthesized, loaded from the input file."""

    request_id: str  # unique id for this request
    text: str  # source text to synthesize
    language_code: str  # ISO language code, e.g. "en"
    language_name: str  # human-readable language, e.g. "English"
    filename_text: str  # text used in the output file name
    expected_text: str  # reference text for evaluation
    source_file: str  # originating input file path
    source_type: str  # input format, e.g. "csv", "txt"
    row_index: int  # row position in the input file
    output_format: str = "mp3"  # requested audio format, e.g. "mp3"
    column_index: int | None = None  # column position (multi-column inputs)
    metadata: dict[str, Any] = field(default_factory=dict)  # extra pass-through data
    # Per-request voice settings ("speed", "stability", "similarity") layered over the
    # configuration's own; see TTSProvider.apply_generate_overrides.
    overrides: dict[str, Any] = field(default_factory=dict)


class TTSProvider(ServiceProvider, ABC):
    """Common interface for all TTS provider implementations.

    The TTS "config" dict (from "service_provider.config" in JSON) has two
    sub-dicts: ""generate"" and ""evaluate"".  These are accessed via the:
    attr:`generate_config 'and: attr:`evaluate_config` properties.

    Active state for TTS is driven by "config.generate.enabled" rather than
    the top-level "enabled" field, so: meth:`is_active` is overridden here.
    """

    @property
    def generate_config(self) -> dict[str, Any]:
        """Return a copy of the "generate" sub-dict from "self.config"."""
        return dict(self.config.get("generate", {}))

    @property
    def evaluate_config(self) -> dict[str, Any]:
        """Return a copy of the ""evaluate"" sub-dict from "self.config"."""
        return dict(self.config.get("evaluate", {}))

    def resolve_language_generate(self, language_code: str) -> dict[str, Any]:
        """Return a merged generate dict with language-specific overrides applied.

        Lookup order for the "languages" map:
        1. Exact "language_code" key.
        2. "en" fallback (if present).
        3. First entry in the map (arbitrary but deterministic in Python 3.7+).
        4. No overrides — return the base generate dict as-is.
        """
        base: dict[str, Any] = self.generate_config
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

    def resolve_request_generate(self, request: TTSRequest) -> dict[str, Any]:
        """Return the generate dict for the request's language with its per-request overrides applied."""
        return self.apply_generate_overrides(self.resolve_language_generate(request.language_code), request.overrides)

    def apply_generate_overrides(self, generate: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        """Return *generate* with the per-request *overrides* layered over it.

        "speed" replaces the configured speed; "stability" and "similarity" go into
        "voice_settings" as "stability" and "similarity_boost". A provider that names a
        setting differently overrides this.
        """
        merged = dict(generate)
        if overrides.get("speed") is not None:
            merged["speed"] = overrides["speed"]
        voice_settings = {
            key: overrides[name]
            for name, key in (("stability", "stability"), ("similarity", "similarity_boost"))
            if overrides.get(name) is not None
        }
        if voice_settings:
            configured = merged.get("voice_settings")
            merged["voice_settings"] = {**(configured if isinstance(configured, dict) else {}), **voice_settings}
        return merged

    def build_prompt_text(self, request: TTSRequest) -> str:
        """Combine the configured prompt prefix with the request text."""
        gen = self.generate_config
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
        from src.core.config import settings  # pylint: disable=import-outside-toplevel

        output_dir = settings.raw_output_dir(request.language_code, self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_stem = settings.build_output_stem(request)
        return output_dir / f"{output_stem}.{extension.lstrip('.')}"

    @abstractmethod
    async def generate(self, request: TTSRequest, client: Any) -> GeneratedAudio:
        """Generate raw audio for a single request."""

    async def evaluate(self, *_: Any, **__: Any) -> None:
        """Optional STT evaluation hook; providers that support it override this."""
        return None
