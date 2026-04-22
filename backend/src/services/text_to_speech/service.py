"""Text-to-speech service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing_extensions import override

from src.core.models import GeneratedAudio
from src.services.models import Service, ServiceProvider
from src.services.provider_factory import ServiceProviderFactory
from src.services.text_to_speech.models import TTSProvider, TTSRequest


class NoActiveTTSProvidersError(Exception):
    """Raised when there are no active TTS providers."""


class TTSProviderNotFoundError(Exception):
    """Raised when a requested TTS provider is not active or missing."""


class TextToSpeechProviderFactory(ServiceProviderFactory[TTSProvider]):
    """TTS-specific provider registry."""
    unsupported_label = "TTS provider"


class TextToSpeechService(Service):
    """Orchestrator for all TTS providers.

    Loads providers from config files and exposes: meth:`active_providers`
    which routes and the pipeline use to iterate enabled providers.
    """

    def __init__(
            self,
            service_name: str,
            package_name: str,
            service_dir: Path,
            base_dir: Path,
    ) -> None:
        super().__init__(service_name, package_name, service_dir, base_dir)
        self._providers: list[TTSProvider] = []

    @override
    def replace_providers(self, providers: list[ServiceProvider]) -> None:
        """Replace loaded TTS providers with *providers*."""
        self._providers = [p for p in providers if isinstance(p, TTSProvider)]

    @override
    def providers_snapshot(self) -> list[ServiceProvider]:
        """Return loaded providers, including disabled ones."""
        return self.all_providers

    @property
    def all_providers(self) -> list[TTSProvider]:
        """Return all loaded providers, including disabled ones."""
        return list(self._providers)

    @property
    def active_providers(self) -> list[TTSProvider]:
        """Return active providers ordered by ascending priority."""
        return [p for p in self._providers if p.is_active()]

    @override
    def get_all_providers(self) -> list[TTSProvider]:
        """Return all loaded providers, including disabled ones."""
        return self.all_providers

    @override
    def get_active_providers(self) -> list[TTSProvider]:
        """Return active providers."""
        return self.active_providers

    def _resolve_active_provider(self, provider_name: str | None = None) -> TTSProvider:
        """Return the requested active provider or first active by priority."""
        providers = self.active_providers
        if not providers:
            raise NoActiveTTSProvidersError("No active TTS providers configured.")

        if provider_name is None:
            return providers[0]

        for provider in providers:
            if provider.name == provider_name:
                return provider
        raise TTSProviderNotFoundError(f"Provider '{provider_name}' not found or not active.")

    async def generate(
            self,
            request: TTSRequest,
            client: Any,
            *,
            provider_name: str | None = None,
    ) -> GeneratedAudio:
        """Generate audio with the requested provider or highest-priority active provider."""
        provider = self._resolve_active_provider(provider_name=provider_name)
        return await provider.generate(request, client)
