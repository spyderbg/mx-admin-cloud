"""Translation subsystem: base class, factory, implementations, and helpers."""

from src.services.text_translation.models import TextTranslate
from src.services.text_translation.service import (
    TranslatorFactory,
    build_translator,
    build_translators,
    translate_with_all_active,
)

__all__ = [
    "TextTranslate",
    "TranslatorFactory",
    "build_translator",
    "build_translators",
    "translate_with_all_active",
]

# Trigger self-registration of all implementations
from src.services.text_translation import providers as _providers  # noqa: F401

# Register this service with the plugin registry
from src.services.plugin_registry import ServicePlugin, ServicePluginRegistry  # noqa: E402
from src.services.text_translation.config_loader import load_translator_configs as _load_tt_cfgs  # noqa: E402


def _tt_load(settings):
    return _load_tt_cfgs(base_dir=settings.BASE_DIR, config_dir=settings.config_path)


def _tt_build(configs, settings):
    return build_translators(configs, settings.BASE_DIR)


ServicePluginRegistry.register(ServicePlugin(
    service_key="text_translation",
    registry_attr="translators",
    load_configs=_tt_load,
    build_instances=_tt_build,
))
