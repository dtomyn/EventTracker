from __future__ import annotations

import importlib
import json
import os
import unicodedata
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from typing import Protocol

from openai import AsyncOpenAI

from app.env import load_app_env
from app.services.extraction import ExtractionResult


DEFAULT_AI_PROVIDER = "openai"
DEFAULT_COPILOT_MODEL_ID = "gpt-5"
SUPPORTED_AI_PROVIDERS = {DEFAULT_AI_PROVIDER, "copilot"}
_STRAY_GENERATED_HTML_CHARACTERS = "\ufeff\u200b\u200c\u200d\u2060\ufffd"
GENERATION_SYSTEM_PROMPT = (
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
)


class DraftGenerationError(Exception):
    pass


class DraftGenerationConfigurationError(DraftGenerationError):
    pass


@dataclass(frozen=True, slots=True)
class GeneratedEntrySuggestion:
    title: str
    draft_html: str
    event_year: int | None = None
    event_month: int | None = None
    event_day: int | None = None


class DraftGenerator(Protocol):
    async def generate_entry_suggestion(
        self, title: str, extraction: ExtractionResult | None = None
    ) -> GeneratedEntrySuggestion: ...


@dataclass(frozen=True, slots=True)
class OpenAISettings:
    api_key: str
    model_id: str
    base_url: str | None = None


@dataclass(frozen=True, slots=True)
class CopilotSettings:
    model_id: str = DEFAULT_COPILOT_MODEL_ID
    cli_path: str | None = None
    cli_url: str | None = None


class OpenAIChatDraftGenerator:
    def __init__(self, settings: OpenAISettings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url or None,
        )

    async def generate_entry_suggestion(
        self, title: str, extraction: ExtractionResult | None = None
    ) -> GeneratedEntrySuggestion:
        normalized_title = _normalize_text(title)
        if not normalized_title and extraction is None:
            raise ValueError("Provide a title or source URL to generate a draft.")

        response = await self._client.chat.completions.create(
            model=self._settings.model_id,
            messages=_build_generation_messages(normalized_title, extraction),
        )
        message = response.choices[0].message.content if response.choices else None
        return _finalize_suggestion(message or "", normalized_title, extraction)


class CopilotChatDraftGenerator:
    def __init__(self, settings: CopilotSettings) -> None:
        self._settings = settings

    async def generate_entry_suggestion(
        self, title: str, extraction: ExtractionResult | None = None
    ) -> GeneratedEntrySuggestion:
        normalized_title = _normalize_text(title)
        if not normalized_title and extraction is None:
            raise ValueError("Provide a title or source URL to generate a draft.")

        response_content = await self._generate_response_content(
            _build_generation_messages(normalized_title, extraction)
        )
        return _finalize_suggestion(response_content, normalized_title, extraction)

    async def _generate_response_content(self, messages: list[dict[str, str]]) -> str:
        client = _instantiate_copilot_client(self._settings)
        try:
            async with AsyncExitStack() as exit_stack:
                active_client = await _prepare_copilot_client(exit_stack, client)
                session = await _create_copilot_session(
                    active_client, self._settings.model_id
                )
                active_session = await _prepare_copilot_resource(exit_stack, session)
                response = await _send_copilot_messages(active_session, messages)
        except DraftGenerationError:
            raise
        except Exception as exc:
            raise DraftGenerationConfigurationError(
                "GitHub Copilot draft generation is not configured correctly. "
                "Install the GitHub Copilot SDK and ensure the Copilot CLI is available. "
                "If `copilot --version` already works, leave COPILOT_CLI_PATH and "
                "COPILOT_CLI_URL blank unless you intentionally need an override."
            ) from exc

        content = _extract_copilot_message_content(response)
        if not content:
            raise DraftGenerationError("The AI provider returned an empty draft.")
        return content


async def generate_entry_suggestion(
    title: str, extraction: ExtractionResult | None = None
) -> GeneratedEntrySuggestion:
    generator = get_draft_generator()
    return await generator.generate_entry_suggestion(title, extraction)


@lru_cache(maxsize=1)
def get_draft_generator() -> DraftGenerator:
    provider = load_ai_provider()
    if provider == "copilot":
        return CopilotChatDraftGenerator(load_copilot_settings())
    return OpenAIChatDraftGenerator(load_openai_settings())


