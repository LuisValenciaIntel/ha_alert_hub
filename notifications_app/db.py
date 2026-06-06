from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CATEGORY_COLOR_PALETTE = [
    "#38BDF8",
    "#A78BFA",
    "#F472B6",
    "#34D399",
    "#F59E0B",
    "#FB7185",
    "#22D3EE",
    "#F97316",
]
DEFAULT_CATEGORY_ICON = "🏷️"

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
    category TEXT,
    image_path TEXT,
    image_url TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL,
    icon TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
        ensure_notifications_schema(connection)
        sync_categories_from_notifications(connection)


def ensure_notifications_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1]).strip().lower()
        for row in connection.execute("PRAGMA table_info(notifications)").fetchall()
    }
    if "category" not in columns:
        connection.execute("ALTER TABLE notifications ADD COLUMN category TEXT")


def sync_categories_from_notifications(connection: sqlite3.Connection) -> None:
    category_rows = connection.execute(
        """
        SELECT DISTINCT TRIM(category) AS category
        FROM notifications
        WHERE category IS NOT NULL AND TRIM(category) <> ''
        ORDER BY LOWER(TRIM(category)) ASC, TRIM(category) ASC
        """
    ).fetchall()
    for row in category_rows:
        ensure_category_record(connection, str(row["category"]))


def normalize_category(category: str | None) -> str | None:
    value = str(category or "").strip()
    return value or None


def default_category_color(category: str | None) -> str:
    normalized = normalize_category(category) or "default"
    index = sum(ord(character) for character in normalized.lower()) % len(CATEGORY_COLOR_PALETTE)
    return CATEGORY_COLOR_PALETTE[index]


def default_category_icon(category: str | None) -> str:
    normalized = (normalize_category(category) or "").lower()
    icon_map = {
        "security": "🛡️",
        "garage": "🚗",
        "car": "🚗",
        "maintenance": "🔧",
        "weather": "☁️",
        "camera": "🎥",
        "motion": "👀",
        "door": "🚪",
        "alarm": "🚨",
        "water": "💧",
        "washer": "🧺",
        "laundry": "🧺",
        "package": "📦",
        "delivery": "📦",
        "energy": "⚡",
        "power": "⚡",
        "family": "👨‍👩‍👧",
    }
    for keyword, icon in icon_map.items():
        if keyword in normalized:
            return icon
    return DEFAULT_CATEGORY_ICON


def validate_category_color(color: str | None, category: str | None) -> str:
    value = str(color or "").strip()
    if not value:
        return default_category_color(category)
    if not HEX_COLOR_RE.fullmatch(value):
        raise ValueError("Category color must be a hex value like #38BDF8")
    return value.upper()


def resolve_category_color(color: str | None, category: str | None) -> str | None:
    if normalize_category(category) is None:
        return None
    value = str(color or "").strip()
    if HEX_COLOR_RE.fullmatch(value):
        return value.upper()
    return default_category_color(category)


def validate_category_icon(icon: str | None, category: str | None) -> str:
    value = str(icon or "").strip()
    if not value:
        return default_category_icon(category)
    if len(value) > 16:
        raise ValueError("Category icon must be 16 characters or fewer")
    return value


def resolve_category_icon(icon: str | None, category: str | None) -> str | None:
    if normalize_category(category) is None:
        return None
    value = str(icon or "").strip()
    return value[:16] if value else default_category_icon(category)


