"""Users' API routes."""

from sqlite3 import IntegrityError  # pylint: disable=import-error

from fastapi import APIRouter, Depends, HTTPException, status  # pylint: disable=import-error

from src.api.dependencies import require_uds, require_admin
from src.api.v1.schemas.users import (
    ChangePasswordRequest,
    ChangeRoleRequest,
    CreateUserRequest,
    UserOut,
)
from src.db.database import UsersDb

router = APIRouter(
    dependencies=[Depends(require_uds), Depends(require_admin)],
)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: CreateUserRequest):
    """Create a new user."""
    try:
        hashed = UsersDb.hash_password(body.password)
        last_id = UsersDb.create_user(body.username, body.role, hashed)
        return UserOut(id=last_id, username=body.username, role=body.role)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User '{body.username}' already exists.",
        ) from exc


@router.get("/users", response_model=list[UserOut])
def list_users(username: str | None = None, role: str | None = None) -> list[UserOut]:
    """List all users with optional filtering."""
    rows = UsersDb.list_users(username=username, role=role)
    return [UserOut(id=r["id"], username=r["username"], role=r["role"]) for r in rows]


@router.delete("/users/{username}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(username: str):
    """Delete a user."""
    role = UsersDb.get_user_role(username)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{username}' not found.",
        )

    if role == "admin":
        admin_count = UsersDb.count_admins()
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete the last admin user.",
            )

    UsersDb.delete_user(username)


@router.patch("/users/{username}/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(username: str, body: ChangePasswordRequest):
    """Change a user's password."""
    user = UsersDb.get_user_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{username}' not found.",
        )

    hashed = UsersDb.hash_password(body.password)
    UsersDb.update_password(username, hashed)


@router.patch("/users/{username}/role", response_model=UserOut)
def change_role(username: str, body: ChangeRoleRequest):
    """Change a user's role."""
    user = UsersDb.get_user_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{username}' not found.",
        )

    if user["role"] == "admin" and body.role != "admin":
        admin_count = UsersDb.count_admins()
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote the last admin user.",
            )

    UsersDb.update_role(username, body.role)
    return UserOut(id=user["id"], username=user["username"], role=body.role)
