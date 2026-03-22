"""Common API routes."""

from pydantic import BaseModel
from fastapi import APIRouter, Depends  # pylint: disable=import-error
from src.core.tasks import ping, status, operation, list_files
from src.api.dependencies import get_current_user

router = APIRouter()

@router.get("/ping")
def get_ping():
    """Ping the server."""
    return ping()

@router.get("/status")
def get_status(_user: dict = Depends(get_current_user)):
    """Get the server status."""
    return status()

@router.post("/operation")
def post_operation(name: str, _user: dict = Depends(get_current_user)):
    """Perform an operation."""
    return operation(name)

class ListFilesRequest(BaseModel):
    """Request body for listing files."""
    depth: int

@router.post("/list-files")
def post_list_files(data: ListFilesRequest, _user: dict = Depends(get_current_user)):
    """List files in the configured content directory."""
    depth = max(1, data.depth)
    return list_files(depth)
