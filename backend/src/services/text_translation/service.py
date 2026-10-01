"""Text translation service business logic: factory and orchestration."""

from __future__ import annotations
from pathlib import Path
from typing_extensions import override

from src.services.models import LangCodeText, Service, ServiceProvider
from src.services.provider_strategy import ProviderSelectionStrategy
from src.services.provider_factory import ServiceProviderFactory
from src.services.text_translation.models import (
    TextTranslationServiceProvider,
    TranslateResult,
    TranslateTextRequest,
)


class TextTranslationProviderFactory(ServiceProviderFactory[TextTranslationServiceProvider]):
    """Translation-specific provider registry."""

    unsupported_label: str = "translator"


class TextTranslationService(Service):
    """Orchestrator for all text translation providers.

    Loads providers from config files and exposes: meth:`translate_text` which
    tries active providers in priority order and returns the first successful
    translation (FIRST_SUCCESS).
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

    @override
    def get_all_providers(self) -> list[TextTranslationServiceProvider]:
        """Return all loaded translation providers, including disabled ones."""
        return list(self._providers)

    @override
    def get_active_providers(self) -> list[TextTranslationServiceProvider]:
        """Return providers that report themselves as active."""
        return [p for p in self._providers if p.is_active()]

    ################################################################################

    async def translate_text(
            self,
            text: str,
            input_lang_code: str | None = None,
            target_lang_code: str | None = None,
            category: str | None = None,
    ) -> TranslateResult:
        """Translate *text* with the first active provider that can (FIRST_SUCCESS).

        Providers are tried in priority order, skipping those that don't support the language
        pair. The first one to return a translation wins; one that returns "None" (can't
        handle it) or raises hands over to the next, and raised errors are reported in
        *errors*. Without any translation, *lang_text* echoes the input text in its language.
        """
        errors: list[str] = []
        input_language: str | None = None
        for provider in self.get_active_providers():
            inp = input_lang_code or provider.default_input_language()
            tgt = target_lang_code or provider.default_output_language()
            if not inp or not tgt or not provider.supports(inp, tgt):
                continue
            input_language = input_language or inp
            try:
                result = await provider.translate_text(
                    TranslateTextRequest(text=text, input_lang_code=inp, target_lang_code=tgt, category=category)
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(f"{provider.name}: {exc}")
                continue
            if result is not None:
                return TranslateResult(success=True, lang_text=LangCodeText(lang_code=tgt, text=result), errors=errors)

        if input_language is None:
            return TranslateResult(success=False, lang_text=None, errors=errors)
        return TranslateResult(
            success=False,
            lang_text=LangCodeText(lang_code=input_language, text=text),
            errors=errors,
        )
