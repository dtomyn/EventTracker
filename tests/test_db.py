from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import SchemaDriftError, init_db


class TestDatabaseInitialization(unittest.TestCase):
    def test_init_db_fails_fast_without_dropping_unexpected_entries_schema(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "EventTracker-test.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE entries (
                    id INTEGER PRIMARY KEY,
                    event_year INTEGER NOT NULL,
                    event_month INTEGER NOT NULL,
                    event_day INTEGER NULL,
                    sort_key INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    final_text TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL,
                    unexpected_column TEXT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO entries(
                    event_year,
                    event_month,
                    event_day,
                    sort_key,
                    title,
                    final_text,
                    created_utc,
                    updated_utc,
                    unexpected_column
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    2026,
                    3,
                    18,
                    20260318,
                    "Existing entry",
                    "Existing body",
                    "2026-03-18T00:00:00Z",
                    "2026-03-18T00:00:00Z",
                    "keep-me",
                ),
            )
            connection.commit()
            connection.close()

            with patch.dict(
                os.environ, {"EVENTTRACKER_DB_PATH": str(db_path)}, clear=False
            ):
                with self.assertRaises(SchemaDriftError):
                    init_db()

            persisted = sqlite3.connect(db_path)
            row = persisted.execute(
                "SELECT title, unexpected_column FROM entries"
            ).fetchone()
            persisted.close()

        self.assertEqual(row, ("Existing entry", "keep-me"))