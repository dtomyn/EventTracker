from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.db import connection_context, init_db
from app.models import Entry, EntryLink
from app.services.poster_board import (
    OTHER_CATEGORY,
    UNTAGGED_CATEGORY,
    ConnectionEdge,
    build_poster_board_payload,
    filter_entries_by_year,
    list_board_years,
    normalize_board_limit,
)


def _entry(
    entry_id: int,
    *,
    sort_key: int = 20260115,
    title: str | None = None,
    group_id: int = 1,
    group_name: str = "Work",
    tags: list[str] | None = None,
    source_url: str | None = None,
    links: list[EntryLink] | None = None,
    final_text: str = "<p>Body</p>",
    preview_text: str = "Body",
) -> Entry:
    year, month, day = sort_key // 10000, (sort_key // 100) % 100, sort_key % 100
    return Entry(
        id=entry_id,
        event_year=year,
        event_month=month,
        event_day=day or None,
        sort_key=sort_key,
        group_id=group_id,
        group_name=group_name,
        title=f"Entry {entry_id}" if title is None else title,
        source_url=source_url,
        generated_text=None,
        final_text=final_text,
        created_utc="2026-01-01T00:00:00+00:00",
        updated_utc="2026-01-01T00:00:00+00:00",
        tags=tags or [],
        links=links or [],
        display_date=f"Jan {day}, {year}" if day else f"Jan {year}",
        preview_text=preview_text,
    )


def _link(link_id: int, url: str, note: str = "") -> EntryLink:
    return EntryLink(id=link_id, url=url, note=note, created_utc="2026-01-01T00:00:00+00:00")


