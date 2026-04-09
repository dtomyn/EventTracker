from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db import connection_context, init_db
from app.main import app
from app.models import Entry, SearchResult
from app.schemas import EntryPayload
from app.services.entries import create_timeline_group, save_entry
from app.services.event_chat import (
    EventChatCitation,
    EventChatConfigurationError,
    build_event_chat_citations,
    build_event_chat_prompt,
    get_event_chat_generator,
    retrieve_event_chat_citations,
    stream_event_chat_answer,
    stream_event_chat_events,
)
from tests.csrf_helpers import csrf_data


class _FakeEventChatGenerator:
    provider_name = "copilot"

    def __init__(
        self,
        *,
        chunks: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.chunks = chunks or []
        self.error = error
        self.calls: list[tuple[str, list[EventChatCitation]]] = []

    async def stream_answer(self, question: str, citations: list[EventChatCitation]):
        self.calls.append((question, list(citations)))
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk


async def _collect_events(async_events):
    events = []
    async for event in async_events:
        events.append(event)
    return events


def _parse_sse_events(raw_body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    event_name = "message"
    data_lines: list[str] = []

    for line in raw_body.splitlines():
        if not line.strip():
            if data_lines:
                events.append((event_name, json.loads("\n".join(data_lines))))
            event_name = "message"
            data_lines = []
            continue

        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())

    if data_lines:
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


class _EventChatTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_env = {
            key: os.environ.get(key)
            for key in (
                "EVENTTRACKER_DB_PATH",
                "EVENTTRACKER_AI_PROVIDER",
                "TESTING",
            )
        }
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "EventTracker-test.db"
        )
        os.environ["EVENTTRACKER_AI_PROVIDER"] = "copilot"
        os.environ["TESTING"] = "1"
        init_db()
        get_event_chat_generator.cache_clear()

    def tearDown(self) -> None:
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_event_chat_generator.cache_clear()
        self.temp_dir.cleanup()

    def _create_entry(
        self,
        *,
        title: str,
        final_text: str,
        group_id: int = 1,
        tags: list[str] | None = None,
        year: int = 2026,
        month: int = 3,
        day: int | None = 18,
    ) -> int:
        with connection_context() as connection:
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
                    tags=tags or [],
                    links=[],
                ),
            )


class TestEventChatService(_EventChatTestCase):
    def test_retrieve_event_chat_citations_respects_group_scope(self) -> None:
        with connection_context() as connection:
            other_group = create_timeline_group(connection, "Other Group")

        selected_entry_id = self._create_entry(
            title="Security review",
            final_text="<p>Security incident review completed for the release candidate.</p>",
            tags=["security", "release"],
        )
        self._create_entry(
            title="Outside scope",
            final_text="<p>Security incident follow-up happened in another group.</p>",
            group_id=other_group.id,
            tags=["security"],
        )

        with connection_context() as connection:
            citations = retrieve_event_chat_citations(
                connection,
                "security incident",
                group_id=1,
            )

        self.assertEqual([citation.entry_id for citation in citations], [selected_entry_id])
        self.assertEqual(citations[0].group_name, "Agentic Coding")
        self.assertCountEqual(citations[0].tags, ["release", "security"])

    def test_build_event_chat_prompt_includes_grounding_rules_and_context(self) -> None:
        citation = EventChatCitation(
            entry_id=12,
            title="Security review",
            display_date="March 18, 2026",
            group_name="Agentic Coding",
            tags=["release", "security"],
            preview_text="Security incident review completed for the release candidate.",
            rank=1.0,
        )

        prompt = build_event_chat_prompt(
            "What changed in security?",
            [citation],
        )

        self.assertIn("Question: What changed in security?", prompt)
        self.assertIn("Do not use outside knowledge", prompt)
        self.assertIn("Entry ID: 12", prompt)
        self.assertIn("Tags: release, security", prompt)
        self.assertIn("Preview: Security incident review completed", prompt)

    def test_build_event_chat_citations_strips_markup_from_snippets(self) -> None:
        entry = Entry(
            id=7,
            event_year=2026,
            event_month=3,
            event_day=18,
            sort_key=20260318,
            group_id=1,
            group_name="Agentic Coding",
            title="Release check",
            source_url=None,
            generated_text=None,
            final_text="<p>Release verification is complete.</p>",
            created_utc="2026-03-18T00:00:00+00:00",
            updated_utc="2026-03-18T00:00:00+00:00",
            tags=["release"],
            display_date="March 18, 2026",
            preview_text="",
        )
        citation = build_event_chat_citations(
            [
                SearchResult(
                    entry=entry,
                    snippet="<mark>Release</mark> verification is complete.",
                    rank=0.9,
                )
            ]
        )

        self.assertEqual(citation[0].preview_text, "Release verification is complete.")

    def test_stream_event_chat_answer_maps_configuration_failures_to_error_events(
        self,
    ) -> None:
        fake_generator = _FakeEventChatGenerator(
            error=EventChatConfigurationError("Copilot is unavailable."),
        )
        citation = EventChatCitation(
            entry_id=9,
            title="Release note",
            display_date="March 18, 2026",
            group_name="Agentic Coding",
            tags=[],
            preview_text="Release note text.",
            rank=1.0,
        )

        with patch(
            "app.services.event_chat.get_event_chat_generator",
            return_value=fake_generator,
        ):
            events = asyncio.run(
                _collect_events(
                    stream_event_chat_answer("What happened?", [citation])
                )
            )

        self.assertEqual(events[0]["kind"], "error")
        self.assertEqual(events[0]["message"], "Copilot is unavailable.")
        self.assertEqual(events[1], {"kind": "complete", "ok": False})

    def test_stream_event_chat_events_returns_no_results_without_invoking_provider(
        self,
    ) -> None:
        fake_generator = _FakeEventChatGenerator(chunks=["Should not run"])

        with connection_context() as connection:
            with patch(
                "app.services.event_chat.get_event_chat_generator",
                return_value=fake_generator,
            ):
                events = asyncio.run(
                    _collect_events(
                        stream_event_chat_events(connection, "missing topic")
                    )
                )

        self.assertEqual(events[0]["kind"], "answer_chunk")
        self.assertIn("couldn't find relevant stored events", events[0]["text"].lower())
        self.assertEqual(events[1], {"kind": "citations", "items": []})
        self.assertEqual(events[2], {"kind": "complete", "ok": True})
        self.assertEqual(fake_generator.calls, [])


