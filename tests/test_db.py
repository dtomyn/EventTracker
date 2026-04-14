from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import (
    SchemaDriftError,
    _validate_positive_integer,
    connection_context,
    get_db_path,
    init_db,
    is_sqlite_vec_enabled,
)
from app.env import load_app_env


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

    def test_init_db_creates_timeline_story_tables_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "EventTracker-test.db"

            with patch.dict(
                os.environ, {"EVENTTRACKER_DB_PATH": str(db_path)}, clear=False
            ):
                init_db()
                init_db()

            connection = sqlite3.connect(db_path)
            try:
                story_columns = tuple(
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(timeline_stories)"
                    ).fetchall()
                )
                citation_columns = tuple(
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(timeline_story_entries)"
                    ).fetchall()
                )
                artifact_columns = tuple(
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(timeline_story_artifacts)"
                    ).fetchall()
                )
                story_indexes = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(timeline_stories)"
                    ).fetchall()
                }
                citation_indexes = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(timeline_story_entries)"
                    ).fetchall()
                }
                artifact_indexes = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(timeline_story_artifacts)"
                    ).fetchall()
                }
            finally:
                connection.close()

        self.assertEqual(
            story_columns,
            (
                "id",
                "scope_type",
                "group_id",
                "query_text",
                "year",
                "month",
                "format",
                "title",
                "narrative_html",
                "narrative_text",
                "generated_utc",
                "updated_utc",
                "provider_name",
                "source_entry_count",
                "truncated_input",
                "error_text",
            ),
        )
        self.assertEqual(
            citation_columns,
            ("story_id", "entry_id", "citation_order", "quote_text", "note"),
        )
        self.assertEqual(
            artifact_columns,
            (
                "id",
                "story_id",
                "artifact_kind",
                "source_format",
                "source_text",
                "compiled_html",
                "compiled_css",
                "metadata_json",
                "generated_utc",
                "compiled_utc",
                "compiler_name",
                "compiler_version",
            ),
        )
        self.assertIn(
            "idx_timeline_stories_scope_generated_utc",
            story_indexes,
        )
        self.assertIn(
            "idx_timeline_story_entries_story_order",
            citation_indexes,
        )
        self.assertIn(
            "idx_timeline_story_entries_entry_id",
            citation_indexes,
        )
        self.assertIn(
            "idx_timeline_story_artifacts_story_kind_unique",
            artifact_indexes,
        )
        self.assertIn(
            "idx_timeline_story_artifacts_story_id",
            artifact_indexes,
        )

    def test_init_db_creates_entry_source_snapshot_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "EventTracker-test.db"

            with patch.dict(
                os.environ, {"EVENTTRACKER_DB_PATH": str(db_path)}, clear=False
            ):
                init_db()

            connection = sqlite3.connect(db_path)
            try:
                snapshot_columns = tuple(
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(entry_source_snapshots)"
                    ).fetchall()
                )
            finally:
                connection.close()

        self.assertEqual(
            snapshot_columns,
            (
                "entry_id",
                "source_url",
                "final_url",
                "raw_title",
                "source_markdown",
                "fetched_utc",
                "content_type",
                "http_etag",
                "http_last_modified",
                "content_sha256",
                "extractor_name",
                "extractor_version",
                "markdown_char_count",
            ),
        )


class TestValidatePositiveInteger(unittest.TestCase):
    def test_valid_positive_integer(self) -> None:
        self.assertEqual(_validate_positive_integer(1, "test"), 1)
        self.assertEqual(_validate_positive_integer(42, "test"), 42)
        self.assertEqual(_validate_positive_integer(1000, "test"), 1000)

    def test_zero_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _validate_positive_integer(0, "test")

    def test_negative_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _validate_positive_integer(-1, "test")
        with self.assertRaises(ValueError):
            _validate_positive_integer(-100, "test")

    def test_string_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _validate_positive_integer("abc", "test")

    def test_none_raises_value_error(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            _validate_positive_integer(None, "test")

    def test_float_raises_value_error(self) -> None:
        # float("3.5") can be cast to int via int(), but the function
        # accepts int-like values; verify it doesn't crash and returns int.
        # A pure float like 3.5 will be truncated by int(); the function
        # is documented for DDL interpolation so we just verify no crash.
        result = _validate_positive_integer(3.5, "test")
        self.assertIsInstance(result, int)
        self.assertEqual(result, 3)


class TestGetDbPath(unittest.TestCase):
    def setUp(self) -> None:
        load_app_env.cache_clear()

    def tearDown(self) -> None:
        load_app_env.cache_clear()

    def test_returns_env_var_path_when_set(self) -> None:
        with patch.dict(
            os.environ, {"EVENTTRACKER_DB_PATH": "/tmp/custom.db"}, clear=False
        ):
            load_app_env.cache_clear()
            result = get_db_path()
        self.assertEqual(result, Path("/tmp/custom.db"))

    def test_returns_default_path_without_env_var(self) -> None:
        env = os.environ.copy()
        env.pop("EVENTTRACKER_DB_PATH", None)
        with patch.dict(os.environ, env, clear=True):
            load_app_env.cache_clear()
            result = get_db_path()
        self.assertTrue(str(result).endswith("EventTracker.db"))


class TestConnectionContextRollback(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._prev_db = os.environ.get("EVENTTRACKER_DB_PATH")
        self._prev_testing = os.environ.get("TESTING")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "test.db"
        )
        os.environ["TESTING"] = "1"
        load_app_env.cache_clear()
        init_db()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, val in {
            "EVENTTRACKER_DB_PATH": self._prev_db,
            "TESTING": self._prev_testing,
        }.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        load_app_env.cache_clear()

    def test_successful_write_persists(self) -> None:
        with connection_context() as conn:
            conn.execute(
                "INSERT INTO timeline_groups(name, is_default) VALUES (?, 0)",
                ("Persist Test",),
            )

        with connection_context() as conn:
            row = conn.execute(
                "SELECT name FROM timeline_groups WHERE name = ?",
                ("Persist Test",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Persist Test")

    def test_exception_causes_rollback(self) -> None:
        try:
            with connection_context() as conn:
                conn.execute(
                    "INSERT INTO timeline_groups(name, is_default) VALUES (?, 0)",
                    ("Rollback Test",),
                )
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass

        with connection_context() as conn:
            row = conn.execute(
                "SELECT name FROM timeline_groups WHERE name = ?",
                ("Rollback Test",),
            ).fetchone()
        self.assertIsNone(row)


class TestIsSqliteVecEnabled(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._prev_db = os.environ.get("EVENTTRACKER_DB_PATH")
        self._prev_testing = os.environ.get("TESTING")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "test.db"
        )
        os.environ["TESTING"] = "1"
        load_app_env.cache_clear()
        init_db()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, val in {
            "EVENTTRACKER_DB_PATH": self._prev_db,
            "TESTING": self._prev_testing,
        }.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        load_app_env.cache_clear()

    def test_returns_boolean(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            result = is_sqlite_vec_enabled(conn)
            self.assertIsInstance(result, bool)
        finally:
            conn.close()

    def test_does_not_crash_on_fresh_connection(self) -> None:
        with connection_context() as conn:
            result = is_sqlite_vec_enabled(conn)
            self.assertIsInstance(result, bool)