class PosterBoardPayloadTests(unittest.TestCase):
    def test_empty_scope_returns_empty_board(self) -> None:
        payload = build_poster_board_payload([])

        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["categories"], [])
        self.assertEqual(payload["total_in_scope"], 0)

    def test_connected_entries_rank_above_isolated_ones(self) -> None:
        entries = [_entry(1), _entry(2), _entry(3)]
        edges = [
            ConnectionEdge(source_id=3, target_id=2, note=""),
            ConnectionEdge(source_id=3, target_id=1, note=""),
        ]

        payload = build_poster_board_payload(entries, edges=edges)

        self.assertEqual([item["id"] for item in payload["items"]][0], 3)
        self.assertEqual(payload["items"][0]["rank"], 1)
        self.assertEqual(payload["items"][0]["tier"], 1)
        self.assertEqual(payload["items"][0]["connection_count"], 2)

    def test_limit_keeps_top_entries_and_reports_scope_total(self) -> None:
        entries = [_entry(index, sort_key=20260100 + index) for index in range(1, 26)]

        payload = build_poster_board_payload(entries, limit=20)

        self.assertEqual(len(payload["items"]), 20)
        self.assertEqual(payload["total_in_scope"], 25)
        # recency breaks the tie between otherwise identical entries
        self.assertEqual(payload["items"][0]["id"], 25)

    def test_tiers_follow_reference_mix(self) -> None:
        entries = [_entry(index) for index in range(1, 31)]

        tiers = [item["tier"] for item in build_poster_board_payload(entries)["items"]]

        self.assertEqual(tiers.count(1), 3)
        self.assertEqual(tiers.count(2), 9)
        self.assertEqual(tiers.count(3), 15)
        self.assertEqual(tiers.count(4), 3)
        self.assertEqual(tiers, sorted(tiers))

    def test_single_group_categories_come_from_most_common_tag(self) -> None:
        entries = [
            _entry(1, tags=["policy", "eu"]),
            _entry(2, tags=["policy"]),
            _entry(3, tags=["eu", "policy"]),
            _entry(4),
        ]

        payload = build_poster_board_payload(entries)
        by_id = {item["id"]: item for item in payload["items"]}

        self.assertEqual(payload["category_basis"], "tag")
        self.assertEqual(by_id[1]["category"], "policy")
        self.assertEqual(by_id[3]["category"], "policy")
        self.assertEqual(by_id[4]["category"], UNTAGGED_CATEGORY)
        self.assertEqual(by_id[4]["hue"], -1)
        self.assertEqual(payload["categories"][0], {"name": "policy", "hue": 0, "count": 3})

    def test_multiple_groups_categorize_by_group(self) -> None:
        entries = [
            _entry(1, group_id=1, group_name="Work"),
            _entry(2, group_id=2, group_name="Home"),
        ]

        payload = build_poster_board_payload(entries)

        self.assertEqual(payload["category_basis"], "group")
        self.assertEqual({item["category"] for item in payload["items"]}, {"Work", "Home"})

    def test_rare_categories_fold_into_other(self) -> None:
        entries = [_entry(index, tags=[f"tag-{index}"]) for index in range(1, 13)]

        payload = build_poster_board_payload(entries)
        names = [category["name"] for category in payload["categories"]]

        self.assertEqual(len(names), 9)
        self.assertEqual(names[-1], OTHER_CATEGORY)
        self.assertEqual(payload["categories"][-1]["hue"], -1)
        self.assertEqual(payload["categories"][-1]["count"], 4)

    def test_sources_keep_only_http_urls_and_deduplicate(self) -> None:
        entry = _entry(
            1,
            source_url="https://www.example.com/story",
            links=[
                _link(1, "https://www.example.com/story", "dupe"),
                _link(2, "javascript:alert(1)", "bad"),
                _link(3, "https://news.example.org/a", "Follow-up"),
                _link(4, "https://other.example.net/b"),
            ],
        )

        sources = build_poster_board_payload([entry])["items"][0]["sources"]

        self.assertEqual(
            sources,
            [
                {"name": "example.com", "url": "https://www.example.com/story"},
                {"name": "Follow-up", "url": "https://news.example.org/a"},
                {"name": "other.example.net", "url": "https://other.example.net/b"},
            ],
        )

    def test_body_html_is_sanitized(self) -> None:
        entry = _entry(
            1,
            final_text='<p onclick="x()">Safe</p><script>alert(1)</script><img src=x onerror=y>',
        )

        body_html = build_poster_board_payload([entry])["items"][0]["body_html"]

        self.assertEqual(body_html, "<p>Safe</p>")

    def test_headline_falls_back_to_preview_text(self) -> None:
        long_preview = "word " * 40
        entry = _entry(1, title="  ", final_text=f"<p>{long_preview.strip()}</p>")

        headline = build_poster_board_payload([entry])["items"][0]["headline"]

        self.assertTrue(headline.endswith("…"))
        self.assertLessEqual(len(headline), 90)

    def test_connections_list_board_posters_first(self) -> None:
        entries = [_entry(1), _entry(2, title="Zulu"), _entry(3, title="Alpha")]
        edges = [
            ConnectionEdge(source_id=1, target_id=2, note="caused"),
            ConnectionEdge(source_id=3, target_id=1, note=""),
        ]

        payload = build_poster_board_payload(entries, edges=edges, limit=2)
        top = payload["items"][0]

        self.assertEqual(top["id"], 1)
        on_board = {item["id"] for item in payload["items"]}
        connection_ids = [connection["id"] for connection in top["connections"]]
        self.assertEqual(len(connection_ids), 2)
        self.assertIn(connection_ids[0], on_board)
        self.assertNotIn(connection_ids[1], on_board)

    def test_bidirectional_connection_counts_once(self) -> None:
        entries = [_entry(1), _entry(2)]
        edges = [
            ConnectionEdge(source_id=1, target_id=2, note="caused"),
            ConnectionEdge(source_id=2, target_id=1, note="followed"),
        ]

        items = build_poster_board_payload(entries, edges=edges)["items"]

        for item in items:
            self.assertEqual(item["connection_count"], 1)
            self.assertEqual(len(item["connections"]), 1)

    def test_date_range_spans_selected_entries(self) -> None:
        entries = [_entry(1, sort_key=20260105), _entry(2, sort_key=20260120)]

        self.assertEqual(
            build_poster_board_payload(entries)["date_range"],
            "Jan 5, 2026 – Jan 20, 2026",
        )


