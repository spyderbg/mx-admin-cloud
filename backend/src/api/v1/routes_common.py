"""Common API routes."""

from fastapi import APIRouter, Depends  # pylint: disable=import-error

from src.api.dependencies import get_current_user, require_write_access
from src.api.v1.schemas.common import ListFilesRequest
from src.core.tasks import ping, status, operation, list_files

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
def post_operation(name: str, _user: dict = Depends(require_write_access)):
    """Perform an operation; viewers may not."""
    return operation(name)


@router.post("/list-files")
def post_list_files(data: ListFilesRequest, _user: dict = Depends(get_current_user)):
    """List files in the configured content directory."""
    depth = max(1, data.depth)
    return list_files(depth)
