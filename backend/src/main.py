from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from src.api.internal import routes_voice_gen
from src.api.v1 import routes_auth, routes_tasks, routes_content, routes_users
from src.core.config import settings
from src.db.database import init_db
from src.services.logging import LoggingSystem


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    log_config = Path(settings.TTS_CONFIG_DIR) / "logging.json"
    if log_config.exists():
        logging_system = LoggingSystem(
            base_dir=Path("."),
            output_dir=Path(settings.TTS_OUTPUT_DIR),
            config_path=log_config,
            profile=settings.TTS_LOG_PROFILE,
        )
        logging_system.configure()
    yield


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.include_router(routes_auth.router, prefix=settings.API_V1_STR, tags=["auth"])
app.include_router(routes_tasks.router, prefix=settings.API_V1_STR, tags=["tasks"])
app.include_router(routes_content.router, prefix=settings.API_V1_STR, tags=["content"])
app.include_router(routes_users.router, prefix=settings.API_V1_STR, tags=["users"])
app.include_router(routes_voice_gen.router, tags=["internal"])


@app.get("/")
def read_root():
    return {"message": f"Welcome to {settings.PROJECT_NAME}."}