def load_ai_provider() -> str:
    load_app_env()
    provider = (
        os.getenv("EVENTTRACKER_AI_PROVIDER", DEFAULT_AI_PROVIDER).strip().lower()
    )
    if provider not in SUPPORTED_AI_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_AI_PROVIDERS))
        raise DraftGenerationConfigurationError(
            f"Unsupported EVENTTRACKER_AI_PROVIDER value: {provider}. Use one of: {allowed}."
        )
    return provider


def load_openai_settings() -> OpenAISettings:
    load_app_env()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model_id = os.getenv("OPENAI_CHAT_MODEL_ID", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None

    missing = [
        name
        for name, value in (
            ("OPENAI_API_KEY", api_key),
            ("OPENAI_CHAT_MODEL_ID", model_id),
        )
        if not value
    ]
    if missing:
        names = ", ".join(missing)
        raise DraftGenerationConfigurationError(
            f"Draft generation is not configured. Set {names} in your environment."
        )

    return OpenAISettings(api_key=api_key, model_id=model_id, base_url=base_url)


def load_copilot_settings() -> CopilotSettings:
    load_app_env()
    model_id = os.getenv("COPILOT_CHAT_MODEL_ID", DEFAULT_COPILOT_MODEL_ID).strip()
    cli_path = os.getenv("COPILOT_CLI_PATH", "").strip() or None
    cli_url = os.getenv("COPILOT_CLI_URL", "").strip() or None
    return CopilotSettings(
        model_id=model_id or DEFAULT_COPILOT_MODEL_ID,
        cli_path=cli_path,
        cli_url=cli_url,
    )


def _build_generation_messages(
    title: str, extraction: ExtractionResult | None
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": GENERATION_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": _build_user_prompt(title, extraction),
        },
    ]


def _finalize_suggestion(
    value: str, title: str, extraction: ExtractionResult | None
) -> GeneratedEntrySuggestion:
    suggestion = _parse_generation_response(value)
    if not suggestion.title:
        suggestion = GeneratedEntrySuggestion(
            title=title or _normalize_text(extraction.title if extraction else ""),
            draft_html=suggestion.draft_html,
            event_year=suggestion.event_year,
            event_month=suggestion.event_month,
            event_day=suggestion.event_day,
        )
    if not suggestion.title:
        raise DraftGenerationError("The AI provider returned an empty title.")
    return suggestion


def _resolve_copilot_client_class() -> type[Any]:
    module = _resolve_copilot_module()
    client_class = getattr(module, "CopilotClient", None)
    if client_class is None:
        raise DraftGenerationConfigurationError(
            "The installed GitHub Copilot SDK does not expose CopilotClient."
        )
    return client_class


def _resolve_copilot_module() -> Any:
    module = None
    import_errors: list[ImportError] = []
    for module_name in ("copilot", "github_copilot_sdk"):
        try:
            module = importlib.import_module(module_name)
            break
        except ImportError as exc:
            import_errors.append(exc)

    if module is None:
        raise DraftGenerationConfigurationError(
            "GitHub Copilot draft generation requires the github-copilot-sdk package."
        ) from import_errors[-1]

    return module


def _resolve_copilot_permission_handler() -> Any:
    module = _resolve_copilot_module()
    permission_handler = getattr(module, "PermissionHandler", None)
    if permission_handler is None or not hasattr(permission_handler, "approve_all"):
        raise DraftGenerationConfigurationError(
            "The installed GitHub Copilot SDK does not expose PermissionHandler.approve_all."
        )
    return permission_handler.approve_all


def _instantiate_copilot_client(settings: CopilotSettings) -> Any:
    client_class = _resolve_copilot_client_class()
    options = {
        "cli_path": settings.cli_path,
        "cli_url": settings.cli_url,
    }
    filtered_options = {
        key: value for key, value in options.items() if value is not None
    }
    try:
        return client_class(filtered_options or None)
    except TypeError as exc:
        raise DraftGenerationConfigurationError(
            "Unable to initialize the GitHub Copilot client with the current settings. "
            "Most setups should leave COPILOT_CLI_PATH and COPILOT_CLI_URL unset."
        ) from exc


async def _create_copilot_session(client: Any, model_id: str) -> Any:
    method = getattr(client, "create_session", None)
    if method is None:
        raise DraftGenerationConfigurationError(
            "The installed GitHub Copilot SDK does not expose create_session(...)."
        )

    return await method(
        {
            "model": model_id,
            "on_permission_request": _resolve_copilot_permission_handler(),
            "system_message": {
                "mode": "append",
                "content": GENERATION_SYSTEM_PROMPT,
            },
        }
    )


