from __future__ import annotations

import base64
import json
import os
import secrets
import re
import logging
import uuid
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

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
from . import webpush

DEFAULT_APP_NAME = "Home Alert Hub"
IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
MAX_IMAGE_DOWNLOAD_BYTES = 10 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


def build_home_assistant_network_hint(base_url: str) -> str:
    parsed_url = urlparse(base_url)
    hostname = (parsed_url.hostname or "").strip().lower()
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return (
            " The configured Home Assistant URL points to localhost, which inside Docker means the container itself."
            " On Linux, set HOME_ASSISTANT_BASE_URL to the real LAN IP or DNS name of your Home Assistant server,"
            " or run the container with host networking if Home Assistant is on the same Linux host."
        )
    return (
        " Verify that HOME_ASSISTANT_BASE_URL points to an address reachable from inside the container"
        " such as your Home Assistant LAN IP or DNS name."
        " If Home Assistant runs on the same Linux host, consider network_mode: host in Docker Compose."
    )


def slugify_filename(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "alert"


def guess_extension(image_url: str, mime_type: str | None = None) -> str:
    if mime_type:
        extension = IMAGE_EXTENSIONS.get(mime_type.lower())
        if extension:
            return extension

    path_extension = Path(urlparse(image_url).path).suffix.lstrip(".").lower()
    if path_extension in {"jpg", "jpeg", "png", "webp", "gif"}:
        return "jpg" if path_extension == "jpeg" else path_extension

    return "jpg"


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    base_path = Path(__file__).resolve().parents[1]
    configured_instance_path = test_config.get("INSTANCE_PATH") if test_config else None
    instance_path = Path(str(configured_instance_path)) if configured_instance_path is not None else base_path / "instance"

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
        HOME_ASSISTANT_API_TOKEN=os.getenv("HOME_ASSISTANT_API_TOKEN", ""),
        HOME_ASSISTANT_BASE_URL=os.getenv("HOME_ASSISTANT_BASE_URL", "").rstrip("/"),
        HOME_ASSISTANT_ACCESS_TOKEN=os.getenv("HOME_ASSISTANT_ACCESS_TOKEN", ""),
        HOME_ASSISTANT_AUTOMATION_ENTITY_ID=os.getenv("HOME_ASSISTANT_AUTOMATION_ENTITY_ID", ""),
        HOME_ASSISTANT_BUTTON_LABEL=os.getenv("HOME_ASSISTANT_BUTTON_LABEL", "Run Home Assistant automation"),
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

    public_base_url = str(app.config.get("PUBLIC_BASE_URL", "")).strip().rstrip("/")
    vapid_subject = public_base_url or "mailto:admin@localhost"
    vapid_keys = webpush.ensure_vapid_keys(resolved_instance_path, vapid_subject)
    app.config["WEB_PUSH_ENABLED"] = bool(public_base_url)
    app.config["VAPID_PUBLIC_KEY"] = vapid_keys["public_key"]
    app.config["VAPID_PRIVATE_KEY_PATH"] = vapid_keys["private_key_path"]
    app.config["VAPID_SUBJECT"] = vapid_keys["subject"]

    if not app.config["WEB_PUSH_ENABLED"]:
        LOGGER.warning(
            "Web push is disabled because PUBLIC_BASE_URL is not configured. "
            "Set PUBLIC_BASE_URL to your final HTTPS URL to enable Android and desktop push notifications."
        )

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

    def save_downloaded_image(content: bytes, title: str, image_url: str, mime_type: str | None) -> str:
        extension = guess_extension(image_url, mime_type)
        slug = slugify_filename(title)
        filename = f"{slug}-{uuid.uuid4().hex[:8]}.{extension}"
        image_path = Path(app.config["MEDIA_DIR"]) / filename
        image_path.write_bytes(content)
        return filename

    def build_push_payload(notification: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": notification.get("title") or "New alert",
            "body": notification.get("message") or "A new Home Assistant notification is available.",
            "icon": url_for("static", filename="icons/icon-192.svg", _external=True),
            "badge": url_for("static", filename="icons/badge.svg", _external=True),
            "image": notification.get("image") or None,
            "tag": f"alert-{notification.get('id')}",
            "url": url_for("notifications_page", _external=True),
            "notificationId": notification.get("id"),
            "category": notification.get("category") or "",
        }

    def push_notifications_ready() -> bool:
        return bool(
            app.config.get("WEB_PUSH_ENABLED")
            and app.config.get("VAPID_PUBLIC_KEY")
            and app.config.get("VAPID_PRIVATE_KEY_PATH")
            and app.config.get("VAPID_SUBJECT")
        )

    def send_push_notifications(notification: dict[str, Any]) -> dict[str, int]:
        if not push_notifications_ready():
            return {"sent": 0, "removed": 0, "failed": 0}

        results = {"sent": 0, "removed": 0, "failed": 0}
        payload = build_push_payload(notification)
        for subscription in db.list_subscriptions(database_path):
            endpoint = str(subscription.get("endpoint") or "").strip()
            if not endpoint:
                continue
            try:
                delivered = webpush.send_web_push(
                    subscription_info=subscription,
                    vapid_private_key_path=str(app.config["VAPID_PRIVATE_KEY_PATH"]),
                    vapid_subject=str(app.config["VAPID_SUBJECT"]),
                    payload=payload,
                )
                if delivered:
                    results["sent"] += 1
                else:
                    db.delete_subscription(database_path, endpoint)
                    results["removed"] += 1
            except Exception:
                LOGGER.exception("Failed to send web push notification to endpoint=%s", endpoint)
                results["failed"] += 1
        return results

    def download_image_from_url(image_url: str, title: str) -> str:
        try:
            request_headers = {"User-Agent": "Home-Alert-Hub/1.0"}
            with urlopen(Request(image_url, headers=request_headers), timeout=15) as response:
                content_type = response.headers.get_content_type()
                content = response.read(MAX_IMAGE_DOWNLOAD_BYTES + 1)
                if len(content) > MAX_IMAGE_DOWNLOAD_BYTES:
                    raise ValueError("Downloaded image is too large")
                return save_downloaded_image(content, title, image_url, content_type)
        except HTTPError as exc:
            raise ValueError(f"Could not download image_url: HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError(f"Could not download image_url: {exc.reason}") from exc

    def trigger_home_assistant_automation(entity_id: str) -> None:
        base_url = str(app.config.get("HOME_ASSISTANT_BASE_URL", "")).strip().rstrip("/")
        access_token = str(app.config.get("HOME_ASSISTANT_ACCESS_TOKEN", "")).strip()
        if not base_url:
            raise ValueError("Home Assistant base URL is not configured")
        if not access_token:
            raise ValueError("Home Assistant access token is not configured")
        if not entity_id.strip():
            raise ValueError("Home Assistant automation entity_id is required")

        endpoint = f"{base_url}/api/services/script/turn_on"
        payload = json.dumps({"entity_id": entity_id.strip()}, separators=(",", ":")).encode("utf-8")
        request_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Home-Alert-Hub/1.0",
        }

        try:
            with urlopen(Request(endpoint, data=payload, headers=request_headers, method="POST"), timeout=15) as response:
                response_body = response.read().decode("utf-8", errors="ignore").strip()
                if response.status >= 400:
                    detail = f": {response_body}" if response_body else ""
                    raise ValueError(f"Home Assistant returned HTTP {response.status}{detail}")
        except HTTPError as exc:
            error_body = ""
            if exc.fp is not None:
                error_body = exc.read().decode("utf-8", errors="ignore").strip()
            detail = f": {error_body}" if error_body else ""
            error_message = f"Home Assistant returned HTTP {exc.code}{detail}"
            LOGGER.exception("Home Assistant automation trigger failed: %s", error_message)
            raise ValueError(error_message) from exc
        except URLError as exc:
            error_message = (
                f"Could not reach Home Assistant at {base_url}: {exc.reason}."
                f"{build_home_assistant_network_hint(base_url)}"
            )
            LOGGER.exception("Home Assistant automation trigger failed: %s", error_message)
            raise ValueError(error_message) from exc

    def parse_image_base64(raw_value: str, mime_type: str | None) -> str:
        value = raw_value.strip()
        header = None
        if value.startswith("data:") and "," in value:
            header, value = value.split(",", 1)
        if header and ";base64" in header:
            mime_type = header.split(":", 1)[1].split(";", 1)[0]
        image_bytes = base64.b64decode(value, validate=True)
        return save_image_bytes(image_bytes, mime_type)

    def extract_notification_request() -> dict[str, Any]:
        title = ""
        message = ""
        source = "home-assistant"
        category = None
        image_path = None
        image_url = None

        if request.is_json:
            payload = request.get_json(silent=True) or {}
            title = str(payload.get("title") or payload.get("source") or "Home alert").strip()
            message = str(payload.get("message") or payload.get("text") or "").strip()
            source = str(payload.get("source") or "home-assistant").strip()
            category = str(payload.get("category") or "").strip() or None
            image_url = str(payload.get("image_url") or "").strip() or None
            image_base64 = str(payload.get("image_base64") or "").strip()
            image_mime = str(payload.get("image_mime") or "").strip() or None
            if image_base64:
                image_path = parse_image_base64(image_base64, image_mime)
            elif image_url:
                image_path = download_image_from_url(image_url, title or source)
        else:
            title = str(request.form.get("title") or request.form.get("source") or "Home alert").strip()
            message = str(request.form.get("message") or request.form.get("text") or "").strip()
            source = str(request.form.get("source") or "home-assistant").strip()
            category = str(request.form.get("category") or "").strip() or None
            image_url = str(request.form.get("image_url") or "").strip() or None
            image_file = request.files.get("image")
            if image_file and image_file.filename:
                image_path = save_image_bytes(image_file.read(), image_file.mimetype)
            elif image_url:
                image_path = download_image_from_url(image_url, title or source)

        if not title:
            title = "Home alert"
        if not any([title, message, image_path, image_url]):
            raise ValueError("Notification must include a title, message, or image")

        return {
            "title": title,
            "message": message,
            "source": source,
            "category": category,
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
        selected_category = str(request.args.get("category") or "").strip() or None
        notifications = db.list_notifications(database_path, category=selected_category)
        categories = db.list_categories(database_path)
        automation_entity_id = str(app.config.get("HOME_ASSISTANT_AUTOMATION_ENTITY_ID", "")).strip()
        ha_trigger_ready = bool(
            str(app.config.get("HOME_ASSISTANT_BASE_URL", "")).strip()
            and str(app.config.get("HOME_ASSISTANT_ACCESS_TOKEN", "")).strip()
            and automation_entity_id
        )
        return render_template(
            "notifications.html",
            notifications=notifications,
            categories=categories,
            selected_category=selected_category or "",
            username=session.get("username", ""),
            poll_interval_ms=int(app.config["POLL_INTERVAL_SECONDS"]) * 1000,
            home_assistant_button_label=str(app.config.get("HOME_ASSISTANT_BUTTON_LABEL", "Run Home Assistant automation")),
            home_assistant_trigger_ready=ha_trigger_ready,
            home_assistant_automation_entity_id=automation_entity_id,
            web_push_enabled=push_notifications_ready(),
            vapid_public_key=str(app.config.get("VAPID_PUBLIC_KEY", "")),
            public_base_url=public_base_url,
        )

    @app.route("/categories", methods=["GET", "POST"])
    @login_required
    def categories_page():
        if request.method == "POST":
            action = str(request.form.get("action") or "").strip().lower()
            try:
                if action == "create":
                    category = db.upsert_category(
                        database_path,
                        name=str(request.form.get("name") or ""),
                        color=str(request.form.get("color") or ""),
                        icon=str(request.form.get("icon") or ""),
                    )
                    flash(f"Category '{category['name']}' saved successfully.", "success")
                elif action == "update":
                    category = db.upsert_category(
                        database_path,
                        name=str(request.form.get("name") or ""),
                        color=str(request.form.get("color") or ""),
                        icon=str(request.form.get("icon") or ""),
                    )
                    flash(f"Category '{category['name']}' updated successfully.", "success")
                else:
                    raise ValueError("Unsupported category action")
            except ValueError as exc:
                flash(str(exc), "error")
            return redirect(url_for("categories_page"))

        return render_template(
            "categories.html",
            categories=db.list_categories(database_path),
            username=session.get("username", ""),
        )

    @app.get("/api/notifications")
    @login_required
    def api_notifications():
        limit = int(request.args.get("limit", 50))
        category = str(request.args.get("category") or "").strip() or None
        categories = db.list_categories(database_path)
        return jsonify(
            {
                "notifications": db.list_notifications(database_path, limit=limit, category=category),
                "categories": categories,
                "selected_category": category or "",
            }
        )

    @app.get("/api/push/config")
    @login_required
    def api_push_config():
        return jsonify(
            {
                "enabled": push_notifications_ready(),
                "public_key": str(app.config.get("VAPID_PUBLIC_KEY", "")) if push_notifications_ready() else "",
                "public_base_url": public_base_url,
            }
        )

    @app.post("/api/push/subscribe")
    @login_required
    def api_push_subscribe():
        if not push_notifications_ready():
            return jsonify({"ok": False, "error": "Web push is not enabled. Configure PUBLIC_BASE_URL with your HTTPS URL."}), 400

        subscription = request.get_json(silent=True) or {}
        try:
            db.upsert_subscription(database_path, subscription)
            return jsonify({"ok": True}), 201
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/push/unsubscribe")
    @login_required
    def api_push_unsubscribe():
        subscription = request.get_json(silent=True) or {}
        endpoint = str(subscription.get("endpoint") or "").strip()
        if not endpoint:
            return jsonify({"ok": False, "error": "Subscription endpoint is required"}), 400
        db.delete_subscription(database_path, endpoint)
        return jsonify({"ok": True}), 200

    @app.post("/api/home-assistant/trigger")
    @login_required
    def api_home_assistant_trigger():
        payload = request.get_json(silent=True) or {}
        entity_id = str(
            payload.get("entity_id")
            or app.config.get("HOME_ASSISTANT_AUTOMATION_ENTITY_ID", "")
            or ""
        ).strip()

        try:
            trigger_home_assistant_automation(entity_id)
            return jsonify({"ok": True, "entity_id": entity_id}), 200
        except ValueError as exc:
            LOGGER.exception("Home Assistant automation trigger request failed for entity_id=%s", entity_id)
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/ingest")
    def api_ingest():
        require_ingest_token()
        try:
            payload = extract_notification_request()
            notification = db.create_notification(database_path, **payload)
            push_results = send_push_notifications(notification)
            return jsonify({"ok": True, "notification": notification, "push": push_results}), 201
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


