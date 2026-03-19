from __future__ import annotations

import json
import os
import unicodedata
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionSystemMessageParam
from openai.types.chat import ChatCompletionUserMessageParam

from app.env import load_app_env
from app.models import Entry, StoryFormat, TimelineStoryScope
from app.services import copilot_runtime
from app.services.ai_generate import (
    CopilotSettings,
    DEFAULT_AI_PROVIDER,
    DEFAULT_COPILOT_MODEL_ID,
    OpenAISettings,
    SUPPORTED_AI_PROVIDERS,
)
from app.services.copilot_runtime import COPILOT_CLIENT_SETTINGS_MESSAGE
from app.services.copilot_runtime import COPILOT_SDK_REQUIRED_MESSAGE
from app.services.entries import preview_text
from .story_mode import prepare_story_input_entries


DEFAULT_STORY_INPUT_ENTRY_COUNT = 40
DEFAULT_STORY_ENTRY_SUMMARY_LENGTH = 280
_STRAY_STORY_CHARACTERS = "\ufeff\u200b\u200c\u200d\u2060\ufffd"
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_FORMAT_TITLES: dict[StoryFormat, str] = {
    "executive_summary": "Executive Summary",
    "detailed_chronology": "Detailed Chronology",
    "recent_changes": "Recent Changes",
}
_FORMAT_GUIDANCE: dict[StoryFormat, str] = {
    "executive_summary": (
        "Write a concise executive briefing. Highlight the main arc, the most important "
        "turning points, and why the changes matter. Prefer 2 to 4 sections."
    ),
    "detailed_chronology": (
        "Write a chronological narrative. Move from earliest to latest developments and "
        "group the story into clear phases. Prefer 3 to 6 sections."
    ),
    "recent_changes": (
        "Focus on the most recent developments, what shifted lately, and what they imply "
        "about the current direction. Prefer 2 to 4 sections."
    ),
}
STORY_GENERATION_SYSTEM_PROMPT = (
    "You write grounded timeline stories from structured entry context. Return JSON only "
    'with this exact schema: {"title": string, "sections": [{"heading": string, '
    '"body": string, "citations": [{"entry_id": number, "quote_text": string|null, '
    '"note": string|null}]}]}. Do not wrap the JSON in markdown fences. '
    "Each section body must be plain text, not HTML. Cite only entry_id values that "
    "appear in the provided context. Keep the story factual, concise, and anchored in "
    "the supplied entries. Do not invent events, dates, motives, or outcomes."
)


class StoryGenerationError(Exception):
    pass


class StoryGenerationConfigurationError(StoryGenerationError):
    pass


@dataclass(slots=True)
class GeneratedStoryCitation:
    citation_order: int
    entry_id: int
    quote_text: str | None = None
    note: str | None = None


@dataclass(slots=True)
class GeneratedStorySection:
    heading: str
    body: str
    citation_orders: list[int] = field(default_factory=list)


@dataclass(slots=True)
class GeneratedTimelineStory:
    format: StoryFormat
    title: str
    sections: list[GeneratedStorySection]
    citations: list[GeneratedStoryCitation]
    provider_name: str
    source_entry_count: int
    truncated_input: bool


class TimelineStoryGenerator(Protocol):
    provider_name: str

    async def generate_story(
        self,
        scope: TimelineStoryScope,
        story_format: StoryFormat,
        entries: list[Entry],
        *,
        max_entries: int = DEFAULT_STORY_INPUT_ENTRY_COUNT,
        max_entry_summary_length: int = DEFAULT_STORY_ENTRY_SUMMARY_LENGTH,
    ) -> GeneratedTimelineStory: ...