async def _prepare_copilot_resource(exit_stack: AsyncExitStack, resource: Any) -> Any:
    if hasattr(resource, "__aenter__") and hasattr(resource, "__aexit__"):
        return await exit_stack.enter_async_context(resource)

    for method_name in ("destroy", "aclose", "close", "stop", "shutdown"):
        method = getattr(resource, method_name, None)
        if callable(method):
            exit_stack.push_async_callback(_invoke_cleanup_method, method)
            break
    return resource


async def _prepare_copilot_client(exit_stack: AsyncExitStack, client: Any) -> Any:
    if hasattr(client, "__aenter__") and hasattr(client, "__aexit__"):
        return await exit_stack.enter_async_context(client)

    start = getattr(client, "start", None)
    if callable(start):
        await _invoke_cleanup_method(start)

    for method_name in ("force_stop", "stop", "aclose", "close", "shutdown"):
        method = getattr(client, method_name, None)
        if callable(method):
            exit_stack.push_async_callback(_invoke_cleanup_method, method)
            break

    return client


async def _send_copilot_messages(session: Any, messages: list[dict[str, str]]) -> Any:
    method = getattr(session, "send_and_wait", None)
    if method is None:
        raise DraftGenerationConfigurationError(
            "The installed GitHub Copilot SDK does not expose send_and_wait(...)."
        )

    return await method(
        {
            "prompt": messages[-1]["content"],
        },
        timeout=60.0,
    )


def _extract_copilot_message_content(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("content", "text", "message", "response", "output", "data"):
            extracted = _extract_copilot_message_content(response.get(key))
            if extracted:
                return extracted
        for key in ("messages", "events", "items"):
            items = response.get(key)
            if isinstance(items, list):
                for item in reversed(items):
                    extracted = _extract_copilot_message_content(item)
                    if extracted:
                        return extracted
        return ""
    if isinstance(response, (list, tuple)):
        for item in reversed(response):
            extracted = _extract_copilot_message_content(item)
            if extracted:
                return extracted
        return ""

    for attr_name in ("content", "text", "message", "response", "output", "data"):
        if hasattr(response, attr_name):
            extracted = _extract_copilot_message_content(getattr(response, attr_name))
            if extracted:
                return extracted
    return ""


async def _invoke_cleanup_method(method: Any) -> None:
    result = method()
    if hasattr(result, "__await__"):
        await result


def _build_user_prompt(title: str, extraction: ExtractionResult | None) -> str:
    prompt = [
        "Create a structured suggestion for a personal timeline entry.",
    ]

    if title:
        prompt.append(f"Current title hint: {title}")
    else:
        prompt.append("Current title hint: none provided")

    if extraction and extraction.text:
        prompt.append(
            "Source context: "
            f"Title={_normalize_text(extraction.title or '')}; "
            f"Excerpt={_normalize_text(extraction.text[:2000])}"
        )

    prompt.append(
        "Keep it factual, readable, and ready for manual editing. If there is not "
        "enough evidence for the date, return null for the missing parts."
    )
    return "\n".join(prompt)


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _normalize_html(value: str) -> str:
    return value.replace("\r\n", "\n").strip()


def _normalize_generated_html(value: str) -> str:
    cleaned = value.translate(str.maketrans("", "", _STRAY_GENERATED_HTML_CHARACTERS))
    return _normalize_html(unicodedata.normalize("NFC", cleaned))


def _parse_generation_response(value: str) -> GeneratedEntrySuggestion:
    content = value.strip()
    if not content:
        raise DraftGenerationError("The AI provider returned an empty draft.")

    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise DraftGenerationError(
            "The AI provider returned invalid structured output."
        ) from exc

    if not isinstance(payload, dict):
        raise DraftGenerationError(
            "The AI provider returned invalid structured output."
        )

    draft_html = _normalize_generated_html(str(payload.get("draft_html", "")))
    if not draft_html:
        raise DraftGenerationError("The AI provider returned an empty draft.")

    return GeneratedEntrySuggestion(
        title=_normalize_text(str(payload.get("title", ""))),
        draft_html=draft_html,
        event_year=_coerce_optional_int(
            payload.get("event_year"), minimum=1900, maximum=2100
        ),
        event_month=_coerce_optional_int(
            payload.get("event_month"), minimum=1, maximum=12
        ),
        event_day=_coerce_optional_int(payload.get("event_day"), minimum=1, maximum=31),
    )


def _coerce_optional_int(value: object, *, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    if coerced < minimum or coerced > maximum:
        return None
    return coerced
