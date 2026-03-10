from sqlite3 import IntegrityError

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.dependencies import require_uds, require_admin
from src.db.database import get_db, hash_password

router = APIRouter(
    dependencies=[Depends(require_uds), Depends(require_admin)],
)


# --------------- schemas ---------------

class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1)
    role: str = Field(default="viewer", pattern=r"^(admin|viewer)$")


class ChangePasswordRequest(BaseModel):
    password: str = Field(min_length=1)


class ChangeRoleRequest(BaseModel):
    role: str = Field(pattern=r"^(admin|viewer)$")


class UserOut(BaseModel):
    id: int
    username: str
    role: str


# --------------- endpoints ---------------

@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: CreateUserRequest):
    conn = get_db()
    try:
        hashed = hash_password(body.password)
        cur = conn.execute(
            "INSERT INTO users (username, role, hashed_password) VALUES (?, ?, ?)",
            (body.username, body.role, hashed),
        )
        conn.commit()
        return UserOut(id=cur.lastrowid, username=body.username, role=body.role)
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User '{body.username}' already exists.",
        )
    finally:
        conn.close()


@router.get("/users", response_model=list[UserOut])
def list_users():
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()
        return [UserOut(id=r["id"], username=r["username"], role=r["role"]) for r in rows]
    finally:
        conn.close()


@router.delete("/users/{username}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(username: str):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT role FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{username}' not found.",
            )

        if row["role"] == "admin":
            admin_count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE role = 'admin'"
            ).fetchone()["cnt"]
            if admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot delete the last admin user.",
                )

        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
    finally:
        conn.close()


@router.patch("/users/{username}/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(username: str, body: ChangePasswordRequest):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{username}' not found.",
            )

        hashed = hash_password(body.password)
        conn.execute(
            "UPDATE users SET hashed_password = ? WHERE username = ?",
            (hashed, username),
        )
        conn.commit()
    finally:
        conn.close()


@router.patch("/users/{username}/role", response_model=UserOut)
def change_role(username: str, body: ChangeRoleRequest):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, username, role FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{username}' not found.",
            )

        if row["role"] == "admin" and body.role != "admin":
            admin_count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE role = 'admin'"
            ).fetchone()["cnt"]
            if admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot demote the last admin user.",
                )

        conn.execute(
            "UPDATE users SET role = ? WHERE username = ?",
            (body.role, username),
        )
        conn.commit()
        return UserOut(id=row["id"], username=row["username"], role=body.role)
    finally:
        conn.close()