class OpenAIChatStoryGenerator:
    provider_name = "openai"

    def __init__(self, settings: OpenAISettings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url or None,
        )

    async def generate_story(
        self,
        scope: TimelineStoryScope,
        story_format: StoryFormat,
        entries: list[Entry],
        *,
        max_entries: int = DEFAULT_STORY_INPUT_ENTRY_COUNT,
        max_entry_summary_length: int = DEFAULT_STORY_ENTRY_SUMMARY_LENGTH,
    ) -> GeneratedTimelineStory:
        prepared_entries, truncated_input = prepare_story_input_entries(
            entries,
            max_entries=max_entries,
        )
        if not prepared_entries:
            raise ValueError("Provide at least one entry to generate a story.")

        prompt = _build_user_prompt(
            scope,
            story_format,
            prepared_entries,
            truncated_input=truncated_input,
            max_entry_summary_length=max_entry_summary_length,
        )

        response = await self._client.chat.completions.create(
            model=self._settings.model_id,
            messages=_build_generation_messages(prompt),
        )
        message = response.choices[0].message.content if response.choices else None
        return _finalize_story(
            message or "",
            scope,
            story_format,
            prepared_entries,
            provider_name=self.provider_name,
            truncated_input=truncated_input,
        )


class CopilotChatStoryGenerator:
    provider_name = "copilot"

    def __init__(self, settings: CopilotSettings) -> None:
        self._settings = settings

    async def generate_story(
        self,
        scope: TimelineStoryScope,
        story_format: StoryFormat,
        entries: list[Entry],
        *,
        max_entries: int = DEFAULT_STORY_INPUT_ENTRY_COUNT,
        max_entry_summary_length: int = DEFAULT_STORY_ENTRY_SUMMARY_LENGTH,
    ) -> GeneratedTimelineStory:
        prepared_entries, truncated_input = prepare_story_input_entries(
            entries,
            max_entries=max_entries,
        )
        if not prepared_entries:
            raise ValueError("Provide at least one entry to generate a story.")

        prompt = _build_user_prompt(
            scope,
            story_format,
            prepared_entries,
            truncated_input=truncated_input,
            max_entry_summary_length=max_entry_summary_length,
        )
        response_content = await self._generate_response_content(prompt)
        return _finalize_story(
            response_content,
            scope,
            story_format,
            prepared_entries,
            provider_name=self.provider_name,
            truncated_input=truncated_input,
        )

    async def _generate_response_content(self, prompt: str) -> str:
        client = copilot_runtime.instantiate_copilot_client(
            self._settings,
            configuration_error_type=StoryGenerationConfigurationError,
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
                    system_message=STORY_GENERATION_SYSTEM_PROMPT,
                )
                active_session = await copilot_runtime.prepare_copilot_resource(
                    exit_stack, session
                )
                response = await copilot_runtime.send_copilot_prompt(
                    active_session,
                    prompt,
                    timeout=60.0,
                )
        except StoryGenerationError:
            raise
        except Exception as exc:
            raise StoryGenerationConfigurationError(
                "GitHub Copilot story generation is not configured correctly. "
                "Install the GitHub Copilot SDK and ensure the Copilot CLI is available. "
                "If `copilot --version` already works, leave COPILOT_CLI_PATH and "
                "COPILOT_CLI_URL blank unless you intentionally need an override."
            ) from exc

        content = copilot_runtime.extract_copilot_message_content(response)
        if not content:
            raise StoryGenerationError("The AI provider returned an empty story.")
        return content


async def generate_timeline_story(
    scope: TimelineStoryScope,
    story_format: StoryFormat,
    entries: list[Entry],
    *,
    max_entries: int = DEFAULT_STORY_INPUT_ENTRY_COUNT,
    max_entry_summary_length: int = DEFAULT_STORY_ENTRY_SUMMARY_LENGTH,
) -> GeneratedTimelineStory:
    generator = get_story_generator()
    return await generator.generate_story(
        scope,
        story_format,
        entries,
        max_entries=max_entries,
        max_entry_summary_length=max_entry_summary_length,
    )


@lru_cache(maxsize=1)
def get_story_generator() -> TimelineStoryGenerator:
    provider = load_story_ai_provider()
    if provider == "copilot":
        return CopilotChatStoryGenerator(load_story_copilot_settings())
    return OpenAIChatStoryGenerator(load_story_openai_settings())


