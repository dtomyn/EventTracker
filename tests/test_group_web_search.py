from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from threading import Thread
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.db import init_db
from app.services.ai_generate import CopilotSettings
from app.services.entries import (
    TimelineGroupValidationError,
    normalize_timeline_group_web_search_query,
)
from app.services.group_web_search import (
    GROUP_WEB_SEARCH_SYSTEM_PROMPT,
    _clear_group_web_search_cache,
    _build_broadened_search_prompt,
    _build_search_prompt,
    _select_diverse_group_web_search_items,
    _parse_group_web_search_response,
    get_group_web_search_request_timeout_ms,
    GroupWebSearchItem,
    search_group_web,
)


def _run_async(coro):
    result: dict[str, object] = {}

    def runner() -> None:
        import asyncio

        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - re-raised in caller.
            result["error"] = exc

    thread = Thread(target=runner)
    thread.start()
    thread.join()

    if "error" in result:
        raise result["error"]
    return result.get("value")


class _FakeCopilotSession:
    def __init__(
        self, response: object, emitted_events: list[object] | None = None
    ) -> None:
        self.response = response
        self.emitted_events = emitted_events or []
        self.closed = False
        self.send_calls: list[dict[str, object]] = []
        self.timeouts: list[float | None] = []
        self._handlers: list[object] = []

    async def send_and_wait(
        self, options: dict[str, object], timeout: float | None = None
    ) -> object:
        self.send_calls.append(options)
        self.timeouts.append(timeout)
        for event in self.emitted_events:
            for handler in list(self._handlers):
                handler(event)
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response

    def on(self, handler: object):
        self._handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    async def close(self) -> None:
        self.closed = True


class _FakeCopilotClient:
    def __init__(
        self, response: object, emitted_events: list[object] | None = None
    ) -> None:
        self.response = response
        self.entered = False
        self.exited = False
        self.config: dict[str, object] | None = None
        self.session = _FakeCopilotSession(response, emitted_events=emitted_events)

    async def __aenter__(self) -> _FakeCopilotClient:
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited = True

    async def create_session(self, config: dict[str, object]) -> _FakeCopilotSession:
        self.config = config
        return self.session


class TestTimelineGroupWebSearchQueryValidation(unittest.TestCase):
    def test_blank_query_normalizes_to_none(self) -> None:
        self.assertIsNone(normalize_timeline_group_web_search_query("   "))

    def test_overlong_query_raises_field_error(self) -> None:
        with self.assertRaises(TimelineGroupValidationError) as context:
            normalize_timeline_group_web_search_query("x" * 401)

        self.assertEqual(context.exception.field, "web_search_query")
        self.assertEqual(
            str(context.exception),
            "Web search query must be 400 characters or fewer.",
        )


class TestTimelineGroupSchemaMigration(unittest.TestCase):
    def test_init_db_adds_web_search_query_column_to_existing_groups_table(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "EventTracker-test.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                "CREATE TABLE timeline_groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE)"
            )
            connection.commit()
            connection.close()

            with patch.dict(
                os.environ, {"EVENTTRACKER_DB_PATH": str(db_path)}, clear=False
            ):
                init_db()

            migrated = sqlite3.connect(db_path)
            migrated.row_factory = sqlite3.Row
            columns = [
                row["name"]
                for row in migrated.execute(
                    "PRAGMA table_info(timeline_groups)"
                ).fetchall()
            ]
            migrated.close()

        self.assertIn("web_search_query", columns)

    def test_init_db_adds_is_default_column_and_marks_seeded_group_when_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "EventTracker-test.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                "CREATE TABLE timeline_groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, web_search_query TEXT NULL)"
            )
            connection.execute(
                "INSERT INTO timeline_groups(name, web_search_query) VALUES (?, ?)",
                ("Agentic Coding", "latest agentic coding model announcements"),
            )
            connection.commit()
            connection.close()

            with patch.dict(
                os.environ, {"EVENTTRACKER_DB_PATH": str(db_path)}, clear=False
            ):
                init_db()

            migrated = sqlite3.connect(db_path)
            migrated.row_factory = sqlite3.Row
            columns = [
                row["name"]
                for row in migrated.execute(
                    "PRAGMA table_info(timeline_groups)"
                ).fetchall()
            ]
            row = migrated.execute(
                "SELECT is_default FROM timeline_groups WHERE name = ?",
                ("Agentic Coding",),
            ).fetchone()
            migrated.close()

        self.assertIn("is_default", columns)
        self.assertIsNotNone(row)
        self.assertEqual(int(row["is_default"]), 1)


