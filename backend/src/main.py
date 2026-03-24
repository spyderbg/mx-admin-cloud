"""FastAPI application entry point: app factory, lifespan, and root router registration."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from src.api.internal import routes_voice_gen
from src.api.v1 import routes_auth, routes_common, routes_content, routes_users
from src.api.dependencies import require_admin, require_uds
from src.core.config import settings
from src.core.utils import is_windows_platform, is_debug_mode
from src.db.database import UsersDb
from src.services.logging import LoggingSystem


@asynccontextmanager
async def lifespan(app_lifespan: FastAPI) -> AsyncIterator[None]:
    """Initialize shared services during application startup."""
    await run_in_threadpool(UsersDb.init_db)

    log_config = Path(settings.CONFIG_DIR) / "loggers"
    app_lifespan.state.logging_system = None
    if log_config.exists():
        logging_system = LoggingSystem(
            base_dir=Path("."),
            output_dir=Path(settings.OUTPUT_DIR),
            config_path=log_config,
            profile=settings.LOG_PROFILE,
        )
        await run_in_threadpool(logging_system.configure)
        app_lifespan.state.logging_system = logging_system

    if is_debug_mode() or is_windows_platform():
        if is_debug_mode():
            logging.warning("Backend is starting in DEBUG MODE - security dependencies are disabled!!!")
        else:
            logging.info("Unix domain sockets are disabled on Windows platform - UDS dependencies are bypassed.")
        app_lifespan.dependency_overrides[require_uds] = lambda: None
        if is_debug_mode():
            app_lifespan.dependency_overrides[require_admin] = lambda: None

    yield


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.include_router(routes_auth.router, prefix=settings.API_V1_STR, tags=["auth"])
app.include_router(routes_common.router, prefix=settings.API_V1_STR, tags=["tasks"])
app.include_router(routes_content.router, prefix=settings.API_V1_STR, tags=["content"])
app.include_router(routes_users.router, prefix=settings.API_V1_STR, tags=["users"])
app.include_router(routes_voice_gen.router, tags=["internal"])


@app.get("/")
def read_root():
    """Read the root endpoint."""
    return {"message": f"Welcome to {settings.PROJECT_NAME}."}
