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
from app.services import copilot_runtime
from app.services.ai_generate import (
    CopilotChatDraftGenerator,
    CopilotSettings,
    DraftGenerationConfigurationError,
    DraftGenerationError,
    OpenAIChatDraftGenerator,
    OpenAISettings,
    _build_user_prompt,
    _parse_generation_response,
    get_draft_generator,
    load_ai_provider,
)


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
        except BaseException as exc:  # pragma: no cover - re-raised in caller.
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


class _FakeDisconnectOnlySession:
    def __init__(self) -> None:
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True


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


class _FakeStartStopClient:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def create_session(self, config: dict[str, object]) -> _FakeCopilotSession:
        return _FakeCopilotSession(response=config)


class TestDraftGeneratorSelection(unittest.TestCase):
    def tearDown(self) -> None:
        get_draft_generator.cache_clear()
        load_app_env.cache_clear()

    def _patch_empty_env_file(self):
        temp_dir = tempfile.TemporaryDirectory()
        env_file = Path(temp_dir.name) / ".env"
        env_file.write_text("", encoding="utf-8")
        patcher = patch("app.env.DEFAULT_ENV_FILE", env_file)
        return temp_dir, patcher

    def test_get_draft_generator_defaults_to_openai(self) -> None:
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
                with patch("app.services.ai_generate.AsyncOpenAI"):
                    load_app_env.cache_clear()
                    generator = get_draft_generator()

        temp_dir.cleanup()

        self.assertIsInstance(generator, OpenAIChatDraftGenerator)

    def test_get_draft_generator_returns_copilot_when_configured(self) -> None:
        temp_dir, env_file_patcher = self._patch_empty_env_file()
        with patch.dict(
            os.environ,
            {
                "EVENTTRACKER_AI_PROVIDER": "copilot",
            },
            clear=True,
        ):
            with env_file_patcher:
                load_app_env.cache_clear()
                generator = get_draft_generator()

        temp_dir.cleanup()

        self.assertIsInstance(generator, CopilotChatDraftGenerator)

    def test_get_draft_generator_rejects_invalid_provider(self) -> None:
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
                with self.assertRaises(DraftGenerationConfigurationError):
                    get_draft_generator()

        temp_dir.cleanup()

    def test_load_ai_provider_reads_workspace_dotenv_when_env_is_not_preloaded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("EVENTTRACKER_AI_PROVIDER=copilot\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                with patch("app.env.DEFAULT_ENV_FILE", env_file):
                    load_app_env.cache_clear()
                    self.assertEqual(load_ai_provider(), "copilot")


class TestOpenAIChatDraftGenerator(unittest.TestCase):
    def test_generate_suggestion_omits_temperature_and_parses_fields(
        self,
    ) -> None:
        create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "title": "Launch Momentum",
                                    "draft_html": "<p>Released the <b>first</b> milestone.</p><ul><li>Validated the app</li></ul>",
                                    "event_year": 2026,
                                    "event_month": 3,
                                    "event_day": 16,
                                    "suggested_tags": ["release", "milestone"],
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

        with patch("app.services.ai_generate.AsyncOpenAI", return_value=client):
            generator = OpenAIChatDraftGenerator(
                OpenAISettings(api_key="test-key", model_id="gpt-5")
            )
            suggestion = _run_async(
                generator.generate_entry_suggestion("Release milestone")
            )

        self.assertEqual(suggestion.title, "Launch Momentum")
        self.assertEqual(
            suggestion.draft_html,
            "<p>Released the <b>first</b> milestone.</p><ul><li>Validated the app</li></ul>",
        )
        self.assertEqual(suggestion.event_year, 2026)
        self.assertEqual(suggestion.event_month, 3)
        self.assertEqual(suggestion.event_day, 16)
        self.assertEqual(suggestion.suggested_tags, ["release", "milestone"])
        await_args = create.await_args
        self.assertIsNotNone(await_args)
        assert await_args is not None
        self.assertNotIn("temperature", await_args.kwargs)


class TestGeneratedDraftNormalization(unittest.TestCase):
    def test_parse_generation_response_removes_known_stray_html_characters(
        self,
    ) -> None:
        suggestion = _parse_generation_response(
            json.dumps(
                {
                    "title": "Launch Momentum",
                    "draft_html": "\ufeff<p>Release\u200b update\ufffd for Café</p>\r\n",
                    "event_year": 2026,
                    "event_month": 3,
                    "event_day": None,
                    "suggested_tags": ["  Release ", "milestone", "release", "launch"],
                }
            )
        )

        self.assertEqual(suggestion.draft_html, "<p>Release update for Café</p>")
        self.assertEqual(suggestion.suggested_tags, ["Release", "milestone", "launch"])

    def test_parse_generation_response_truncates_suggested_tags_to_five(self) -> None:
        suggestion = _parse_generation_response(
            json.dumps(
                {
                    "title": "Launch Momentum",
                    "draft_html": "<p>Release update.</p>",
                    "event_year": 2026,
                    "event_month": 3,
                    "event_day": 18,
                    "suggested_tags": [
                        "release",
                        "milestone",
                        "launch",
                        "timeline",
                        "summary",
                        "extra",
                    ],
                }
            )
        )

        self.assertEqual(
            suggestion.suggested_tags,
            ["release", "milestone", "launch", "timeline", "summary"],
        )


class TestPromptBuilder(unittest.TestCase):
    def test_build_user_prompt_appends_summary_instructions(self) -> None:
        prompt = _build_user_prompt(
            "Release milestone",
            None,
            ["release", "milestone"],
            " Focus on the technical impact and omit promotional phrasing. ",
        )

        self.assertIn("Current title hint: Release milestone", prompt)
        self.assertIn(
            "Additional summarization instructions: Focus on the technical impact and omit promotional phrasing.",
            prompt,
        )
        self.assertIn(
            "Preferred existing tags for this timeline group: release, milestone",
            prompt,
        )


class TestCopilotSdkWrapper(unittest.TestCase):
    def test_wrapper_exposes_installed_copilot_sdk_symbols(self) -> None:
        from app.services import copilot_sdk

        self.assertEqual(copilot_sdk.CopilotClient.__module__, "copilot.client")
        self.assertIn(
            copilot_sdk.PermissionHandler.__module__,
            {"copilot.session", "copilot.types"},
        )

    def test_create_copilot_session_supports_keyword_config(self) -> None:
        client = SimpleNamespace(create_session=AsyncMock(return_value="session"))

        async def exercise() -> None:
            with patch(
                "app.services.copilot_runtime.get_permission_handler",
                return_value="approve-all",
            ):
                session = await copilot_runtime.create_copilot_session(
                    cast(Any, client),
                    model_id="gpt-5",
                    system_message="System prompt",
                    reasoning_effort="medium",
                    streaming=True,
                )

            self.assertEqual(session, "session")

        _run_async(exercise())

        client.create_session.assert_awaited_once_with(
            model="gpt-5",
            on_permission_request="approve-all",
            reasoning_effort="medium",
            streaming=True,
            system_message={
                "mode": "append",
                "content": "System prompt",
            },
        )

    def test_prepare_copilot_client_supports_start_stop_lifecycle(self) -> None:
        client = _FakeStartStopClient()

        async def exercise() -> None:
            async with AsyncExitStack() as exit_stack:
                prepared = await copilot_runtime.prepare_copilot_client(
                    exit_stack, client
                )
                self.assertIs(prepared, client)
                self.assertTrue(client.started)
                self.assertFalse(client.stopped)

        from contextlib import AsyncExitStack

        _run_async(exercise())

        self.assertTrue(client.stopped)

    def test_prepare_copilot_resource_supports_disconnect_cleanup(self) -> None:
        session = _FakeDisconnectOnlySession()

        async def exercise() -> None:
            async with AsyncExitStack() as exit_stack:
                prepared = await copilot_runtime.prepare_copilot_resource(
                    exit_stack, session
                )
                self.assertIs(prepared, session)
                self.assertFalse(session.disconnected)

        from contextlib import AsyncExitStack

        _run_async(exercise())

        self.assertTrue(session.disconnected)


class TestCopilotChatDraftGenerator(unittest.TestCase):
    def test_generate_suggestion_parses_copilot_response(self) -> None:
        fake_client = _FakeCopilotClient(
            response=SimpleNamespace(
                content=json.dumps(
                    {
                        "title": "Copilot Draft",
                        "draft_html": "<p>Summarized from Copilot.</p>",
                        "event_year": 2026,
                        "event_month": 3,
                        "event_day": None,
                        "suggested_tags": ["copilot", "release"],
                    }
                )
            )
        )

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
            generator = CopilotChatDraftGenerator(CopilotSettings(model_id="gpt-5"))
            suggestion = _run_async(
                generator.generate_entry_suggestion(
                    "Release milestone",
                    preferred_tags=["release", "milestone"],
                )
            )

        self.assertEqual(suggestion.title, "Copilot Draft")
        self.assertEqual(suggestion.draft_html, "<p>Summarized from Copilot.</p>")
        self.assertEqual(
            fake_client.config,
            {
                "model": "gpt-5",
                "on_permission_request": "approve-all",
                "system_message": {
                    "mode": "append",
                    "content": (
                        "You write concise personal timeline entry suggestions. Return JSON only with "
                        'this exact schema: {"title": string, "draft_html": string, '
                        '"event_year": number|null, "event_month": number|null, '
                        '"event_day": number|null, "suggested_tags": string[]}. Do not wrap the JSON in markdown fences. '
                        "The title should be short, specific, and catchy. The draft_html should be "
                        "factual, concise, and may use only simple HTML tags such as <p>, <b>, "
                        "<strong>, <i>, <em>, <ul>, <ol>, <li>, <br>, <blockquote>, <code>, and <u>. "
                        "Prefer two to three short paragraphs unless a short list genuinely improves clarity. "
                        "Infer the date from the supplied content only when reasonably supported; use "
                        "null for unknown fields, especially event_day when the day is not explicit. "
                        "Return up to 5 concise suggested_tags. Prefer supplied existing tags when they fit, "
                        "avoid duplicates or near-duplicates, and create new tags only when needed."
                    ),
                },
            },
        )
        self.assertTrue(fake_client.entered)
        self.assertTrue(fake_client.exited)
        self.assertTrue(fake_client.session.closed)
        self.assertEqual(
            fake_client.session.send_calls[0]["prompt"],
            (
                "Create a structured suggestion for a personal timeline entry.\n"
                "Current title hint: Release milestone\n"
                "Preferred existing tags for this timeline group: release, milestone\n"
                "Keep it factual, readable, and ready for manual editing. If there is not enough evidence for the date, return null for the missing parts. Return up to 5 tags, and prefer the supplied group tags whenever they are a good fit."
            ),
        )
        self.assertEqual(fake_client.session.timeouts[0], 60.0)

    def test_generate_suggestion_reuses_parse_error_path(self) -> None:
        fake_client = _FakeCopilotClient(response=SimpleNamespace(content="not-json"))

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
            generator = CopilotChatDraftGenerator(CopilotSettings(model_id="gpt-5"))
            with self.assertRaises(DraftGenerationError):
                _run_async(generator.generate_entry_suggestion("Release milestone"))


class TestNormalizeText(unittest.TestCase):
    def test_collapses_multiple_spaces(self) -> None:
        from app.services.ai_generate import _normalize_text

        self.assertEqual(_normalize_text("hello   world"), "hello world")

    def test_collapses_tabs_and_newlines(self) -> None:
        from app.services.ai_generate import _normalize_text

        self.assertEqual(_normalize_text("hello\t\tworld\nfoo"), "hello world foo")

    def test_already_normal_text_unchanged(self) -> None:
        from app.services.ai_generate import _normalize_text

        self.assertEqual(_normalize_text("hello world"), "hello world")

    def test_empty_string(self) -> None:
        from app.services.ai_generate import _normalize_text

        self.assertEqual(_normalize_text(""), "")

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        from app.services.ai_generate import _normalize_text

        self.assertEqual(_normalize_text("  hello  "), "hello")


class TestNormalizeHtml(unittest.TestCase):
    def test_replaces_crlf_with_lf(self) -> None:
        from app.services.ai_generate import _normalize_html

        self.assertEqual(_normalize_html("line1\r\nline2"), "line1\nline2")

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        from app.services.ai_generate import _normalize_html

        self.assertEqual(_normalize_html("  <p>hello</p>  "), "<p>hello</p>")

    def test_already_normal_html_unchanged(self) -> None:
        from app.services.ai_generate import _normalize_html

        self.assertEqual(_normalize_html("<p>hello</p>"), "<p>hello</p>")


class TestNormalizeGeneratedHtml(unittest.TestCase):
    def test_removes_bom_character(self) -> None:
        from app.services.ai_generate import _normalize_generated_html

        self.assertEqual(_normalize_generated_html("\ufeff<p>hello</p>"), "<p>hello</p>")

    def test_removes_zero_width_space(self) -> None:
        from app.services.ai_generate import _normalize_generated_html

        self.assertEqual(
            _normalize_generated_html("<p>hel\u200blo</p>"), "<p>hello</p>"
        )

    def test_normal_text_unchanged(self) -> None:
        from app.services.ai_generate import _normalize_generated_html

        self.assertEqual(
            _normalize_generated_html("<p>Café</p>"), "<p>Café</p>"
        )

    def test_strips_and_normalizes_crlf(self) -> None:
        from app.services.ai_generate import _normalize_generated_html

        self.assertEqual(
            _normalize_generated_html("  <p>hi</p>\r\n"), "<p>hi</p>"
        )


class TestCoerceOptionalInt(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        from app.services.ai_generate import _coerce_optional_int

        self.assertIsNone(_coerce_optional_int(None, minimum=1, maximum=100))

    def test_empty_string_returns_none(self) -> None:
        from app.services.ai_generate import _coerce_optional_int

        self.assertIsNone(_coerce_optional_int("", minimum=1, maximum=100))

    def test_bool_returns_none(self) -> None:
        from app.services.ai_generate import _coerce_optional_int

        self.assertIsNone(_coerce_optional_int(True, minimum=0, maximum=100))
        self.assertIsNone(_coerce_optional_int(False, minimum=0, maximum=100))

    def test_valid_int(self) -> None:
        from app.services.ai_generate import _coerce_optional_int

        self.assertEqual(_coerce_optional_int(5, minimum=1, maximum=100), 5)

    def test_float_with_zero_fraction(self) -> None:
        from app.services.ai_generate import _coerce_optional_int

        self.assertEqual(_coerce_optional_int(5.0, minimum=1, maximum=100), 5)

    def test_float_with_nonzero_fraction_returns_none(self) -> None:
        from app.services.ai_generate import _coerce_optional_int

        self.assertIsNone(_coerce_optional_int(5.5, minimum=1, maximum=100))

    def test_out_of_range_returns_none(self) -> None:
        from app.services.ai_generate import _coerce_optional_int

        self.assertIsNone(_coerce_optional_int(200, minimum=1, maximum=100))
        self.assertIsNone(_coerce_optional_int(0, minimum=1, maximum=100))

    def test_string_number(self) -> None:
        from app.services.ai_generate import _coerce_optional_int

        self.assertEqual(_coerce_optional_int("5", minimum=1, maximum=100), 5)

    def test_string_non_numeric_returns_none(self) -> None:
        from app.services.ai_generate import _coerce_optional_int

        self.assertIsNone(_coerce_optional_int("abc", minimum=1, maximum=100))


class TestNormalizeSuggestedTags(unittest.TestCase):
    def test_none_returns_empty_list(self) -> None:
        from app.services.ai_generate import _normalize_suggested_tags

        self.assertEqual(_normalize_suggested_tags(None), [])

    def test_empty_string_returns_empty_list(self) -> None:
        from app.services.ai_generate import _normalize_suggested_tags

        self.assertEqual(_normalize_suggested_tags(""), [])

    def test_list_of_strings(self) -> None:
        from app.services.ai_generate import _normalize_suggested_tags

        self.assertEqual(
            _normalize_suggested_tags(["release", "milestone"]),
            ["release", "milestone"],
        )

    def test_single_string(self) -> None:
        from app.services.ai_generate import _normalize_suggested_tags

        self.assertEqual(_normalize_suggested_tags("release"), ["release"])

    def test_truncates_to_five(self) -> None:
        from app.services.ai_generate import _normalize_suggested_tags

        tags = ["a", "b", "c", "d", "e", "f", "g"]
        result = _normalize_suggested_tags(tags)
        self.assertEqual(len(result), 5)
        self.assertEqual(result, ["a", "b", "c", "d", "e"])

    def test_list_with_non_string_items(self) -> None:
        from app.services.ai_generate import _normalize_suggested_tags

        result = _normalize_suggested_tags(["release", 42, None])
        self.assertIsInstance(result, list)
        self.assertIn("release", result)


class TestBuildUserPromptDetailed(unittest.TestCase):
    def test_title_only(self) -> None:
        prompt = _build_user_prompt("My event", None, None, "")
        self.assertIn("Current title hint: My event", prompt)
        self.assertNotIn("Source context:", prompt)
        self.assertNotIn("Additional summarization instructions:", prompt)
        self.assertNotIn("Preferred existing tags", prompt)

    def test_with_extraction(self) -> None:
        from app.services.extraction import ExtractionResult

        extraction = ExtractionResult(
            source_url="https://example.com",
            final_url="https://example.com/final",
            title="Example Page",
            text="Some extracted content here.",
            markdown="# Example Page\n\nSome extracted content here.",
            fetched_utc="2026-04-07T12:00:00+00:00",
            content_type="text/html",
            http_etag=None,
            http_last_modified=None,
            content_sha256="abc123",
            extractor_name="markitdown",
            extractor_version="0.1.5",
            markdown_char_count=42,
        )
        prompt = _build_user_prompt("My event", extraction, None, "")
        self.assertIn("Source context in Markdown:", prompt)
        self.assertIn("Example Page", prompt)

    def test_with_preferred_tags(self) -> None:
        prompt = _build_user_prompt("My event", None, ["tag1", "tag2"], "")
        self.assertIn("Preferred existing tags for this timeline group: tag1, tag2", prompt)

    def test_with_summary_instructions(self) -> None:
        prompt = _build_user_prompt("My event", None, None, "Be concise.")
        self.assertIn("Additional summarization instructions: Be concise.", prompt)

    def test_all_combined(self) -> None:
        from app.services.extraction import ExtractionResult

        extraction = ExtractionResult(
            source_url="https://example.com",
            final_url="https://example.com/final",
            title="Page Title",
            text="Page content.",
            markdown="# Page Title\n\nPage content.",
            fetched_utc="2026-04-07T12:00:00+00:00",
            content_type="text/html",
            http_etag=None,
            http_last_modified=None,
            content_sha256="def456",
            extractor_name="markitdown",
            extractor_version="0.1.5",
            markdown_char_count=27,
        )
        prompt = _build_user_prompt(
            "My event", extraction, ["tag1"], "Be brief."
        )
        self.assertIn("Current title hint: My event", prompt)
        self.assertIn("Source context in Markdown:", prompt)
        self.assertIn("Additional summarization instructions: Be brief.", prompt)
        self.assertIn("Preferred existing tags for this timeline group: tag1", prompt)


class TestLoadOpenAISettings(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_file = Path(self.temp_dir.name) / ".env"
        self.env_file.write_text("", encoding="utf-8")
        self.env_file_patcher = patch("app.env.DEFAULT_ENV_FILE", self.env_file)
        self.env_file_patcher.start()
        load_app_env.cache_clear()

    def tearDown(self) -> None:
        self.env_file_patcher.stop()
        load_app_env.cache_clear()
        self.temp_dir.cleanup()

    def test_missing_api_key_raises_error(self) -> None:
        from app.services.ai_generate import load_openai_settings

        with patch.dict(os.environ, {"OPENAI_CHAT_MODEL_ID": "gpt-5"}, clear=True):
            load_app_env.cache_clear()
            with self.assertRaises(DraftGenerationConfigurationError):
                load_openai_settings()

    def test_missing_model_raises_error(self) -> None:
        from app.services.ai_generate import load_openai_settings

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            load_app_env.cache_clear()
            with self.assertRaises(DraftGenerationConfigurationError):
                load_openai_settings()

    def test_valid_config_returns_settings(self) -> None:
        from app.services.ai_generate import load_openai_settings

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test-key", "OPENAI_CHAT_MODEL_ID": "gpt-5"},
            clear=True,
        ):
            load_app_env.cache_clear()
            settings = load_openai_settings()
        self.assertEqual(settings.api_key, "test-key")
        self.assertEqual(settings.model_id, "gpt-5")
        self.assertIsNone(settings.base_url)


class TestLoadCopilotSettings(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_file = Path(self.temp_dir.name) / ".env"
        self.env_file.write_text("", encoding="utf-8")
        self.env_file_patcher = patch("app.env.DEFAULT_ENV_FILE", self.env_file)
        self.env_file_patcher.start()
        load_app_env.cache_clear()

    def tearDown(self) -> None:
        self.env_file_patcher.stop()
        load_app_env.cache_clear()
        self.temp_dir.cleanup()

    def test_default_model_id_is_gpt5(self) -> None:
        from app.services.ai_generate import load_copilot_settings

        with patch.dict(os.environ, {}, clear=True):
            load_app_env.cache_clear()
            settings = load_copilot_settings()
        self.assertEqual(settings.model_id, "gpt-5")

    def test_custom_model_id(self) -> None:
        from app.services.ai_generate import load_copilot_settings

        with patch.dict(
            os.environ, {"COPILOT_CHAT_MODEL_ID": "custom-model"}, clear=True
        ):
            load_app_env.cache_clear()
            settings = load_copilot_settings()
        self.assertEqual(settings.model_id, "custom-model")

    def test_empty_model_id_falls_back_to_default(self) -> None:
        from app.services.ai_generate import load_copilot_settings

        with patch.dict(os.environ, {"COPILOT_CHAT_MODEL_ID": ""}, clear=True):
            load_app_env.cache_clear()
            settings = load_copilot_settings()
        self.assertEqual(settings.model_id, "gpt-5")
