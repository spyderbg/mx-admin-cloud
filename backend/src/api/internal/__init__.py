"""Internal API routes (service-to-service and admin CLI, protected by service token or admin JWT)."""

from src.api.internal import (
    jobs_routes,
    translators_routes,
    generators_routes,
    optimizers_routes,
    evaluators_routes,
)

__all__ = [
    "jobs_routes",
    "translators_routes",
    "generators_routes",
    "optimizers_routes",
    "evaluators_routes",
]
