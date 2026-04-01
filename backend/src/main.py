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
from src.services.acceptance import ConfigApplyResult
from src.services.service_registry import ServiceRegistry, ServiceRegistryBuilder
from src.services.text_to_speech.service import TextToSpeechService
from src.core.utils import is_windows_platform, is_debug_mode
from src.db.database import UsersDb
from src.security.tokens import create_internal_token
from src.core.logging import LoggingSystem


@asynccontextmanager
async def lifespan(app_lifespan: FastAPI) -> AsyncIterator[None]:
    """Initialize shared services during application startup."""
    await run_in_threadpool(UsersDb.init_db)

    # Logging system
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

    # Service registry
    registry = ServiceRegistry.build_runtime(settings.BASE_DIR)
    registry_builder = ServiceRegistryBuilder(registry, settings.BASE_DIR, settings.config_path)
    registry_builder.load_all_from_disk()
    app_lifespan.state.registry = registry
    app_lifespan.state.registry_builder = registry_builder
    registry.dump(settings.BASE_DIR)

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
            reg: ServiceRegistry = app_lifespan.state.registry
            builder: ServiceRegistryBuilder = app_lifespan.state.registry_builder
            async for _ in awatch(settings.config_path):
                logging.info("config_changed reloading_registry")
                await run_in_threadpool(builder.load_all_from_disk)
                try:
                    tts_service = reg.get_service("text_to_speech")
                    providers_count = len(
                        tts_service.active_providers
                        if isinstance(tts_service, TextToSpeechService)
                        else []
                    )
                except KeyError:
                    providers_count = 0
                logging.info("registry_reloaded active_tts_providers=%s", providers_count)
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
app.include_router(src.api.internal.service_config_routes.router)
app.include_router(src.api.internal.text_translation_routes.router)
app.include_router(src.api.internal.text_to_speech_routes.router)
app.include_router(src.api.internal.optimizers_routes.router)
app.include_router(src.api.internal.evaluators_routes.router)


@app.get("/")
def read_root():
    """Read the root endpoint."""
    return {"message": f"Welcome to {settings.PROJECT_NAME}."}
