from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.db import connection_context, init_db
from app.schemas import (
    TimelineStoryArtifactSavePayload,
    TimelineStoryCitationPayload,
    TimelineStorySavePayload,
)
from app.services.embeddings import load_embedding_settings
from app.services.entries import EntryPayload, create_timeline_group, save_entry
from app.services.story_mode import (
    get_story_artifact,
    get_story,
    list_story_citations,
    list_story_entries,
    prepare_story_input_entries,
    resolve_story_scope,
    save_story_artifact,
    save_story,
)


class TestStoryModeService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("EVENTTRACKER_DB_PATH")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "EventTracker-test.db"
        )
        load_embedding_settings.cache_clear()
        init_db()

    def tearDown(self) -> None:
        if self.previous_db_path is None:
            os.environ.pop("EVENTTRACKER_DB_PATH", None)
        else:
            os.environ["EVENTTRACKER_DB_PATH"] = self.previous_db_path
        load_embedding_settings.cache_clear()
        self.temp_dir.cleanup()

    def test_resolve_story_scope_uses_default_group_and_time_filters(self) -> None:
        with connection_context() as connection:
            scope = resolve_story_scope(
                connection,
                q="  ",
                group_id="",
                year="2026",
                month="3",
            )

        self.assertEqual(scope.scope_type, "timeline")
        self.assertEqual(scope.group_id, 1)
        self.assertIsNone(scope.query_text)
        self.assertEqual(scope.year, 2026)
        self.assertEqual(scope.month, 3)

    def test_resolve_story_scope_requires_year_when_month_is_provided(self) -> None:
        with connection_context() as connection:
            with self.assertRaisesRegex(
                ValueError, "Year is required when month is provided"
            ):
                resolve_story_scope(connection, month="3")

    def test_list_story_entries_reorders_search_results_chronologically(self) -> None:
        with connection_context() as connection:
            other_group = create_timeline_group(connection, "Other Group")
            earliest_id = self._create_entry(
                connection,
                year=2024,
                month=11,
                day=2,
                title="Alpha milestone",
                final_text="<p>First milestone shipped.</p>",
            )
            latest_id = self._create_entry(
                connection,
                year=2026,
                month=3,
                day=18,
                title="Gamma milestone",
                final_text="<p>Latest milestone review completed.</p>",
            )
            middle_id = self._create_entry(
                connection,
                year=2025,
                month=5,
                day=10,
                title="Beta milestone",
                final_text="<p>Second milestone announced.</p>",
            )
            self._create_entry(
                connection,
                year=2025,
                month=6,
                day=1,
                group_id=other_group.id,
                title="Unrelated entry",
                final_text="<p>No keyword match here.</p>",
            )

            scope = resolve_story_scope(connection, q="milestone", group_id="")
            entries = list_story_entries(connection, scope)

        self.assertEqual(scope.scope_type, "search")
        self.assertEqual(scope.group_id, 1)
        self.assertEqual(
            [entry.id for entry in entries], [earliest_id, middle_id, latest_id]
        )

    def test_prepare_story_input_entries_truncates_to_most_recent_entries_in_order(
        self,
    ) -> None:
        with connection_context() as connection:
            earliest_id = self._create_entry(
                connection,
                year=2024,
                month=11,
                day=2,
                title="Alpha milestone",
                final_text="<p>First milestone shipped.</p>",
            )
            latest_id = self._create_entry(
                connection,
                year=2026,
                month=3,
                day=18,
                title="Gamma milestone",
                final_text="<p>Latest milestone review completed.</p>",
            )
            middle_id = self._create_entry(
                connection,
                year=2025,
                month=5,
                day=10,
                title="Beta milestone",
                final_text="<p>Second milestone announced.</p>",
            )
            scope = resolve_story_scope(connection, q="milestone", group_id="")
            entries = list_story_entries(connection, scope)

        prepared_entries, truncated = prepare_story_input_entries(
            entries, max_entries=2
        )

        self.assertTrue(truncated)
        self.assertEqual(
            [entry.id for entry in prepared_entries], [middle_id, latest_id]
        )
        self.assertNotIn(earliest_id, [entry.id for entry in prepared_entries])

    def test_save_and_reload_story_preserves_snapshot_and_citations(self) -> None:
        with connection_context() as connection:
            first_entry_id = self._create_entry(
                connection,
                year=2025,
                month=2,
                day=14,
                title="First cited event",
                final_text="<p>Initial cited event.</p>",
            )
            second_entry_id = self._create_entry(
                connection,
                year=2025,
                month=9,
                day=3,
                title="Second cited event",
                final_text="<p>Follow-up cited event.</p>",
            )
            story_id = save_story(
                connection,
                TimelineStorySavePayload(
                    scope_type="search",
                    group_id=None,
                    query_text="cited",
                    year=2025,
                    month=None,
                    format="detailed_chronology",
                    title="2025 cited story",
                    narrative_html="<p>Story body</p>",
                    narrative_text="Story body",
                    generated_utc="2026-03-19T12:00:00+00:00",
                    updated_utc="2026-03-19T12:05:00+00:00",
                    provider_name="copilot",
                    source_entry_count=2,
                    truncated_input=True,
                    error_text=None,
                    citations=[
                        TimelineStoryCitationPayload(
                            entry_id=second_entry_id,
                            citation_order=2,
                            quote_text="Later quote",
                            note="Second citation",
                        ),
                        TimelineStoryCitationPayload(
                            entry_id=first_entry_id,
                            citation_order=1,
                            quote_text="Earlier quote",
                            note="First citation",
                        ),
                    ],
                ),
            )

            story = get_story(connection, story_id)
            citations = list_story_citations(connection, story_id)

        self.assertIsNotNone(story)
        assert story is not None
        self.assertEqual(story.id, story_id)
        self.assertEqual(story.scope_type, "search")
        self.assertEqual(story.query_text, "cited")
        self.assertEqual(story.format, "detailed_chronology")
        self.assertEqual(story.source_entry_count, 2)
        self.assertTrue(story.truncated_input)
        self.assertEqual(
            [citation.entry_id for citation in story.citations],
            [first_entry_id, second_entry_id],
        )
        self.assertEqual(
            [citation.entry_id for citation in citations],
            [first_entry_id, second_entry_id],
        )
        self.assertEqual(story.citations[0].quote_text, "Earlier quote")
        self.assertEqual(story.citations[1].note, "Second citation")

    def test_save_and_reload_story_artifact_preserves_compiled_presentation(self) -> None:
        with connection_context() as connection:
            story_id = save_story(
                connection,
                TimelineStorySavePayload(
                    scope_type="timeline",
                    group_id=1,
                    query_text=None,
                    year=None,
                    month=None,
                    format="executive_summary",
                    title="Deck-backed story",
                    narrative_html="<p>Story body</p>",
                    narrative_text="Story body",
                    generated_utc="2026-03-20T12:00:00+00:00",
                    updated_utc="2026-03-20T12:00:00+00:00",
                    provider_name="copilot",
                    source_entry_count=0,
                    truncated_input=False,
                    citations=[],
                ),
            )

            artifact_id = save_story_artifact(
                connection,
                story_id,
                TimelineStoryArtifactSavePayload(
                    artifact_kind="executive_deck",
                    source_format="marpit_markdown",
                    source_text="---\nmarpit: true\n---\n# Deck",
                    compiled_html='<div class="marpit"><section><h1>Deck</h1></section></div>',
                    compiled_css="section { color: #123456; }",
                    metadata_json='{"slide_count":1}',
                    generated_utc="2026-03-20T12:00:00+00:00",
                    compiled_utc="2026-03-20T12:00:02+00:00",
                    compiler_name="marpit",
                    compiler_version="4.1.2",
                ),
            )

            artifact = get_story_artifact(connection, story_id, "executive_deck")

        self.assertEqual(artifact_id, 1)
        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual(artifact.story_id, story_id)
        self.assertEqual(artifact.artifact_kind, "executive_deck")
        self.assertEqual(artifact.source_format, "marpit_markdown")
        self.assertIn("marpit: true", artifact.source_text)
        self.assertIn('<div class="marpit">', artifact.compiled_html)
        self.assertEqual(artifact.compiler_name, "marpit")
        self.assertEqual(artifact.compiler_version, "4.1.2")

    def _create_entry(
        self,
        connection,
        *,
        year: int,
        month: int,
        day: int | None,
        group_id: int = 1,
        title: str,
        final_text: str,
    ) -> int:
        return save_entry(
            connection,
            EntryPayload(
                event_year=year,
                event_month=month,
                event_day=day,
                group_id=group_id,
                title=title,
                source_url=None,
                generated_text=None,
                final_text=final_text,
                tags=[],
                links=[],
            ),
        )


