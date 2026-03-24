"""Internal translator-configuration endpoints.

Exposes translation service configs loaded from ``backend/config/translators/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends  # pylint: disable=import-error

from src.api.dependencies import require_internal
from src.core.config import settings

router = APIRouter(prefix="/internal/voice", tags=["internal-translators"])


@router.get(
    "/translators",
    summary="List translator configurations",
    description=(
        "Return all configured translation services with their service type, "
        "activation state, priority, and config path."
    ),
)
async def list_translators(
    _user: dict = Depends(require_internal),
) -> list[dict[str, Any]]:
    """Return configured translators with service metadata."""
    from src.core.config_loader import load_translator_configs  # pylint: disable=import-outside-toplevel
    configs = load_translator_configs(
        base_dir=settings.BASE_DIR,
        config_dir=settings.config_path,
    )
    return [
        {
            "service_type": c.type,
            "name": c.name,
            "active": c.active,
            "priority": c.priority,
            "config_path": str(c.config_path),
        }
        for c in configs
    ]
