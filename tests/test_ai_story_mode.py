from __future__ import annotations

from collections.abc import Coroutine
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
from types import SimpleNamespace
from typing import Any, TypeVar, cast
import unittest
from unittest.mock import AsyncMock, patch

from app.env import load_app_env
from app.models import Entry, GeneratedExecutiveDeck, TimelineStoryScope
from app.services.ai_story_mode import (
    CopilotChatStoryGenerator,
    GeneratedTimelineStory,
    OpenAIChatStoryGenerator,
    StoryGenerationConfigurationError,
    StoryGenerationError,
    _parse_deck_generation_response,
    _parse_generation_response,
    get_story_generator,
    get_story_generation_timeout_seconds,
    load_story_ai_provider,
)
from app.services.ai_generate import CopilotSettings, OpenAISettings


T = TypeVar("T")
_UNSET = object()


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    value: T | object = _UNSET
    error: BaseException | None = None

    def runner() -> None:
        nonlocal error, value
        import asyncio

        try:
            value = asyncio.run(coro)
        except BaseException as exc:
            error = exc

    thread = Thread(target=runner)
    thread.start()
    thread.join()

    if error is not None:
        raise error
    if value is _UNSET:
        raise AssertionError("Expected coroutine to produce a result.")
    return cast(T, value)


class _FakeCopilotSession:
    def __init__(self, response: object) -> None:
        self.response = response
        self.send_calls: list[dict[str, object]] = []
        self.closed = False
        self.timeouts: list[float | None] = []

    async def send_and_wait(
        self, options: dict[str, object], timeout: float | None = None
    ) -> object:
        self.send_calls.append(options)
        self.timeouts.append(timeout)
        return self.response

    async def close(self) -> None:
        self.closed = True


class _FakeCopilotClient:
    def __init__(self, response: object, **kwargs: object) -> None:
        self.response = response
        self.options = kwargs if kwargs else None
        self.entered = False
        self.exited = False
        self.session = _FakeCopilotSession(response)
        self.config: dict[str, object] | None = None

    async def __aenter__(self) -> _FakeCopilotClient:
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited = True

    async def create_session(self, config: dict[str, object]) -> _FakeCopilotSession:
        self.config = config
        return self.session


class TestStoryGeneratorSelection(unittest.TestCase):
    def tearDown(self) -> None:
        get_story_generator.cache_clear()
        load_app_env.cache_clear()

    def _patch_empty_env_file(self):
        temp_dir = tempfile.TemporaryDirectory()
        env_file = Path(temp_dir.name) / ".env"
        env_file.write_text("", encoding="utf-8")
        patcher = patch("app.env.DEFAULT_ENV_FILE", env_file)
        return temp_dir, patcher

    def test_get_story_generator_defaults_to_openai(self) -> None:
        temp_dir, env_file_patcher = self._patch_empty_env_file()
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_CHAT_MODEL_ID": "gpt-5",
            },
            clear=True,
        ):
            with env_file_patcher:
                with patch("app.services.ai_story_mode.AsyncOpenAI"):
                    load_app_env.cache_clear()
                    generator = get_story_generator()

        temp_dir.cleanup()

        self.assertIsInstance(generator, OpenAIChatStoryGenerator)

    def test_load_story_ai_provider_rejects_invalid_provider(self) -> None:
        temp_dir, env_file_patcher = self._patch_empty_env_file()
        with patch.dict(
            os.environ,
            {
                "EVENTTRACKER_AI_PROVIDER": "invalid",
            },
            clear=True,
        ):
            with env_file_patcher:
                load_app_env.cache_clear()
                with self.assertRaises(StoryGenerationConfigurationError):
                    load_story_ai_provider()

        temp_dir.cleanup()


