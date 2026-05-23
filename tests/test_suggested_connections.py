from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from app.db import connection_context, init_db
from app.schemas import EntryPayload
from app.services.embeddings import SemanticMatch
from app.services.entries import save_entry
from app.services.suggested_connections import (
    _parse_numbered_lines,
    accept_suggestion,
    dismiss_suggestion,
    find_similar_entries_by_text,
    get_pending_suggestions,
    save_suggestions,
)


class TestSuggestedConnections(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("EVENTTRACKER_DB_PATH")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "EventTracker-test.db"
        )
        init_db()

    def tearDown(self) -> None:
        if self.previous_db_path is None:
            os.environ.pop("EVENTTRACKER_DB_PATH", None)
        else:
            os.environ["EVENTTRACKER_DB_PATH"] = self.previous_db_path
        self.temp_dir.cleanup()

    def _add_entry(self, title: str, *, day: int | None) -> int:
        with connection_context() as connection:
            return save_entry(
                connection,
                EntryPayload(
                    event_year=2026,
                    event_month=3,
                    event_day=day,
                    group_id=1,
                    title=title,
                    source_url=f"https://example.com/{uuid.uuid4()}",
                    generated_text="",
                    final_text="Body",
                    tags=[],
                    links=[],
                ),
            )

    def test_parse_numbered_lines_maps_supported_line_formats(self) -> None:
        parsed = _parse_numbered_lines(
            "1. First\n2) Second\n4. Out of range\nno number\n0. Invalid",
            3,
        )

        self.assertEqual(parsed, ["First", "Second", ""])

    def test_save_suggestions_replaces_only_pending_rows(self) -> None:
        source_id = self._add_entry("Source", day=1)
        target_a = self._add_entry("Target A", day=2)
        target_b = self._add_entry("Target B", day=None)
        now = "2026-03-24T00:00:00+00:00"

        with connection_context() as connection:
            connection.execute(
                """
                INSERT INTO suggested_connections
                    (entry_id, suggested_entry_id, distance, suggested_note, status, created_utc, updated_utc)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (source_id, target_a, 0.2, "old pending", now, now),
            )
            connection.execute(
                """
                INSERT INTO suggested_connections
                    (entry_id, suggested_entry_id, distance, suggested_note, status, created_utc, updated_utc)
                VALUES (?, ?, ?, ?, 'dismissed', ?, ?)
                """,
                (source_id, target_b, 0.3, "keep dismissed", now, now),
            )
            connection.commit()

            saved = save_suggestions(
                connection,
                source_id,
                [
                    {
                        "entry_id": target_a,
                        "distance": 0.1,
                        "suggested_note": "new pending",
                    }
                ],
                now,
            )

            statuses = connection.execute(
                "SELECT suggested_entry_id, status, suggested_note FROM suggested_connections WHERE entry_id = ?",
                (source_id,),
            ).fetchall()

        self.assertEqual(saved, 1)
        self.assertEqual(
            {(int(row["suggested_entry_id"]), str(row["status"])) for row in statuses},
            {(target_a, "pending"), (target_b, "dismissed")},
        )

    def test_get_pending_suggestions_formats_month_day_and_month_year(self) -> None:
        source_id = self._add_entry("Source", day=1)
        day_entry = self._add_entry("Day Entry", day=12)
        month_entry = self._add_entry("Month Entry", day=None)
        now = "2026-03-24T00:00:00+00:00"

        with connection_context() as connection:
            connection.execute(
                """
                INSERT INTO suggested_connections
                    (entry_id, suggested_entry_id, distance, suggested_note, status, created_utc, updated_utc)
                VALUES (?, ?, ?, ?, 'pending', ?, ?), (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    source_id,
                    day_entry,
                    0.11,
                    "with day",
                    now,
                    now,
                    source_id,
                    month_entry,
                    0.22,
                    "month only",
                    now,
                    now,
                ),
            )
            connection.commit()

            suggestions = get_pending_suggestions(connection, source_id)

        dates_by_title = {
            suggestion.suggested_entry_title: suggestion.suggested_entry_date
            for suggestion in suggestions
        }
        self.assertEqual(dates_by_title["Day Entry"], "March 12, 2026")
        self.assertEqual(dates_by_title["Month Entry"], "March 2026")

    def test_accept_suggestion_creates_connection_and_marks_accepted(self) -> None:
        source_id = self._add_entry("Source", day=1)
        target_id = self._add_entry("Target", day=2)
        now = "2026-03-24T00:00:00+00:00"

        with connection_context() as connection:
            cursor = connection.execute(
                """
                INSERT INTO suggested_connections
                    (entry_id, suggested_entry_id, distance, suggested_note, status, created_utc, updated_utc)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (source_id, target_id, 0.12, "linked note", now, now),
            )
            suggestion_id = int(cursor.lastrowid)
            connection.commit()

            accepted = accept_suggestion(connection, suggestion_id, now)
            status_row = connection.execute(
                "SELECT status FROM suggested_connections WHERE id = ?", (suggestion_id,)
            ).fetchone()
            link_row = connection.execute(
                "SELECT source_entry_id, target_entry_id, note FROM entry_connections WHERE source_entry_id = ? AND target_entry_id = ?",
                (source_id, target_id),
            ).fetchone()

        self.assertEqual(accepted, (source_id, target_id))
        self.assertEqual(str(status_row["status"]), "accepted")
        self.assertEqual(
            (int(link_row["source_entry_id"]), int(link_row["target_entry_id"]), str(link_row["note"])),
            (source_id, target_id, "linked note"),
        )

    def test_dismiss_suggestion_updates_only_pending_rows(self) -> None:
        source_id = self._add_entry("Source", day=1)
        target_id = self._add_entry("Target", day=2)
        now = "2026-03-24T00:00:00+00:00"

        with connection_context() as connection:
            cursor = connection.execute(
                """
                INSERT INTO suggested_connections
                    (entry_id, suggested_entry_id, distance, suggested_note, status, created_utc, updated_utc)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (source_id, target_id, 0.1, "dismiss me", now, now),
            )
            suggestion_id = int(cursor.lastrowid)
            connection.commit()

            first = dismiss_suggestion(connection, suggestion_id, now)
            second = dismiss_suggestion(connection, suggestion_id, now)

        self.assertTrue(first)
        self.assertFalse(second)

    def test_find_similar_entries_by_text_filters_threshold_missing_and_excluded(self) -> None:
        source_id = self._add_entry("Source", day=1)
        kept_id = self._add_entry("Kept", day=7)
        filtered_id = self._add_entry("Too Far", day=None)

        with connection_context() as connection:
            with patch(
                "app.services.embeddings.search_semantic_matches",
                return_value=[
                    SemanticMatch(entry_id=source_id, distance=0.05),
                    SemanticMatch(entry_id=kept_id, distance=0.2),
                    SemanticMatch(entry_id=999999, distance=0.1),
                    SemanticMatch(entry_id=filtered_id, distance=0.9),
                ],
            ):
                results = find_similar_entries_by_text(
                    connection,
                    "query text",
                    exclude_entry_id=source_id,
                    distance_threshold=0.5,
                    limit=5,
                )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["entry_id"], kept_id)
        self.assertEqual(results[0]["title"], "Kept")
        self.assertEqual(results[0]["display_date"], "March 7, 2026")
        self.assertEqual(results[0]["group_name"], "Agentic Coding")


if __name__ == "__main__":
    unittest.main()
