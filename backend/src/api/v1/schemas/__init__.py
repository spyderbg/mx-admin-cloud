"""Pydantic schemas for v1 API routes.

These models define the public HTTP contract and are used for OpenAPI output.
"""

from src.api.v1.schemas.common import ListFilesRequest
from src.api.v1.schemas.content import (
    CreateCategoryBody,
    RenameCategoryBody,
    RestoreCategoryBody,
)
from src.api.v1.schemas.users import (
    ChangePasswordRequest,
    ChangeRoleRequest,
    CreateUserRequest,
    UserOut,
)

__all__ = [
    "ChangePasswordRequest",
    "ChangeRoleRequest",
    "CreateCategoryBody",
    "CreateUserRequest",
    "ListFilesRequest",
    "RenameCategoryBody",
    "RestoreCategoryBody",
    "UserOut",
]