class TestGroupWebSearchParsing(unittest.TestCase):
    def test_parse_response_filters_invalid_and_duplicate_urls(self) -> None:
        response = _parse_group_web_search_response(
            json.dumps(
                {
                    "query": "latest agentic coding model announcements",
                    "items": [
                        {
                            "title": "First result",
                            "url": "https://example.com/one",
                            "snippet": "First snippet.",
                            "source": "Example One",
                        },
                        {
                            "title": "Duplicate result",
                            "url": "https://example.com/one",
                            "snippet": "Duplicate snippet.",
                            "source": "Example One",
                        },
                        {
                            "title": "Invalid URL",
                            "url": "ftp://example.com/two",
                            "snippet": "Ignore this.",
                            "source": "Example Two",
                        },
                        {
                            "title": "Second result",
                            "url": "https://example.com/two",
                            "snippet": "Second snippet.",
                            "source": "Example Two",
                        },
                    ],
                }
            ),
            "latest agentic coding model announcements",
        )

        self.assertEqual(response.query, "latest agentic coding model announcements")
        self.assertEqual(
            [item.title for item in response.items], ["First result", "Second result"]
        )
        self.assertEqual(
            [item.url for item in response.items],
            ["https://example.com/one", "https://example.com/two"],
        )

    def test_parse_response_keeps_valid_article_dates_and_discards_invalid_ones(
        self,
    ) -> None:
        response = _parse_group_web_search_response(
            json.dumps(
                {
                    "query": "AI developer tools launches and benchmarks",
                    "items": [
                        {
                            "title": "Dated result",
                            "url": "https://example.com/dated",
                            "snippet": "Has a valid date.",
                            "source": "Example One",
                            "article_date": "2026-03-17",
                        },
                        {
                            "title": "Invalid date result",
                            "url": "https://example.com/invalid",
                            "snippet": "Has a bad date.",
                            "source": "Example Two",
                            "article_date": "March 17, 2026",
                        },
                    ],
                }
            ),
            "AI developer tools launches and benchmarks",
        )

        self.assertEqual(response.items[0].article_date, "2026-03-17")
        self.assertIsNone(response.items[1].article_date)

    def test_diversity_selection_prefers_named_company_coverage(self) -> None:
        items = [
            GroupWebSearchItem(
                title="AWS result one",
                url="https://aws.amazon.com/one",
                snippet="Amazon launched a new agentic coding feature.",
                source="aws.amazon.com",
            ),
            GroupWebSearchItem(
                title="AWS result two",
                url="https://aws.amazon.com/two",
                snippet="Another Amazon update.",
                source="aws.amazon.com",
            ),
            GroupWebSearchItem(
                title="Microsoft result",
                url="https://blogs.microsoft.com/post",
                snippet="Microsoft announced an agentic coding workflow.",
                source="blogs.microsoft.com",
            ),
            GroupWebSearchItem(
                title="Anthropic result",
                url="https://www.anthropic.com/news/post",
                snippet="Anthropic shipped a new coding model.",
                source="Anthropic",
            ),
        ]

        ordered = _select_diverse_group_web_search_items(
            items,
            query="Agentic coding announcements made by Amazon, Microsoft, and Anthropic",
        )

        self.assertEqual(
            [item.title for item in ordered[:3]],
            ["AWS result one", "Microsoft result", "Anthropic result"],
        )

    def test_build_search_prompt_mentions_named_organizations_when_present(
        self,
    ) -> None:
        prompt = _build_search_prompt(
            "Agentic coding announcements made by Amazon, Microsoft, Google, Anthropic and NVIDIA"
        )

        self.assertIn("prefer coverage across those organizations", prompt)
        self.assertIn("amazon, microsoft, google, anthropic, nvidia", prompt)


