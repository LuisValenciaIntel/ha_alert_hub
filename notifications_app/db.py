from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'home-assistant',
    image_path TEXT,
    image_url TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    subscription_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)


def count_users(db_path: Path) -> int:
    with connect(db_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def upsert_user(db_path: Path, username: str, password: str) -> None:
    now = utc_now_iso()
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO users (username, password_hash, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash
            """,
            (username.strip(), generate_password_hash(password), now),
        )


def ensure_admin_user(
    db_path: Path,
    instance_path: Path,
    username: str = "admin",
    password: str | None = None,
) -> dict[str, Any] | None:
    if count_users(db_path) > 0:
        return None

    generated_password = password or secrets.token_urlsafe(12)
    upsert_user(db_path, username, generated_password)

    password_file = instance_path / "initial_admin_password.txt"
    password_file.write_text(generated_password + "\n", encoding="utf-8")

    return {
        "username": username,
        "password": generated_password,
        "password_file": password_file,
        "generated": password is None,
    }


def verify_user(db_path: Path, username: str, password: str) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()

    if row is None or not check_password_hash(row["password_hash"], password):
        return None

    return {"id": row["id"], "username": row["username"]}


def ensure_automation_token(instance_path: Path, token: str | None = None) -> dict[str, Any]:
    token_file = instance_path / "automation_token.txt"
    instance_path.mkdir(parents=True, exist_ok=True)

    if token:
        token_file.write_text(token.strip() + "\n", encoding="utf-8")
        return {"token": token.strip(), "token_file": token_file, "generated": False}

    if token_file.exists():
        existing = token_file.read_text(encoding="utf-8").strip()
        if existing:
            return {"token": existing, "token_file": token_file, "generated": False}

    generated = secrets.token_urlsafe(32)
    token_file.write_text(generated + "\n", encoding="utf-8")
    return {"token": generated, "token_file": token_file, "generated": True}


def create_notification(
    db_path: Path,
    *,
    title: str,
    message: str,
    source: str,
    image_path: str | None = None,
    image_url: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or utc_now_iso()
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO notifications (title, message, source, image_path, image_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title.strip(), message.strip(), source.strip(), image_path, image_url, timestamp),
        )
        notification_id = cursor.lastrowid
        if notification_id is None:
            raise RuntimeError("Failed to create notification record")
        row = connection.execute(
            "SELECT * FROM notifications WHERE id = ?",
            (int(notification_id),),
        ).fetchone()

    return serialize_notification(row)


def list_notifications(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()

    return [serialize_notification(row) for row in rows]


def serialize_notification(row: sqlite3.Row) -> dict[str, Any]:
    image = None
    if row["image_path"]:
        image = f"/media/{row['image_path']}"
    elif row["image_url"]:
        image = row["image_url"]

    return {
        "id": row["id"],
        "title": row["title"],
        "message": row["message"],
        "source": row["source"],
        "image": image,
        "created_at": row["created_at"],
    }


def upsert_subscription(db_path: Path, subscription: dict[str, Any]) -> None:
    endpoint = str(subscription.get("endpoint", "")).strip()
    if not endpoint:
        raise ValueError("Subscription endpoint is required")

    payload = json.dumps(subscription, separators=(",", ":"), ensure_ascii=False)
    now = utc_now_iso()
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO push_subscriptions (endpoint, subscription_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                subscription_json=excluded.subscription_json,
                updated_at=excluded.updated_at
            """,
            (endpoint, payload, now, now),
        )


def delete_subscription(db_path: Path, endpoint: str) -> None:
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?",
            (endpoint,),
        )


def list_subscriptions(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT subscription_json FROM push_subscriptions ORDER BY id DESC"
        ).fetchall()

    return [json.loads(row["subscription_json"]) for row in rows]


