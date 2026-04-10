"""Internal API routes (service-to-service and admin CLI, protected by service token or admin JWT)."""

from src.api.internal import (
    jobs_routes,
    logging_routes,
    service_config_routes,
    text_translation_routes,
    text_to_speech_routes,
    optimizers_routes,
    evaluators_routes,
)

__all__ = [
    "jobs_routes",
    "logging_routes",
    "service_config_routes",
    "text_translation_routes",
    "text_to_speech_routes",
    "optimizers_routes",
    "evaluators_routes",
]
