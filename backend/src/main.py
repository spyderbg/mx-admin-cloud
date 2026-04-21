"""FastAPI application entry point: app factory, lifespan, and root router registration."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from src.api.dependencies import require_admin, require_uds
from src.api.internal import (
    jobs_routes,
    services_routes,
)
from src.api.v1 import routes_auth, routes_common, routes_content, routes_users
from src.core.config import settings
from src.core.job_store import JobStore
from src.core.utils import is_windows_platform, is_debug_mode
from src.db.database import UsersDb
from src.security.tokens import create_internal_token
from src.services.configuration import routes as configuration_routes
from src.services.evaluation import routes as evaluators_routes
from src.services.logging import routes as logging_routes
from src.services.logging.service import LoggingService
from src.services.service_registry import ServiceRegistry, ServiceRegistryBuilder
from src.services.text_to_speech import routes as text_to_speech_routes
from src.services.text_translation import routes as text_translation_routes
from src.services.voice_optimization import routes as optimizers_routes


@asynccontextmanager
async def lifespan(app_lifespan: FastAPI) -> AsyncIterator[None]:
    """Initialize shared services during application startup."""
    await run_in_threadpool(UsersDb.init_db)

    # Service registry (includes logging service)
    registry = ServiceRegistry.build_runtime(settings.BASE_DIR)
    registry_builder = ServiceRegistryBuilder(registry, settings.BASE_DIR, settings.config_path)
    registry_builder.load_all_from_disk()
    app_lifespan.state.registry = registry
    app_lifespan.state.registry_builder = registry_builder
    registry.dump(settings.BASE_DIR)

    # Configure logging via the registry's logging service
    try:
        logging_service = registry.get_service("logging")
        if isinstance(logging_service, LoggingService):
            logging_service.configure(
                output_dir=settings.output_path,
                profile=settings.LOG_PROFILE,
            )
    except KeyError:
        logging.info("No logging service registered — using stdlib defaults")

    # Job store — in-memory with TTL eviction; swap for SQLite/Redis for multi-worker
    app_lifespan.state.job_store = JobStore(ttl_seconds=3600)

    # Internal service token — freshly signed on each startup, 24-hour lifetime
    app_lifespan.state.internal_token = create_internal_token(
        "backend-internal", timedelta(hours=24)
    )

    # Configure the logging HTTP client for inter-service log calls
    from src.core import log_client as logging_client  # pylint: disable=import-outside-toplevel
    logging_client.configure(
        base_url=str(settings.API_BASE_URL),
        internal_token=app_lifespan.state.internal_token,
    )

    # Background task: watch config dir for changes and hot-reload the registry
    async def _watch_configs() -> None:
        try:
            from watchfiles import awatch  # pylint: disable=import-outside-toplevel
            builder: ServiceRegistryBuilder = app_lifespan.state.registry_builder
            async for _ in awatch(settings.config_path):
                logging.info("config_changed reloading_registry")
                await run_in_threadpool(builder.load_all_from_disk)
                registry.dump(settings.BASE_DIR)
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

    await logging_client.shutdown()


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.mount("/content", StaticFiles(directory=str(settings.content_path)), name="content")

app.include_router(routes_auth.router, prefix=settings.API_V1_STR, tags=["auth"])
app.include_router(routes_common.router, prefix=settings.API_V1_STR, tags=["tasks"])
app.include_router(routes_content.router, prefix=settings.API_V1_STR, tags=["content"])
app.include_router(routes_users.router, prefix=settings.API_V1_STR, tags=["users"])
app.include_router(jobs_routes.router)
app.include_router(services_routes.router)
app.include_router(logging_routes.router)
app.include_router(configuration_routes.router)
app.include_router(text_translation_routes.router)
app.include_router(text_to_speech_routes.router)
app.include_router(optimizers_routes.router)
app.include_router(evaluators_routes.router)


@app.get("/")
def read_root():
    """Read the root endpoint."""
    return {"message": f"Welcome to {settings.PROJECT_NAME}."}
