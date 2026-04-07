from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from app.db import connection_context, init_db
from app.models import Entry, EntryLink
from app.schemas import EntryLinkPayload, EntryPayload
from app.services.entries import (
    _is_valid_url,
    _parse_int,
    blank_form_state,
    build_timeline_groups,
    compute_sort_key,
    decode_timeline_cursor,
    encode_timeline_cursor,
    format_plain_text,
    form_state_from_entry,
    get_heatmap_counts,
    list_group_tag_vocabulary,
    list_saved_entry_urls,
    list_timeline_month_buckets,
    list_timeline_summary_groups,
    list_timeline_year_buckets,
    normalize_tags,
    normalize_timeline_page_size,
    parse_link_rows,
    parse_links_json,
    plain_text_from_html,
    preview_text,
    save_entry,
    timeline_playback_profile,
    validate_entry_form,
    validate_link_rows,
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


class TestEntryFormValidation(unittest.TestCase):
    def test_validate_entry_form_preserves_summary_instructions_in_state_only(
        self,
    ) -> None:
        state, payload = validate_entry_form(
            {
                "event_year": "2026",
                "event_month": "4",
                "event_day": "6",
                "group_id": "1",
                "title": "Timeline milestone",
                "source_url": "https://example.com/source",
                "summary_instructions": " Focus on the technical outcome only. ",
                "generated_text": "",
                "final_text": "<p>Draft summary.</p>",
                "tags": "release, milestone",
            }
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(
            state.values["summary_instructions"],
            "Focus on the technical outcome only.",
        )
        self.assertFalse(hasattr(payload, "summary_instructions"))


class TestComputeSortKey(unittest.TestCase):
    def test_normal_date(self) -> None:
        self.assertEqual(compute_sort_key(2026, 3, 18), 20260318)

    def test_none_day_yields_zero(self) -> None:
        self.assertEqual(compute_sort_key(2026, 3, None), 20260300)

    def test_boundary_january_first(self) -> None:
        self.assertEqual(compute_sort_key(1900, 1, 1), 19000101)

    def test_boundary_december_thirty_first(self) -> None:
        self.assertEqual(compute_sort_key(2100, 12, 31), 21001231)


class TestNormalizeTags(unittest.TestCase):
    def test_empty_string(self) -> None:
        self.assertEqual(normalize_tags(""), [])

    def test_duplicates_with_different_case(self) -> None:
        result = normalize_tags("Release, release, RELEASE")
        self.assertEqual(result, ["Release"])

    def test_whitespace_only_tags(self) -> None:
        self.assertEqual(normalize_tags("  ,  ,  "), [])

    def test_multiple_spaces_in_tags(self) -> None:
        result = normalize_tags("multi   word   tag, another")
        self.assertEqual(result, ["multi word tag", "another"])

    def test_all_comma_input(self) -> None:
        self.assertEqual(normalize_tags(",,,"), [])

    def test_preserves_first_casing(self) -> None:
        result = normalize_tags("Alpha, beta, alpha, Beta")
        self.assertEqual(result, ["Alpha", "beta"])


class TestIsValidUrl(unittest.TestCase):
    def test_http_valid(self) -> None:
        self.assertTrue(_is_valid_url("http://example.com"))

    def test_https_valid(self) -> None:
        self.assertTrue(_is_valid_url("https://example.com/path"))

    def test_ftp_invalid(self) -> None:
        self.assertFalse(_is_valid_url("ftp://example.com"))

    def test_no_scheme(self) -> None:
        self.assertFalse(_is_valid_url("example.com"))

    def test_no_netloc(self) -> None:
        self.assertFalse(_is_valid_url("http://"))

    def test_empty_string(self) -> None:
        self.assertFalse(_is_valid_url(""))

    def test_just_http_colon_slash_slash(self) -> None:
        self.assertFalse(_is_valid_url("http://"))


class TestParseInt(unittest.TestCase):
    def test_valid_value(self) -> None:
        errors: dict[str, str] = {}
        result = _parse_int("5", "field", errors, minimum=1, maximum=10)
        self.assertEqual(result, 5)
        self.assertEqual(errors, {})

    def test_empty_string(self) -> None:
        errors: dict[str, str] = {}
        result = _parse_int("", "field", errors, minimum=1, maximum=10)
        self.assertIsNone(result)
        self.assertIn("field", errors)

    def test_non_numeric(self) -> None:
        errors: dict[str, str] = {}
        result = _parse_int("abc", "field", errors, minimum=1, maximum=10)
        self.assertIsNone(result)
        self.assertEqual(errors["field"], "Enter a valid number.")

    def test_below_minimum(self) -> None:
        errors: dict[str, str] = {}
        result = _parse_int("0", "field", errors, minimum=1, maximum=10)
        self.assertIsNone(result)
        self.assertIn("between", errors["field"])

    def test_above_maximum(self) -> None:
        errors: dict[str, str] = {}
        result = _parse_int("11", "field", errors, minimum=1, maximum=10)
        self.assertIsNone(result)
        self.assertIn("between", errors["field"])

    def test_at_boundaries(self) -> None:
        errors: dict[str, str] = {}
        self.assertEqual(_parse_int("1", "f", errors, minimum=1, maximum=10), 1)
        self.assertEqual(_parse_int("10", "g", errors, minimum=1, maximum=10), 10)
        self.assertEqual(errors, {})


class TestPreviewText(unittest.TestCase):
    def test_empty_string(self) -> None:
        self.assertEqual(preview_text(""), "")

    def test_short_text(self) -> None:
        self.assertEqual(preview_text("Hello world"), "Hello world")

    def test_exactly_at_max_length(self) -> None:
        text = "a" * 280
        self.assertEqual(preview_text(text), text)

    def test_over_max_length_truncates_with_ellipsis(self) -> None:
        text = "a" * 300
        result = preview_text(text)
        self.assertEqual(len(result), 280)
        self.assertTrue(result.endswith("\u2026"))

    def test_html_tags_stripped(self) -> None:
        result = preview_text("<p>Hello <strong>world</strong></p>")
        self.assertEqual(result, "Hello world")

    def test_custom_max_length(self) -> None:
        result = preview_text("abcdefghij", max_length=5)
        self.assertEqual(result, "abcd\u2026")


class TestFormatPlainText(unittest.TestCase):
    def test_special_chars_escaped(self) -> None:
        result = format_plain_text("<script>alert('x')</script>")
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)

    def test_newlines_converted_to_br(self) -> None:
        result = format_plain_text("line1\nline2")
        self.assertEqual(result, "line1<br>line2")

    def test_both_escaping_and_newlines(self) -> None:
        result = format_plain_text("<b>bold</b>\nnext")
        self.assertIn("&lt;b&gt;", result)
        self.assertIn("<br>", result)


class TestPlainTextFromHtml(unittest.TestCase):
    def test_empty_string(self) -> None:
        self.assertEqual(plain_text_from_html(""), "")

    def test_tags_stripped(self) -> None:
        self.assertEqual(plain_text_from_html("<p>Hello</p>"), "Hello")

    def test_nested_tags(self) -> None:
        result = plain_text_from_html("<div><p>Hello <strong>world</strong></p></div>")
        self.assertEqual(result, "Hello world")

    def test_plain_text_passthrough(self) -> None:
        self.assertEqual(plain_text_from_html("Just text"), "Just text")


class TestValidateEntryFormExtended(unittest.TestCase):
    """Extended validation tests beyond the existing TestEntryFormValidation."""

    def _valid_form(self, **overrides: str) -> dict[str, str]:
        base: dict[str, str] = {
            "event_year": "2026",
            "event_month": "4",
            "event_day": "6",
            "group_id": "1",
            "title": "Test entry",
            "source_url": "",
            "summary_instructions": "",
            "generated_text": "",
            "final_text": "<p>Content.</p>",
            "tags": "",
        }
        base.update(overrides)
        return base

    def test_valid_form_returns_payload(self) -> None:
        state, payload = validate_entry_form(self._valid_form())
        self.assertIsNotNone(payload)
        self.assertEqual(state.errors, {})

    def test_missing_title(self) -> None:
        state, payload = validate_entry_form(self._valid_form(title=""))
        self.assertIsNone(payload)
        self.assertIn("title", state.errors)

    def test_missing_year(self) -> None:
        state, payload = validate_entry_form(self._valid_form(event_year=""))
        self.assertIsNone(payload)
        self.assertIn("event_year", state.errors)

    def test_invalid_month(self) -> None:
        state, payload = validate_entry_form(self._valid_form(event_month="13"))
        self.assertIsNone(payload)
        self.assertIn("event_month", state.errors)

    def test_invalid_day_feb_30(self) -> None:
        state, payload = validate_entry_form(
            self._valid_form(event_month="2", event_day="30")
        )
        self.assertIsNone(payload)
        self.assertIn("event_day", state.errors)

    def test_valid_leap_day(self) -> None:
        state, payload = validate_entry_form(
            self._valid_form(event_year="2024", event_month="2", event_day="29")
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.event_day, 29)

    def test_invalid_source_url(self) -> None:
        state, payload = validate_entry_form(
            self._valid_form(source_url="not-a-url")
        )
        self.assertIsNone(payload)
        self.assertIn("source_url", state.errors)

    def test_empty_final_text(self) -> None:
        state, payload = validate_entry_form(self._valid_form(final_text=""))
        self.assertIsNone(payload)
        self.assertIn("final_text", state.errors)

    def test_valid_form_with_getlist_links(self) -> None:
        class FormWithLinks(dict):  # type: ignore[type-arg]
            def getlist(self, key: str) -> list[str]:
                data = {
                    "link_url": ["https://example.com"],
                    "link_note": ["A note"],
                }
                return data.get(key, [])

        form = FormWithLinks(self._valid_form())
        state, payload = validate_entry_form(form)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(len(payload.links), 1)


class TestParseLinkRows(unittest.TestCase):
    def test_no_getlist_method(self) -> None:
        result = parse_link_rows({"key": "value"})
        self.assertEqual(result, [])

    def test_empty_lists(self) -> None:
        class Form(dict):  # type: ignore[type-arg]
            def getlist(self, key: str) -> list[str]:
                return []

        self.assertEqual(parse_link_rows(Form()), [])

    def test_unequal_lengths(self) -> None:
        class Form(dict):  # type: ignore[type-arg]
            def getlist(self, key: str) -> list[str]:
                if key == "link_url":
                    return ["https://a.com", "https://b.com"]
                return ["Note A"]

        result = parse_link_rows(Form())
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["note"], "")

    def test_whitespace_stripping(self) -> None:
        class Form(dict):  # type: ignore[type-arg]
            def getlist(self, key: str) -> list[str]:
                if key == "link_url":
                    return ["  https://example.com  "]
                return ["  Some note  "]

        result = parse_link_rows(Form())
        self.assertEqual(result[0]["url"], "https://example.com")
        self.assertEqual(result[0]["note"], "Some note")