def load_story_ai_provider() -> str:
    load_app_env()
    provider = (
        os.getenv("EVENTTRACKER_AI_PROVIDER", DEFAULT_AI_PROVIDER).strip().lower()
    )
    if provider not in SUPPORTED_AI_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_AI_PROVIDERS))
        raise StoryGenerationConfigurationError(
            f"Unsupported EVENTTRACKER_AI_PROVIDER value: {provider}. Use one of: {allowed}."
        )
    return provider


def load_story_openai_settings() -> OpenAISettings:
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
        raise StoryGenerationConfigurationError(
            f"Story generation is not configured. Set {names} in your environment."
        )

    return OpenAISettings(api_key=api_key, model_id=model_id, base_url=base_url)


def load_story_copilot_settings() -> CopilotSettings:
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
        "content": STORY_GENERATION_SYSTEM_PROMPT,
    }
    user_message: ChatCompletionUserMessageParam = {
        "role": "user",
        "content": prompt,
    }
    return cast(list[ChatCompletionMessageParam], [system_message, user_message])


def _finalize_story(
    value: str,
    scope: TimelineStoryScope,
    story_format: StoryFormat,
    entries: list[Entry],
    *,
    provider_name: str,
    truncated_input: bool,
) -> GeneratedTimelineStory:
    story = _parse_generation_response(
        value,
        story_format=story_format,
        allowed_entry_ids={entry.id for entry in entries},
    )
    if not story.title:
        story.title = _default_story_title(scope, story_format)
    if not story.title:
        raise StoryGenerationError("The AI provider returned an empty story title.")
    story.provider_name = provider_name
    story.source_entry_count = len(entries)
    story.truncated_input = truncated_input
    return story


def _build_user_prompt(
    scope: TimelineStoryScope,
    story_format: StoryFormat,
    entries: list[Entry],
    *,
    truncated_input: bool,
    max_entry_summary_length: int,
) -> str:
    scope_summary = _describe_scope(scope)
    prompt = [
        "Create a structured Timeline Story Mode narrative.",
        f"Requested format: {story_format}",
        f"Format guidance: {_FORMAT_GUIDANCE[story_format]}",
        f"Scope: {scope_summary}",
        f"Entry count in prompt: {len(entries)}",
    ]
    if truncated_input:
        prompt.append(
            "Input was truncated to the most recent scoped entries to keep the prompt bounded."
        )
    prompt.append(
        "Use only the entry_id values provided below when creating citations. Repeat a "
        "citation entry_id when the same entry supports multiple sections."
    )
    prompt.append("Entries are ordered chronologically from oldest to newest:")
    prompt.append(_format_entry_context(entries, max_entry_summary_length))
    return "\n".join(prompt)


def _format_entry_context(entries: list[Entry], max_entry_summary_length: int) -> str:
    lines: list[str] = []
    for entry in entries:
        title = _normalize_text(entry.title) or "Untitled entry"
        summary = preview_text(entry.final_text, max_length=max_entry_summary_length)
        lines.append(
            f"- entry_id={entry.id} | date={_format_entry_date(entry)} | title={title}"
        )
        lines.append(f"  summary={summary}")
    return "\n".join(lines)


