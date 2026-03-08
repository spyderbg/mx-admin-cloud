from fastapi import FastAPI
from src.api.v1 import routes_auth, routes_tasks, routes_content
from src.core.config import settings

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(routes_auth.router, prefix=settings.API_V1_STR, tags=["auth"])
app.include_router(routes_tasks.router, prefix=settings.API_V1_STR, tags=["tasks"])
app.include_router(routes_content.router, prefix=settings.API_V1_STR, tags=["content"])

@app.get("/")
def read_root():
    return {"message": f"Welcome to {settings.PROJECT_NAME}."}