class TestOpenAIChatStoryGenerator(unittest.TestCase):
    def test_generate_story_uses_bounded_chronological_entry_context(self) -> None:
        create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "title": "Recent Changes: Product Milestones",
                                    "sections": [
                                        {
                                            "heading": "Momentum increased",
                                            "body": "The team moved from validation into release work.",
                                            "citations": [
                                                {
                                                    "entry_id": 2,
                                                    "quote_text": "Validated the second milestone",
                                                    "note": "Shows the transition point",
                                                },
                                                {
                                                    "entry_id": 3,
                                                    "quote_text": "Shipped the latest milestone",
                                                    "note": "Confirms the newest change",
                                                },
                                            ],
                                        }
                                    ],
                                }
                            )
                        )
                    )
                ]
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        scope = TimelineStoryScope(scope_type="timeline", group_id=1)
        entries = [
            _entry(
                3,
                2026,
                3,
                18,
                "Latest milestone",
                "<p>Shipped the latest milestone after the review was complete.</p>",
            ),
            _entry(
                1,
                2024,
                11,
                2,
                "Earliest milestone",
                "<p>Earliest milestone with a very long body.</p>" + (" extra" * 200),
            ),
            _entry(
                2,
                2025,
                5,
                10,
                "Second milestone",
                "<p>Validated the second milestone and prepared the release.</p>",
            ),
        ]

        with patch("app.services.ai_story_mode.AsyncOpenAI", return_value=client):
            generator = OpenAIChatStoryGenerator(
                OpenAISettings(api_key="test-key", model_id="gpt-5")
            )
            story = _run_async(
                generator.generate_story(
                    scope,
                    "recent_changes",
                    entries,
                    max_entries=2,
                )
            )

        self.assertIsInstance(story, GeneratedTimelineStory)
        self.assertEqual(story.provider_name, "openai")
        self.assertEqual(story.format, "recent_changes")
        self.assertEqual(story.source_entry_count, 2)
        self.assertTrue(story.truncated_input)
        self.assertEqual(
            [section.heading for section in story.sections], ["Momentum increased"]
        )
        self.assertEqual(story.sections[0].citation_orders, [1, 2])
        self.assertEqual([citation.entry_id for citation in story.citations], [2, 3])

        await_args = create.await_args
        self.assertIsNotNone(await_args)
        assert await_args is not None
        prompt = await_args.kwargs["messages"][1]["content"]
        self.assertIn(
            "Detailed context is limited to the most recent scoped entries.",
            prompt,
        )
        self.assertIn("Older history summary (1 earlier scoped entry", prompt)
        self.assertIn("Earliest milestone", prompt)
        self.assertIn(
            "Detailed recent entries for citation and specifics (2 most recent scoped entries)",
            prompt,
        )
        self.assertIn("entry_id=2", prompt)
        self.assertIn("entry_id=3", prompt)
        self.assertNotIn("entry_id=1", prompt)
        self.assertNotIn("extra extra extra extra extra", prompt)
        self.assertNotIn("temperature", await_args.kwargs)

    def test_generate_executive_deck_uses_bounded_chronological_entry_context(
        self,
    ) -> None:
        create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "title": "Launch deck",
                                    "subtitle": "Recent movement",
                                    "slides": [
                                        {
                                            "slide_key": "launch-title",
                                            "headline": "Launch at a glance",
                                            "purpose": "title",
                                            "body_points": [
                                                "Launch readiness moved into delivery.",
                                                "The latest milestone confirmed momentum.",
                                            ],
                                            "callouts": [
                                                "Validated the second milestone.",
                                            ],
                                            "visuals": [{"kind": "pull_quote"}],
                                            "citations": [2, 3],
                                        }
                                    ],
                                }
                            )
                        )
                    )
                ]
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        scope = TimelineStoryScope(scope_type="timeline", group_id=1)
        entries = [
            _entry(
                3,
                2026,
                3,
                18,
                "Latest milestone",
                "<p>Shipped the latest milestone after the review was complete.</p>",
            ),
            _entry(
                1,
                2024,
                11,
                2,
                "Earliest milestone",
                "<p>Earliest milestone with a very long body.</p>" + (" extra" * 200),
            ),
            _entry(
                2,
                2025,
                5,
                10,
                "Second milestone",
                "<p>Validated the second milestone and prepared the release.</p>",
            ),
        ]

        with patch("app.services.ai_story_mode.AsyncOpenAI", return_value=client):
            generator = OpenAIChatStoryGenerator(
                OpenAISettings(api_key="test-key", model_id="gpt-5")
            )
            deck = _run_async(
                generator.generate_executive_deck(
                    scope,
                    entries,
                    max_entries=2,
                )
            )

        self.assertIsInstance(deck, GeneratedExecutiveDeck)
        self.assertEqual(deck.provider_name, "openai")
        self.assertEqual(deck.title, "Launch deck")
        self.assertEqual(deck.source_entry_count, 2)
        self.assertTrue(deck.truncated_input)
        self.assertEqual(deck.slides[0].visuals, ["pull_quote"])
        self.assertEqual(deck.slides[0].citations, [2, 3])

        await_args = create.await_args
        self.assertIsNotNone(await_args)
        assert await_args is not None
        prompt = await_args.kwargs["messages"][1]["content"]
        self.assertIn("Allowed slide purposes", prompt)
        self.assertIn(
            "Detailed context is limited to the most recent scoped entries.",
            prompt,
        )
        self.assertIn("Older history summary (1 earlier scoped entry", prompt)
        self.assertIn("Earliest milestone", prompt)
        self.assertIn(
            "Detailed recent entries for citation and specifics (2 most recent scoped entries)",
            prompt,
        )
        self.assertIn("entry_id=2", prompt)
        self.assertIn("entry_id=3", prompt)
        self.assertNotIn("entry_id=1", prompt)


