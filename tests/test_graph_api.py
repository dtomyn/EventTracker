"""API tests for the Connection Graph and Tag Clusters data endpoints."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.db import connection_context, init_db

_TS = "2026-03-01T00:00:00+00:00"


class TestGraphAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EVENTTRACKER_DB_PATH"] = os.path.join(self.tmp.name, "test.db")
        os.environ["TESTING"] = "1"
        init_db()
        with connection_context() as conn:
            conn.execute("INSERT OR IGNORE INTO timeline_groups (id, name) VALUES (1, 'Test')")
            conn.execute("INSERT OR IGNORE INTO timeline_groups (id, name) VALUES (2, 'Other')")
            rows = [
                (1, 2025, 3, 15, 20250315, 1, "Alpha", "<p>Alpha <b>body</b> text.</p>"),
                (2, 2025, 6, None, 20250600, 1, "Beta", "<p>" + ("long " * 120) + "</p>"),
                (3, 2025, 9, 2, 20250902, 1, "Gamma", "<p>Gamma</p>"),
                (4, 2025, 1, 1, 20250101, 2, "Elsewhere", "<p>Other group</p>"),
            ]
            for row in rows:
                conn.execute(
                    "INSERT INTO entries (id, event_year, event_month, event_day, sort_key, group_id, "
                    "title, final_text, created_utc, updated_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*row, _TS, _TS),
                )
            for source, target in ((1, 2), (2, 3)):
                conn.execute(
                    "INSERT INTO entry_connections (source_entry_id, target_entry_id, note, created_utc) "
                    "VALUES (?, ?, '', ?)",
                    (source, target, _TS),
                )
            for name in ("zeta", "ai"):
                conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
            conn.execute(
                "INSERT INTO entry_tags (entry_id, tag_id) SELECT 1, id FROM tags WHERE name IN ('zeta', 'ai')"
            )
            graph = {
                "nodes": [
                    {"id": "ai", "label": "ai", "entry_ids": [1, 3, 4], "size": 3},
                ],
                "edges": [],
            }
            conn.execute(
                "INSERT INTO topic_cluster_cache (group_id, graph_json, updated_utc) VALUES (1, ?, ?)",
                (json.dumps(graph), _TS),
            )
            conn.commit()

        from app.main import app

        self.client = TestClient(app)

    def tearDown(self) -> None:
        for key in ("EVENTTRACKER_DB_PATH", "TESTING"):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def test_connection_nodes_include_dates_tags_and_excerpt(self) -> None:
        resp = self.client.get("/api/groups/1/connections")
        self.assertEqual(resp.status_code, 200)
        nodes = {node["id"]: node for node in resp.json()["nodes"]}
        self.assertEqual(set(nodes), {1, 2, 3})

        alpha = nodes[1]
        self.assertEqual(alpha["date"], "2025-03-15")
        self.assertEqual(alpha["sort_key"], 20250315)
        self.assertEqual(alpha["tags"], ["ai", "zeta"])
        self.assertEqual(alpha["excerpt"], "Alpha body text.")

        beta = nodes[2]
        # A missing day falls back to the first of the month for ordering.
        self.assertEqual(beta["date"], "2025-06-01")
        self.assertEqual(beta["display_date"], "June 2025")
        self.assertEqual(beta["tags"], [])
        self.assertLessEqual(len(beta["excerpt"]), 240)
        self.assertTrue(beta["excerpt"].endswith("…"))

    def test_topics_payload_includes_entry_summaries_scoped_to_group(self) -> None:
        resp = self.client.get("/api/groups/1/topics")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([node["id"] for node in data["nodes"]], ["ai"])
        entries = data["entries"]
        # Entry 4 belongs to another group and must not leak into this payload.
        self.assertEqual(set(entries), {"1", "3"})
        self.assertEqual(entries["1"]["title"], "Alpha")
        self.assertEqual(entries["1"]["display_date"], "Mar 15, 2025")
        self.assertEqual(entries["3"]["sort_key"], 20250902)

    def test_topics_payload_empty_cache_has_empty_entries(self) -> None:
        resp = self.client.get("/api/groups/2/topics")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"nodes": [], "edges": [], "entries": {}})

    def test_graph_endpoints_404_for_missing_group(self) -> None:
        self.assertEqual(self.client.get("/api/groups/999/connections").status_code, 404)
        self.assertEqual(self.client.get("/api/groups/999/topics").status_code, 404)


if __name__ == "__main__":
    unittest.main()
