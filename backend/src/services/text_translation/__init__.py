"""Translation subsystem: base class, factory, implementations, and helpers."""

from src.services.text_translation.models import TextTranslate, TranslatorConfig
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

ServicePluginRegistry.register(
    ServicePlugin(
        registry_attr="translators",
        config_dir="text_translation",
        config_class=TranslatorConfig,
        factory_create=TranslatorFactory.create,
        base_dir_key="BASE_DIR",
        sort_key="priority",
    ),
    module_name=__name__,
)