class TestStoryGenerationParsing(unittest.TestCase):
    def test_parse_generation_response_rejects_unknown_citation_entry(self) -> None:
        with self.assertRaises(StoryGenerationError):
            _parse_generation_response(
                json.dumps(
                    {
                        "title": "Invalid story",
                        "sections": [
                            {
                                "heading": "Section",
                                "body": "Body",
                                "citations": [
                                    {"entry_id": 99, "quote_text": None, "note": None}
                                ],
                            }
                        ],
                    }
                ),
                story_format="executive_summary",
                allowed_entry_ids={1, 2},
            )

    def test_parse_generation_response_normalizes_fenced_json_and_deduplicates_citations(
        self,
    ) -> None:
        story = _parse_generation_response(
            "```json\n"
            + json.dumps(
                {
                    "title": "  Product  Arc  ",
                    "sections": [
                        {
                            "heading": "  Turning  Point ",
                            "body": "\ufeffFirst section body.\r\n",
                            "citations": [
                                {
                                    "entry_id": "2",
                                    "quote_text": "A useful quote",
                                    "note": None,
                                }
                            ],
                        },
                        {
                            "heading": "Outcome",
                            "body": "Second section body.",
                            "citations": [
                                {
                                    "entry_id": 2,
                                    "quote_text": None,
                                    "note": "Still relevant later",
                                }
                            ],
                        },
                    ],
                }
            )
            + "\n```",
            story_format="executive_summary",
            allowed_entry_ids={2},
        )

        self.assertEqual(story.title, "Product Arc")
        self.assertEqual(story.sections[0].heading, "Turning Point")
        self.assertEqual(story.sections[0].body, "First section body.")
        self.assertEqual(story.sections[0].citation_orders, [1])
        self.assertEqual(story.sections[1].citation_orders, [1])
        self.assertEqual(len(story.citations), 1)
        self.assertEqual(story.citations[0].entry_id, 2)
        self.assertEqual(story.citations[0].quote_text, "A useful quote")
        self.assertEqual(story.citations[0].note, "Still relevant later")

    def test_parse_deck_generation_response_drops_unknown_citation_entry(
        self,
    ) -> None:
        deck = _parse_deck_generation_response(
            json.dumps(
                {
                    "title": "Filtered deck",
                    "subtitle": None,
                    "slides": [
                        {
                            "slide_key": "filtered-slide",
                            "headline": "Filtered slide",
                            "purpose": "summary",
                            "body_points": ["Body"],
                            "callouts": ["Callout"],
                            "visuals": [{"kind": "kpi_strip"}],
                            "citations": [99],
                        }
                    ],
                }
            ),
            allowed_entry_ids={1, 2},
        )
        self.assertEqual(len(deck.slides), 1)
        self.assertEqual(deck.slides[0].citations, [])

    def test_parse_deck_generation_response_normalizes_order_and_visuals(self) -> None:
        deck = _parse_deck_generation_response(
            "```json\n"
            + json.dumps(
                {
                    "title": "  Launch   deck  ",
                    "subtitle": "  Executive  readout ",
                    "slides": [
                        {
                            "slide_key": "summary slide",
                            "headline": "Summary",
                            "purpose": "summary",
                            "body_points": ["Momentum increased."],
                            "callouts": ["Validation completed."],
                            "visuals": ["kpi_strip", {"kind": "pull_quote"}],
                            "citations": [2, "3", 2],
                        },
                        {
                            "slide_key": " title slide ",
                            "headline": "Launch at a glance",
                            "purpose": "title",
                            "body_points": ["Delivery is now underway."],
                            "callouts": ["Latest signals are positive."],
                            "visuals": [],
                            "citations": [3],
                        },
                        {
                            "slide_key": "wrap-up",
                            "headline": "Close",
                            "purpose": "close",
                            "body_points": ["Watch follow-through on execution."],
                            "callouts": ["Execution risk is lower than last quarter."],
                            "visuals": [{"kind": "phase_timeline"}],
                            "citations": [2],
                        },
                    ],
                }
            )
            + "\n```",
            allowed_entry_ids={2, 3},
        )

        self.assertEqual(deck.title, "Launch deck")
        self.assertEqual(deck.subtitle, "Executive readout")
        self.assertEqual([slide.purpose for slide in deck.slides], ["title", "summary", "close"])
        self.assertEqual(deck.slides[0].slide_key, "title-slide")
        self.assertEqual(deck.slides[1].visuals, ["kpi_strip", "pull_quote"])
        self.assertEqual(deck.slides[1].citations, [2, 3])


