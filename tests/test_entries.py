from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from app.db import connection_context, init_db
from app.models import Entry
from app.schemas import EntryLinkPayload, EntryPayload
from app.services.entries import (
    build_timeline_groups,
    list_group_tag_vocabulary,
    list_saved_entry_urls,
    list_timeline_month_buckets,
    list_timeline_summary_groups,
    list_timeline_year_buckets,
    save_entry,
    timeline_playback_profile,
)


def _entry(
    entry_id: int,
    *,
    year: int,
    month: int,
    day: int | None,
    title: str,
) -> Entry:
    return Entry(
        id=entry_id,
        event_year=year,
        event_month=month,
        event_day=day,
        sort_key=(year * 10000) + (month * 100) + (day or 0),
        group_id=1,
        group_name="Agentic Coding",
        title=title,
        source_url=None,
        generated_text=None,
        final_text=f"<p>{title}</p>",
        created_utc="2026-03-18T00:00:00+00:00",
        updated_utc="2026-03-18T00:00:00+00:00",
    )


class TestTimelineViewModels(unittest.TestCase):
    def test_build_timeline_groups_preserves_month_order_and_entries(self) -> None:
        entries = [
            _entry(3, year=2026, month=3, day=18, title="March latest"),
            _entry(2, year=2026, month=3, day=12, title="March earlier"),
            _entry(1, year=2026, month=2, day=25, title="February event"),
        ]

        groups = build_timeline_groups(entries)

        self.assertEqual(
            [group["label"] for group in groups], ["March 2026", "February 2026"]
        )
        self.assertEqual(
            [[entry.title for entry in group["entries"]] for group in groups],
            [["March latest", "March earlier"], ["February event"]],
        )
        self.assertEqual(groups[0]["key"], "2026-03")
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[0]["focus_key"], "month-2026-03")
        self.assertEqual(groups[0]["playback_burst_level"], "steady")

    def test_timeline_playback_profile_accelerates_dense_months(self) -> None:
        sparse_intro, sparse_interval, sparse_outro, sparse_level = (
            timeline_playback_profile(1)
        )
        busy_intro, busy_interval, busy_outro, busy_level = timeline_playback_profile(4)
        surge_intro, surge_interval, surge_outro, surge_level = (
            timeline_playback_profile(9)
        )

        self.assertGreater(sparse_intro, busy_intro)
        self.assertGreater(sparse_interval, busy_interval)
        self.assertGreater(busy_interval, surge_interval)
        self.assertGreaterEqual(sparse_outro, busy_outro)
        self.assertEqual(sparse_level, "steady")
        self.assertEqual(busy_level, "burst")
        self.assertEqual(surge_level, "surge")

    def test_timeline_bucket_builders_count_entries_by_year_and_month(self) -> None:
        entries = [
            _entry(4, year=2026, month=3, day=18, title="March latest"),
            _entry(3, year=2026, month=3, day=12, title="March earlier"),
            _entry(2, year=2026, month=2, day=25, title="February event"),
            _entry(1, year=2025, month=12, day=31, title="Prior year"),
        ]

        year_buckets = list_timeline_year_buckets(entries)
        month_buckets = list_timeline_month_buckets(entries)
        filtered_month_buckets = list_timeline_month_buckets(entries, year=2026)

        self.assertEqual(
            [
                (bucket["event_year"], bucket["count"], bucket["drill_view"])
                for bucket in year_buckets
            ],
            [(2026, 3, "months"), (2025, 1, "months")],
        )
        self.assertEqual(
            [
                (
                    bucket["event_year"],
                    bucket["event_month"],
                    bucket["count"],
                    bucket["drill_view"],
                )
                for bucket in month_buckets
            ],
            [(2026, 3, 2, "events"), (2026, 2, 1, "events"), (2025, 12, 1, "events")],
        )
        self.assertEqual(
            [
                (bucket["event_year"], bucket["event_month"])
                for bucket in filtered_month_buckets
            ],
            [(2026, 3), (2026, 2)],
        )

    def test_timeline_summary_groups_apply_year_and_month_filters(self) -> None:
        entries = [
            _entry(3, year=2026, month=3, day=18, title="March latest"),
            _entry(2, year=2026, month=2, day=25, title="February event"),
            _entry(1, year=2025, month=12, day=31, title="Prior year"),
        ]

        groups = list_timeline_summary_groups(entries, year=2026, month=3)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["label"], "March 2026")
        self.assertEqual(
            [entry.title for entry in groups[0]["entries"]], ["March latest"]
        )


class TestSavedEntryUrls(unittest.TestCase):
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

    def test_list_saved_entry_urls_includes_source_and_additional_links(self) -> None:
        with connection_context() as connection:
            save_entry(
                connection,
                EntryPayload(
                    event_year=2026,
                    event_month=3,
                    event_day=20,
                    group_id=1,
                    title="Primary source entry",
                    source_url="https://example.com/source",
                    generated_text=None,
                    final_text="<p>Primary source content.</p>",
                    tags=[],
                    links=[
                        EntryLinkPayload(
                            url="https://example.com/additional",
                            note="Additional context",
                        )
                    ],
                ),
            )
            saved_urls = list_saved_entry_urls(connection)

        self.assertEqual(
            saved_urls,
            {
                "https://example.com/source",
                "https://example.com/additional",
            },
        )


class TestGroupTagVocabulary(unittest.TestCase):
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

    def test_list_group_tag_vocabulary_prefers_most_used_tags_in_selected_group(
        self,
    ) -> None:
        with connection_context() as connection:
            group_cursor = connection.execute(
                "INSERT INTO timeline_groups(name, web_search_query, is_default) VALUES (?, NULL, 0)",
                ("Other Group",),
            )
            assert group_cursor.lastrowid is not None
            group_two_id = int(group_cursor.lastrowid)
            save_entry(
                connection,
                EntryPayload(
                    event_year=2026,
                    event_month=3,
                    event_day=20,
                    group_id=1,
                    title="Release prep",
                    source_url=None,
                    generated_text=None,
                    final_text="<p>Prepared the release.</p>",
                    tags=["release", "milestone", "launch"],
                    links=[],
                ),
            )
            save_entry(
                connection,
                EntryPayload(
                    event_year=2026,
                    event_month=3,
                    event_day=21,
                    group_id=1,
                    title="Release shipped",
                    source_url=None,
                    generated_text=None,
                    final_text="<p>Shipped the release.</p>",
                    tags=["release", "announcement"],
                    links=[],
                ),
            )
            save_entry(
                connection,
                EntryPayload(
                    event_year=2026,
                    event_month=3,
                    event_day=22,
                    group_id=group_two_id,
                    title="Other group",
                    source_url=None,
                    generated_text=None,
                    final_text="<p>Separate group tags.</p>",
                    tags=["release", "unrelated"],
                    links=[],
                ),
            )

            vocabulary = list_group_tag_vocabulary(connection, 1)

        self.assertEqual(vocabulary, ["release", "announcement", "launch", "milestone"])

    def test_list_group_tag_vocabulary_returns_empty_for_zero_limit(self) -> None:
        with connection_context() as connection:
            vocabulary = list_group_tag_vocabulary(connection, 1, limit=0)

        self.assertEqual(vocabulary, [])
