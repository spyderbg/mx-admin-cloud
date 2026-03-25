"""FastAPI application entry point: app factory, lifespan, and root router registration."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncIterator

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

import src.api.internal
from src.api.v1 import routes_auth, routes_common, routes_content, routes_users
from src.api.dependencies import require_admin, require_uds
from src.core.config import settings
from src.core.job_store import JobStore
from src.services.registry_builder import build_registry, reload_registry
from src.core.utils import is_windows_platform, is_debug_mode
from src.db.database import UsersDb
from src.security.tokens import create_internal_token
from src.core.logging import LoggingSystem


@asynccontextmanager
async def lifespan(app_lifespan: FastAPI) -> AsyncIterator[None]:
    """Initialize shared services during application startup."""
    await run_in_threadpool(UsersDb.init_db)

    log_config = settings.config_path / "loggers"
    app_lifespan.state.logging_system = None
    if log_config.exists():
        logging_system = LoggingSystem(
            base_dir=settings.BASE_DIR,
            output_dir=settings.output_path,
            config_path=log_config,
            profile=settings.LOG_PROFILE,
        )
        await run_in_threadpool(logging_system.configure)
        app_lifespan.state.logging_system = logging_system

    # Build the service registry (parses all JSON configs, instantiates providers)
    registry = await build_registry(
        base_dir=settings.BASE_DIR,
        config_dir=settings.config_path,
        output_dir=settings.output_path,
    )
    app_lifespan.state.registry = registry
    logging.info("registry_built providers=%s translators=%s optimizers=%s",
                 len(registry.providers), len(registry.translators), len(registry.optimizers))

    # Job store — in-memory with TTL eviction; swap for SQLite/Redis for multi-worker
    app_lifespan.state.job_store = JobStore(ttl_seconds=3600)

    # Internal service token — freshly signed on each startup, 24-hour lifetime
    app_lifespan.state.internal_token = create_internal_token(
        "backend-internal", timedelta(hours=24)
    )

    # Background task: watch config dir for changes and hot-reload the registry
    async def _watch_configs() -> None:
        try:
            from watchfiles import awatch  # pylint: disable=import-outside-toplevel
            async for _ in awatch(settings.config_path):
                logging.info("config_changed reloading_registry")
                await reload_registry(
                    registry, settings.BASE_DIR, settings.config_path, settings.output_path,
                )
                logging.info("registry_reloaded providers=%s", len(registry.providers))
        except ImportError:
            logging.warning("watchfiles not installed — config hot-reload disabled")

    # Background task: evict expired job records every 5 minutes
    async def _evict_jobs() -> None:
        job_store: JobStore = app_lifespan.state.job_store
        while True:
            await asyncio.sleep(300)
            await job_store.evict_expired()

    asyncio.create_task(_watch_configs())
    asyncio.create_task(_evict_jobs())

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
app.include_router(src.api.internal.jobs_routes.router)
app.include_router(src.api.internal.translators_routes.router)
app.include_router(src.api.internal.generators_routes.router)
app.include_router(src.api.internal.optimizers_routes.router)
app.include_router(src.api.internal.evaluators_routes.router)


@app.get("/")
def read_root():
    """Read the root endpoint."""
    return {"message": f"Welcome to {settings.PROJECT_NAME}."}