class TestValidateLinkRows(unittest.TestCase):
    def test_empty_row_skipped(self) -> None:
        errors: dict[str, str] = {}
        result = validate_link_rows([{"url": "", "note": ""}], errors)
        self.assertEqual(result, [])
        self.assertEqual(errors, {})

    def test_url_only_produces_error(self) -> None:
        errors: dict[str, str] = {}
        validate_link_rows([{"url": "https://example.com", "note": ""}], errors)
        self.assertIn("link_note_0", errors)

    def test_note_only_produces_error(self) -> None:
        errors: dict[str, str] = {}
        validate_link_rows([{"url": "", "note": "Some note"}], errors)
        self.assertIn("link_url_0", errors)

    def test_valid_pair(self) -> None:
        errors: dict[str, str] = {}
        result = validate_link_rows(
            [{"url": "https://example.com", "note": "A note"}], errors
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(errors, {})

    def test_invalid_url_in_pair(self) -> None:
        errors: dict[str, str] = {}
        validate_link_rows(
            [{"url": "not-valid", "note": "A note"}], errors
        )
        self.assertIn("link_url_0", errors)


class TestTimelineCursor(unittest.TestCase):
    def test_round_trip(self) -> None:
        entry = _entry(42, year=2026, month=3, day=18, title="Test")
        entry.updated_utc = "2026-03-18T00:00:00+00:00"
        cursor = encode_timeline_cursor(entry)
        sort_key, updated_utc, entry_id = decode_timeline_cursor(cursor)
        self.assertEqual(sort_key, entry.sort_key)
        self.assertEqual(updated_utc, entry.updated_utc)
        self.assertEqual(entry_id, entry.id)

    def test_invalid_base64(self) -> None:
        with self.assertRaises(ValueError):
            decode_timeline_cursor("!!!invalid!!!")

    def test_invalid_json(self) -> None:
        import base64

        encoded = base64.urlsafe_b64encode(b"not json").decode("ascii")
        with self.assertRaises(ValueError):
            decode_timeline_cursor(encoded)

    def test_missing_keys(self) -> None:
        import base64
        import json

        encoded = base64.urlsafe_b64encode(
            json.dumps({"sort_key": 1}).encode()
        ).decode("ascii")
        with self.assertRaises(ValueError):
            decode_timeline_cursor(encoded)

    def test_wrong_types(self) -> None:
        import base64
        import json

        encoded = base64.urlsafe_b64encode(
            json.dumps(
                {"sort_key": "not_int", "updated_utc": "2026-01-01", "id": 1}
            ).encode()
        ).decode("ascii")
        with self.assertRaises(ValueError):
            decode_timeline_cursor(encoded)


class TestNormalizeTimelinePageSize(unittest.TestCase):
    def test_none_returns_default(self) -> None:
        self.assertEqual(normalize_timeline_page_size(None), 25)

    def test_zero_clamps_to_one(self) -> None:
        self.assertEqual(normalize_timeline_page_size(0), 1)

    def test_negative_clamps_to_one(self) -> None:
        self.assertEqual(normalize_timeline_page_size(-10), 1)

    def test_over_max_clamps_to_fifty(self) -> None:
        self.assertEqual(normalize_timeline_page_size(100), 50)

    def test_valid_value_unchanged(self) -> None:
        self.assertEqual(normalize_timeline_page_size(25), 25)

    def test_boundary_one(self) -> None:
        self.assertEqual(normalize_timeline_page_size(1), 1)

    def test_boundary_fifty(self) -> None:
        self.assertEqual(normalize_timeline_page_size(50), 50)


class TestBlankFormState(unittest.TestCase):
    def test_all_values_empty_strings(self) -> None:
        state = blank_form_state()
        for key, value in state.values.items():
            self.assertEqual(value, "", f"Expected empty string for {key}")

    def test_errors_empty(self) -> None:
        state = blank_form_state()
        self.assertEqual(state.errors, {})

    def test_no_link_rows(self) -> None:
        state = blank_form_state()
        self.assertEqual(len(state.link_rows), 0)


class TestFormStateFromEntry(unittest.TestCase):
    def test_with_all_fields(self) -> None:
        entry = Entry(
            id=1,
            event_year=2026,
            event_month=3,
            event_day=18,
            sort_key=20260318,
            group_id=1,
            group_name="Test",
            title="Test entry",
            source_url="https://example.com",
            generated_text="Generated",
            final_text="<p>Final</p>",
            created_utc="2026-03-18T00:00:00+00:00",
            updated_utc="2026-03-18T00:00:00+00:00",
            tags=["alpha", "beta"],
        )
        state = form_state_from_entry(entry)
        self.assertEqual(state.values["event_year"], "2026")
        self.assertEqual(state.values["event_month"], "3")
        self.assertEqual(state.values["event_day"], "18")
        self.assertEqual(state.values["source_url"], "https://example.com")
        self.assertEqual(state.values["tags"], "alpha, beta")
        self.assertEqual(state.errors, {})

    def test_with_none_day(self) -> None:
        entry = _entry(1, year=2026, month=3, day=None, title="No day")
        state = form_state_from_entry(entry)
        self.assertEqual(state.values["event_day"], "")

    def test_with_none_source_url(self) -> None:
        entry = _entry(1, year=2026, month=3, day=1, title="No URL")
        state = form_state_from_entry(entry)
        self.assertEqual(state.values["source_url"], "")

    def test_with_links(self) -> None:
        entry = _entry(1, year=2026, month=3, day=1, title="With links")
        entry.links = [
            EntryLink(
                id=1,
                url="https://example.com",
                note="A link",
                created_utc="2026-03-18T00:00:00+00:00",
            )
        ]
        state = form_state_from_entry(entry)
        self.assertEqual(len(state.link_rows), 1)
        self.assertEqual(state.link_rows[0]["url"], "https://example.com")
        self.assertEqual(state.link_rows[0]["note"], "A link")

    def test_without_links_gets_empty_row(self) -> None:
        entry = _entry(1, year=2026, month=3, day=1, title="No links")
        state = form_state_from_entry(entry)
        self.assertEqual(len(state.link_rows), 1)
        self.assertEqual(state.link_rows[0], {"url": "", "note": ""})


class TestParseLinksJson(unittest.TestCase):
    def test_none_returns_empty(self) -> None:
        self.assertEqual(parse_links_json(None), [])

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(parse_links_json(""), [])

    def test_valid_json(self) -> None:
        import json

        data = [
            {
                "id": 1,
                "url": "https://example.com",
                "note": "A note",
                "created_utc": "2026-01-01T00:00:00+00:00",
            }
        ]
        result = parse_links_json(json.dumps(data))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].url, "https://example.com")
        self.assertEqual(result[0].note, "A note")

    def test_json_with_null_items(self) -> None:
        import json

        data = [
            None,
            {
                "id": 1,
                "url": "https://example.com",
                "note": "Valid",
                "created_utc": "2026-01-01T00:00:00+00:00",
            },
            None,
        ]
        result = parse_links_json(json.dumps(data))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].note, "Valid")


