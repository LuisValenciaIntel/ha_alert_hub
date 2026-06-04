from __future__ import annotations

import base64
import os
import secrets
import re
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

DEFAULT_APP_NAME = "Home Alert Hub"
IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
MAX_IMAGE_DOWNLOAD_BYTES = 10 * 1024 * 1024


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
            elif image_url:
                image_path = download_image_from_url(image_url, title or source)
        else:
            title = str(request.form.get("title") or request.form.get("source") or "Home alert").strip()
            message = str(request.form.get("message") or request.form.get("text") or "").strip()
            source = str(request.form.get("source") or "home-assistant").strip()
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

    @app.post("/api/ingest")
    def api_ingest():
        require_ingest_token()
        try:
            payload = extract_notification_request()
            notification = db.create_notification(database_path, **payload)
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


