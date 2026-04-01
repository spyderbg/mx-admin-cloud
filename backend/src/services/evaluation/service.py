from __future__ import annotations

from pathlib import Path
from typing import Sequence

from src.services.models import Service
from src.services.provider_factory import ServiceProviderFactory
from src.services.evaluation.models import EvaluationServiceProvider

class EvaluationProviderFactory(ServiceProviderFactory[EvaluationServiceProvider]):
    """TTS-specific provider registry."""
    unsupported_label = "TTS provider"


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

    def replace_providers(self, providers: list[object]) -> None:
        """Replace loaded TTS providers with *providers*."""
        self._providers = [p for p in providers if isinstance(p, EvaluationServiceProvider)]

    def providers_snapshot(self) -> list[object]:
        """Return loaded TTS providers, including disabled ones."""
        return list(self._providers)

    @property
    def active_providers(self) -> list[EvaluationServiceProvider]:
        """Return providers that report themselves as active (generate.enabled=true)."""
        return [p for p in self._providers if p.is_active()]
