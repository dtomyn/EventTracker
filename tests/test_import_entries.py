from __future__ import annotations

import unittest

from scripts.import_entries import (
    parse_date_and_title,
    parse_entries_document,
    parse_entries_export,
)


class TestImportEntries(unittest.TestCase):
    def test_parse_heading_with_day(self) -> None:
        year, month, day, title = parse_date_and_title(
            "Nov 30, 2022: OpenAI releases ChatGPT"
        )

        self.assertEqual((year, month, day), (2022, 11, 30))
        self.assertEqual(title, "OpenAI releases ChatGPT")

    def test_parse_heading_without_day_and_with_suffix(self) -> None:
        year, month, day, title = parse_date_and_title(
            "Dec 2024 (AWS re:Invent): Amazon unveils Nova & Bedrock platform"
        )

        self.assertEqual((year, month, day), (2024, 12, None))
        self.assertEqual(title, "Amazon unveils Nova & Bedrock platform")

    def test_parse_heading_strips_markdown_bold_markers(self) -> None:
        year, month, day, title = parse_date_and_title(
            "Mar 9, 2026: Microsoft’s **Copilot Cowork brings agentic AI to Office apps**"
        )

        self.assertEqual((year, month, day), (2026, 3, 9))
        self.assertEqual(
            title, "Microsoft’s Copilot Cowork brings agentic AI to Office apps"
        )

    def test_parse_entries_document_preserves_paragraph_html(self) -> None:
        parsed_entries = parse_entries_document(
            """
            <li>
                <h4><b>Mar 2023: Anthropic's Claude (beta)</b></h4>
                <p>AI startup <b>Anthropic</b> unveils <i>Claude</i>.</p>
            </li>
            """
        )

        self.assertEqual(len(parsed_entries), 1)
        payload = parsed_entries[0].payload
        self.assertEqual(
            (payload.event_year, payload.event_month, payload.event_day),
            (2023, 3, None),
        )
        self.assertEqual(payload.title, "Anthropic's Claude (beta)")
        self.assertEqual(
            payload.final_text,
            "<p>AI startup <b>Anthropic</b> unveils <i>Claude</i>.</p>",
        )

    def test_parse_entries_export_maps_old_json_into_default_group(self) -> None:
        parsed_entries = parse_entries_export(
            """
            {
                "count": 1,
                "entries": [
                    {
                        "id": 37,
                        "event_year": 2026,
                        "event_month": 3,
                        "event_day": 10,
                        "sort_key": 20260310,
                        "title": "Gemini Embedding 2",
                        "source_url": "https://example.com/gemini",
                        "generated_text": "<p>Generated.</p>",
                        "final_text": "<p>Final.</p>",
                        "created_utc": "2026-03-17T00:20:06+00:00",
                        "updated_utc": "2026-03-17T00:20:06+00:00",
                        "tags": ["embeddings", "multimodal"],
                        "display_date": "March 10, 2026"
                    }
                ]
            }
            """
        )

        self.assertEqual(len(parsed_entries), 1)
        parsed_entry = parsed_entries[0]
        self.assertEqual(parsed_entry.payload.group_id, 1)
        self.assertEqual(parsed_entry.payload.title, "Gemini Embedding 2")
        self.assertEqual(parsed_entry.payload.source_url, "https://example.com/gemini")
        self.assertEqual(parsed_entry.payload.tags, ["embeddings", "multimodal"])
        self.assertEqual(parsed_entry.created_utc, "2026-03-17T00:20:06+00:00")
        self.assertEqual(parsed_entry.updated_utc, "2026-03-17T00:20:06+00:00")


if __name__ == "__main__":
    unittest.main()
