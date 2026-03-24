from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db import connection_context
from app.main import app
from app.services.ai_generate import (
    DraftGenerationError,
    GeneratedEntrySuggestion,
    get_draft_generator,
)
from app.services.embeddings import SemanticMatch
from app.services.embeddings import load_embedding_settings
from app.services.group_web_search import GroupWebSearchItem, GroupWebSearchResponse


ENV_KEYS = (
    "EVENTTRACKER_DB_PATH",
    "EVENTTRACKER_AI_PROVIDER",
    "COPILOT_CHAT_MODEL_ID",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_CHAT_MODEL_ID",
    "OPENAI_EMBEDDING_MODEL_ID",
)


class TestAppSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_env = {key: os.environ.get(key) for key in ENV_KEYS}

        for key in ENV_KEYS:
            os.environ.pop(key, None)

        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "EventTracker-test.db"
        )
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

    def test_timeline_loads_with_empty_database(self) -> None:
        with TestClient(app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Timeline", response.text)
        self.assertIn("Agentic Coding", response.text)
        self.assertIn("No entries yet", response.text)
        self.assertNotIn('href="/visualization"', response.text)
        self.assertIn('href="/entries/export"', response.text)
        self.assertIn(">Export</a>", response.text)
        self.assertNotIn('id="export-json-button"', response.text)

    def test_visualization_loads_with_empty_database(self) -> None:
        with TestClient(app) as client:
            response = client.get("/visualization", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/")

    def test_create_edit_and_search_flow(self) -> None:
        with TestClient(app) as client:
            create_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "16",
                    "group_id": "1",
                    "title": "Smoke suite release",
                    "source_url": "",
                    "link_url": [
                        "https://example.com/reference",
                        "https://example.com/postmortem",
                    ],
                    "link_note": ["Release checklist", "Retrospective notes"],
                    "generated_text": "",
                    "final_text": "<p>Shipped the <b>first</b> smoke test coverage for EventTracker.</p><ul><li>Covered startup</li></ul>",
                    "tags": "release, testing, Release",
                },
                follow_redirects=False,
            )
            self.assertEqual(create_response.status_code, 303)

            entry_id = self._first_entry_id()
            self.assertEqual(
                create_response.headers["location"], f"/entries/{entry_id}/view"
            )

            timeline_response = client.get("/")
            self.assertEqual(timeline_response.status_code, 200)
            self.assertIn("Smoke suite release", timeline_response.text)
            self.assertIn("<b>first</b>", timeline_response.text)
            self.assertIn("<ul><li>Covered startup</li></ul>", timeline_response.text)
            self.assertIn("Agentic Coding", timeline_response.text)

            self.assertIn("Details", timeline_response.text)
            self.assertIn("Summaries", timeline_response.text)
            self.assertIn(f'href="/entries/{entry_id}/view"', timeline_response.text)
            self.assertIn(f'href="/entries/{entry_id}"', timeline_response.text)
            self.assertIn("data-view-history", timeline_response.text)
            self.assertIn(f'data-focus-key="entry-{entry_id}"', timeline_response.text)
            self.assertIn("data-open-month-view", timeline_response.text)
            self.assertIn('data-target-year="2026"', timeline_response.text)
            self.assertIn('data-target-month="3"', timeline_response.text)
            self.assertIn('data-zoom-target="months"', timeline_response.text)
            self.assertIn('data-zoom-target="years"', timeline_response.text)
            self.assertIn('data-playback-action="play"', timeline_response.text)
            self.assertIn('data-playback-action="pause"', timeline_response.text)
            self.assertIn('data-playback-action="restart"', timeline_response.text)
            self.assertIn("data-playback-status", timeline_response.text)
            self.assertIn("data-playback-panel", timeline_response.text)
            self.assertIn('aria-label="Play summaries replay"', timeline_response.text)
            self.assertIn("hidden", timeline_response.text)
            self.assertNotIn('data-playback-mode="year"', timeline_response.text)
            self.assertNotIn("Replay summaries oldest first.", timeline_response.text)
            self.assertNotIn(
                "Replay the timeline oldest first. Busy months tighten the pacing so bursts of activity feel faster.",
                timeline_response.text,
            )
            self.assertNotIn(
                "Switch between detailed entries and summary views. Click a year card to drill into its months, then click a month card to open its event summaries.",
                timeline_response.text,
            )
            self.assertIn('id="timeline-state"', timeline_response.text)
            self.assertIn("data-detail-sentinel", timeline_response.text)
            self.assertIn("/timeline/details", timeline_response.text)
            self.assertNotIn(
                "Use the mouse wheel over the visualization to switch between events, months, and years.",
                timeline_response.text,
            )

            edit_response = client.post(
                f"/entries/{entry_id}",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "17",
                    "group_id": "1",
                    "title": "Smoke suite verification",
                    "source_url": "https://example.com/update",
                    "link_url": ["https://example.com/update-details"],
                    "link_note": ["Release update details"],
                    "generated_text": "Draft text",
                    "final_text": "<p>Updated the smoke suite and <i>verified search</i> still works.</p>",
                    "tags": "testing, verification",
                },
                follow_redirects=False,
            )
            self.assertEqual(edit_response.status_code, 303)
            self.assertEqual(
                edit_response.headers["location"], f"/entries/{entry_id}/view"
            )

            view_response = client.get(f"/entries/{entry_id}/view")
            self.assertEqual(view_response.status_code, 200)
            self.assertIn("Additional Links", view_response.text)
            self.assertIn("Release update details", view_response.text)
            self.assertIn("https://example.com/update-details", view_response.text)

            search_response = client.get("/", params={"q": "verified search"})
            self.assertEqual(search_response.status_code, 200)
            self.assertIn("Filtered Timeline", search_response.text)
            self.assertIn("Smoke suite verification", search_response.text)
            self.assertIn("verified search", search_response.text)
            self.assertNotIn("Search Results", search_response.text)

        with connection_context() as connection:
            tags = connection.execute(
                """
                SELECT t.name
                FROM tags t
                JOIN entry_tags et ON et.tag_id = t.id
                WHERE et.entry_id = ?
                ORDER BY t.name ASC
                """,
                (entry_id,),
            ).fetchall()

        self.assertEqual([row["name"] for row in tags], ["testing", "verification"])

        with connection_context() as connection:
            links = connection.execute(
                "SELECT url, note FROM entry_links WHERE entry_id = ? ORDER BY id ASC",
                (entry_id,),
            ).fetchall()

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["url"], "https://example.com/update-details")
        self.assertEqual(links[0]["note"], "Release update details")

    def test_create_entry_rejects_duplicate_source_url_within_group(self) -> None:
        with TestClient(app) as client:
            first_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "16",
                    "group_id": "1",
                    "title": "Original entry",
                    "source_url": "https://example.com/duplicate",
                    "generated_text": "",
                    "final_text": "<p>Original entry content.</p>",
                    "tags": "",
                },
                follow_redirects=False,
            )
            self.assertEqual(first_response.status_code, 303)

            duplicate_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "17",
                    "group_id": "1",
                    "title": "Duplicate entry",
                    "source_url": "https://example.com/duplicate",
                    "generated_text": "",
                    "final_text": "<p>Duplicate entry content.</p>",
                    "tags": "",
                },
                follow_redirects=False,
            )

        self.assertEqual(duplicate_response.status_code, 400)
        self.assertIn(
            "This timeline group already has an entry with the same source URL.",
            duplicate_response.text,
        )
        self.assertIn('id="source_url"', duplicate_response.text)
        self.assertIn("is-invalid", duplicate_response.text)

        with connection_context() as connection:
            entries = connection.execute(
                "SELECT COUNT(*) AS count FROM entries WHERE group_id = ? AND source_url = ?",
                (1, "https://example.com/duplicate"),
            ).fetchone()

        self.assertIsNotNone(entries)
        self.assertEqual(entries["count"], 1)

    def test_edit_entry_rejects_duplicate_source_url_within_group(self) -> None:
        with TestClient(app) as client:
            first_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "16",
                    "group_id": "1",
                    "title": "First entry",
                    "source_url": "https://example.com/original",
                    "generated_text": "",
                    "final_text": "<p>First entry content.</p>",
                    "tags": "",
                },
                follow_redirects=False,
            )
            self.assertEqual(first_response.status_code, 303)

            first_entry_id = self._first_entry_id()

            second_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "17",
                    "group_id": "1",
                    "title": "Second entry",
                    "source_url": "https://example.com/second",
                    "generated_text": "",
                    "final_text": "<p>Second entry content.</p>",
                    "tags": "",
                },
                follow_redirects=False,
            )
            self.assertEqual(second_response.status_code, 303)

            edit_response = client.post(
                f"/entries/{first_entry_id}",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "16",
                    "group_id": "1",
                    "title": "First entry",
                    "source_url": "https://example.com/second",
                    "generated_text": "",
                    "final_text": "<p>First entry content.</p>",
                    "tags": "",
                },
                follow_redirects=False,
            )

        self.assertEqual(edit_response.status_code, 400)
        self.assertIn(
            "This timeline group already has an entry with the same source URL.",
            edit_response.text,
        )

        with connection_context() as connection:
            first_entry = connection.execute(
                "SELECT source_url FROM entries WHERE id = ?",
                (first_entry_id,),
            ).fetchone()

        self.assertIsNotNone(first_entry)
        self.assertEqual(first_entry["source_url"], "https://example.com/original")

    def test_timeline_filter_supports_english_query_via_semantic_match(self) -> None:
        with TestClient(app) as client:
            first_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "15",
                    "group_id": "1",
                    "title": "VS Code extension work",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Built tooling and workflows around Visual Studio Code extensions.</p>",
                    "tags": "editor, tooling",
                },
                follow_redirects=False,
            )
            self.assertEqual(first_response.status_code, 303)

            first_entry_id = self._first_entry_id()

            second_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "16",
                    "group_id": "1",
                    "title": "Non matching event",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Worked on backyard landscaping plans.</p>",
                    "tags": "personal",
                },
                follow_redirects=False,
            )
            self.assertEqual(second_response.status_code, 303)

            with patch(
                "app.services.search.search_semantic_matches",
                return_value=[SemanticMatch(entry_id=first_entry_id, distance=0.05)],
            ):
                response = client.get("/", params={"q": "VS Code related"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Filtered Timeline", response.text)
        self.assertIn("VS Code related", response.text)
        self.assertIn("VS Code extension work", response.text)
        self.assertNotIn("Non matching event", response.text)
        self.assertIn("Showing 1 matching entry", response.text)

    def test_timeline_filter_prefers_exact_tag_match_over_semantic_breadth(self) -> None:
        with TestClient(app) as client:
            first_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "20",
                    "group_id": "1",
                    "title": "Tagged release entry",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Release notes and launch checklist.</p>",
                    "tags": "release focus, launch",
                },
                follow_redirects=False,
            )
            self.assertEqual(first_response.status_code, 303)

            second_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "21",
                    "group_id": "1",
                    "title": "Unrelated entry",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>This should not appear for the exact tag query.</p>",
                    "tags": "operations",
                },
                follow_redirects=False,
            )
            self.assertEqual(second_response.status_code, 303)

            with connection_context() as connection:
                rows = connection.execute(
                    "SELECT id FROM entries ORDER BY id ASC"
                ).fetchall()

            self.assertEqual(len(rows), 2)
            first_entry_id = int(rows[0]["id"])
            second_entry_id = int(rows[1]["id"])

            with patch(
                "app.services.search.search_semantic_matches",
                return_value=[
                    SemanticMatch(entry_id=first_entry_id, distance=0.02),
                    SemanticMatch(entry_id=second_entry_id, distance=0.03),
                ],
            ):
                response = client.get("/", params={"q": "release focus"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Filtered Timeline", response.text)
        self.assertIn("release focus", response.text)
        self.assertIn("Tagged release entry", response.text)
        self.assertNotIn("Unrelated entry", response.text)
        self.assertIn("Showing 1 matching entry", response.text)

    def test_ranked_search_route_stays_distinct_from_timeline_filter(self) -> None:
        with TestClient(app) as client:
            create_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "18",
                    "group_id": "1",
                    "title": "Search-focused entry",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Investigated semantic search and ranked search results for developer tools.</p>",
                    "tags": "search, tools",
                },
                follow_redirects=False,
            )
            self.assertEqual(create_response.status_code, 303)

            response = client.get("/search", params={"q": "semantic search"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Search Results", response.text)
        self.assertIn("Search-focused entry", response.text)
        self.assertIn('href="/story?group_id=1&q=semantic%20search"', response.text)
        self.assertIn("Clear search", response.text)
        self.assertIn("Filter Timeline", response.text)
        self.assertNotIn("Filtered Timeline", response.text)
        self.assertIn('id="search-state"', response.text)
        self.assertIn("/search/results", response.text)

    def test_ranked_search_can_be_scoped_to_selected_group(self) -> None:
        with TestClient(app) as client:
            create_group_response = client.post(
                "/admin/groups",
                data={"name": "Product Launches"},
                follow_redirects=False,
            )
            self.assertEqual(create_group_response.status_code, 303)

            with connection_context() as connection:
                row = connection.execute(
                    "SELECT id FROM timeline_groups WHERE name = ?",
                    ("Product Launches",),
                ).fetchone()

            self.assertIsNotNone(row)
            product_group_id = int(row["id"])

            first_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "20",
                    "group_id": "1",
                    "title": "Agentic search note",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Semantic search milestone for agentic coding.</p>",
                    "tags": "search",
                },
                follow_redirects=False,
            )
            self.assertEqual(first_response.status_code, 303)

            second_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "21",
                    "group_id": str(product_group_id),
                    "title": "Product launch search note",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Semantic search milestone for product launches.</p>",
                    "tags": "search",
                },
                follow_redirects=False,
            )
            self.assertEqual(second_response.status_code, 303)

            response = client.get(
                "/search",
                params={"q": "semantic search", "group_id": str(product_group_id)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Product Launches", response.text)
        self.assertIn("Product launch search note", response.text)
        self.assertNotIn("Agentic search note", response.text)
        self.assertIn("Clear search", response.text)
        self.assertIn(f'href="/search?group_id={product_group_id}"', response.text)

    def test_visualization_keeps_newest_entries_first(self) -> None:
        with TestClient(app) as client:
            first_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2025",
                    "event_month": "1",
                    "event_day": "5",
                    "group_id": "1",
                    "title": "Older milestone",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Older body text.</p>",
                    "tags": "",
                },
                follow_redirects=False,
            )
            self.assertEqual(first_response.status_code, 303)

            second_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "17",
                    "group_id": "1",
                    "title": "Newer milestone",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Newer body text.</p>",
                    "tags": "",
                },
                follow_redirects=False,
            )
            self.assertEqual(second_response.status_code, 303)

            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertLess(
            response.text.index("Newer milestone"),
            response.text.index("Older milestone"),
        )
        self.assertIn("Newer body text.", response.text)
        self.assertIn("Older body text.", response.text)

    def test_timeline_initial_render_uses_first_page_only_and_detail_endpoint_continues(
        self,
    ) -> None:
        with TestClient(app) as client:
            for day in range(1, 29):
                create_response = client.post(
                    "/entries/new",
                    data={
                        "event_year": "2026",
                        "event_month": "3",
                        "event_day": str(day),
                        "group_id": "1",
                        "title": f"Paged milestone {day:02d}",
                        "source_url": "",
                        "generated_text": "",
                        "final_text": f"<p>Paged body {day:02d}</p>",
                        "tags": "",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(create_response.status_code, 303)

            response = client.get("/")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Paged milestone 28", response.text)
            self.assertIn("Paged milestone 04", response.text)
            self.assertNotIn("Paged milestone 03", response.text)
            self.assertNotIn("Paged milestone 01", response.text)

            state_start = response.text.index('id="timeline-state"')
            json_start = response.text.index(">", state_start) + 1
            json_end = response.text.index("</script>", json_start)
            timeline_state = json.loads(response.text[json_start:json_end])

            self.assertTrue(timeline_state["hasMore"])
            self.assertIsNotNone(timeline_state["nextCursor"])

            detail_response = client.get(
                "/timeline/details",
                params={"cursor": timeline_state["nextCursor"]},
            )

        self.assertEqual(detail_response.status_code, 200)
        payload = detail_response.json()
        self.assertFalse(payload["has_more"])
        self.assertIsNone(payload["next_cursor"])
        self.assertEqual(payload["loaded_count"], 3)
        self.assertIn("Paged milestone 03", payload["items_html"])
        self.assertIn("Paged milestone 01", payload["items_html"])
        self.assertNotIn("Paged milestone 28", payload["items_html"])

    def test_timeline_details_rejects_invalid_cursor(self) -> None:
        with TestClient(app) as client:
            response = client.get(
                "/timeline/details", params={"cursor": "not-a-cursor"}
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid timeline cursor")

    def test_timeline_year_month_and_summary_endpoints_return_accurate_scope_counts(
        self,
    ) -> None:
        with TestClient(app) as client:
            self._create_entry(
                client,
                year=2025,
                month=1,
                day=4,
                title="January planning",
            )
            self._create_entry(
                client,
                year=2025,
                month=1,
                day=9,
                title="January launch",
            )
            self._create_entry(
                client,
                year=2025,
                month=2,
                day=3,
                title="February follow-up",
            )
            self._create_entry(
                client,
                year=2026,
                month=3,
                day=2,
                title="March kickoff",
            )
            self._create_entry(
                client,
                year=2026,
                month=3,
                day=8,
                title="March recap",
            )

            years_response = client.get("/timeline/years")
            months_response = client.get("/timeline/months", params={"year": 2025})
            summaries_response = client.get(
                "/timeline/summaries",
                params={"year": 2025, "month": 1},
            )

        self.assertEqual(years_response.status_code, 200)
        years_payload = years_response.json()
        self.assertEqual(years_payload["total_entries"], 5)
        self.assertEqual(years_payload["bucket_count"], 2)
        self.assertIn("2026", years_payload["items_html"])
        self.assertIn(">2</div>", years_payload["items_html"])
        self.assertIn(">3</div>", years_payload["items_html"])

        self.assertEqual(months_response.status_code, 200)
        months_payload = months_response.json()
        self.assertEqual(months_payload["bucket_count"], 2)
        self.assertIn("January 2025", months_payload["items_html"])
        self.assertIn("February 2025", months_payload["items_html"])

        self.assertEqual(summaries_response.status_code, 200)
        summaries_payload = summaries_response.json()
        self.assertIn("January planning", summaries_payload["items_html"])
        self.assertIn("January launch", summaries_payload["items_html"])
        self.assertNotIn("February follow-up", summaries_payload["items_html"])

    def test_search_initial_render_uses_first_page_only_and_results_endpoint_continues(
        self,
    ) -> None:
        with TestClient(app) as client:
            for day in range(1, 29):
                create_response = client.post(
                    "/entries/new",
                    data={
                        "event_year": "2026",
                        "event_month": "4",
                        "event_day": str(day),
                        "group_id": "1",
                        "title": f"Search page item {day:02d}",
                        "source_url": "",
                        "generated_text": "",
                        "final_text": f"<p>Ranked pagination keyword {day:02d}</p>",
                        "tags": "",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(create_response.status_code, 303)

            response = client.get("/search", params={"q": "ranked pagination keyword"})

            self.assertEqual(response.status_code, 200)
            self.assertIn("Search page item 28", response.text)
            self.assertIn("Search page item 09", response.text)
            self.assertNotIn("Search page item 08", response.text)
            self.assertNotIn("Search page item 01", response.text)

            state_start = response.text.index('id="search-state"')
            json_start = response.text.index(">", state_start) + 1
            json_end = response.text.index("</script>", json_start)
            search_state = json.loads(response.text[json_start:json_end])

            self.assertTrue(search_state["hasMore"])
            self.assertIsNotNone(search_state["nextCursor"])

            results_response = client.get(
                "/search/results",
                params={
                    "q": "ranked pagination keyword",
                    "cursor": search_state["nextCursor"],
                },
            )

        self.assertEqual(results_response.status_code, 200)
        payload = results_response.json()
        self.assertFalse(payload["has_more"])
        self.assertIsNone(payload["next_cursor"])
        self.assertEqual(payload["loaded_count"], 8)
        self.assertIn("Search page item 08", payload["items_html"])
        self.assertIn("Search page item 01", payload["items_html"])
        self.assertNotIn("Search page item 28", payload["items_html"])

    def test_search_results_reject_invalid_cursor(self) -> None:
        with TestClient(app) as client:
            response = client.get(
                "/search/results",
                params={"q": "semantic search", "cursor": "not-a-cursor"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid search cursor")

    def test_invalid_submission_preserves_form_errors(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "",
                    "group_id": "",
                    "title": "",
                    "source_url": "not-a-url",
                    "link_url": ["https://example.com/missing-note"],
                    "link_note": [""],
                    "generated_text": "",
                    "final_text": "",
                    "tags": "",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("This field is required.", response.text)
        self.assertIn("Title is required.", response.text)
        self.assertIn("Event summary is required.", response.text)
        self.assertIn("Provide a valid http or https URL.", response.text)
        self.assertIn("Add a brief note for this URL.", response.text)

    def test_invalid_calendar_dates_are_rejected(self) -> None:
        invalid_dates = (("2025", "2", "29"), ("2026", "4", "31"))

        with TestClient(app) as client:
            for year, month, day in invalid_dates:
                response = client.post(
                    "/entries/new",
                    data={
                        "event_year": year,
                        "event_month": month,
                        "event_day": day,
                        "group_id": "1",
                        "title": "Invalid date",
                        "source_url": "",
                        "generated_text": "",
                        "final_text": "<p>Should fail.</p>",
                        "tags": "",
                    },
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("Provide a valid calendar date.", response.text)

        with connection_context() as connection:
            count = connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

        self.assertEqual(count, 0)

    def test_leap_day_is_accepted_for_real_calendar_dates(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/entries/new",
                data={
                    "event_year": "2024",
                    "event_month": "2",
                    "event_day": "29",
                    "group_id": "1",
                    "title": "Leap day entry",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Saved on a leap day.</p>",
                    "tags": "calendar",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)

        with connection_context() as connection:
            row = connection.execute(
                "SELECT event_year, event_month, event_day FROM entries"
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(
            (row["event_year"], row["event_month"], row["event_day"]), (2024, 2, 29)
        )

    def test_group_admin_can_create_and_rename_groups(self) -> None:
        with TestClient(app) as client:
            create_response = client.post(
                "/admin/groups",
                data={
                    "name": "Product Launches",
                    "web_search_query": " latest AI launches and benchmark results ",
                },
                follow_redirects=False,
            )
            self.assertEqual(create_response.status_code, 303)

            admin_response = client.get("/admin/groups")
            self.assertEqual(admin_response.status_code, 200)
            self.assertIn("Product Launches", admin_response.text)
            self.assertIn(
                "latest AI launches and benchmark results", admin_response.text
            )

            with connection_context() as connection:
                row = connection.execute(
                    "SELECT id, web_search_query FROM timeline_groups WHERE name = ?",
                    ("Product Launches",),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(
                row["web_search_query"],
                "latest AI launches and benchmark results",
            )

            rename_response = client.post(
                f"/admin/groups/{int(row['id'])}",
                data={
                    "name": "Product Milestones",
                    "web_search_query": "product launch keynotes and release posts",
                },
                follow_redirects=False,
            )
            self.assertEqual(rename_response.status_code, 303)

            updated_response = client.get("/admin/groups")
            self.assertEqual(updated_response.status_code, 200)
            self.assertIn("Product Milestones", updated_response.text)
            self.assertNotIn("Product Launches", updated_response.text)
            self.assertIn(
                "product launch keynotes and release posts", updated_response.text
            )

            delete_response = client.post(
                f"/admin/groups/{int(row['id'])}/delete",
                follow_redirects=False,
            )
            self.assertEqual(delete_response.status_code, 303)

            deleted_response = client.get("/admin/groups")
            self.assertEqual(deleted_response.status_code, 200)
            self.assertNotIn("Product Milestones", deleted_response.text)

    def test_group_admin_rejects_overlong_web_search_query(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/admin/groups",
                data={
                    "name": "Product Launches",
                    "web_search_query": "x" * 401,
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Web search query must be 400 characters or fewer.", response.text
        )
        self.assertIn("Product Launches", response.text)

    def test_timeline_group_web_search_endpoint_handles_disabled_and_success_states(
        self,
    ) -> None:
        with TestClient(app) as client:
            create_response = client.post(
                "/admin/groups",
                data={
                    "name": "Product Launches",
                    "web_search_query": "AI developer tools launches and benchmarks",
                },
                follow_redirects=False,
            )
            self.assertEqual(create_response.status_code, 303)

            with connection_context() as connection:
                row = connection.execute(
                    "SELECT id FROM timeline_groups WHERE name = ?",
                    ("Product Launches",),
                ).fetchone()

            self.assertIsNotNone(row)
            group_id = int(row["id"])

            timeline_response = client.get("/", params={"group_id": str(group_id)})
            self.assertEqual(timeline_response.status_code, 200)
            self.assertIn("Recent Developments", timeline_response.text)
            self.assertNotIn(
                "AI developer tools launches and benchmarks", timeline_response.text
            )

            disabled_response = client.get(
                "/timeline/group-web-search",
                params={"group_id": str(group_id)},
            )

        self.assertEqual(disabled_response.status_code, 200)
        self.assertEqual(
            disabled_response.json(),
            {
                "enabled": False,
                "query": "AI developer tools launches and benchmarks",
                "items": [],
                "message": "Available when GitHub Copilot is the active AI provider.",
            },
        )

        with patch.dict(
            os.environ, {"EVENTTRACKER_AI_PROVIDER": "copilot"}, clear=False
        ):
            with patch(
                "app.main.search_group_web",
                return_value=GroupWebSearchResponse(
                    query="AI developer tools launches and benchmarks",
                    items=[
                        GroupWebSearchItem(
                            title="New benchmark roundup",
                            url="https://example.com/benchmarks",
                            snippet="Recent benchmark updates for AI developer tools.",
                            source="Example News",
                            article_date="2026-03-17",
                        )
                    ],
                ),
            ):
                with TestClient(app) as client:
                    success_response = client.get(
                        "/timeline/group-web-search",
                        params={"group_id": str(group_id)},
                    )

        self.assertEqual(success_response.status_code, 200)
        self.assertEqual(
            success_response.json(),
            {
                "enabled": True,
                "query": "AI developer tools launches and benchmarks",
                "items": [
                    {
                        "title": "New benchmark roundup",
                        "url": "https://example.com/benchmarks",
                        "snippet": "Recent benchmark updates for AI developer tools.",
                        "source": "Example News",
                        "article_date": "2026-03-17",
                    }
                ],
                "message": None,
            },
        )

    def test_timeline_group_web_search_refresh_endpoint_uses_force_refresh(
        self,
    ) -> None:
        with patch.dict(
            os.environ, {"EVENTTRACKER_AI_PROVIDER": "copilot"}, clear=False
        ):
            with TestClient(app) as client:
                create_response = client.post(
                    "/admin/groups",
                    data={
                        "name": "Product Launches",
                        "web_search_query": "AI developer tools launches and benchmarks",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(create_response.status_code, 303)

                with connection_context() as connection:
                    row = connection.execute(
                        "SELECT id FROM timeline_groups WHERE name = ?",
                        ("Product Launches",),
                    ).fetchone()

                self.assertIsNotNone(row)
                group_id = int(row["id"])

                with patch(
                    "app.main.search_group_web",
                    return_value=GroupWebSearchResponse(
                        query="AI developer tools launches and benchmarks",
                        items=[],
                    ),
                ) as search_group_web_mock:
                    response = client.post(
                        "/timeline/group-web-search/refresh",
                        params={"group_id": str(group_id)},
                    )

        self.assertEqual(response.status_code, 200)
        search_group_web_mock.assert_called_once()
        call_args, call_kwargs = search_group_web_mock.call_args
        self.assertEqual(call_args, ("AI developer tools launches and benchmarks",))
        self.assertTrue(call_kwargs.get("force_refresh"))
        self.assertEqual(call_kwargs.get("existing_urls"), set())

    def test_timeline_group_web_search_stream_endpoint_emits_progress_and_result(
        self,
    ) -> None:
        async def fake_search_group_web(
            query: str,
            *,
            force_refresh: bool = False,
            existing_urls: set[str] | None = None,
            event_sink=None,
        ) -> GroupWebSearchResponse:
            self.assertEqual(query, "AI developer tools launches and benchmarks")
            self.assertFalse(force_refresh)
            self.assertEqual(existing_urls, set())
            if event_sink is not None:
                event_sink(
                    {
                        "kind": "status",
                        "phase": "initial",
                        "message": "Starting initial web search pass.",
                    }
                )
                event_sink(
                    {
                        "kind": "copilot_event",
                        "phase": "initial",
                        "eventType": "assistant.reasoning_delta",
                        "message": "thinking...",
                        "raw": {"type": "assistant.reasoning_delta"},
                    }
                )
            return GroupWebSearchResponse(
                query=query,
                items=[
                    GroupWebSearchItem(
                        title="New benchmark roundup",
                        url="https://example.com/benchmarks",
                        snippet="Recent benchmark updates for AI developer tools.",
                        source="Example News",
                    )
                ],
            )

        with patch.dict(
            os.environ, {"EVENTTRACKER_AI_PROVIDER": "copilot"}, clear=False
        ):
            with TestClient(app) as client:
                create_response = client.post(
                    "/admin/groups",
                    data={
                        "name": "Product Launches",
                        "web_search_query": "AI developer tools launches and benchmarks",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(create_response.status_code, 303)

                with connection_context() as connection:
                    row = connection.execute(
                        "SELECT id FROM timeline_groups WHERE name = ?",
                        ("Product Launches",),
                    ).fetchone()

                self.assertIsNotNone(row)
                group_id = int(row["id"])

                with patch(
                    "app.main.search_group_web", side_effect=fake_search_group_web
                ):
                    response = client.get(
                        "/timeline/group-web-search/stream",
                        params={"group_id": str(group_id)},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: status", response.text)
        self.assertIn("assistant.reasoning_delta", response.text)
        self.assertIn("event: result", response.text)
        self.assertIn("New benchmark roundup", response.text)

    def test_group_admin_blocks_deleting_group_with_entries(self) -> None:
        with TestClient(app) as client:
            create_group_response = client.post(
                "/admin/groups",
                data={"name": "Product Launches"},
                follow_redirects=False,
            )
            self.assertEqual(create_group_response.status_code, 303)

            with connection_context() as connection:
                row = connection.execute(
                    "SELECT id FROM timeline_groups WHERE name = ?",
                    ("Product Launches",),
                ).fetchone()

            self.assertIsNotNone(row)
            group_id = int(row["id"])

            create_entry_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "16",
                    "group_id": str(group_id),
                    "title": "Launch prep",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Prepared a launch timeline.</p>",
                    "tags": "launch",
                },
                follow_redirects=False,
            )
            self.assertEqual(create_entry_response.status_code, 303)

            delete_response = client.post(
                f"/admin/groups/{group_id}/delete",
                follow_redirects=False,
            )
            self.assertEqual(delete_response.status_code, 400)
            self.assertIn(
                "cannot be deleted while it still has entries", delete_response.text
            )

            admin_response = client.get("/admin/groups")
            self.assertEqual(admin_response.status_code, 200)
            self.assertIn("Product Launches", admin_response.text)
            self.assertIn(
                "Delete is disabled until this group has no entries.",
                admin_response.text,
            )

    def test_group_admin_blocks_deleting_default_group(self) -> None:
        with TestClient(app) as client:
            response = client.post("/admin/groups/1/delete", follow_redirects=False)

        self.assertEqual(response.status_code, 400)
        self.assertIn("default timeline group cannot be deleted", response.text)

    def test_saved_default_group_applies_to_timeline_and_search_until_cleared(
        self,
    ) -> None:
        with TestClient(app) as client:
            create_response = client.post(
                "/admin/groups",
                data={
                    "name": "Product Launches",
                    "web_search_query": "",
                    "is_default": "on",
                },
                follow_redirects=False,
            )
            self.assertEqual(create_response.status_code, 303)

            with connection_context() as connection:
                created_group = connection.execute(
                    "SELECT id, is_default FROM timeline_groups WHERE name = ?",
                    ("Product Launches",),
                ).fetchone()
                seeded_group = connection.execute(
                    "SELECT is_default FROM timeline_groups WHERE name = ?",
                    ("Agentic Coding",),
                ).fetchone()
                default_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM timeline_groups WHERE is_default = 1"
                ).fetchone()

            self.assertIsNotNone(created_group)
            self.assertEqual(int(created_group["is_default"]), 1)
            self.assertIsNotNone(seeded_group)
            self.assertEqual(int(seeded_group["is_default"]), 0)
            self.assertEqual(int(default_count["count"]), 1)

            timeline_response = client.get("/")
            self.assertEqual(timeline_response.status_code, 200)
            self.assertIn("Product Launches Timeline", timeline_response.text)

            search_response = client.get("/search")
            self.assertEqual(search_response.status_code, 200)
            self.assertIn("Product Launches", search_response.text)

            explicit_all_response = client.get("/", params={"group_id": "all"})
            self.assertEqual(explicit_all_response.status_code, 200)
            self.assertIn("All groups Timeline", explicit_all_response.text)

            clear_default_response = client.post(
                f"/admin/groups/{int(created_group['id'])}",
                data={
                    "name": "Product Launches",
                    "web_search_query": "",
                },
                follow_redirects=False,
            )
            self.assertEqual(clear_default_response.status_code, 303)

            with connection_context() as connection:
                default_count_after_clear = connection.execute(
                    "SELECT COUNT(*) AS count FROM timeline_groups WHERE is_default = 1"
                ).fetchone()

            self.assertEqual(int(default_count_after_clear["count"]), 0)

            cleared_timeline_response = client.get("/")
            self.assertEqual(cleared_timeline_response.status_code, 200)
            self.assertIn("All groups Timeline", cleared_timeline_response.text)

    def test_generation_failure_returns_partial_error(self) -> None:
        with patch(
            "app.main.generate_entry_suggestion",
            side_effect=DraftGenerationError("Provider offline."),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/entries/generate",
                    data={
                        "title": "Summarize this milestone.",
                        "source_url": "",
                        "generated_text": "",
                    },
                )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Provider offline.", response.text)

    def test_generation_returns_structured_suggestions_in_partial(self) -> None:
        with patch(
            "app.main.generate_entry_suggestion",
            return_value=GeneratedEntrySuggestion(
                title="Launch Momentum",
                draft_html="<p>Released the <b>first</b> milestone.</p><ul><li>Validated search</li></ul>",
                event_year=2026,
                event_month=3,
                event_day=16,
            ),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/entries/generate",
                    data={
                        "title": "Release milestone",
                        "source_url": "",
                        "generated_text": "",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Launch Momentum", response.text)
        self.assertIn('id="generated_suggested_event_year" value="2026"', response.text)
        self.assertIn('id="generated_suggested_event_month" value="3"', response.text)
        self.assertIn('id="generated_suggested_event_day" value="16"', response.text)
        self.assertIn(
            'id="generated_text" name="generated_text" value="&lt;p&gt;Released the &lt;b&gt;first&lt;/b&gt; milestone.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Validated search&lt;/li&gt;&lt;/ul&gt;"',
            response.text,
        )
        self.assertIn(
            "Summary, title, and date suggestions generated from the current input.",
            response.text,
        )

    def test_preview_html_endpoint_sanitizes_like_saved_entries(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/entries/preview-html",
                data={
                    "raw_html": '<p class="lead"><strong data-x="1">Safe</strong><script>alert(1)</script><a href="https://example.com">link</a><img src="x" onerror="alert(2)"></p>'
                },
            )
            empty_response = client.post(
                "/entries/preview-html",
                data={"raw_html": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("<strong>Safe</strong>", response.text)
        self.assertIn("link", response.text)
        self.assertNotIn("<script", response.text)
        self.assertNotIn("alert(1)", response.text)
        self.assertNotIn("<a", response.text)
        self.assertNotIn("<img", response.text)
        self.assertNotIn("data-x", response.text)

        self.assertEqual(empty_response.status_code, 200)
        self.assertIn("Rendered preview updates here as you type.", empty_response.text)

    def test_export_returns_all_entries_as_json(self) -> None:
        with TestClient(app) as client:
            create_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "3",
                    "event_day": "16",
                    "group_id": "1",
                    "title": "Exportable entry",
                    "source_url": "https://example.com/export",
                    "link_url": ["https://example.com/export/details"],
                    "link_note": ["Supporting export detail"],
                    "generated_text": "Generated draft",
                    "final_text": "<p>Export this entry.</p>",
                    "tags": "export, json",
                },
                follow_redirects=False,
            )
            self.assertEqual(create_response.status_code, 303)

            response = client.get("/entries/export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertRegex(
            response.headers["content-disposition"],
            r"EventTracker-export-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.json",
        )

        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["entries"][0]["title"], "Exportable entry")
        self.assertEqual(payload["entries"][0]["tags"], ["export", "json"])
        self.assertEqual(len(payload["entries"][0]["links"]), 1)
        self.assertEqual(
            payload["entries"][0]["links"][0]["url"],
            "https://example.com/export/details",
        )
        self.assertEqual(
            payload["entries"][0]["links"][0]["note"],
            "Supporting export detail",
        )
        self.assertEqual(
            payload["entries"][0]["final_text"], "<p>Export this entry.</p>"
        )

    def test_view_page_shows_empty_additional_links_state(self) -> None:
        with TestClient(app) as client:
            create_response = client.post(
                "/entries/new",
                data={
                    "event_year": "2026",
                    "event_month": "4",
                    "event_day": "1",
                    "group_id": "1",
                    "title": "View-only entry",
                    "source_url": "",
                    "generated_text": "",
                    "final_text": "<p>Entry without extra links.</p>",
                    "tags": "",
                },
                follow_redirects=False,
            )
            self.assertEqual(create_response.status_code, 303)

            entry_id = self._first_entry_id()
            response = client.get(f"/entries/{entry_id}/view")

        self.assertEqual(response.status_code, 200)
        self.assertIn("No additional links saved for this event.", response.text)

    @staticmethod
    def _first_entry_id() -> int:
        with connection_context() as connection:
            row = connection.execute(
                "SELECT id FROM entries ORDER BY id ASC LIMIT 1"
            ).fetchone()

        if row is None:
            raise AssertionError("Expected at least one entry in the test database.")
        return int(row["id"])

    def _create_entry(
        self,
        client: TestClient,
        *,
        year: int,
        month: int,
        day: int,
        title: str,
        group_id: int = 1,
        final_text: str | None = None,
    ) -> None:
        response = client.post(
            "/entries/new",
            data={
                "event_year": str(year),
                "event_month": str(month),
                "event_day": str(day),
                "group_id": str(group_id),
                "title": title,
                "source_url": "",
                "generated_text": "",
                "final_text": final_text or f"<p>{title}</p>",
                "tags": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
