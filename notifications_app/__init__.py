from __future__ import annotations

import base64
import os
import secrets
import uuid
from functools import wraps
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from . import db
from .webpush import send_web_push, ensure_vapid_keys

DEFAULT_APP_NAME = "Home Alert Hub"
DEFAULT_VAPID_SUBJECT = "mailto:alerts@example.com"
IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    base_path = Path(__file__).resolve().parents[1]
    configured_instance_path = test_config.get("INSTANCE_PATH") if test_config else None
    instance_path = Path(configured_instance_path) if configured_instance_path else base_path / "instance"

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        instance_path=str(instance_path),
        instance_relative_config=True,
    )

    app.config.update(
        APP_NAME=os.getenv("APP_NAME", DEFAULT_APP_NAME),
        SECRET_KEY=os.getenv("SECRET_KEY", "change-me-in-production"),
        DATABASE_PATH=instance_path / "notifications.db",
        MEDIA_DIR=base_path / "media",
        POLL_INTERVAL_SECONDS=int(os.getenv("POLL_INTERVAL_SECONDS", "20")),
        PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        VAPID_SUBJECT=os.getenv("VAPID_SUBJECT", DEFAULT_VAPID_SUBJECT),
        HOME_ASSISTANT_API_TOKEN=os.getenv("HOME_ASSISTANT_API_TOKEN", ""),
        BOOTSTRAP_ADMIN_USERNAME=os.getenv("APP_ADMIN_USERNAME", ""),
        BOOTSTRAP_ADMIN_PASSWORD=os.getenv("APP_ADMIN_PASSWORD", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    resolved_instance_path = Path(app.instance_path or instance_path)
    resolved_instance_path.mkdir(parents=True, exist_ok=True)
    Path(app.config["MEDIA_DIR"]).mkdir(parents=True, exist_ok=True)

    database_path = Path(app.config["DATABASE_PATH"])
    db.init_db(database_path)

    bootstrap_username = str(app.config.get("BOOTSTRAP_ADMIN_USERNAME", "")).strip()
    bootstrap_password = str(app.config.get("BOOTSTRAP_ADMIN_PASSWORD", "")).strip()
    if bootstrap_username and bootstrap_password:
        db.upsert_user(database_path, bootstrap_username, bootstrap_password)
    else:
        created_admin = db.ensure_admin_user(database_path, resolved_instance_path)
        if created_admin:
            print(
                f"[notifications_page] Initial admin user '{created_admin['username']}' created. "
                f"Password stored at {created_admin['password_file']}"
            )

    token_info = db.ensure_automation_token(
        resolved_instance_path,
        token=str(app.config.get("HOME_ASSISTANT_API_TOKEN", "")).strip() or None,
    )
    app.config["HOME_ASSISTANT_API_TOKEN"] = token_info["token"]
    if token_info["generated"]:
        print(
            f"[notifications_page] Automation token generated and stored at {token_info['token_file']}"
        )

    vapid_subject = str(app.config.get("VAPID_SUBJECT", DEFAULT_VAPID_SUBJECT))
    vapid_keys = ensure_vapid_keys(resolved_instance_path, vapid_subject)
    app.config["VAPID_PUBLIC_KEY"] = vapid_keys["public_key"]
    app.config["VAPID_PRIVATE_KEY"] = vapid_keys["private_key"]

    def login_required(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not session.get("user_id"):
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
            return view(*args, **kwargs)

        return wrapped_view

    def valid_ingest_token() -> bool:
        expected_token = str(app.config.get("HOME_ASSISTANT_API_TOKEN", "")).strip()
        if not expected_token:
            return False

        provided = request.headers.get("X-API-Key", "").strip()
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            provided = auth_header.split(" ", 1)[1].strip()

        return bool(provided) and secrets.compare_digest(provided, expected_token)

    def require_ingest_token() -> None:
        if not valid_ingest_token():
            abort(401, description="Invalid or missing automation token")

    def save_image_bytes(content: bytes, mime_type: str | None) -> str:
        extension = IMAGE_EXTENSIONS.get((mime_type or "").lower(), "jpg")
        filename = f"{uuid.uuid4().hex}.{extension}"
        image_path = Path(app.config["MEDIA_DIR"]) / filename
        image_path.write_bytes(content)
        return filename

    def parse_image_base64(raw_value: str, mime_type: str | None) -> str:
        value = raw_value.strip()
        header = None
        if value.startswith("data:") and "," in value:
            header, value = value.split(",", 1)
        if header and ";base64" in header:
            mime_type = header.split(":", 1)[1].split(";", 1)[0]
        image_bytes = base64.b64decode(value, validate=True)
        return save_image_bytes(image_bytes, mime_type)

    def build_notification_payload(notification: dict[str, Any]) -> dict[str, Any]:
        app_url = str(app.config.get("PUBLIC_BASE_URL") or request.url_root.rstrip("/"))
        image_url = str(notification.get("image") or "") or None
        if image_url and image_url.startswith("/"):
            image_url = app_url + image_url

        return {
            "title": notification["title"],
            "body": notification["message"] or "New home alert received",
            "icon": app_url + "/static/icons/icon-192.svg",
            "badge": app_url + "/static/icons/badge.svg",
            "image": image_url,
            "url": app_url + url_for("notifications_page"),
            "tag": f"alert-{notification['id']}",
            "notificationId": notification["id"],
        }

    def broadcast_notification(notification: dict[str, Any]) -> None:
        subscriptions = db.list_subscriptions(database_path)
        stale_endpoints: list[str] = []
        payload = build_notification_payload(notification)

        for subscription in subscriptions:
            try:
                was_sent = send_web_push(
                    subscription_info=subscription,
                    vapid_private_key=app.config["VAPID_PRIVATE_KEY"],
                    vapid_subject=app.config["VAPID_SUBJECT"],
                    payload=payload,
                )
                if not was_sent:
                    stale_endpoints.append(str(subscription.get("endpoint", "")))
            except Exception as exc:
                print(f"[notifications_page] Push delivery failed: {exc}")

        for endpoint in stale_endpoints:
            if endpoint:
                db.delete_subscription(database_path, endpoint)

    def extract_notification_request() -> dict[str, Any]:
        title = ""
        message = ""
        source = "home-assistant"
        image_path = None
        image_url = None

        if request.is_json:
            payload = request.get_json(silent=True) or {}
            title = str(payload.get("title") or payload.get("source") or "Home alert").strip()
            message = str(payload.get("message") or payload.get("text") or "").strip()
            source = str(payload.get("source") or "home-assistant").strip()
            image_url = str(payload.get("image_url") or "").strip() or None
            image_base64 = str(payload.get("image_base64") or "").strip()
            image_mime = str(payload.get("image_mime") or "").strip() or None
            if image_base64:
                image_path = parse_image_base64(image_base64, image_mime)
        else:
            title = str(request.form.get("title") or request.form.get("source") or "Home alert").strip()
            message = str(request.form.get("message") or request.form.get("text") or "").strip()
            source = str(request.form.get("source") or "home-assistant").strip()
            image_url = str(request.form.get("image_url") or "").strip() or None
            image_file = request.files.get("image")
            if image_file and image_file.filename:
                image_path = save_image_bytes(image_file.read(), image_file.mimetype)

        if not title:
            title = "Home alert"
        if not any([title, message, image_path, image_url]):
            raise ValueError("Notification must include a title, message, or image")

        return {
            "title": title,
            "message": message,
            "source": source,
            "image_path": image_path,
            "image_url": image_url,
        }

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {"app_name": app.config["APP_NAME"]}

    @app.get("/")
    def index():
        if session.get("user_id"):
            return redirect(url_for("notifications_page"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("user_id"):
            return redirect(url_for("notifications_page"))

        if request.method == "POST":
            username = str(request.form.get("username", "")).strip()
            password = str(request.form.get("password", ""))
            user = db.verify_user(database_path, username, password)
            if user:
                session.clear()
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                next_url = request.args.get("next") or url_for("notifications_page")
                return redirect(next_url)
            flash("Invalid username or password.")

        return render_template("login.html")

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/notifications")
    @login_required
    def notifications_page():
        notifications = db.list_notifications(database_path)
        return render_template(
            "notifications.html",
            notifications=notifications,
            username=session.get("username", ""),
            poll_interval_ms=int(app.config["POLL_INTERVAL_SECONDS"]) * 1000,
        )

    @app.get("/api/notifications")
    @login_required
    def api_notifications():
        limit = int(request.args.get("limit", 50))
        return jsonify({"notifications": db.list_notifications(database_path, limit=limit)})

    @app.get("/api/push/public-key")
    @login_required
    def api_push_public_key():
        return jsonify({"publicKey": app.config["VAPID_PUBLIC_KEY"]})

    @app.post("/api/push/subscribe")
    @login_required
    def api_push_subscribe():
        subscription = request.get_json(silent=True) or {}
        db.upsert_subscription(database_path, subscription)
        return jsonify({"ok": True})

    @app.post("/api/push/unsubscribe")
    @login_required
    def api_push_unsubscribe():
        payload = request.get_json(silent=True) or {}
        endpoint = str(payload.get("endpoint") or "").strip()
        if endpoint:
            db.delete_subscription(database_path, endpoint)
        return jsonify({"ok": True})

    @app.post("/api/ingest")
    def api_ingest():
        require_ingest_token()
        try:
            payload = extract_notification_request()
            notification = db.create_notification(database_path, **payload)
            broadcast_notification(notification)
            return jsonify({"ok": True, "notification": notification}), 201
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "app": app.config["APP_NAME"]})

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(str(app.static_folder or ""), "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/service-worker.js")
    def service_worker():
        response = send_from_directory(str(app.static_folder or ""), "service-worker.js", mimetype="application/javascript")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/media/<path:filename>")
    def media(filename: str):
        return send_from_directory(app.config["MEDIA_DIR"], filename)

    return app


