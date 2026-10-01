"""Text-to-speech service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing_extensions import override

from src.core.models import GeneratedAudio
from src.services.models import Service, ServiceProvider
from src.services.provider_strategy import ProviderSelectionStrategy, first_success
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

    Loads providers from config files and exposes: "get_active_providers()"
    which routes and the pipeline use to iterate enabled providers.
    """

    SUPPORTED_STRATEGIES = (ProviderSelectionStrategy.FIRST_SUCCESS,)
    DEFAULT_STRATEGY = ProviderSelectionStrategy.FIRST_SUCCESS

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
        return self.get_all_providers()

    @override
    def get_all_providers(self) -> list[TTSProvider]:
        """Return all loaded providers, including disabled ones."""
        return list(self._providers)

    @override
    def get_active_providers(self) -> list[TTSProvider]:
        """Return active providers ordered by ascending priority."""
        return [p for p in self._providers if p.is_active()]

    ################################################################################

    def _resolve_active_provider(self, provider_name: str | None = None) -> TTSProvider:
        """Return the requested active provider or first active by priority."""
        providers = self.get_active_providers()
        if not providers:
            raise NoActiveTTSProvidersError("No active TTS providers configured.")

        if provider_name is None:
            return providers[0]

        for provider in providers:
            if provider.name == provider_name:
                return provider
        raise TTSProviderNotFoundError(f"Provider '{provider_name}' not found or not active.")

    def generation_parameters(self, provider_name: str, language_code: str) -> dict[str, Any]:
        """Return the effective configured generation parameters for a provider and language."""
        return self._resolve_active_provider(provider_name).resolve_language_generate(language_code)

    async def generate(
            self,
            request: TTSRequest,
            client: Any,
            *,
            provider_name: str | None = None,
    ) -> GeneratedAudio:
        """Generate audio with the requested provider, or with the first active provider that succeeds.

        With *provider_name* only that provider runs and its result is returned as is, even when
        unsuccessful. Otherwise (FIRST_SUCCESS) active providers are tried in priority order and
        a provider that raises or returns an unsuccessful result hands over to the next one;
        AllProvidersFailedError carries every provider's error when none succeeds.
        """
        if provider_name is not None:
            provider = self._resolve_active_provider(provider_name=provider_name)
            return await provider.generate(request, client)
        providers = self.get_active_providers()
        if not providers:
            raise NoActiveTTSProvidersError("No active TTS providers configured.")
        return await first_success(
            providers,
            lambda provider: provider.generate(request, client),
            failure=lambda result: None if result.success else (result.error or "Generation failed."),
        )
