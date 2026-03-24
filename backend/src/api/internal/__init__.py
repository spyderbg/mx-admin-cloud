"""Internal API routes (service-to-service and admin CLI, protected by service token or admin JWT)."""

from src.api.internal import (
    routes_evaluators,
    routes_generators,
    routes_jobs,
    routes_optimizers,
    routes_translators,
)

__all__ = [
    "routes_evaluators",
    "routes_generators",
    "routes_jobs",
    "routes_optimizers",
    "routes_translators",
]