class TestGroupWebSearchService(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_group_web_search_cache()

    def test_search_group_web_uses_copilot_session_and_parses_items(self) -> None:
        fake_client = _FakeCopilotClient(
            response=SimpleNamespace(
                content=json.dumps(
                    {
                        "query": "AI developer tools launches and benchmarks",
                        "items": [
                            {
                                "title": "Benchmark roundup",
                                "url": "https://example.com/benchmarks",
                                "snippet": "Recent benchmark updates.",
                                "source": "Example News",
                                "article_date": "2026-03-17",
                            }
                        ],
                    }
                )
            )
        )

        with (
            patch(
                "app.services.group_web_search.load_ai_provider", return_value="copilot"
            ),
            patch(
                "app.services.group_web_search.load_copilot_settings",
                return_value=CopilotSettings(model_id="gpt-5"),
            ),
            patch(
                "app.services.group_web_search._instantiate_copilot_client",
                return_value=fake_client,
            ),
            patch(
                "app.services.group_web_search._resolve_copilot_permission_handler",
                return_value="approve-all",
            ),
        ):
            response = _run_async(
                search_group_web("AI developer tools launches and benchmarks")
            )

        self.assertEqual(response.query, "AI developer tools launches and benchmarks")
        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].title, "Benchmark roundup")
        self.assertEqual(response.items[0].article_date, "2026-03-17")
        self.assertEqual(
            fake_client.config,
            {
                "model": "gpt-5",
                "reasoning_effort": "low",
                "on_permission_request": "approve-all",
                "system_message": {
                    "mode": "append",
                    "content": GROUP_WEB_SEARCH_SYSTEM_PROMPT,
                },
            },
        )
        self.assertEqual(
            fake_client.session.send_calls[0]["prompt"],
            _build_search_prompt("AI developer tools launches and benchmarks"),
        )
        self.assertEqual(fake_client.session.timeouts[0], 60.0)
        self.assertTrue(fake_client.entered)
        self.assertTrue(fake_client.exited)
        self.assertTrue(fake_client.session.closed)

    def test_search_group_web_reuses_cached_response(self) -> None:
        first_client = _FakeCopilotClient(
            response=SimpleNamespace(
                content=json.dumps(
                    {
                        "query": "AI developer tools launches and benchmarks",
                        "items": [
                            {
                                "title": "Benchmark roundup",
                                "url": "https://example.com/benchmarks",
                                "snippet": "Recent benchmark updates.",
                                "source": "Example News",
                            },
                            {
                                "title": "Launch roundup",
                                "url": "https://example.com/launches",
                                "snippet": "Recent launch updates.",
                                "source": "Example Launches",
                            },
                            {
                                "title": "Model roundup",
                                "url": "https://example.com/models",
                                "snippet": "Recent model updates.",
                                "source": "Example Models",
                            },
                        ],
                    }
                )
            )
        )

        with (
            patch(
                "app.services.group_web_search.load_ai_provider", return_value="copilot"
            ),
            patch(
                "app.services.group_web_search.load_copilot_settings",
                return_value=CopilotSettings(model_id="gpt-5"),
            ),
            patch(
                "app.services.group_web_search._instantiate_copilot_client",
                return_value=first_client,
            ) as instantiate_client,
            patch(
                "app.services.group_web_search._resolve_copilot_permission_handler",
                return_value="approve-all",
            ),
            patch.dict(
                os.environ,
                {"EVENTTRACKER_GROUP_WEB_SEARCH_CACHE_TTL_SECONDS": "300"},
                clear=False,
            ),
        ):
            first_response = _run_async(
                search_group_web("AI developer tools launches and benchmarks")
            )
            second_response = _run_async(
                search_group_web("AI developer tools launches and benchmarks")
            )

        self.assertEqual(first_response, second_response)
        self.assertEqual(instantiate_client.call_count, 1)
        self.assertEqual(len(first_client.session.send_calls), 1)

    def test_search_group_web_force_refresh_bypasses_cached_response(
        self,
    ) -> None:
        first_client = _FakeCopilotClient(
            response=SimpleNamespace(
                content=json.dumps(
                    {
                        "query": "AI developer tools launches and benchmarks",
                        "items": [
                            {
                                "title": "Cached result",
                                "url": "https://example.com/cached",
                                "snippet": "Original cached item.",
                                "source": "Example News",
                            },
                            {
                                "title": "Second cached result",
                                "url": "https://example.com/cached-two",
                                "snippet": "Another original item.",
                                "source": "Example Two",
                            },
                            {
                                "title": "Third cached result",
                                "url": "https://example.com/cached-three",
                                "snippet": "A third original item.",
                                "source": "Example Three",
                            },
                        ],
                    }
                )
            )
        )
        refreshed_client = _FakeCopilotClient(
            response=SimpleNamespace(
                content=json.dumps(
                    {
                        "query": "AI developer tools launches and benchmarks",
                        "items": [
                            {
                                "title": "Refreshed result",
                                "url": "https://example.com/refreshed",
                                "snippet": "Fresh item.",
                                "source": "Example Refreshed",
                            },
                            {
                                "title": "Second refreshed result",
                                "url": "https://example.com/refreshed-two",
                                "snippet": "Another fresh item.",
                                "source": "Example Refreshed Two",
                            },
                            {
                                "title": "Third refreshed result",
                                "url": "https://example.com/refreshed-three",
                                "snippet": "A third fresh item.",
                                "source": "Example Refreshed Three",
                            },
                        ],
                    }
                )
            )
        )

        with (
            patch(
                "app.services.group_web_search.load_ai_provider", return_value="copilot"
            ),
            patch(
                "app.services.group_web_search.load_copilot_settings",
                return_value=CopilotSettings(model_id="gpt-5"),
            ),
            patch(
                "app.services.group_web_search._instantiate_copilot_client",
                side_effect=[first_client, refreshed_client],
            ) as instantiate_client,
            patch(
                "app.services.group_web_search._resolve_copilot_permission_handler",
                return_value="approve-all",
            ),
            patch.dict(
                os.environ,
                {"EVENTTRACKER_GROUP_WEB_SEARCH_CACHE_TTL_SECONDS": "300"},
                clear=False,
            ),
        ):
            first_response = _run_async(
                search_group_web("AI developer tools launches and benchmarks")
            )
            refreshed_response = _run_async(
                search_group_web(
                    "AI developer tools launches and benchmarks",
                    force_refresh=True,
                )
            )

        self.assertEqual(first_response.items[0].title, "Cached result")
        self.assertEqual(refreshed_response.items[0].title, "Refreshed result")
        self.assertEqual(instantiate_client.call_count, 2)

    def test_search_group_web_uses_env_configured_timeouts(self) -> None:
        fake_client = _FakeCopilotClient(
            response=[
                SimpleNamespace(
                    content=json.dumps(
                        {
                            "query": "AI developer tools launches and benchmarks",
                            "items": [
                                {
                                    "title": "Single result",
                                    "url": "https://example.com/one",
                                    "snippet": "Only one item.",
                                    "source": "Example One",
                                }
                            ],
                        }
                    )
                ),
                SimpleNamespace(
                    content=json.dumps(
                        {
                            "query": "AI developer tools launches and benchmarks",
                            "items": [
                                {
                                    "title": "Second result",
                                    "url": "https://example.com/two",
                                    "snippet": "Second item.",
                                    "source": "Example Two",
                                },
                                {
                                    "title": "Third result",
                                    "url": "https://example.com/three",
                                    "snippet": "Third item.",
                                    "source": "Example Three",
                                },
                            ],
                        }
                    )
                ),
            ]
        )

        with (
            patch(
                "app.services.group_web_search.load_ai_provider", return_value="copilot"
            ),
            patch(
                "app.services.group_web_search.load_copilot_settings",
                return_value=CopilotSettings(model_id="gpt-5"),
            ),
            patch(
                "app.services.group_web_search._instantiate_copilot_client",
                return_value=fake_client,
            ),
            patch(
                "app.services.group_web_search._resolve_copilot_permission_handler",
                return_value="approve-all",
            ),
            patch.dict(
                os.environ,
                {
                    "EVENTTRACKER_GROUP_WEB_SEARCH_TIMEOUT_SECONDS": "90",
                    "EVENTTRACKER_GROUP_WEB_SEARCH_BROADENED_TIMEOUT_SECONDS": "75",
                    "EVENTTRACKER_GROUP_WEB_SEARCH_CACHE_TTL_SECONDS": "0",
                },
                clear=False,
            ),
        ):
            _run_async(search_group_web("AI developer tools launches and benchmarks"))

        self.assertEqual(fake_client.session.timeouts[0], 90.0)
        self.assertEqual(fake_client.session.timeouts[1], 75.0)

    def test_search_group_web_emits_raw_copilot_events(self) -> None:
        fake_client = _FakeCopilotClient(
            response=SimpleNamespace(
                content=json.dumps(
                    {
                        "query": "AI developer tools launches and benchmarks",
                        "items": [
                            {
                                "title": "Benchmark roundup",
                                "url": "https://example.com/benchmarks",
                                "snippet": "Recent benchmark updates.",
                                "source": "Example News",
                            }
                        ],
                    }
                )
            ),
            emitted_events=[
                SimpleNamespace(
                    type="assistant.reasoning_delta",
                    data=SimpleNamespace(delta_content="thinking..."),
                ),
                SimpleNamespace(
                    type="tool.execution_progress",
                    data=SimpleNamespace(
                        tool_name="web_search",
                        progress_message="searching authoritative sites",
                    ),
                ),
            ],
        )
        emitted_payloads: list[dict[str, object]] = []

        with (
            patch(
                "app.services.group_web_search.load_ai_provider", return_value="copilot"
            ),
            patch(
                "app.services.group_web_search.load_copilot_settings",
                return_value=CopilotSettings(model_id="gpt-5"),
            ),
            patch(
                "app.services.group_web_search._instantiate_copilot_client",
                return_value=fake_client,
            ),
            patch(
                "app.services.group_web_search._resolve_copilot_permission_handler",
                return_value="approve-all",
            ),
        ):
            _run_async(
                search_group_web(
                    "AI developer tools launches and benchmarks",
                    event_sink=emitted_payloads.append,
                )
            )

        self.assertTrue(
            any(
                payload.get("kind") == "copilot_event"
                and payload.get("eventType") == "assistant.reasoning_delta"
                and payload.get("message") == "thinking..."
                for payload in emitted_payloads
            )
        )
        self.assertTrue(
            any(
                payload.get("kind") == "copilot_event"
                and payload.get("eventType") == "tool.execution_progress"
                and payload.get("message") == "searching authoritative sites"
                for payload in emitted_payloads
            )
        )

    def test_search_group_web_broadens_when_first_pass_is_sparse(self) -> None:
        fake_client = _FakeCopilotClient(
            response=[
                SimpleNamespace(
                    content=json.dumps(
                        {
                            "query": "AI developer tools launches and benchmarks",
                            "items": [
                                {
                                    "title": "Single result",
                                    "url": "https://example.com/one",
                                    "snippet": "Only one item.",
                                    "source": "Example One",
                                }
                            ],
                        }
                    )
                ),
                SimpleNamespace(
                    content=json.dumps(
                        {
                            "query": "AI developer tools launches and benchmarks",
                            "items": [
                                {
                                    "title": "Second result",
                                    "url": "https://example.com/two",
                                    "snippet": "Second item.",
                                    "source": "Example Two",
                                },
                                {
                                    "title": "Third result",
                                    "url": "https://example.com/three",
                                    "snippet": "Third item.",
                                    "source": "Example Three",
                                },
                            ],
                        }
                    )
                ),
            ]
        )

        with (
            patch(
                "app.services.group_web_search.load_ai_provider", return_value="copilot"
            ),
            patch(
                "app.services.group_web_search.load_copilot_settings",
                return_value=CopilotSettings(model_id="gpt-5"),
            ),
            patch(
                "app.services.group_web_search._instantiate_copilot_client",
                return_value=fake_client,
            ),
            patch(
                "app.services.group_web_search._resolve_copilot_permission_handler",
                return_value="approve-all",
            ),
            patch.dict(
                os.environ,
                {"EVENTTRACKER_GROUP_WEB_SEARCH_CACHE_TTL_SECONDS": "0"},
                clear=False,
            ),
        ):
            response = _run_async(
                search_group_web("AI developer tools launches and benchmarks")
            )

        self.assertEqual(len(response.items), 3)
        self.assertEqual(
            [item.title for item in response.items],
            ["Single result", "Second result", "Third result"],
        )
        self.assertEqual(len(fake_client.session.send_calls), 2)
        self.assertEqual(
            fake_client.session.send_calls[1]["prompt"],
            _build_broadened_search_prompt(
                "AI developer tools launches and benchmarks"
            ),
        )
        self.assertEqual(fake_client.session.timeouts[1], 45.0)


class TestGroupWebSearchUiTimeoutConfiguration(unittest.TestCase):
    def test_request_timeout_defaults_to_backend_timeout_with_buffer(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EVENTTRACKER_GROUP_WEB_SEARCH_TIMEOUT_SECONDS", None)
            os.environ.pop(
                "EVENTTRACKER_GROUP_WEB_SEARCH_BROADENED_TIMEOUT_SECONDS", None
            )
            os.environ.pop("EVENTTRACKER_GROUP_WEB_SEARCH_REQUEST_TIMEOUT_MS", None)

            self.assertEqual(get_group_web_search_request_timeout_ms(), 65000)

    def test_request_timeout_uses_explicit_env_override(self) -> None:
        with patch.dict(
            os.environ,
            {"EVENTTRACKER_GROUP_WEB_SEARCH_REQUEST_TIMEOUT_MS": "120000"},
            clear=False,
        ):
            self.assertEqual(get_group_web_search_request_timeout_ms(), 120000)