class TestHeatmapCounts(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.previous_db_path = os.environ.get("EVENTTRACKER_DB_PATH")
        os.environ["EVENTTRACKER_DB_PATH"] = self.db_path
        init_db()
        with connection_context() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO timeline_groups (id, name) VALUES (1, 'Test Group')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO timeline_groups (id, name) VALUES (2, 'Other Group')"
            )
            # Entry with specific day
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 1, 'A', '<p>A</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            # Another entry on the same day
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 1, 'B', '<p>B</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            # Entry without a day
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 6, NULL, 20250600, 1, 'C', '<p>C</p>', '2025-06-01T00:00:00+00:00', '2025-06-01T00:00:00+00:00')"
            )
            # Entry in a different group
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 2, 'D', '<p>D</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            # Entry in a different year
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2024, 1, 10, 20240110, 1, 'E', '<p>E</p>', '2024-01-10T00:00:00+00:00', '2024-01-10T00:00:00+00:00')"
            )
            conn.commit()

    def tearDown(self) -> None:
        if self.previous_db_path is None:
            os.environ.pop("EVENTTRACKER_DB_PATH", None)
        else:
            os.environ["EVENTTRACKER_DB_PATH"] = self.previous_db_path
        self.tmp.cleanup()

    def test_counts_entries_with_specific_days(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2025)
        self.assertEqual(result.counts.get("2025-03-15"), 3)  # 2 from group 1 + 1 from group 2
        self.assertEqual(result.year, 2025)

    def test_distributes_dayless_entries_across_month(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2025)
        # The dayless June entry should be distributed to some day in June
        june_keys = [k for k in result.counts if k.startswith("2025-06-")]
        self.assertEqual(sum(result.counts[k] for k in june_keys), 1)

    def test_filters_by_group_id(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2025, group_id=1)
        # Only group 1: 2 entries on Mar 15 + 1 dayless in June
        self.assertEqual(result.counts.get("2025-03-15"), 2)
        self.assertEqual(result.total, 3)

    def test_returns_years_available(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2025)
        self.assertIn(2024, result.years_available)
        self.assertIn(2025, result.years_available)

    def test_empty_year_returns_zero_total(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2020)
        self.assertEqual(result.total, 0)
        self.assertEqual(result.counts, {})
        self.assertIn(2024, result.years_available)

    def test_multiple_dayless_entries_distribute_evenly(self) -> None:
        with connection_context() as conn:
            for i in range(3):
                conn.execute(
                    "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                    "VALUES (2025, 9, NULL, 20250900, 1, ?, '<p>X</p>', '2025-09-01T00:00:00+00:00', '2025-09-01T00:00:00+00:00')",
                    (f"Sep{i}",),
                )
            conn.commit()
            result = get_heatmap_counts(conn, year=2025)
        sept_keys = [k for k in result.counts if k.startswith("2025-09-")]
        # 3 entries should be spread across 3 different days
        self.assertEqual(len(sept_keys), 3)
        self.assertEqual(sum(result.counts[k] for k in sept_keys), 3)
