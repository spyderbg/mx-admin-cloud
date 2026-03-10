from pydantic import BaseModel
from fastapi import APIRouter, Depends
from src.core.tasks import ping, status, operation, list_files
from src.api.dependencies import get_current_user

router = APIRouter()

@router.get("/ping")
def get_ping():
    return ping()

@router.get("/status")
def get_status(user: dict = Depends(get_current_user)):
    return status()

@router.post("/operation")
def post_operation(name: str, user: dict = Depends(get_current_user)):
    return operation(name)

class ListFilesRequest(BaseModel):
    depth: int

@router.post("/list-files")
def post_list_files(data: ListFilesRequest, user: dict = Depends(get_current_user)):
    if data.depth < 1:
        data.depth = 1
    return list_files(data.depth)
