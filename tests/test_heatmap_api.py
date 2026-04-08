from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.db import connection_context, init_db


class TestHeatmapAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        os.environ["EVENTTRACKER_DB_PATH"] = self.db_path
        os.environ["TESTING"] = "1"
        init_db()
        with connection_context() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO timeline_groups (id, name) VALUES (1, 'Test')"
            )
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 1, 'A', '<p>A</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 1, 'B', '<p>B</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 6, NULL, 20250600, 1, 'C', '<p>C</p>', '2025-06-01T00:00:00+00:00', '2025-06-01T00:00:00+00:00')"
            )
            conn.commit()

        from app.main import app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for key in ("EVENTTRACKER_DB_PATH", "TESTING"):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def test_heatmap_returns_counts(self) -> None:
        resp = self.client.get("/api/heatmap?year=2025")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["year"], 2025)
        self.assertEqual(data["counts"]["2025-03-15"], 2)
        self.assertEqual(data["counts"]["2025-06-01"], 1)
        self.assertEqual(data["total"], 3)

    def test_heatmap_filters_by_group(self) -> None:
        resp = self.client.get("/api/heatmap?year=2025&group_id=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 3)

    def test_heatmap_defaults_to_latest_year(self) -> None:
        resp = self.client.get("/api/heatmap")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["year"], 2025)

    def test_heatmap_empty_year(self) -> None:
        resp = self.client.get("/api/heatmap?year=2020")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["counts"], {})

    def test_heatmap_includes_years_available(self) -> None:
        resp = self.client.get("/api/heatmap?year=2025")
        data = resp.json()
        self.assertIn(2025, data["years_available"])

    def test_heatmap_entries_returns_html(self) -> None:
        resp = self.client.get("/timeline/heatmap/entries?year=2025&month=3&day=15")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("A", resp.text)
        self.assertIn("B", resp.text)

    def test_heatmap_entries_first_day_includes_dayless_entries(self) -> None:
        resp = self.client.get("/timeline/heatmap/entries?year=2025&month=6&day=1")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("C", resp.text)

    def test_heatmap_entries_empty_date(self) -> None:
        resp = self.client.get("/timeline/heatmap/entries?year=2025&month=1&day=1")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("No entries", resp.text)