def _parse_generation_response(
    value: str,
    *,
    story_format: StoryFormat,
    allowed_entry_ids: set[int],
) -> GeneratedTimelineStory:
    content = value.strip()
    if not content:
        raise StoryGenerationError("The AI provider returned an empty story.")

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
        raise StoryGenerationError(
            "The AI provider returned invalid structured output."
        ) from exc

    if not isinstance(payload, dict):
        raise StoryGenerationError(
            "The AI provider returned invalid structured output."
        )

    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise StoryGenerationError(
            "The AI provider returned invalid structured output."
        )

    citations: list[GeneratedStoryCitation] = []
    citation_orders_by_entry_id: dict[int, int] = {}
    sections: list[GeneratedStorySection] = []

    for raw_section in raw_sections:
        if not isinstance(raw_section, dict):
            raise StoryGenerationError(
                "The AI provider returned invalid structured output."
            )
        heading = _normalize_text(str(raw_section.get("heading", "")))
        body = _normalize_story_text(str(raw_section.get("body", "")))
        raw_citations = raw_section.get("citations", [])
        if not heading or not body or not isinstance(raw_citations, list):
            raise StoryGenerationError(
                "The AI provider returned invalid structured output."
            )

        citation_orders: list[int] = []
        for raw_citation in raw_citations:
            citation = _parse_story_citation(
                raw_citation,
                allowed_entry_ids=allowed_entry_ids,
            )
            if citation.entry_id not in citation_orders_by_entry_id:
                citation_orders_by_entry_id[citation.entry_id] = len(citations) + 1
                citation.citation_order = len(citations) + 1
                citations.append(citation)
            else:
                existing = citations[citation_orders_by_entry_id[citation.entry_id] - 1]
                if existing.quote_text is None and citation.quote_text is not None:
                    existing.quote_text = citation.quote_text
                if existing.note is None and citation.note is not None:
                    existing.note = citation.note

            citation_order = citation_orders_by_entry_id[citation.entry_id]
            if citation_order not in citation_orders:
                citation_orders.append(citation_order)

        sections.append(
            GeneratedStorySection(
                heading=heading,
                body=body,
                citation_orders=citation_orders,
            )
        )

    if not citations:
        raise StoryGenerationError(
            "The AI provider returned a story without any citations."
        )

    return GeneratedTimelineStory(
        format=story_format,
        title=_normalize_text(str(payload.get("title", ""))),
        sections=sections,
        citations=citations,
        provider_name="",
        source_entry_count=0,
        truncated_input=False,
    )


def _parse_story_citation(
    raw_citation: object,
    *,
    allowed_entry_ids: set[int],
) -> GeneratedStoryCitation:
    if not isinstance(raw_citation, dict):
        raise StoryGenerationError(
            "The AI provider returned invalid structured output."
        )

    entry_id = _coerce_required_int(raw_citation.get("entry_id"))
    if entry_id is None or entry_id not in allowed_entry_ids:
        raise StoryGenerationError(
            "The AI provider returned invalid structured output."
        )

    quote_text = _normalize_optional_text(raw_citation.get("quote_text"))
    note = _normalize_optional_text(raw_citation.get("note"))
    return GeneratedStoryCitation(
        citation_order=0,
        entry_id=entry_id,
        quote_text=quote_text,
        note=note,
    )


def _coerce_required_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            return None
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _normalize_story_text(value: str) -> str:
    cleaned = value.translate(str.maketrans("", "", _STRAY_STORY_CHARACTERS))
    normalized = unicodedata.normalize("NFC", cleaned).replace("\r\n", "\n").strip()
    if not normalized:
        return ""
    lines = [line.strip() for line in normalized.split("\n")]
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                collapsed.append("")
            previous_blank = True
            continue
        collapsed.append(line)
        previous_blank = False
    return "\n".join(collapsed).strip()


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = _normalize_story_text(str(value))
    return normalized or None


def _format_entry_date(entry: Entry) -> str:
    if entry.display_date:
        return entry.display_date
    if entry.event_day is not None:
        return f"{_MONTH_NAMES[entry.event_month - 1]} {entry.event_day}, {entry.event_year}"
    return f"{_MONTH_NAMES[entry.event_month - 1]} {entry.event_year}"


def _describe_scope(scope: TimelineStoryScope) -> str:
    parts = [
        f"type={scope.scope_type}",
        "group=all groups" if scope.group_id is None else f"group_id={scope.group_id}",
    ]
    if scope.query_text:
        parts.append(f"query={_normalize_text(scope.query_text)}")
    if scope.year is not None:
        if scope.month is not None:
            parts.append(f"time={_MONTH_NAMES[scope.month - 1]} {scope.year}")
        else:
            parts.append(f"time={scope.year}")
    return "; ".join(parts)


def _default_story_title(scope: TimelineStoryScope, story_format: StoryFormat) -> str:
    base_title = _FORMAT_TITLES[story_format]
    if scope.query_text:
        return f"{base_title}: {_normalize_text(scope.query_text)}"
    if scope.year is not None and scope.month is not None:
        return f"{base_title}: {_MONTH_NAMES[scope.month - 1]} {scope.year}"
    if scope.year is not None:
        return f"{base_title}: {scope.year}"
    return base_title
