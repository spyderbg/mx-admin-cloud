"""Internal API routes (service-to-service and admin CLI, protected by service token or admin JWT)."""

from src.api.internal import (
    evaluators_routes,
    generators_routes,
    jobs_routes,
    optimizers_routes,
    translators_routes,
)

__all__ = [
    "evaluators_routes.py",
    "generators_routes.py",
    "jobs_routes.py",
    "optimizers_routes.py",
    "translators_routes.py",
]