class TestParseStoryGroupId(unittest.TestCase):
    def test_empty_string_returns_none(self) -> None:
        from app.services.story_mode import _parse_story_group_id

        self.assertIsNone(_parse_story_group_id(""))

    def test_all_lowercase_returns_none(self) -> None:
        from app.services.story_mode import _parse_story_group_id

        self.assertIsNone(_parse_story_group_id("all"))

    def test_all_uppercase_returns_none(self) -> None:
        from app.services.story_mode import _parse_story_group_id

        self.assertIsNone(_parse_story_group_id("ALL"))

    def test_valid_int_string(self) -> None:
        from app.services.story_mode import _parse_story_group_id

        self.assertEqual(_parse_story_group_id("5"), 5)

    def test_invalid_string_raises_value_error(self) -> None:
        from app.services.story_mode import _parse_story_group_id

        with self.assertRaises(ValueError):
            _parse_story_group_id("abc")

    def test_zero_raises_value_error(self) -> None:
        from app.services.story_mode import _parse_story_group_id

        with self.assertRaises(ValueError):
            _parse_story_group_id("0")

    def test_negative_raises_value_error(self) -> None:
        from app.services.story_mode import _parse_story_group_id

        with self.assertRaises(ValueError):
            _parse_story_group_id("-1")


