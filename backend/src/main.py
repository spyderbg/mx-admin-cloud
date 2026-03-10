from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.v1 import routes_auth, routes_tasks, routes_content, routes_users
from src.core.config import settings
from src.db.database import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.include_router(routes_auth.router, prefix=settings.API_V1_STR, tags=["auth"])
app.include_router(routes_tasks.router, prefix=settings.API_V1_STR, tags=["tasks"])
app.include_router(routes_content.router, prefix=settings.API_V1_STR, tags=["content"])
app.include_router(routes_users.router, prefix=settings.API_V1_STR, tags=["users"])


@app.get("/")
def read_root():
    return {"message": f"Welcome to {settings.PROJECT_NAME}."}
