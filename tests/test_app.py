from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from notifications_app import create_app
from notifications_app import db


class NotificationsPageTestCase(unittest.TestCase):
	def setUp(self) -> None:
		self.temp_dir = tempfile.TemporaryDirectory()
		temp_root = Path(self.temp_dir.name)
		self.instance_path = temp_root / "instance"
		self.media_dir = temp_root / "media"
		self.app = create_app(
			{
				"TESTING": True,
				"SECRET_KEY": "test-secret",
				"INSTANCE_PATH": self.instance_path,
				"MEDIA_DIR": self.media_dir,
				"HOME_ASSISTANT_API_TOKEN": "test-token",
				"BOOTSTRAP_ADMIN_USERNAME": "admin",
				"BOOTSTRAP_ADMIN_PASSWORD": "admin-pass",
			}
		)
		self.client = self.app.test_client()
		self.database_path = Path(self.app.config["DATABASE_PATH"])

	def tearDown(self) -> None:
		self.temp_dir.cleanup()

	def login(self) -> None:
		response = self.client.post(
			"/login",
			data={"username": "admin", "password": "admin-pass"},
		)
		self.assertEqual(response.status_code, 302)

	def test_ingest_json_persists_category_and_api_lists_categories(self) -> None:
		response = self.client.post(
			"/api/ingest",
			headers={"Authorization": "Bearer test-token"},
			json={
				"title": "Front Door",
				"message": "Motion detected",
				"source": "home-assistant",
				"category": "security",
			},
		)

		self.assertEqual(response.status_code, 201)
		payload = response.get_json()
		self.assertIsNotNone(payload)
		self.assertEqual(payload["notification"]["category"], "security")
		self.assertEqual(payload["notification"]["category_icon"], "️")
		self.assertRegex(payload["notification"]["category_color"], r"^#[0-9A-F]{6}$")

		self.login()
		response = self.client.get("/api/notifications")
		self.assertEqual(response.status_code, 200)
		payload = response.get_json()
		self.assertEqual(payload["categories"][0]["name"], "security")
		self.assertEqual(payload["categories"][0]["icon"], "️")
		self.assertEqual(payload["categories"][0]["notification_count"], 1)
		self.assertEqual(payload["notifications"][0]["category"], "security")

	def test_api_notifications_can_filter_by_category(self) -> None:
		for title, category in (("Front Door", "security"), ("Washer", "appliances")):
			response = self.client.post(
				"/api/ingest",
				headers={"Authorization": "Bearer test-token"},
				json={"title": title, "message": "Test", "category": category},
			)
			self.assertEqual(response.status_code, 201)

		self.login()
		response = self.client.get("/api/notifications?category=security")
		self.assertEqual(response.status_code, 200)
		payload = response.get_json()
		self.assertEqual(payload["selected_category"], "security")
		self.assertEqual([item["title"] for item in payload["notifications"]], ["Front Door"])
		self.assertEqual([item["name"] for item in payload["categories"]], ["appliances", "security"])

	def test_ingest_form_accepts_category_field(self) -> None:
		response = self.client.post(
			"/api/ingest",
			headers={"X-API-Key": "test-token"},
			data={
				"title": "Boiler",
				"message": "Maintenance reminder",
				"category": "maintenance",
			},
		)

		self.assertEqual(response.status_code, 201)
		payload = response.get_json()
		self.assertEqual(payload["notification"]["category"], "maintenance")
		category = db.get_category(self.database_path, "maintenance")
		self.assertIsNotNone(category)
		self.assertEqual(category["name"], "maintenance")

	def test_category_management_page_can_create_and_update_category_metadata(self) -> None:
		self.login()
		response = self.client.post(
			"/categories",
			data={"action": "create", "name": "security", "color": "#123456", "icon": ""},
		)
		self.assertEqual(response.status_code, 302)

		created = db.get_category(self.database_path, "security")
		self.assertIsNotNone(created)
		self.assertEqual(created["color"], "#123456")
		self.assertEqual(created["icon"], "")
		self.assertEqual(created["notification_count"], 0)

		response = self.client.post(
			"/categories",
			data={"action": "update", "name": "security", "color": "#654321", "icon": ""},
		)
		self.assertEqual(response.status_code, 302)

		updated = db.get_category(self.database_path, "security")
		self.assertIsNotNone(updated)
		self.assertEqual(updated["color"], "#654321")
		self.assertEqual(updated["icon"], "")

	def test_notifications_page_renders_category_filter_options_and_management_link(self) -> None:
		db.upsert_category(self.database_path, name="security", color="#123456", icon="️")
		response = self.client.post(
			"/api/ingest",
			headers={"Authorization": "Bearer test-token"},
			json={"title": "Front Door", "message": "Test", "category": "security"},
		)
		self.assertEqual(response.status_code, 201)

		self.login()
		response = self.client.get("/notifications")
		self.assertEqual(response.status_code, 200)
		html = response.get_data(as_text=True)
		self.assertIn("Show all notifications available", html)
		self.assertIn('href="/categories"', html)
		self.assertIn('<option value="security">️ security</option>', html)
		self.assertIn('badge badge-category', html)

	def test_notifications_page_can_render_only_the_selected_category(self) -> None:
		for title, category in (("Front Door", "security"), ("Washer", "appliances")):
			response = self.client.post(
				"/api/ingest",
				headers={"Authorization": "Bearer test-token"},
				json={"title": title, "message": "Test", "category": category},
			)
			self.assertEqual(response.status_code, 201)

		self.login()
		response = self.client.get("/notifications?category=security")
		self.assertEqual(response.status_code, 200)
		html = response.get_data(as_text=True)
		self.assertIn("Front Door", html)
		self.assertNotIn("Washer", html)
		self.assertIn('<option value="security" selected>', html)

	def test_notifications_page_renders_fullscreen_image_trigger_markup(self) -> None:
		db.create_notification(
			self.database_path,
			title="Front Door",
			message="Snapshot ready",
			source="home-assistant",
			image_url="https://example.local/snapshot.jpg",
		)

		self.login()
		response = self.client.get("/notifications")
		self.assertEqual(response.status_code, 200)
		html = response.get_data(as_text=True)
		self.assertIn('class="notification-image-button"', html)
		self.assertIn('id="image-viewer"', html)
		self.assertIn('data-image-src="https://example.local/snapshot.jpg"', html)

	def test_home_assistant_trigger_error_includes_linux_docker_network_hint_for_localhost(self) -> None:
		app = create_app(
			{
				"TESTING": True,
				"SECRET_KEY": "test-secret",
				"INSTANCE_PATH": self.instance_path / "trigger-instance",
				"MEDIA_DIR": self.media_dir,
				"HOME_ASSISTANT_API_TOKEN": "test-token",
				"BOOTSTRAP_ADMIN_USERNAME": "admin",
				"BOOTSTRAP_ADMIN_PASSWORD": "admin-pass",
				"HOME_ASSISTANT_BASE_URL": "http://127.0.0.1:8123",
				"HOME_ASSISTANT_ACCESS_TOKEN": "secret",
				"HOME_ASSISTANT_AUTOMATION_ENTITY_ID": "automation.test",
			}
		)
		client = app.test_client()
		login_response = client.post("/login", data={"username": "admin", "password": "admin-pass"})
		self.assertEqual(login_response.status_code, 302)

		response = client.post("/api/home-assistant/trigger", json={"entity_id": "automation.test"})
		self.assertEqual(response.status_code, 400)
		payload = response.get_json()
		self.assertIsNotNone(payload)
		self.assertIn("127.0.0.1:8123", payload["error"])
		self.assertIn("inside Docker means the container itself", payload["error"])

	def test_category_management_page_renders_existing_categories(self) -> None:
		db.upsert_category(self.database_path, name="garage", color="#654321", icon="")

		self.login()
		response = self.client.get("/categories")
		self.assertEqual(response.status_code, 200)
		html = response.get_data(as_text=True)
		self.assertIn("Category management", html)
		self.assertIn("Create category", html)
		self.assertIn("garage", html)
		self.assertIn("", html)

	def test_init_db_migrates_existing_database_without_category_column(self) -> None:
		legacy_db_path = self.instance_path / "legacy.db"
		legacy_db_path.parent.mkdir(parents=True, exist_ok=True)
		with sqlite3.connect(legacy_db_path) as connection:
			connection.executescript(
				"""
				CREATE TABLE notifications (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					title TEXT NOT NULL,
					message TEXT NOT NULL DEFAULT '',
					source TEXT NOT NULL DEFAULT 'home-assistant',
					image_path TEXT,
					image_url TEXT,
					created_at TEXT NOT NULL
				);
				"""
			)

		db.init_db(legacy_db_path)
		notification = db.create_notification(
			legacy_db_path,
			title="Garage",
			message="Opened",
			source="home-assistant",
			category="garage",
		)

		self.assertEqual(notification["category"], "garage")
		self.assertEqual(db.list_categories(legacy_db_path)[0]["name"], "garage")

		with sqlite3.connect(legacy_db_path) as connection:
			columns = [row[1] for row in connection.execute("PRAGMA table_info(notifications)").fetchall()]
		self.assertIn("category", columns)

	def test_init_db_backfills_categories_from_existing_notifications(self) -> None:
		legacy_db_path = self.instance_path / "backfill.db"
		legacy_db_path.parent.mkdir(parents=True, exist_ok=True)
		with sqlite3.connect(legacy_db_path) as connection:
			connection.executescript(
				"""
				CREATE TABLE notifications (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					title TEXT NOT NULL,
					message TEXT NOT NULL DEFAULT '',
					source TEXT NOT NULL DEFAULT 'home-assistant',
					category TEXT,
					image_path TEXT,
					image_url TEXT,
					created_at TEXT NOT NULL
				);
				INSERT INTO notifications (title, message, source, category, created_at)
				VALUES ('Front Door', 'Motion detected', 'home-assistant', 'security', '2026-01-01T00:00:00');
				"""
			)

		db.init_db(legacy_db_path)
		categories = db.list_categories(legacy_db_path)
		self.assertEqual([item["name"] for item in categories], ["security"])
		self.assertEqual(categories[0]["icon"], "️")
		self.assertEqual(categories[0]["notification_count"], 1)


if __name__ == "__main__":
	unittest.main()



