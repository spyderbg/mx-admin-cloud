"""The /content static file mount, served only while a content provider is active."""

from __future__ import annotations

from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from src.services.content.routes.common import get_content_service


class ContentStaticFiles(StaticFiles):
    """Serve the content directory's files, answering 503 while no content provider is active.

    The mount reads CONTENT_DIR directly rather than through a provider, so without this check
    a disabled content configuration would keep exposing the files the content API refuses.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # The content API routes' own check. Its HTTPException reaches the app's exception
        # middleware like StaticFiles' 404 and 405 do; scope["app"] is the mounting app.
        get_content_service(scope["app"].state.registry)
        await super().__call__(scope, receive, send)