class PosterBoardHelperTests(unittest.TestCase):
    def test_normalize_board_limit(self) -> None:
        self.assertEqual(normalize_board_limit(None), 30)
        self.assertEqual(normalize_board_limit(""), 30)
        self.assertEqual(normalize_board_limit("45"), 45)
        self.assertEqual(normalize_board_limit("1000"), 60)
        self.assertEqual(normalize_board_limit("7"), 20)
        self.assertEqual(normalize_board_limit("abc"), 30)

    def test_year_helpers(self) -> None:
        entries = [_entry(1, sort_key=20240101), _entry(2, sort_key=20260101)]

        self.assertEqual(list_board_years(entries), [2026, 2024])
        self.assertEqual([entry.id for entry in filter_entries_by_year(entries, 2024)], [1])
        self.assertEqual(len(filter_entries_by_year(entries, None)), 2)


class PosterBoardRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EVENTTRACKER_DB_PATH"] = os.path.join(self.tmp.name, "test.db")
        os.environ["TESTING"] = "1"
        init_db()
        with connection_context() as conn:
            conn.execute("UPDATE timeline_groups SET is_default = 0")
            conn.execute(
                "INSERT INTO timeline_groups (id, name, is_default) VALUES (50, 'Board', 1)"
            )
            rows = [
                (2025, 3, 15, 20250315, "Older </script><script>alert(1)</script>"),
                (2026, 6, 1, 20260601, "Newest event"),
            ]
            for year, month, day, sort_key, title in rows:
                conn.execute(
                    "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                    "VALUES (?, ?, ?, ?, 50, ?, '<p>Text</p>', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                    (year, month, day, sort_key, title),
                )

        from app.main import app

        self.client = TestClient(app)

    def tearDown(self) -> None:
        for key in ("EVENTTRACKER_DB_PATH", "TESTING"):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def _payload(self, html: str) -> dict[str, object]:
        match = re.search(
            r'<script id="board-data" type="application/json">(.*?)</script>', html, re.S
        )
        assert match is not None
        return json.loads(match.group(1))

    def test_board_page_renders_scope_and_live_links(self) -> None:
        response = self.client.get("/timeline/board?group_id=50")

        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>Board | Poster Board</title>", response.text)
        self.assertIn('data-entry-url="/entries/{id}/view"', response.text)
        self.assertIn('href="/timeline/board/export?group_id=50&amp;limit=30"', response.text)
        self.assertIn('href="/?group_id=50"', response.text)
        payload = self._payload(response.text)
        self.assertEqual(payload["total_in_scope"], 2)

    def test_board_data_cannot_break_out_of_its_script_tag(self) -> None:
        response = self.client.get("/timeline/board?group_id=50")

        self.assertNotIn("</script><script>alert(1)", response.text)
        titles = [item["headline"] for item in self._payload(response.text)["items"]]  # type: ignore[index]
        self.assertIn("Older </script><script>alert(1)</script>", titles)

    def test_board_year_filter(self) -> None:
        response = self.client.get("/timeline/board?group_id=50&year=2026")

        payload = self._payload(response.text)
        self.assertEqual(payload["total_in_scope"], 1)
        self.assertIn("Board · 2026", response.text)

    def test_board_rejects_invalid_year(self) -> None:
        self.assertEqual(self.client.get("/timeline/board?year=abc").status_code, 422)
        self.assertEqual(self.client.get("/timeline/board?year=0").status_code, 422)

    def test_board_unknown_group_is_404(self) -> None:
        self.assertEqual(self.client.get("/timeline/board?group_id=999").status_code, 404)

    def test_export_is_self_contained_download(self) -> None:
        response = self.client.get("/timeline/board/export?group_id=50&year=2026")

        self.assertEqual(response.status_code, 200)
        disposition = response.headers["content-disposition"]
        self.assertRegex(
            disposition,
            r'^attachment; filename="EventTracker-board-board-2026-\d{4}-\d{2}-\d{2}\.html"$',
        )
        html = response.text
        self.assertNotIn("data-entry-url=", html)
        self.assertNotIn("Export HTML", html)
        self.assertNotIn("<link", html)
        self.assertNotIn("<script src", html)
        self.assertNotIn("csrf", html.lower())
        self.assertIn("exported", html)
        self.assertIn(".poster{", html)
        self.assertIn('getElementById("board-data")', html)

    def test_timeline_links_to_board_with_scope(self) -> None:
        response = self.client.get("/?group_id=50")

        self.assertIn('href="/timeline/board?group_id=50"', response.text)


if __name__ == "__main__":
    unittest.main()
