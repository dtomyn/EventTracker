from __future__ import annotations

import unittest

from app.models import Entry, SearchResult
from app.services.search import (
    build_fts_query,
    decode_search_cursor,
    encode_search_cursor,
    normalize_search_page_size,
    paginate_search_results,
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
