from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.models import Entry, SearchResult
from app.services.search import (
    _find_exact_tag_entry_ids,
    _rrf_score,
    build_fts_query,
    decode_search_cursor,
    encode_search_cursor,
    filter_timeline_entries,
    normalize_search_page_size,
    paginate_search_results,
    search_entries,
)


def _entry(entry_id: int, *, title: str) -> Entry:
    return Entry(
        id=entry_id,
        event_year=2026,
        event_month=3,
        event_day=18,
        sort_key=20260318,
        group_id=1,
        group_name="Agentic Coding",
        title=title,
        source_url=None,
        generated_text=None,
        final_text=f"<p>{title}</p>",
        created_utc="2026-03-18T00:00:00+00:00",
        updated_utc="2026-03-18T00:00:00+00:00",
    )


def _search_result(entry_id: int, *, title: str, rank: float) -> SearchResult:
    return SearchResult(entry=_entry(entry_id, title=title), snippet=title, rank=rank)


class TestSearchHelpers(unittest.TestCase):
    def test_normalize_search_page_size_clamps_and_defaults(self) -> None:
        self.assertEqual(normalize_search_page_size(None), 20)
        self.assertEqual(normalize_search_page_size(0), 1)
        self.assertEqual(normalize_search_page_size(5), 5)
        self.assertEqual(normalize_search_page_size(999), 50)

    def test_search_cursor_round_trip(self) -> None:
        cursor = encode_search_cursor(12)

        self.assertEqual(decode_search_cursor(cursor), 12)

    def test_decode_search_cursor_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid search cursor"):
            decode_search_cursor("not-a-cursor")

        invalid_offset_cursor = encode_search_cursor(0)[:-2] + "xx"
        with self.assertRaisesRegex(ValueError, "Invalid search cursor"):
            decode_search_cursor(invalid_offset_cursor)

    def test_paginate_search_results_returns_next_cursor_when_more_results_exist(
        self,
    ) -> None:
        results = [
            _search_result(1, title="First", rank=1.0),
            _search_result(2, title="Second", rank=0.8),
            _search_result(3, title="Third", rank=0.7),
        ]

        page_results, next_cursor, has_more = paginate_search_results(
            results,
            page_size=2,
        )

        self.assertEqual([result.entry.id for result in page_results], [1, 2])
        self.assertTrue(has_more)
        self.assertIsNotNone(next_cursor)
        assert next_cursor is not None
        self.assertEqual(decode_search_cursor(next_cursor), 2)

    def test_build_fts_query_quotes_tokens(self) -> None:
        self.assertEqual(
            build_fts_query("  release notes alpha-beta  "),
            '"release" "notes" "alpha-beta"',
        )
        self.assertEqual(build_fts_query("..."), "")


class TestRrfScore(unittest.TestCase):
    def test_index_zero_default_k(self) -> None:
        self.assertAlmostEqual(_rrf_score(0), 1.0 / 61)

    def test_index_one_default_k(self) -> None:
        self.assertAlmostEqual(_rrf_score(1), 1.0 / 62)

    def test_custom_k_value(self) -> None:
        self.assertAlmostEqual(_rrf_score(0, k=10), 1.0 / 11)
        self.assertAlmostEqual(_rrf_score(5, k=10), 1.0 / 16)

    def test_index_ten_default_k(self) -> None:
        self.assertAlmostEqual(_rrf_score(10), 1.0 / 71)


