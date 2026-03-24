from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import connection_context, init_db
from app.main import app
from app.services.ai_generate import get_draft_generator
from app.services.embeddings import load_embedding_settings


ENV_KEYS = (
    "EVENTTRACKER_DB_PATH",
    "EVENTTRACKER_AI_PROVIDER",
    "COPILOT_CHAT_MODEL_ID",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_CHAT_MODEL_ID",
    "OPENAI_EMBEDDING_MODEL_ID",
    "TESTING",
)


class TestAdminGroups(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_env = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "EventTracker-test.db"
        )
        os.environ["TESTING"] = "1"
        get_draft_generator.cache_clear()
        load_embedding_settings.cache_clear()

    def tearDown(self) -> None:
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_draft_generator.cache_clear()
        load_embedding_settings.cache_clear()
        self.temp_dir.cleanup()

    def test_create_group_with_empty_name_returns_400(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/admin/groups",
                data={"name": "", "web_search_query": ""},
            )
        self.assertEqual(response.status_code, 400)

    def test_create_group_with_whitespace_only_name_returns_400(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/admin/groups",
                data={"name": "   ", "web_search_query": ""},
            )
        self.assertEqual(response.status_code, 400)

    def test_create_group_success_redirects(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/admin/groups",
                data={"name": "Test Group", "web_search_query": "test query"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

    def test_create_duplicate_group_name_returns_400(self) -> None:
        with TestClient(app) as client:
            client.post(
                "/admin/groups",
                data={"name": "Duplicate", "web_search_query": ""},
                follow_redirects=False,
            )
            response = client.post(
                "/admin/groups",
                data={"name": "Duplicate", "web_search_query": ""},
            )
        self.assertEqual(response.status_code, 400)

    def test_update_nonexistent_group_returns_404(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/admin/groups/99999",
                data={"name": "No Such Group", "web_search_query": ""},
            )
        self.assertEqual(response.status_code, 404)

    def test_delete_nonexistent_group_returns_404(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/admin/groups/99999/delete",
            )
        self.assertEqual(response.status_code, 404)

    def test_delete_group_removes_it(self) -> None:
        with TestClient(app) as client:
            client.post(
                "/admin/groups",
                data={"name": "To Delete", "web_search_query": ""},
                follow_redirects=False,
            )
            with connection_context() as connection:
                row = connection.execute(
                    "SELECT id FROM timeline_groups WHERE name = ?",
                    ("To Delete",),
                ).fetchone()
            self.assertIsNotNone(row)
            group_id = int(row["id"])

            delete_response = client.post(
                f"/admin/groups/{group_id}/delete",
                follow_redirects=False,
            )
            self.assertEqual(delete_response.status_code, 303)

            admin_page = client.get("/admin/groups")
            self.assertNotIn("To Delete", admin_page.text)

    def test_update_group_name_success(self) -> None:
        with TestClient(app) as client:
            client.post(
                "/admin/groups",
                data={"name": "Original Name", "web_search_query": ""},
                follow_redirects=False,
            )
            with connection_context() as connection:
                row = connection.execute(
                    "SELECT id FROM timeline_groups WHERE name = ?",
                    ("Original Name",),
                ).fetchone()
            group_id = int(row["id"])

            update_response = client.post(
                f"/admin/groups/{group_id}",
                data={"name": "Updated Name", "web_search_query": "updated query"},
                follow_redirects=False,
            )
            self.assertEqual(update_response.status_code, 303)

            admin_page = client.get("/admin/groups")
            self.assertIn("Updated Name", admin_page.text)
            self.assertNotIn("Original Name", admin_page.text)

    def test_admin_page_loads(self) -> None:
        with TestClient(app) as client:
            response = client.get("/admin/groups")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Agentic Coding", response.text)

    def test_csrf_rejection_without_token(self) -> None:
        """Verify CSRF middleware rejects POSTs when TESTING is not set."""
        previous_testing = os.environ.pop("TESTING", None)
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.post(
                    "/admin/groups",
                    data={"name": "Should Fail", "web_search_query": ""},
                )
            self.assertEqual(response.status_code, 403)
        finally:
            if previous_testing:
                os.environ["TESTING"] = previous_testing


class TestGetEntry(unittest.TestCase):
    """Tests for entry detail endpoint edge cases."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_env = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "EventTracker-test.db"
        )
        os.environ["TESTING"] = "1"
        get_draft_generator.cache_clear()
        load_embedding_settings.cache_clear()

    def tearDown(self) -> None:
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_draft_generator.cache_clear()
        load_embedding_settings.cache_clear()
        self.temp_dir.cleanup()

    def test_nonexistent_entry_returns_404(self) -> None:
        with TestClient(app) as client:
            response = client.get("/entries/99999")
        self.assertEqual(response.status_code, 404)
