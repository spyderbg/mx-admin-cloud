"""Text-to-speech service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.services.models import Service, ServiceProvider
from src.services.text_to_speech.base import TTSProvider


class TextToSpeechService(Service):
    """Orchestrator for all TTS providers.

    Loads providers from config files and exposes :meth:`active_providers`
    which routes and the pipeline use to iterate enabled providers.
    """

    def __init__(self, name: str, service_dir: Path, base_dir: Path) -> None:
        super().__init__(name=name, service_dir=service_dir)
        self.base_dir = base_dir
        self._providers: list[TTSProvider] = []

    def load_providers(self, config_dir: Path) -> None:
        """Load and build all TTS providers from *config_dir*."""
        from src.services.config_loader import load_service_configs  # pylint: disable=import-outside-toplevel
        from src.services.text_to_speech import ProviderFactory  # pylint: disable=import-outside-toplevel
        raw = load_service_configs(config_dir, ServiceProvider)
        raw.sort(key=lambda p: p.priority)
        self._providers = [ProviderFactory.create(p, self.base_dir) for p in raw]

    def load_runtime_config(self, config_root: Path) -> None:
        self.load_providers(config_root / "text_to_speech")

    def registry_get(self, key: str) -> list[Any] | None:
        if key == "providers":
            return list(self.active_providers)
        return None

    @property
    def active_providers(self) -> list[TTSProvider]:
        """Return providers that report themselves as active (generate.enabled=true)."""
        return [p for p in self._providers if p.is_active()]
