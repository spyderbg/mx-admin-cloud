"""Content API routes (public v1).

Exposes category and item CRUD endpoints. Delegates to ContentService
resolved from the ServiceRegistry.

Split by resource: categories.py (categories and their images/voices), items.py (items and
their images/voices), common.py (shared paths, helpers and dependencies). Categories are
included first so every route keeps its original registration order. static_files.py holds
the /content file mount the app serves the content directory through.
"""

from fastapi import APIRouter, Depends

from src.api.dependencies import require_write_access
from src.core.config import settings
from src.services.content.routes import categories, items
from src.services.content.routes.common import (
    get_content_service,
    get_image_generation_available,
    get_voice_configurations_available,
    get_voice_generation_available,
    get_voice_optimization_available,
)
from src.services.content.routes.static_files import ContentStaticFiles

# Any signed-in user can read content; only admins can change it.
router = APIRouter(prefix=f"{settings.API_V1_STR}/content", dependencies=[Depends(require_write_access)])
router.include_router(categories.router)
router.include_router(items.router)

__all__ = [
    "router",
    "ContentStaticFiles",
    "get_content_service",
    "get_image_generation_available",
    "get_voice_configurations_available",
    "get_voice_generation_available",
    "get_voice_optimization_available",
]