class TestBuildFtsQueryEdgeCases(unittest.TestCase):
    def test_fts5_operators_are_quoted(self) -> None:
        self.assertEqual(build_fts_query("AND"), '"AND"')
        self.assertEqual(build_fts_query("OR"), '"OR"')
        self.assertEqual(build_fts_query("NOT"), '"NOT"')
        self.assertEqual(
            build_fts_query("cats AND dogs"),
            '"cats" "AND" "dogs"',
        )

    def test_unicode_characters(self) -> None:
        result = build_fts_query("caf\u00e9 na\u00efve")
        self.assertEqual(result, '"caf\u00e9" "na\u00efve"')

    def test_hyphens_preserved(self) -> None:
        self.assertEqual(build_fts_query("state-of-the-art"), '"state-of-the-art"')

    def test_punctuation_only_returns_empty(self) -> None:
        self.assertEqual(build_fts_query("!@#$%^&*()"), "")
        self.assertEqual(build_fts_query("...!!!???"), "")

    def test_very_long_query(self) -> None:
        long_query = " ".join(f"word{i}" for i in range(200))
        result = build_fts_query(long_query)
        tokens = result.split(" ")
        self.assertEqual(len(tokens), 200)
        self.assertTrue(all(t.startswith('"') and t.endswith('"') for t in tokens))

    def test_single_character(self) -> None:
        self.assertEqual(build_fts_query("x"), '"x"')
        self.assertEqual(build_fts_query("5"), '"5"')