class TestEventChatRoutes(_EventChatTestCase):
    def test_chat_page_renders_form_and_group_selector(self) -> None:
        with connection_context() as connection:
            create_timeline_group(connection, "Platform")

        with TestClient(app) as client:
            response = client.get("/chat")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Event Chat", response.text)
        self.assertIn('action="/chat/query"', response.text)
        self.assertIn("All groups", response.text)
        self.assertIn("Platform", response.text)

    def test_chat_query_streams_answer_and_citations_for_selected_group(self) -> None:
        with connection_context() as connection:
            other_group = create_timeline_group(connection, "Platform")

        selected_entry_id = self._create_entry(
            title="Release security review",
            final_text="<p>Security review completed before the release milestone.</p>",
            tags=["release", "security"],
        )
        self._create_entry(
            title="Platform security update",
            final_text="<p>Security review completed in the platform group.</p>",
            group_id=other_group.id,
            tags=["security"],
        )
        fake_generator = _FakeEventChatGenerator(
            chunks=["The main shift was a release security review ", "[Entry 1]."],
        )

        with patch(
            "app.services.event_chat.get_event_chat_generator",
            return_value=fake_generator,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat/query",
                    data={
                        "question": "What changed in security?",
                        "group_id": "1",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/event-stream; charset=utf-8")
        parsed_events = _parse_sse_events(response.text)
        self.assertEqual([name for name, _payload in parsed_events[:2]], ["answer_chunk", "answer_chunk"])
        citations_payload = cast(
            dict[str, object],
            next(
            payload for name, payload in parsed_events if name == "citations"
            ),
        )
        citation_items = cast(list[dict[str, object]], citations_payload["items"])
        self.assertEqual(
            [item["entry_id"] for item in citation_items],
            [selected_entry_id],
        )
        self.assertEqual(citation_items[0]["url"], f"/entries/{selected_entry_id}/view")
        self.assertEqual(parsed_events[-1], ("complete", {"ok": True}))
        self.assertEqual(len(fake_generator.calls), 1)
        self.assertEqual(fake_generator.calls[0][0], "What changed in security?")
        self.assertEqual(
            [citation.entry_id for citation in fake_generator.calls[0][1]],
            [selected_entry_id],
        )

    def test_chat_query_accepts_question_with_csrf_enabled(self) -> None:
        previous_testing = os.environ.pop("TESTING", None)
        self._create_entry(
            title="Security review",
            final_text="<p>Security review completed for the release milestone.</p>",
            tags=["release", "security"],
        )
        fake_generator = _FakeEventChatGenerator(
            chunks=["The retrieved event was a release security review [Entry 1]."]
        )

        try:
            with patch(
                "app.services.event_chat.get_event_chat_generator",
                return_value=fake_generator,
            ):
                with TestClient(app) as client:
                    response = client.post(
                        "/chat/query",
                        data=csrf_data(
                            client,
                            {
                                "question": "What changed in security?",
                                "group_id": "1",
                            },
                        ),
                    )
        finally:
            if previous_testing is not None:
                os.environ["TESTING"] = previous_testing

        self.assertEqual(response.status_code, 200)
        parsed_events = _parse_sse_events(response.text)
        self.assertEqual(parsed_events[0][0], "answer_chunk")
        self.assertEqual(parsed_events[-1], ("complete", {"ok": True}))

    def test_chat_query_returns_error_event_for_blank_question(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/chat/query",
                data={"question": "   ", "group_id": "all"},
            )

        self.assertEqual(response.status_code, 400)
        parsed_events = _parse_sse_events(response.text)
        self.assertEqual(
            parsed_events[0],
            ("error", {"message": "Enter a question to ask about your events."}),
        )
        self.assertEqual(parsed_events[1], ("complete", {"ok": False}))