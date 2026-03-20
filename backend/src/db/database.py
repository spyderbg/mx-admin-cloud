import hashlib
import os
import sqlite3
from pathlib import Path

import bcrypt

from src.core.config import settings


def _db_path() -> str:
    return os.path.abspath(settings.DB_PATH)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = get_db()
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
            hashed = hash_password(settings.DEFAULT_ADMIN_PASSWORD)
            conn.execute(
                "INSERT INTO users (username, role, hashed_password) VALUES (?, ?, ?)",
                ("admin", "admin", hashed),
            )
            conn.commit()
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        conn.close()


def verify_password(plain: str, hashed: str) -> bool:
    prehashed = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    return bcrypt.checkpw(prehashed.encode("utf-8"), hashed.encode("utf-8"))


def hash_password(plain: str) -> str:
    prehashed = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    return bcrypt.hashpw(prehashed.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
