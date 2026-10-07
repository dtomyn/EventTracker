from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.db import connection_context, init_db
from app.main import app
from app.schemas import EntryPayload
from app.services.entries import (
    TAG_TONE_COUNT,
    get_timeline_stats,
    save_entry,
    tag_tone,
)


def _payload(
    *,
    year: int,
    month: int,
    day: int | None,
    tags: list[str],
    group_id: int = 1,
    title: str = "Event",
) -> EntryPayload:
    return EntryPayload(
        event_year=year,
        event_month=month,
        event_day=day,
        group_id=group_id,
        title=title,
        source_url=None,
        generated_text=None,
        final_text=f"<p>{title}</p>",
        tags=tags,
        links=[],
    )


class TagToneTests(unittest.TestCase):
    def test_tag_tone_is_stable_and_in_range(self) -> None:
        for tag in ("security", "github-copilot", "release", "x"):
            tone = tag_tone(tag)
            self.assertGreaterEqual(tone, 0)
            self.assertLess(tone, TAG_TONE_COUNT)
            self.assertEqual(tone, tag_tone(tag))

    def test_tag_tone_ignores_case_and_surrounding_space(self) -> None:
        self.assertEqual(tag_tone("Security"), tag_tone("  security "))


class TimelineStatsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("EVENTTRACKER_DB_PATH")
        self.previous_testing = os.environ.get("TESTING")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "EventTracker-test.db"
        )
        os.environ["TESTING"] = "1"
        init_db()

    def tearDown(self) -> None:
        for key, value in (
            ("EVENTTRACKER_DB_PATH", self.previous_db_path),
            ("TESTING", self.previous_testing),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def test_empty_scope_returns_zeroed_stats(self) -> None:
        with connection_context() as connection:
            stats = get_timeline_stats(connection, group_id=1)

        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["span_months"], 0)
        self.assertEqual(stats["top_tags"], [])
        self.assertIsNone(stats["sparkline"])

    def test_short_span_uses_weekly_buckets_and_ranks_tags(self) -> None:
        with connection_context() as connection:
            save_entry(connection, _payload(year=2026, month=3, day=2, tags=["release", "ai"]))
            save_entry(connection, _payload(year=2026, month=3, day=4, tags=["release"]))
            save_entry(connection, _payload(year=2026, month=4, day=None, tags=["ai", "release"]))
            save_entry(connection, _payload(year=2026, month=4, day=20, tags=["zeta"]))
            stats = get_timeline_stats(connection, group_id=1, top_tag_limit=2)

        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["span_months"], 2)
        self.assertEqual(stats["span_value"], "2")
        self.assertEqual(stats["span_unit"], "months")
        self.assertEqual(stats["first_label"], "Mar 2026")
        self.assertEqual(stats["last_label"], "Apr 2026")
        self.assertEqual(stats["tag_count"], 3)
        self.assertEqual(
            [(tag["name"], tag["count"]) for tag in stats["top_tags"]],
            [("release", 3), ("ai", 2)],
        )
        self.assertEqual(stats["top_tags"][0]["tone"], tag_tone("release"))

        sparkline = stats["sparkline"]
        assert sparkline is not None
        self.assertEqual(sparkline["bucket_unit"], "week")
        # Mar 2 (Mon) through the week of Apr 20 is 8 Monday-based weeks.
        self.assertEqual(sparkline["bucket_count"], 8)
        self.assertEqual(sparkline["start_label"], "Mar 2")
        self.assertEqual(sparkline["end_label"], "Apr 20")
        self.assertEqual(sparkline["peak_count"], 2)
        self.assertEqual(sparkline["peak_label"], "Mar 2")
        self.assertEqual(len(sparkline["line_points"].split()), 8)

    def test_long_span_uses_monthly_buckets_and_year_units(self) -> None:
        with connection_context() as connection:
            save_entry(connection, _payload(year=2022, month=1, day=10, tags=[]))
            save_entry(connection, _payload(year=2025, month=6, day=None, tags=[]))
            save_entry(connection, _payload(year=2025, month=6, day=3, tags=[]))
            stats = get_timeline_stats(connection, group_id=None)

        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["span_months"], 42)
        self.assertEqual(stats["span_value"], "3.5")
        self.assertEqual(stats["span_unit"], "years")
        sparkline = stats["sparkline"]
        assert sparkline is not None
        self.assertEqual(sparkline["bucket_unit"], "month")
        self.assertEqual(sparkline["bucket_count"], 36)
        self.assertEqual(sparkline["end_label"], "Jun 2025")
        self.assertEqual(sparkline["peak_label"], "Jun 2025")
        self.assertEqual(sparkline["peak_count"], 2)

    def test_stats_are_scoped_to_group(self) -> None:
        with connection_context() as connection:
            cursor = connection.execute(
                "INSERT INTO timeline_groups(name, web_search_query, is_default) VALUES (?, NULL, 0)",
                ("Other Group",),
            )
            assert cursor.lastrowid is not None
            other_group_id = int(cursor.lastrowid)
            save_entry(connection, _payload(year=2026, month=1, day=1, tags=["one"]))
            save_entry(
                connection,
                _payload(year=2026, month=2, day=1, tags=["two"], group_id=other_group_id),
            )
            group_stats = get_timeline_stats(connection, group_id=1)
            all_stats = get_timeline_stats(connection, group_id=None)

        self.assertEqual(group_stats["total"], 1)
        self.assertEqual([tag["name"] for tag in group_stats["top_tags"]], ["one"])
        self.assertEqual(all_stats["total"], 2)

    def test_timeline_page_renders_hero_stats_and_spine(self) -> None:
        with connection_context() as connection:
            save_entry(
                connection,
                _payload(year=2026, month=3, day=2, tags=["release"], title="Hero entry"),
            )

        with TestClient(app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('class="tl-stats"', html)
        self.assertIn('data-tl-count="1"', html)
        self.assertIn('class="tl-sparkline"', html)
        self.assertIn("timeline-spine-item tag-tone-", html)
        self.assertIn(f"tag-pill tag-tone-{tag_tone('release')}", html)
        self.assertIn("/static/timeline.css", html)
        self.assertIn("/static/timeline.js", html)


if __name__ == "__main__":
    unittest.main()
