from __future__ import annotations

import json
import os
import unicodedata
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import lru_cache
from typing import cast
from typing import Protocol

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionSystemMessageParam
from openai.types.chat import ChatCompletionUserMessageParam

from app.env import load_app_env
from app.services.entries import normalize_tags
from app.services import copilot_runtime
from app.services.copilot_runtime import COPILOT_CLIENT_SETTINGS_MESSAGE
from app.services.copilot_runtime import COPILOT_SDK_REQUIRED_MESSAGE
from app.services.extraction import ExtractionResult


DEFAULT_AI_PROVIDER = "openai"
DEFAULT_COPILOT_MODEL_ID = "gpt-5"
SUPPORTED_AI_PROVIDERS = {DEFAULT_AI_PROVIDER, "copilot"}
_STRAY_GENERATED_HTML_CHARACTERS = "\ufeff\u200b\u200c\u200d\u2060\ufffd"
GENERATION_SYSTEM_PROMPT = (
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
    suggested_tags: list[str] | None = None


class DraftGenerator(Protocol):
    async def generate_entry_suggestion(
        self,
        title: str,
        extraction: ExtractionResult | None = None,
        preferred_tags: list[str] | None = None,
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
        self,
        title: str,
        extraction: ExtractionResult | None = None,
        preferred_tags: list[str] | None = None,
    ) -> GeneratedEntrySuggestion:
        normalized_title = _normalize_text(title)
        if not normalized_title and extraction is None:
            raise ValueError("Provide a title or source URL to generate a draft.")

        prompt = _build_user_prompt(normalized_title, extraction, preferred_tags)

        response = await self._client.chat.completions.create(
            model=self._settings.model_id,
            messages=_build_generation_messages(prompt),
        )
        message = response.choices[0].message.content if response.choices else None
        return _finalize_suggestion(message or "", normalized_title, extraction)


class CopilotChatDraftGenerator:
    def __init__(self, settings: CopilotSettings) -> None:
        self._settings = settings

    async def generate_entry_suggestion(
        self,
        title: str,
        extraction: ExtractionResult | None = None,
        preferred_tags: list[str] | None = None,
    ) -> GeneratedEntrySuggestion:
        normalized_title = _normalize_text(title)
        if not normalized_title and extraction is None:
            raise ValueError("Provide a title or source URL to generate a draft.")

        prompt = _build_user_prompt(normalized_title, extraction, preferred_tags)

        response_content = await self._generate_response_content(prompt)
        return _finalize_suggestion(response_content, normalized_title, extraction)

    async def _generate_response_content(self, prompt: str) -> str:
        client = copilot_runtime.instantiate_copilot_client(
            self._settings,
            configuration_error_type=DraftGenerationConfigurationError,
            missing_sdk_message=COPILOT_SDK_REQUIRED_MESSAGE,
            invalid_settings_message=COPILOT_CLIENT_SETTINGS_MESSAGE,
        )
        try:
            async with AsyncExitStack() as exit_stack:
                active_client = await copilot_runtime.prepare_copilot_client(
                    exit_stack, client
                )
                session = await copilot_runtime.create_copilot_session(
                    active_client,
                    model_id=self._settings.model_id,
                    system_message=GENERATION_SYSTEM_PROMPT,
                )
                active_session = await copilot_runtime.prepare_copilot_resource(
                    exit_stack, session
                )
                response = await copilot_runtime.send_copilot_prompt(
                    active_session,
                    prompt,
                    timeout=60.0,
                )
        except DraftGenerationError:
            raise
        except Exception as exc:
            raise DraftGenerationConfigurationError(
                "GitHub Copilot draft generation is not configured correctly. "
                "Install the GitHub Copilot SDK and ensure the Copilot CLI is available. "
                "If `copilot --version` already works, leave COPILOT_CLI_PATH and "
                "COPILOT_CLI_URL blank unless you intentionally need an override."
            ) from exc

        content = copilot_runtime.extract_copilot_message_content(response)
        if not content:
            raise DraftGenerationError("The AI provider returned an empty draft.")
        return content


async def generate_entry_suggestion(
    title: str,
    extraction: ExtractionResult | None = None,
    preferred_tags: list[str] | None = None,
) -> GeneratedEntrySuggestion:
    generator = get_draft_generator()
    return await generator.generate_entry_suggestion(title, extraction, preferred_tags)


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


def _build_generation_messages(prompt: str) -> list[ChatCompletionMessageParam]:
    system_message: ChatCompletionSystemMessageParam = {
        "role": "system",
        "content": GENERATION_SYSTEM_PROMPT,
    }
    user_message: ChatCompletionUserMessageParam = {
        "role": "user",
        "content": prompt,
    }
    return cast(list[ChatCompletionMessageParam], [system_message, user_message])


def _finalize_suggestion(
    value: str, title: str, extraction: ExtractionResult | None
) -> GeneratedEntrySuggestion:
    suggestion = _parse_generation_response(value)
    if not suggestion.title:
        suggestion = GeneratedEntrySuggestion(
            title=title
            or _normalize_text((extraction.title or "") if extraction else ""),
            draft_html=suggestion.draft_html,
            event_year=suggestion.event_year,
            event_month=suggestion.event_month,
            event_day=suggestion.event_day,
            suggested_tags=suggestion.suggested_tags,
        )
    if not suggestion.title:
        raise DraftGenerationError("The AI provider returned an empty title.")
    return suggestion


def _build_user_prompt(
    title: str,
    extraction: ExtractionResult | None,
    preferred_tags: list[str] | None,
) -> str:
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

    if preferred_tags:
        prompt.append(
            "Preferred existing tags for this timeline group: "
            + ", ".join(preferred_tags)
        )

    prompt.append(
        "Keep it factual, readable, and ready for manual editing. If there is not "
        "enough evidence for the date, return null for the missing parts. Return "
        "up to 5 tags, and prefer the supplied group tags whenever they are a good fit."
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
        suggested_tags=_normalize_suggested_tags(payload.get("suggested_tags")),
    )


def _normalize_suggested_tags(value: object) -> list[str]:
    """Normalize model-returned tags to a short deduplicated list for the form."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_value = value
    elif isinstance(value, list):
        raw_value = ", ".join(str(item) for item in value)
    else:
        return []
    return normalize_tags(raw_value)[:5]


def _coerce_optional_int(value: object, *, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        coerced = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        coerced = int(value)
    elif isinstance(value, str):
        try:
            coerced = int(value)
        except ValueError:
            return None
    else:
        return None

    if coerced < minimum or coerced > maximum:
        return None
    return coerced
