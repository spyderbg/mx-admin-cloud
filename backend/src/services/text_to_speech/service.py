"""Text-to-speech service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path

from src.services.models import Service
from src.services.text_to_speech.base import TTSProvider


class TextToSpeechService(Service):
    """Orchestrator for all TTS providers.

    Loads providers from config files and exposes :meth:`active_providers`
    which routes and the pipeline use to iterate enabled providers.
    """

    def __init__(self, name: str, service_dir: Path, base_dir: Path) -> None:
        super().__init__(name=name, service_dir=service_dir, base_dir=base_dir)
        self._providers: list[TTSProvider] = []

    def replace_providers(self, providers: list[object]) -> None:
        """Replace loaded TTS providers with *providers*."""
        self._providers = [p for p in providers if isinstance(p, TTSProvider)]

    def providers_snapshot(self) -> list[object]:
        """Return loaded TTS providers, including disabled ones."""
        return list(self._providers)

    @property
    def active_providers(self) -> list[TTSProvider]:
        """Return providers that report themselves as active (generate.enabled=true)."""
        return [p for p in self._providers if p.is_active()]