class TestCopilotChatStoryGenerator(unittest.TestCase):
    def test_generate_story_parses_copilot_response(self) -> None:
        fake_client = _FakeCopilotClient(
            response=SimpleNamespace(
                content=json.dumps(
                    {
                        "title": "Executive Summary: Launch",
                        "sections": [
                            {
                                "heading": "Overview",
                                "body": "The launch period moved from planning into delivery.",
                                "citations": [
                                    {
                                        "entry_id": 7,
                                        "quote_text": "Release prep completed",
                                        "note": "Supports the summary",
                                    }
                                ],
                            }
                        ],
                    }
                )
            )
        )
        scope = TimelineStoryScope(
            scope_type="search", query_text="launch", group_id=None
        )
        entries = [
            _entry(
                7,
                2026,
                3,
                12,
                "Release prep",
                "<p>Release prep completed and the team aligned on launch communications.</p>",
            )
        ]

        with (
            patch(
                "app.services.copilot_runtime.instantiate_copilot_client",
                return_value=fake_client,
            ),
            patch(
                "app.services.copilot_runtime.get_permission_handler",
                return_value="approve-all",
            ),
        ):
            generator = CopilotChatStoryGenerator(CopilotSettings(model_id="gpt-5"))
            story = _run_async(
                generator.generate_story(
                    scope,
                    "executive_summary",
                    entries,
                )
            )

        self.assertEqual(story.provider_name, "copilot")
        self.assertEqual(story.title, "Executive Summary: Launch")
        self.assertEqual(story.sections[0].citation_orders, [1])
        self.assertEqual(story.citations[0].entry_id, 7)
        self.assertEqual(
            fake_client.config,
            {
                "model": "gpt-5",
                "on_permission_request": "approve-all",
                "system_message": {
                    "mode": "append",
                    "content": (
                        "You write grounded timeline stories from structured entry context. Return JSON only "
                        'with this exact schema: {"title": string, "sections": [{"heading": string, '
                        '"body": string, "citations": [{"entry_id": number, "quote_text": string|null, '
                        '"note": string|null}]}]}. Do not wrap the JSON in markdown fences. '
                        "Each section body must be plain text, not HTML. Cite only entry_id values that "
                        "appear in the provided context. Keep the story factual, concise, and anchored in "
                        "the supplied entries. Do not invent events, dates, motives, or outcomes."
                    ),
                },
            },
        )
        self.assertTrue(fake_client.entered)
        self.assertTrue(fake_client.exited)
        self.assertTrue(fake_client.session.closed)
        prompt = cast(str, fake_client.session.send_calls[0]["prompt"])
        self.assertIn("Requested format: executive_summary", prompt)
        self.assertEqual(fake_client.session.timeouts[0], get_story_generation_timeout_seconds())

    def test_generate_executive_deck_parses_copilot_response(self) -> None:
        fake_client = _FakeCopilotClient(
            response=SimpleNamespace(
                content=json.dumps(
                    {
                        "title": "Executive deck",
                        "subtitle": None,
                        "slides": [
                            {
                                "slide_key": "deck-title",
                                "headline": "Launch at a glance",
                                "purpose": "title",
                                "body_points": [
                                    "The launch period moved from planning into delivery.",
                                ],
                                "callouts": [
                                    "Release prep completed.",
                                ],
                                "visuals": [{"kind": "pull_quote"}],
                                "citations": [7],
                            }
                        ],
                    }
                )
            )
        )
        scope = TimelineStoryScope(
            scope_type="search", query_text="launch", group_id=None
        )
        entries = [
            _entry(
                7,
                2026,
                3,
                12,
                "Release prep",
                "<p>Release prep completed and the team aligned on launch communications.</p>",
            )
        ]

        with (
            patch(
                "app.services.copilot_runtime.instantiate_copilot_client",
                return_value=fake_client,
            ),
            patch(
                "app.services.copilot_runtime.get_permission_handler",
                return_value="approve-all",
            ),
        ):
            deck = _run_async(
                CopilotChatStoryGenerator(
                    CopilotSettings(model_id="gpt-5")
                ).generate_executive_deck(
                    scope,
                    entries,
                )
            )

        self.assertEqual(deck.provider_name, "copilot")
        self.assertEqual(deck.title, "Executive deck")
        self.assertEqual(deck.slides[0].visuals, ["pull_quote"])
        self.assertEqual(deck.slides[0].citations, [7])
        self.assertEqual(fake_client.config["model"], "gpt-5")
        self.assertEqual(fake_client.config["on_permission_request"], "approve-all")
        system_content = fake_client.config["system_message"]["content"]
        self.assertIn("You write grounded executive presentation decks", system_content)
        self.assertIn('"slides"', system_content)


def _entry(
    entry_id: int,
    year: int,
    month: int,
    day: int | None,
    title: str,
    final_text: str,
) -> Entry:
    display_date = (
        f"{month}/{day or 1}/{year}" if day is not None else f"{month}/{year}"
    )
    return Entry(
        id=entry_id,
        event_year=year,
        event_month=month,
        event_day=day,
        sort_key=(year * 10000) + (month * 100) + (day or 0),
        group_id=1,
        group_name="Default",
        title=title,
        source_url=None,
        generated_text=None,
        final_text=final_text,
        created_utc="2026-03-19T12:00:00+00:00",
        updated_utc="2026-03-19T12:00:00+00:00",
        tags=[],
        links=[],
        display_date=display_date,
        preview_text="",
    )
