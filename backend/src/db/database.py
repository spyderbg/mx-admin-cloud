"""SQLite user store; auto-creates the admin account on first run."""

import hashlib
import os
import sqlite3
from pathlib import Path

import bcrypt

from src.core.config import settings


class UsersDb:
    """Static-method façade over the SQLite users table."""

    @staticmethod
    def _db_path() -> str:
        """Return the absolute path to the SQLite database file."""
        return os.path.abspath(settings.DB_PATH)

    @staticmethod
    def get_db() -> sqlite3.Connection:
        """Open and return a WAL-mode connection with row_factory set."""
        conn = sqlite3.connect(UsersDb._db_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def init_db() -> None:
        """Create the users table and seed the default admin account if absent."""
        path = UsersDb._db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        conn = UsersDb.get_db()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users
                (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    username        TEXT UNIQUE NOT NULL,
                    role            TEXT        NOT NULL DEFAULT 'viewer',
                    hashed_password TEXT        NOT NULL
                )
                """
            )
            conn.commit()

            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", ("admin",)
            ).fetchone()
            if row is None:
                hashed = UsersDb.hash_password(settings.DEFAULT_ADMIN_PASSWORD)
                conn.execute(
                    "INSERT INTO users (username, role, hashed_password) VALUES (?, ?, ?)",
                    ("admin", "admin", hashed),
                )
                conn.commit()
        except Exception as e:
            print(f"Error initializing database: {e}")
        finally:
            conn.close()

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        """Return True if *plain* matches the stored bcrypt hash (SHA-256 pre-hashed)."""
        prehashed = hashlib.sha256(plain.encode("utf-8")).hexdigest()
        return bcrypt.checkpw(prehashed.encode("utf-8"), hashed.encode("utf-8"))

    @staticmethod
    def hash_password(plain: str) -> str:
        """Hash *plain* with SHA-256 pre-hash + bcrypt and return the encoded string."""
        prehashed = hashlib.sha256(plain.encode("utf-8")).hexdigest()
        return bcrypt.hashpw(prehashed.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def authenticate_user(username: str, password: str) -> dict | None:
        """Look up the user in SQLite and verify the password. Returns the row or None."""
        conn = UsersDb.get_db()
        try:
            row = conn.execute(
                "SELECT username, role, hashed_password FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None
        if not UsersDb.verify_password(password, row["hashed_password"]):
            return None
        return {"username": row["username"], "role": row["role"]}

    @staticmethod
    def create_user(username: str, role: str, password_hash: str) -> int:
        """Insert a new user into the database."""
        conn = UsersDb.get_db()
        try:
            cur = conn.execute(
                "INSERT INTO users (username, role, hashed_password) VALUES (?, ?, ?)",
                (username, role, password_hash),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    @staticmethod
    def list_users(username: str | None = None, role: str | None = None) -> list[sqlite3.Row]:
        """List all users with optional filtering."""
        conn = UsersDb.get_db()
        try:
            query = "SELECT id, username, role FROM users"
            params = []
            conditions = []
            if username:
                conditions.append("username LIKE ?")
                params.append(f"%{username}%")
            if role:
                conditions.append("role = ?")
                params.append(role)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY id"
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    @staticmethod
    def get_user_by_username(username: str) -> sqlite3.Row | None:
        """Fetch a user by username."""
        conn = UsersDb.get_db()
        try:
            return conn.execute(
                "SELECT id, username, role FROM users WHERE username = ?", (username,)
            ).fetchone()
        finally:
            conn.close()

    @staticmethod
    def get_user_role(username: str) -> str | None:
        """Fetch a user's role by username."""
        conn = UsersDb.get_db()
        try:
            row = conn.execute(
                "SELECT role FROM users WHERE username = ?", (username,)
            ).fetchone()
            return row["role"] if row else None
        finally:
            conn.close()

    @staticmethod
    def count_admins() -> int:
        """Count the number of admin users."""
        conn = UsersDb.get_db()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE role = 'admin'"
            ).fetchone()
            return row["cnt"]
        finally:
            conn.close()

    @staticmethod
    def delete_user(username: str) -> None:
        """Delete a user by username."""
        conn = UsersDb.get_db()
        try:
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def update_password(username: str, password_hash: str) -> None:
        """Update a user's password."""
        conn = UsersDb.get_db()
        try:
            conn.execute(
                "UPDATE users SET hashed_password = ? WHERE username = ?",
                (password_hash, username),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def update_role(username: str, role: str) -> None:
        """Update a user's role."""
        conn = UsersDb.get_db()
        try:
            conn.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                (role, username),
            )
            conn.commit()
        finally:
            conn.close()
