"""Content API routes (public v1).

Exposes category and item CRUD endpoints. Delegates to ContentService
resolved from the ServiceRegistry.

Split by resource: categories.py (categories and their images/voices), items.py (items and
their images/voices), common.py (shared paths, helpers and dependencies). Categories are
included first so every route keeps its original registration order.
"""

from fastapi import APIRouter

from src.core.config import settings
from src.services.content.routes import categories, items
from src.services.content.routes.common import get_content_service, get_voice_generation_available

router = APIRouter(prefix=f"{settings.API_V1_STR}/content")
router.include_router(categories.router)
router.include_router(items.router)

__all__ = ["router", "get_content_service", "get_voice_generation_available"]
