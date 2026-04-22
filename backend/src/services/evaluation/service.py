"""Evaluation service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing_extensions import override

from src.services.models import Service, ServiceProvider
from src.services.provider_factory import ServiceProviderFactory
from src.services.evaluation.models import EvaluationServiceProvider


class EvaluationProviderFactory(ServiceProviderFactory[EvaluationServiceProvider]):
    """Evaluation-specific provider registry."""
    unsupported_label = "evaluation provider"


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

    @property
    def all_providers(self) -> list[EvaluationServiceProvider]:
        """Return all loaded providers, including disabled ones."""
        return list(self._providers)

    @property
    def active_providers(self) -> list[EvaluationServiceProvider]:
        """Return providers that report themselves as active (generate.enabled=true)."""
        return [p for p in self._providers if p.is_active()]

    @override
    def get_all_providers(self) -> list[EvaluationServiceProvider]:
        """Return all loaded providers, including disabled ones."""
        return self.all_providers

    @override
    def get_active_providers(self) -> list[EvaluationServiceProvider]:
        """Return active providers."""
        return self.active_providers