def ensure_category_record(connection: sqlite3.Connection, category: str) -> None:
    normalized_category = normalize_category(category)
    if normalized_category is None:
        return

    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO categories (name, color, icon, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO NOTHING
        """,
        (
            normalized_category,
            default_category_color(normalized_category),
            default_category_icon(normalized_category),
            now,
            now,
        ),
    )


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
    category: str | None = None,
    image_path: str | None = None,
    image_url: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or utc_now_iso()
    normalized_category = normalize_category(category)
    with connect(db_path) as connection:
        if normalized_category is not None:
            ensure_category_record(connection, normalized_category)
        cursor = connection.execute(
            """
            INSERT INTO notifications (title, message, source, category, image_path, image_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (title.strip(), message.strip(), source.strip(), normalized_category, image_path, image_url, timestamp),
        )
        notification_id = cursor.lastrowid
        if notification_id is None:
            raise RuntimeError("Failed to create notification record")
        row = fetch_notification_row(connection, int(notification_id))

    return serialize_notification(row)


def list_notifications(db_path: Path, limit: int = 50, category: str | None = None) -> list[dict[str, Any]]:
    normalized_category = normalize_category(category)
    with connect(db_path) as connection:
        if normalized_category is None:
            rows = connection.execute(
                """
                SELECT notifications.*, categories.color AS category_color, categories.icon AS category_icon
                FROM notifications
                LEFT JOIN categories ON notifications.category = categories.name
                ORDER BY notifications.id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT notifications.*, categories.color AS category_color, categories.icon AS category_icon
                FROM notifications
                LEFT JOIN categories ON notifications.category = categories.name
                WHERE notifications.category = ?
                ORDER BY notifications.id DESC
                LIMIT ?
                """,
                (normalized_category, max(1, min(limit, 200))),
            ).fetchall()

    return [serialize_notification(row) for row in rows]


def fetch_notification_row(connection: sqlite3.Connection, notification_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT notifications.*, categories.color AS category_color, categories.icon AS category_icon
        FROM notifications
        LEFT JOIN categories ON notifications.category = categories.name
        WHERE notifications.id = ?
        """,
        (notification_id,),
    ).fetchone()


def get_category(db_path: Path, name: str) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT categories.name, categories.color, categories.icon, COUNT(notifications.id) AS notification_count
            FROM categories
            LEFT JOIN notifications ON notifications.category = categories.name
            WHERE categories.name = ?
            GROUP BY categories.name, categories.color, categories.icon
            """,
            (normalize_category(name),),
        ).fetchone()

    return serialize_category_row(row) if row is not None else None


def list_categories(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT categories.name, categories.color, categories.icon, COUNT(notifications.id) AS notification_count
            FROM categories
            LEFT JOIN notifications ON notifications.category = categories.name
            GROUP BY categories.name, categories.color, categories.icon
            ORDER BY LOWER(categories.name) ASC, categories.name ASC
            """
        ).fetchall()

    return [serialize_category_row(row) for row in rows]


def upsert_category(
    db_path: Path,
    *,
    name: str,
    color: str | None = None,
    icon: str | None = None,
) -> dict[str, Any]:
    normalized_name = normalize_category(name)
    if normalized_name is None:
        raise ValueError("Category name is required")

    resolved_color = validate_category_color(color, normalized_name)
    resolved_icon = validate_category_icon(icon, normalized_name)
    now = utc_now_iso()
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO categories (name, color, icon, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                color=excluded.color,
                icon=excluded.icon,
                updated_at=excluded.updated_at
            """,
            (normalized_name, resolved_color, resolved_icon, now, now),
        )
    category_row = get_category(db_path, normalized_name)
    if category_row is None:
        raise RuntimeError("Failed to save category")
    return category_row


def serialize_category_row(row: sqlite3.Row) -> dict[str, Any]:
    name = normalize_category(row["name"])
    return {
        "name": name,
        "color": resolve_category_color(row["color"], name),
        "icon": resolve_category_icon(row["icon"], name),
        "notification_count": int(row["notification_count"]),
    }


def serialize_notification(row: sqlite3.Row) -> dict[str, Any]:
    image = None
    if row["image_path"]:
        image = f"/media/{row['image_path']}"
    elif row["image_url"]:
        image = row["image_url"]

    category = normalize_category(row["category"])

    return {
        "id": row["id"],
        "title": row["title"],
        "message": row["message"],
        "source": row["source"],
        "category": category,
        "category_color": resolve_category_color(row["category_color"] if "category_color" in row.keys() else None, category),
        "category_icon": resolve_category_icon(row["category_icon"] if "category_icon" in row.keys() else None, category),
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


