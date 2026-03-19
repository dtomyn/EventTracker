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
                }
            )
        )

        self.assertEqual(suggestion.draft_html, "<p>Release update for Café</p>")


class TestCopilotSdkWrapper(unittest.TestCase):
    def test_wrapper_exposes_installed_copilot_sdk_symbols(self) -> None:
        from app.services import copilot_sdk

        self.assertEqual(copilot_sdk.CopilotClient.__module__, "copilot.client")
        self.assertEqual(
            copilot_sdk.PermissionHandler.__module__,
            "copilot.types",
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
                generator.generate_entry_suggestion("Release milestone")
            )

        self.assertEqual(suggestion.title, "Copilot Draft")
        self.assertEqual(suggestion.draft_html, "<p>Summarized from Copilot.</p>")
        self.assertEqual(suggestion.event_year, 2026)
        self.assertEqual(suggestion.event_month, 3)
        self.assertIsNone(suggestion.event_day)
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
                        '"event_day": number|null}. Do not wrap the JSON in markdown fences. '
                        "The title should be short, specific, and catchy. The draft_html should be "
                        "factual, concise, and may use only simple HTML tags such as <p>, <b>, "
                        "<strong>, <i>, <em>, <ul>, <ol>, <li>, <br>, <blockquote>, <code>, and <u>. "
                        "Prefer one short paragraph unless a short list genuinely improves clarity. "
                        "Infer the date from the supplied content only when reasonably supported; use "
                        "null for unknown fields, especially event_day when the day is not explicit."
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
                "Keep it factual, readable, and ready for manual editing. If there is not enough evidence for the date, return null for the missing parts."
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
