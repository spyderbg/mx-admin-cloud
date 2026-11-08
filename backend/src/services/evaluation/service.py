"""Evaluation service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing_extensions import override

from src.services.models import Service, ServiceProvider
from src.services.provider_factory import ServiceProviderFactory
from src.services.evaluation.models import EvaluationServiceProvider, STTProvider


class EvaluationProviderFactory(ServiceProviderFactory[EvaluationServiceProvider]):
    """Evaluation-specific provider registry."""
    unsupported_label = "evaluation provider"


class SpeechToTextProviderFactory:
    """STT backends of ``EvaluationEngine``, by the id a TTS configuration names in ``evaluate.stt_provider``.

    Same convention as ``ServiceProviderFactory``: each provider package calls ``register()`` when it is
    imported (``providers/__init__.py`` imports them all). An STT provider has no configuration file, so
    ``create()`` takes the engine's timeout instead of a ``ServiceProvider`` envelope.
    """

    _builders: dict[str, type[STTProvider]] = {}

    @classmethod
    def register(cls, provider_name: str, builder: type[STTProvider]) -> None:
        """Register *builder* under *provider_name*; ValueError if another class already has that id."""
        key = provider_name.strip().lower()
        registered = cls._builders.get(key)
        if registered is not None and registered is not builder:
            raise ValueError(
                f"Duplicate speech-to-text provider '{key}': already registered by {registered.__qualname__}"
            )
        cls._builders[key] = builder

    @classmethod
    def supports(cls, provider_name: str) -> bool:
        """Return whether an STT provider is registered for *provider_name*."""
        return provider_name.strip().lower() in cls._builders

    @classmethod
    def available_names(cls) -> list[str]:
        """Return the registered STT provider ids."""
        return sorted(cls._builders)

    @classmethod
    def create(cls, provider_name: str, timeout_seconds: float) -> STTProvider:
        """Instantiate the STT provider registered for *provider_name*; ValueError if there is none."""
        key = provider_name.strip().lower()
        builder = cls._builders.get(key)
        if builder is None:
            raise ValueError(f"Unsupported speech-to-text provider: '{key}'")
        return builder(timeout_seconds=timeout_seconds)


class EvaluationService(Service):
    """Orchestrator for all evaluation providers."""

    def __init__(
            self,
            service_name: str,
            package_name: str,
            service_dir: Path,
            base_dir: Path,
    ) -> None:
        super().__init__(service_name, package_name, service_dir, base_dir)
        self._providers: list[EvaluationServiceProvider] = []

    @override
    def replace_providers(self, providers: list[ServiceProvider]) -> None:
        """Replace loaded evaluation providers with *providers*."""
        self._providers = [p for p in providers if isinstance(p, EvaluationServiceProvider)]

    @override
    def providers_snapshot(self) -> list[ServiceProvider]:
        """Return loaded evaluation providers, including disabled ones."""
        return list(self._providers)

    @override
    def get_all_providers(self) -> list[EvaluationServiceProvider]:
        """Return all loaded providers, including disabled ones."""
        return list(self._providers)

    @override
    def get_active_providers(self) -> list[EvaluationServiceProvider]:
        """Return providers that report themselves as active (generate.enabled=true)."""
        return [p for p in self._providers if p.is_active()]
