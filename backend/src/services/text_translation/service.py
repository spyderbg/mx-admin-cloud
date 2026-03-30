"""Text translation service business logic: factory and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from src.services.models import Service, ServiceProvider
from src.services.text_translation.models import TextTranslationServiceProvider


class TranslatorFactory:
    """Registry that maps provider names to concrete translator classes."""

    _builders: dict[str, type[TextTranslationServiceProvider]] = {}

    @classmethod
    def register(
            cls,
            translator_name: str,
            builder: type[TextTranslationServiceProvider],
    ) -> None:
        """Register a concrete translator class under *translator_name*.

        Provider modules call this from their module-level ``ProviderClass.register()``
        statements, which are triggered automatically when the module is imported.
        """
        cls._builders[translator_name.strip().lower()] = builder

    @classmethod
    def create(
            cls,
            provider: ServiceProvider,
            base_dir: Path,
    ) -> TextTranslationServiceProvider:
        """Instantiate the concrete translator for *provider*.

        Raises :exc:`ValueError` when no builder is registered for
        ``provider.name``.
        """
        key = provider.name.strip().lower()
        builder = cls._builders.get(key)
        if builder is None:
            raise ValueError(
                f"Unsupported translator: '{provider.name}' "
                f"(file='{provider.config_path}')"
            )
        return builder(
            service_type=provider.service_type,
            name=provider.name,
            enabled=provider.enabled,
            priority=provider.priority,
            config=provider.config,
            config_path=provider.config_path,
            base_dir=base_dir,
        )


class TextTranslationService(Service):
    """Orchestrator for all text translation providers.

    Loads providers from config files and exposes :meth:`translate_text` which
    iterates active providers in priority order, chaining results until the first
    successful translation of each step.
    """

    def __init__(self, name: str, service_dir: Path, base_dir: Path) -> None:
        super().__init__(name=name, service_dir=service_dir)
        self.base_dir = base_dir
        self._providers: list[TextTranslationServiceProvider] = []

    def load_providers(self, config_dir: Path) -> None:
        """Load and build all enabled translators from *config_dir*, sorted by priority."""
        from src.services.config_loader import load_service_configs  # pylint: disable=import-outside-toplevel
        raw = load_service_configs(config_dir, ServiceProvider)
        raw.sort(key=lambda p: p.priority)
        self._providers = [
            TranslatorFactory.create(p, self.base_dir)
            for p in raw
            if p.enabled
        ]

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
        that returns ``None`` is skipped (fallback).  Returns the original *text*
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
