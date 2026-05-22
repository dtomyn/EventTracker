from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.db import connection_context, init_db, is_sqlite_vec_enabled
from app.env import load_app_env
from app.services.embeddings import (
    EmbeddingConfigurationError,
    _utc_now_iso,
    _validate_dimensions,
    get_embedding_index_state,
    load_embedding_settings,
    search_semantic_matches,
    sync_entry_embedding,
)


class TestLoadEmbeddingSettings(unittest.TestCase):
    def setUp(self) -> None:
        load_embedding_settings.cache_clear()
        load_app_env.cache_clear()

    def tearDown(self) -> None:
        load_embedding_settings.cache_clear()
        load_app_env.cache_clear()

    def test_missing_api_key_raises_configuration_error(self) -> None:
        with patch("app.services.embeddings.load_app_env"):
            with patch.dict(
                os.environ,
                {"OPENAI_EMBEDDING_MODEL_ID": "text-embedding-3-small"},
                clear=True,
            ):
                load_embedding_settings.cache_clear()
                with self.assertRaises(EmbeddingConfigurationError):
                    load_embedding_settings()

    def test_missing_model_raises_configuration_error(self) -> None:
        with patch("app.services.embeddings.load_app_env"):
            with patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "sk-test-key"},
                clear=True,
            ):
                load_embedding_settings.cache_clear()
                with self.assertRaises(EmbeddingConfigurationError):
                    load_embedding_settings()

    def test_valid_config_returns_settings(self) -> None:
        with patch("app.services.embeddings.load_app_env"):
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "sk-test-key",
                    "OPENAI_EMBEDDING_MODEL_ID": "text-embedding-3-small",
                },
                clear=True,
            ):
                load_embedding_settings.cache_clear()
                settings = load_embedding_settings()
        self.assertEqual(settings.api_key, "sk-test-key")
        self.assertEqual(settings.model_id, "text-embedding-3-small")


class TestValidateDimensions(unittest.TestCase):
    def test_valid_positive_integer(self) -> None:
        self.assertEqual(_validate_dimensions(128), 128)
        self.assertEqual(_validate_dimensions(1536), 1536)

    def test_zero_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _validate_dimensions(0)

    def test_negative_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _validate_dimensions(-1)
        with self.assertRaises(ValueError):
            _validate_dimensions(-100)

    def test_non_int_raises_value_error(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            _validate_dimensions("abc")  # type: ignore[arg-type]
        with self.assertRaises((TypeError, ValueError)):
            _validate_dimensions(3.5)  # type: ignore[arg-type]


class TestUtcNowIso(unittest.TestCase):
    def test_returns_string(self) -> None:
        result = _utc_now_iso()
        self.assertIsInstance(result, str)

    def test_contains_t_separator(self) -> None:
        result = _utc_now_iso()
        self.assertIn("T", result)

    def test_reasonable_length(self) -> None:
        result = _utc_now_iso()
        # ISO 8601 datetime like "2026-04-06T12:00:00+00:00" is ~25 chars
        self.assertGreater(len(result), 15)
        self.assertLess(len(result), 40)


class TestGetEmbeddingIndexState(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._prev_db = os.environ.get("EVENTTRACKER_DB_PATH")
        self._prev_testing = os.environ.get("TESTING")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "test.db"
        )
        os.environ["TESTING"] = "1"
        load_app_env.cache_clear()
        load_embedding_settings.cache_clear()
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
        load_embedding_settings.cache_clear()

    def test_returns_none_when_no_state_exists(self) -> None:
        with connection_context() as conn:
            state = get_embedding_index_state(conn)
        self.assertIsNone(state)

    def test_returns_state_after_init(self) -> None:
        with connection_context() as conn:
            conn.execute(
                """
                INSERT INTO embedding_index_meta(singleton, model_id, dimensions, updated_utc)
                VALUES (1, ?, ?, ?)
                """,
                ("text-embedding-3-small", 1536, "2026-01-01T00:00:00+00:00"),
            )

        with connection_context() as conn:
            state = get_embedding_index_state(conn)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.model_id, "text-embedding-3-small")
        self.assertEqual(state.dimensions, 1536)
        self.assertEqual(state.updated_utc, "2026-01-01T00:00:00+00:00")


class TestSyncEntryEmbedding(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._prev_db = os.environ.get("EVENTTRACKER_DB_PATH")
        self._prev_testing = os.environ.get("TESTING")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "test.db"
        )
        os.environ["TESTING"] = "1"
        load_app_env.cache_clear()
        load_embedding_settings.cache_clear()
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
        load_embedding_settings.cache_clear()

    def test_empty_text_returns_false(self) -> None:
        with connection_context() as conn:
            result = sync_entry_embedding(conn, 1, "")
        self.assertFalse(result)

    def test_whitespace_only_text_returns_false(self) -> None:
        with connection_context() as conn:
            result = sync_entry_embedding(conn, 1, "   ")
        self.assertFalse(result)

    def test_disabled_sqlite_vec_returns_false(self) -> None:
        with connection_context() as conn:
            with patch(
                "app.services.embeddings.is_sqlite_vec_enabled", return_value=False
            ):
                result = sync_entry_embedding(conn, 1, "some text")
        self.assertFalse(result)


class TestSearchSemanticMatches(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._prev_db = os.environ.get("EVENTTRACKER_DB_PATH")
        self._prev_testing = os.environ.get("TESTING")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "test.db"
        )
        os.environ["TESTING"] = "1"
        load_app_env.cache_clear()
        load_embedding_settings.cache_clear()
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
        load_embedding_settings.cache_clear()

    def test_empty_query_returns_empty_list(self) -> None:
        with connection_context() as conn:
            result = search_semantic_matches(conn, "")
        self.assertEqual(result, [])

    def test_whitespace_query_returns_empty_list(self) -> None:
        with connection_context() as conn:
            result = search_semantic_matches(conn, "   ")
        self.assertEqual(result, [])

    def test_disabled_sqlite_vec_returns_empty_list(self) -> None:
        with connection_context() as conn:
            with patch(
                "app.services.embeddings.is_sqlite_vec_enabled", return_value=False
            ):
                result = search_semantic_matches(conn, "test query")
        self.assertEqual(result, [])