class TestParseOptionalInt(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        from app.services.story_mode import _parse_optional_int

        self.assertIsNone(
            _parse_optional_int(None, field_name="year", minimum=1900, maximum=2100)
        )

    def test_empty_string_returns_none(self) -> None:
        from app.services.story_mode import _parse_optional_int

        self.assertIsNone(
            _parse_optional_int("", field_name="year", minimum=1900, maximum=2100)
        )

    def test_valid_string(self) -> None:
        from app.services.story_mode import _parse_optional_int

        self.assertEqual(
            _parse_optional_int("2025", field_name="year", minimum=1900, maximum=2100),
            2025,
        )

    def test_out_of_range_raises_value_error(self) -> None:
        from app.services.story_mode import _parse_optional_int

        with self.assertRaises(ValueError):
            _parse_optional_int("3000", field_name="year", minimum=1900, maximum=2100)

    def test_non_numeric_raises_value_error(self) -> None:
        from app.services.story_mode import _parse_optional_int

        with self.assertRaises(ValueError):
            _parse_optional_int("abc", field_name="year", minimum=1900, maximum=2100)


class TestOrderStoryEntries(unittest.TestCase):
    def _make_entry(
        self,
        *,
        entry_id: int,
        sort_key: int,
        updated_utc: str,
    ) -> "Entry":
        from app.models import Entry

        return Entry(
            id=entry_id,
            event_year=2025,
            event_month=1,
            event_day=1,
            sort_key=sort_key,
            group_id=1,
            group_name="Default",
            title=f"Entry {entry_id}",
            source_url=None,
            generated_text=None,
            final_text="<p>text</p>",
            created_utc="2025-01-01T00:00:00+00:00",
            updated_utc=updated_utc,
        )

    def test_sorts_by_sort_key_then_updated_utc_then_id(self) -> None:
        from app.services.story_mode import order_story_entries

        entry_a = self._make_entry(
            entry_id=3, sort_key=20250101, updated_utc="2025-01-01T00:00:00+00:00"
        )
        entry_b = self._make_entry(
            entry_id=1, sort_key=20250601, updated_utc="2025-06-01T00:00:00+00:00"
        )
        entry_c = self._make_entry(
            entry_id=2, sort_key=20250101, updated_utc="2025-01-01T00:00:00+00:00"
        )

        result = order_story_entries([entry_b, entry_a, entry_c])
        self.assertEqual([e.id for e in result], [2, 3, 1])

    def test_empty_list(self) -> None:
        from app.services.story_mode import order_story_entries

        self.assertEqual(order_story_entries([]), [])

    def test_single_entry(self) -> None:
        from app.services.story_mode import order_story_entries

        entry = self._make_entry(
            entry_id=1, sort_key=20250101, updated_utc="2025-01-01T00:00:00+00:00"
        )
        result = order_story_entries([entry])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 1)