class _DBTestCase(unittest.TestCase):
    """Base class for search tests that need a real SQLite database."""

    def setUp(self) -> None:
        from app.db import connection_context, init_db

        self.temp_dir = tempfile.TemporaryDirectory()
        self._prev_db = os.environ.get("EVENTTRACKER_DB_PATH")
        self._prev_testing = os.environ.get("TESTING")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "test.db"
        )
        os.environ["TESTING"] = "1"
        init_db()

    def tearDown(self) -> None:
        if self._prev_db is None:
            os.environ.pop("EVENTTRACKER_DB_PATH", None)
        else:
            os.environ["EVENTTRACKER_DB_PATH"] = self._prev_db

        if self._prev_testing is None:
            os.environ.pop("TESTING", None)
        else:
            os.environ["TESTING"] = self._prev_testing

        self.temp_dir.cleanup()

    def _insert_entry(
        self,
        conn,  # noqa: ANN001
        *,
        title: str = "Test Entry",
        final_text: str = "<p>Some text</p>",
        group_id: int = 1,
        tags: list[str] | None = None,
    ) -> int:
        now = "2026-03-18T00:00:00+00:00"
        conn.execute(
            "INSERT INTO entries "
            "(title, final_text, event_year, event_month, event_day, "
            "sort_key, group_id, source_url, created_utc, updated_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, final_text, 2026, 3, 18, 20260318, group_id,
             f"https://example.com/{uuid.uuid4()}", now, now),
        )
        entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        if tags:
            for tag_name in tags:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,)
                )
                tag_id = conn.execute(
                    "SELECT id FROM tags WHERE name = ?", (tag_name,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
                    (entry_id, tag_id),
                )
        return entry_id


class TestFindExactTagEntryIds(_DBTestCase):
    def test_exact_tag_match_case_insensitive(self) -> None:
        from app.db import connection_context

        with connection_context() as conn:
            eid = self._insert_entry(conn, title="Tagged", tags=["Python"])
            result = _find_exact_tag_entry_ids(conn, "python")
            self.assertEqual(result, [eid])

    def test_no_match_returns_empty(self) -> None:
        from app.db import connection_context

        with connection_context() as conn:
            self._insert_entry(conn, title="Entry", tags=["JavaScript"])
            result = _find_exact_tag_entry_ids(conn, "Rust")
            self.assertEqual(result, [])

    def test_with_group_id_filter(self) -> None:
        from app.db import connection_context

        with connection_context() as conn:
            # group_id=1 is the default "Agentic Coding" group seeded by init_db
            eid1 = self._insert_entry(
                conn, title="In Group 1", group_id=1, tags=["Deploy"]
            )
            # Create a second group
            conn.execute(
                "INSERT INTO timeline_groups (name) VALUES (?)", ("Other",)
            )
            gid2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._insert_entry(
                conn, title="In Group 2", group_id=gid2, tags=["Deploy"]
            )

            result = _find_exact_tag_entry_ids(conn, "Deploy", group_id=1)
            self.assertEqual(result, [eid1])

    def test_whitespace_only_query_returns_empty(self) -> None:
        from app.db import connection_context

        with connection_context() as conn:
            self._insert_entry(conn, title="Entry", tags=["Tag"])
            result = _find_exact_tag_entry_ids(conn, "   ")
            self.assertEqual(result, [])


class TestSearchEntries(_DBTestCase):
    def test_empty_query_returns_empty(self) -> None:
        from app.db import connection_context

        with connection_context() as conn:
            result = search_entries(conn, "")
            self.assertEqual(result, [])
            result = search_entries(conn, "   ")
            self.assertEqual(result, [])

    @patch("app.services.search.search_semantic_matches", return_value=[])
    def test_query_matching_entry_returns_results(self, _mock_sem) -> None:  # noqa: ANN001
        from app.db import connection_context

        with connection_context() as conn:
            self._insert_entry(
                conn,
                title="Kubernetes deployment",
                final_text="<p>Kubernetes deployment pipeline setup</p>",
            )
            results = search_entries(conn, "Kubernetes")
            self.assertGreaterEqual(len(results), 1)
            self.assertIn(
                "Kubernetes",
                results[0].entry.title + (results[0].snippet or ""),
            )

    @patch("app.services.search.search_semantic_matches", return_value=[])
    def test_group_id_filtering(self, _mock_sem) -> None:  # noqa: ANN001
        from app.db import connection_context

        with connection_context() as conn:
            self._insert_entry(
                conn,
                title="Alpha item",
                final_text="<p>Alpha unique content here</p>",
                group_id=1,
            )
            conn.execute(
                "INSERT INTO timeline_groups (name) VALUES (?)", ("Second",)
            )
            gid2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._insert_entry(
                conn,
                title="Beta item",
                final_text="<p>Beta unique content here</p>",
                group_id=gid2,
            )

            results_g1 = search_entries(conn, "unique content", group_id=1)
            result_titles = [r.entry.title for r in results_g1]
            self.assertIn("Alpha item", result_titles)
            self.assertNotIn("Beta item", result_titles)

    @patch("app.services.search.search_semantic_matches", return_value=[])
    def test_no_matches_returns_empty(self, _mock_sem) -> None:  # noqa: ANN001
        from app.db import connection_context

        with connection_context() as conn:
            self._insert_entry(conn, title="Hello", final_text="<p>Hello world</p>")
            results = search_entries(conn, "zzzyyyxxx")
            self.assertEqual(results, [])


class TestFilterTimelineEntries(_DBTestCase):
    def test_empty_query_returns_empty(self) -> None:
        from app.db import connection_context

        with connection_context() as conn:
            result = filter_timeline_entries(conn, "")
            self.assertEqual(result, [])

    @patch("app.services.search.search_semantic_matches", return_value=[])
    def test_exact_tag_match_returns_entries(self, _mock_sem) -> None:  # noqa: ANN001
        from app.db import connection_context

        with connection_context() as conn:
            eid = self._insert_entry(
                conn,
                title="Tagged entry",
                final_text="<p>Content for tagged entry</p>",
                tags=["release"],
            )
            entries = filter_timeline_entries(conn, "release")
            ids = [e.id for e in entries]
            self.assertIn(eid, ids)

    @patch("app.services.search.search_semantic_matches", return_value=[])
    def test_falls_back_to_fts_when_no_tag_match(self, _mock_sem) -> None:  # noqa: ANN001
        from app.db import connection_context

        with connection_context() as conn:
            self._insert_entry(
                conn,
                title="Interesting article",
                final_text="<p>Interesting article about architecture</p>",
            )
            entries = filter_timeline_entries(conn, "architecture")
            self.assertGreaterEqual(len(entries), 1)
            self.assertEqual(entries[0].title, "Interesting article")
