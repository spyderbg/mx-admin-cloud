"""Text translation service business logic: factory and orchestration."""

from __future__ import annotations
from collections.abc import Sequence
from pathlib import Path
from typing_extensions import override

from src.services.models import Service, ServiceProvider
from src.services.provider_factory import ServiceProviderFactory
from src.services.text_translation.models import TextTranslationServiceProvider


class TextTranslationProviderFactory(ServiceProviderFactory[TextTranslationServiceProvider]):
    """Translation-specific provider registry."""

    unsupported_label: str = "translator"


class TextTranslationService(Service):
    """Orchestrator for all text translation providers.

    Loads providers from config files and exposes: meth:`translate_text` which
    iterates active providers in priority order, chaining results until the first
    successful translation of each step.
    """

    def __init__(
            self,
            service_name: str,
            package_name: str,
            service_dir: Path,
            base_dir: Path,
    ) -> None:
        super().__init__(service_name, package_name, service_dir, base_dir)
        self._providers: list[TextTranslationServiceProvider] = []

    @override
    def replace_providers(self, providers: list[ServiceProvider]) -> None:
        """Replace loaded translation providers with *providers*."""
        self._providers = [
            p for p in providers if isinstance(p, TextTranslationServiceProvider)
        ]

    @override
    def providers_snapshot(self) -> list[ServiceProvider]:
        """Return loaded translation providers, including disabled ones."""
        return list(self._providers)

    @property
    def all_providers(self) -> list[TextTranslationServiceProvider]:
        """Return all loaded translation providers, including disabled ones."""
        return list(self._providers)

    @property
    def active_providers(self) -> list[TextTranslationServiceProvider]:
        """Return providers that report themselves as active."""
        return [p for p in self._providers if p.is_active()]

    async def translate_text(
            self,
            text: str,
            input_lang_code: str | None = None,
            target_lang_code: str | None = None,
            category: str | None = None,
    ) -> str:
        """Translate *text* through all active providers in priority order.

        Each provider's output is fed as input to the next provider.  A provider
        that returns "None" is skipped (fallback).  Returns the original *text*
        when no provider succeeds.
        """
        current = text
        for provider in self.active_providers:
            inp = input_lang_code or provider.default_input_language()
            tgt = target_lang_code or provider.default_output_language()
            if not inp or not tgt:
                continue
            try:
                result = await provider.translate_text(current, inp, tgt, category)
                if result is not None:
                    current = result
            except Exception:  # pylint: disable=broad-exception-caught
                continue
        return current


async def translate_with_all_active(
        translators: Sequence[TextTranslationServiceProvider],
        text: str,
        input_lang_code: str | None = None,
        target_lang_code: str | None = None,
        category: str | None = None,
) -> str:
    """Module-level wrapper: run *translators* in order and return the result.

    Kept for backward compatibility with call sites that pass an explicit
    list of providers rather than using :class:`TextTranslationService` directly.
    """
    current = text
    for translator in translators:
        inp = input_lang_code or translator.default_input_language()
        tgt = target_lang_code or translator.default_output_language()
        if not inp or not tgt:
            continue
        try:
            result = await translator.translate_text(current, inp, tgt, category)
            if result is not None:
                current = result
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return current
