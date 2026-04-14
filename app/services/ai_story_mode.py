from __future__ import annotations

import json
import os
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionSystemMessageParam
from openai.types.chat import ChatCompletionUserMessageParam

from app.env import load_app_env
from app.models import (
    DeckVisualKind,
    Entry,
    GeneratedExecutiveDeck,
    GeneratedExecutiveDeckSlide,
    StoryFormat,
    TimelineStoryScope,
)
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
from .story_mode import order_story_entries


DEFAULT_STORY_INPUT_ENTRY_COUNT = 40
DEFAULT_STORY_ENTRY_SUMMARY_LENGTH = 280
DEFAULT_STORY_GENERATION_TIMEOUT_SECONDS = 120.0
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
EXECUTIVE_DECK_GENERATION_SYSTEM_PROMPT = (
    "You write grounded executive presentation decks from structured timeline entry "
    "context. Return JSON only with this exact schema: {\"title\": string, "
    '\"subtitle\": string|null, \"slides\": [{\"slide_key\": string, '
    '\"headline\": string, \"purpose\": \"title\"|\"toc\"|\"summary\"|'
    '\"section_header\"|\"turning_point\"|\"highlight\"|\"trajectory\"|'
    '\"quote\"|\"close\"|\"thank_you\", '
    '\"body_points\": [string], \"callouts\": [string], '
    '\"visuals\": [{\"kind\": \"kpi_strip\"|\"phase_timeline\"|'
    '\"pull_quote\"|\"bar_chart\"|\"stat_card\"|\"icon_grid\"}], '
    '"citations": [number]}]}. '
    "Do not wrap the JSON in markdown fences. Do not include HTML or Markdown. "
    "Cite only entry_id values that appear in the provided context. "
    "IMPORTANT: This is a visual presentation, NOT a document. Keep slides concise "
    "and impactful. Use short phrases for body_points (max 10-15 words each), "
    "not full sentences. Limit each slide to 2-4 body_points. Use callouts for "
    "punchy one-liners and metrics. Every slide MUST include at least one visual. "
    "Visuals make the deck engaging — use bar_chart for comparisons or trends, "
    "stat_card for key metrics and numbers, kpi_strip for signal dashboards, "
    "icon_grid for categorized items, phase_timeline for ordered milestones, "
    "and pull_quote for impactful quotes. "
    "Keep the deck factual and executive-ready. Never invent events, dates, motives, "
    "or outcomes."
)
_ALLOWED_DECK_PURPOSES = {
    "title",
    "toc",
    "summary",
    "section_header",
    "turning_point",
    "highlight",
    "trajectory",
    "quote",
    "close",
    "thank_you",
}
_ALLOWED_DECK_VISUAL_KINDS: set[DeckVisualKind] = {
    "kpi_strip",
    "phase_timeline",
    "pull_quote",
    "bar_chart",
    "stat_card",
    "icon_grid",
}


StoryEventSink = Callable[[Mapping[str, object]], None] | None


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
        event_sink: StoryEventSink = None,
    ) -> GeneratedTimelineStory: ...

    async def generate_executive_deck(
        self,
        scope: TimelineStoryScope,
        entries: list[Entry],
        *,
        max_entries: int = DEFAULT_STORY_INPUT_ENTRY_COUNT,
        max_entry_summary_length: int = DEFAULT_STORY_ENTRY_SUMMARY_LENGTH,
        event_sink: StoryEventSink = None,
    ) -> GeneratedExecutiveDeck: ...


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
        event_sink: StoryEventSink = None,
    ) -> GeneratedTimelineStory:
        _emit_story_event(event_sink, {"kind": "status", "phase": "prepare", "message": "Preparing story prompt."})
        prepared_entries, older_entries, truncated_input = _prepare_prompt_entry_sets(
            entries,
            max_entries=max_entries,
        )
        if not prepared_entries:
            raise ValueError("Provide at least one entry to generate a story.")

        prompt = _build_user_prompt(
            scope,
            story_format,
            prepared_entries,
            older_entries=older_entries,
            truncated_input=truncated_input,
            max_entry_summary_length=max_entry_summary_length,
        )

        _emit_story_event(event_sink, {"kind": "status", "phase": "generate", "message": "Sending prompt to AI provider."})
        response = await self._client.chat.completions.create(
            model=self._settings.model_id,
            messages=_build_generation_messages(
                prompt,
                system_prompt=STORY_GENERATION_SYSTEM_PROMPT,
            ),
        )
        message = response.choices[0].message.content if response.choices else None
        _emit_story_event(event_sink, {"kind": "status", "phase": "finalize", "message": "Parsing response and linking citations."})
        return _finalize_story(
            message or "",
            scope,
            story_format,
            prepared_entries,
            provider_name=self.provider_name,
            truncated_input=truncated_input,
        )

    async def generate_executive_deck(
        self,
        scope: TimelineStoryScope,
        entries: list[Entry],
        *,
        max_entries: int = DEFAULT_STORY_INPUT_ENTRY_COUNT,
        max_entry_summary_length: int = DEFAULT_STORY_ENTRY_SUMMARY_LENGTH,
        event_sink: StoryEventSink = None,
    ) -> GeneratedExecutiveDeck:
        _emit_story_event(event_sink, {"kind": "status", "phase": "prepare", "message": "Preparing deck prompt."})
        prepared_entries, older_entries, truncated_input = _prepare_prompt_entry_sets(
            entries,
            max_entries=max_entries,
        )
        if not prepared_entries:
            raise ValueError("Provide at least one entry to generate a deck.")

        prompt = _build_deck_user_prompt(
            scope,
            prepared_entries,
            older_entries=older_entries,
            truncated_input=truncated_input,
            max_entry_summary_length=max_entry_summary_length,
        )

        _emit_story_event(event_sink, {"kind": "status", "phase": "generate", "message": "Sending prompt to AI provider."})
        response = await self._client.chat.completions.create(
            model=self._settings.model_id,
            messages=_build_generation_messages(
                prompt,
                system_prompt=EXECUTIVE_DECK_GENERATION_SYSTEM_PROMPT,
            ),
        )
        message = response.choices[0].message.content if response.choices else None
        _emit_story_event(event_sink, {"kind": "status", "phase": "finalize", "message": "Parsing response and structuring slides."})
        return _finalize_deck(
            message or "",
            scope,
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
        event_sink: StoryEventSink = None,
    ) -> GeneratedTimelineStory:
        _emit_story_event(event_sink, {"kind": "status", "phase": "prepare", "message": "Preparing story prompt."})
        prepared_entries, older_entries, truncated_input = _prepare_prompt_entry_sets(
            entries,
            max_entries=max_entries,
        )
        if not prepared_entries:
            raise ValueError("Provide at least one entry to generate a story.")

        prompt = _build_user_prompt(
            scope,
            story_format,
            prepared_entries,
            older_entries=older_entries,
            truncated_input=truncated_input,
            max_entry_summary_length=max_entry_summary_length,
        )
        _emit_story_event(event_sink, {"kind": "status", "phase": "generate", "message": "Sending prompt to Copilot."})
        response_content = await self._generate_response_content(
            prompt,
            system_message=STORY_GENERATION_SYSTEM_PROMPT,
            event_sink=event_sink,
        )
        _emit_story_event(event_sink, {"kind": "status", "phase": "finalize", "message": "Parsing response and linking citations."})
        return _finalize_story(
            response_content,
            scope,
            story_format,
            prepared_entries,
            provider_name=self.provider_name,
            truncated_input=truncated_input,
        )

    async def generate_executive_deck(
        self,
        scope: TimelineStoryScope,
        entries: list[Entry],
        *,
        max_entries: int = DEFAULT_STORY_INPUT_ENTRY_COUNT,
        max_entry_summary_length: int = DEFAULT_STORY_ENTRY_SUMMARY_LENGTH,
        event_sink: StoryEventSink = None,
    ) -> GeneratedExecutiveDeck:
        _emit_story_event(event_sink, {"kind": "status", "phase": "prepare", "message": "Preparing deck prompt."})
        prepared_entries, older_entries, truncated_input = _prepare_prompt_entry_sets(
            entries,
            max_entries=max_entries,
        )
        if not prepared_entries:
            raise ValueError("Provide at least one entry to generate a deck.")

        prompt = _build_deck_user_prompt(
            scope,
            prepared_entries,
            older_entries=older_entries,
            truncated_input=truncated_input,
            max_entry_summary_length=max_entry_summary_length,
        )
        _emit_story_event(event_sink, {"kind": "status", "phase": "generate", "message": "Sending prompt to Copilot."})
        response_content = await self._generate_response_content(
            prompt,
            system_message=EXECUTIVE_DECK_GENERATION_SYSTEM_PROMPT,
            event_sink=event_sink,
        )
        _emit_story_event(event_sink, {"kind": "status", "phase": "finalize", "message": "Parsing response and structuring slides."})
        return _finalize_deck(
            response_content,
            scope,
            prepared_entries,
            provider_name=self.provider_name,
            truncated_input=truncated_input,
        )

    async def _generate_response_content(
        self,
        prompt: str,
        *,
        system_message: str,
        event_sink: StoryEventSink = None,
    ) -> str:
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
                    system_message=system_message,
                )
                active_session = await copilot_runtime.prepare_copilot_resource(
                    exit_stack, session
                )
                unsubscribe = _subscribe_to_story_session_events(
                    active_session, event_sink=event_sink, phase="generate"
                )
                try:
                    response = await copilot_runtime.send_copilot_prompt(
                        active_session,
                        prompt,
                        timeout=get_story_generation_timeout_seconds(),
                    )
                finally:
                    unsubscribe()
        except TimeoutError as exc:
            raise StoryGenerationError(
                "Story generation timed out. The scope may be too large or "
                "the AI provider is slow. Try narrowing the scope and generating again."
            ) from exc
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
    event_sink: StoryEventSink = None,
) -> GeneratedTimelineStory:
    generator = get_story_generator()
    return await generator.generate_story(
        scope,
        story_format,
        entries,
        max_entries=max_entries,
        max_entry_summary_length=max_entry_summary_length,
        event_sink=event_sink,
    )


async def generate_executive_deck(
    scope: TimelineStoryScope,
    entries: list[Entry],
    *,
    max_entries: int = DEFAULT_STORY_INPUT_ENTRY_COUNT,
    max_entry_summary_length: int = DEFAULT_STORY_ENTRY_SUMMARY_LENGTH,
    event_sink: StoryEventSink = None,
) -> GeneratedExecutiveDeck:
    generator = get_story_generator()
    return await generator.generate_executive_deck(
        scope,
        entries,
        max_entries=max_entries,
        max_entry_summary_length=max_entry_summary_length,
        event_sink=event_sink,
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


def get_story_generation_timeout_seconds() -> float:
    load_app_env()
    raw = os.getenv("EVENTTRACKER_STORY_GENERATION_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_STORY_GENERATION_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STORY_GENERATION_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_STORY_GENERATION_TIMEOUT_SECONDS


def _build_generation_messages(
    prompt: str,
    *,
    system_prompt: str,
) -> list[ChatCompletionMessageParam]:
    system_message: ChatCompletionSystemMessageParam = {
        "role": "system",
        "content": system_prompt,
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


def _finalize_deck(
    value: str,
    scope: TimelineStoryScope,
    entries: list[Entry],
    *,
    provider_name: str,
    truncated_input: bool,
) -> GeneratedExecutiveDeck:
    deck = _parse_deck_generation_response(
        value,
        allowed_entry_ids={entry.id for entry in entries},
    )
    if not deck.title:
        deck.title = _default_deck_title(scope)
    deck.provider_name = provider_name
    deck.source_entry_count = len(entries)
    deck.truncated_input = truncated_input
    return deck


# Generated by GitHub Copilot
def _prepare_prompt_entry_sets(
    entries: list[Entry],
    *,
    max_entries: int | None,
) -> tuple[list[Entry], list[Entry], bool]:
    """Split scoped entries into detailed recent entries and compact older background context."""
    ordered_entries = order_story_entries(entries)
    if max_entries is None or len(ordered_entries) <= max_entries:
        return ordered_entries, [], False
    if max_entries <= 0:
        raise ValueError("max_entries must be greater than zero.")
    return ordered_entries[-max_entries:], ordered_entries[:-max_entries], True


def _build_user_prompt(
    scope: TimelineStoryScope,
    story_format: StoryFormat,
    entries: list[Entry],
    *,
    older_entries: list[Entry] | None = None,
    truncated_input: bool,
    max_entry_summary_length: int,
) -> str:
    scope_summary = _describe_scope(scope)
    summarized_entries = older_entries or []
    prompt = [
        "Create a structured Timeline Story Mode narrative.",
        f"Requested format: {story_format}",
        f"Format guidance: {_FORMAT_GUIDANCE[story_format]}",
        f"Scope: {scope_summary}",
    ]
    if summarized_entries:
        prompt.append(f"Detailed recent entry count: {len(entries)}")
        prompt.append(f"Older summarized entry count: {len(summarized_entries)}")
    else:
        prompt.append(f"Entry count in prompt: {len(entries)}")
    if truncated_input:
        prompt.append(
            "Detailed context is limited to the most recent scoped entries. A compact older-history summary is included below for background context. Do not cite the summary directly."
        )
    prompt.append(
        "Use only the entry_id values provided below when creating citations. Repeat a "
        "citation entry_id when the same entry supports multiple sections."
    )
    if summarized_entries:
        prompt.append(
            f"Older history summary ({len(summarized_entries)} earlier scoped entr{'y' if len(summarized_entries) == 1 else 'ies'}; background only, not for direct citation):"
        )
        prompt.append(
            _format_older_entry_context(
                summarized_entries,
                max_entry_summary_length=max_entry_summary_length,
            )
        )
        prompt.append(
            f"Detailed recent entries for citation and specifics ({len(entries)} most recent scoped entr{'y' if len(entries) == 1 else 'ies'}) are ordered chronologically from oldest to newest:"
        )
    else:
        prompt.append("Entries are ordered chronologically from oldest to newest:")
    prompt.append(_format_entry_context(entries, max_entry_summary_length))
    return "\n".join(prompt)


def _build_deck_user_prompt(
    scope: TimelineStoryScope,
    entries: list[Entry],
    *,
    older_entries: list[Entry] | None = None,
    truncated_input: bool,
    max_entry_summary_length: int,
) -> str:
    summarized_entries = older_entries or []
    prompt = [
        "Create a structured executive presentation deck from the current Timeline Story scope.",
        "",
        "Deck structure (follow this exact order — every slide listed below is REQUIRED):",
        "1. TITLE slide (purpose: title) — bold, compelling title and subtitle. No body_points needed. Use exactly one visual, ideally stat_card or kpi_strip when you have two concise metrics.",
        "2. TABLE OF CONTENTS slide (purpose: toc) — list 4-6 agenda items in body_points (short labels, not sentences). Use icon_grid visual.",
        "3. SUMMARY slide (purpose: summary) — 2-3 short bullet points with stat_card visual for key metrics.",
        "4. One or more SECTION HEADER slides (purpose: section_header) — a bold transition headline before each section. No body_points, just a headline and subtitle in callouts.",
        "5. TURNING POINT slides (purpose: turning_point) — one per major development. 2-3 short bullet points + pull_quote visual.",
        "6. HIGHLIGHT slides (purpose: highlight) — use bar_chart or stat_card visuals for data-rich slides. 2-3 bullet points max.",
        "7. TRAJECTORY slide (purpose: trajectory) — where things are heading. Use phase_timeline visual.",
        "8. QUOTE slide (purpose: quote) — a single powerful pull_quote. No body_points needed.",
        "9. CLOSE slide (purpose: close) — 2-3 key takeaway bullets with kpi_strip visual.",
        "10. THANK YOU slide (purpose: thank_you) — headline is 'Thank You!' with a subtitle. No body_points.",
        "",
        "Aim for 10 to 16 slides total. More entries = more slides. Include multiple turning_point and highlight slides.",
        "",
        "Content guidelines (THIS IS A PRESENTATION, NOT A DOCUMENT):",
        "- Keep text MINIMAL. Max 2-4 body_points per slide, each under 15 words.",
        "- Callouts should be punchy: a metric, a number, a short phrase (e.g. '3x growth', '47 events tracked').",
        "- Headlines must be declarative statements (e.g. 'Revenue doubled after Q3 pivot' not 'Revenue Update').",
        "- Do not repeat the same phrase or metric across headline, subtitle, body_points, callouts, or multiple visuals on the same slide.",
        "- EVERY slide MUST include at least one visual. This is critical for engagement.",
        "- Use bar_chart for comparisons and trends (callouts become bar labels and body_points become bar data descriptions).",
        "- Use stat_card for key metrics and standout numbers (callouts become the big numbers).",
        "- Use icon_grid for categorized lists (body_points become grid items).",
        "- Use kpi_strip for signal dashboards (callouts become signal labels).",
        "- Use phase_timeline for ordered milestones (body_points become the phases).",
        "- Use pull_quote for impactful direct quotes.",
        "- Every content slide should cite at least one entry.",
        "",
        "Allowed slide purposes: title, toc, summary, section_header, turning_point, highlight, trajectory, quote, close, thank_you.",
        "Allowed visual kinds: kpi_strip, phase_timeline, pull_quote, bar_chart, stat_card, icon_grid.",
        f"Scope: {_describe_scope(scope)}",
    ]
    if summarized_entries:
        prompt.append(f"Detailed recent entry count: {len(entries)}")
        prompt.append(f"Older summarized entry count: {len(summarized_entries)}")
    else:
        prompt.append(f"Entry count in prompt: {len(entries)}")
    if truncated_input:
        prompt.append(
            "Detailed context is limited to the most recent scoped entries. A compact older-history summary is included below for background context. Do not cite the summary directly."
        )
    prompt.append(
        "Use only the entry_id values provided below when citing evidence for each slide."
    )
    if summarized_entries:
        prompt.append(
            f"Older history summary ({len(summarized_entries)} earlier scoped entr{'y' if len(summarized_entries) == 1 else 'ies'}; background only, not for direct citation):"
        )
        prompt.append(
            _format_older_entry_context(
                summarized_entries,
                max_entry_summary_length=max_entry_summary_length,
            )
        )
        prompt.append(
            f"Detailed recent entries for citation and specifics ({len(entries)} most recent scoped entr{'y' if len(entries) == 1 else 'ies'}) are ordered chronologically from oldest to newest:"
        )
    else:
        prompt.append("Entries are ordered chronologically from oldest to newest:")
    prompt.append(_format_entry_context(entries, max_entry_summary_length))
    return "\n".join(prompt)


# Generated by GitHub Copilot
def _format_older_entry_context(
    entries: list[Entry],
    *,
    max_entry_summary_length: int,
    max_buckets: int = 5,
) -> str:
    """Collapse older entries into compact chronological buckets for background context."""
    if not entries:
        return ""
    bucket_size = max(1, (len(entries) + max_buckets - 1) // max_buckets)
    highlight_length = max(48, min(96, max_entry_summary_length // 3))
    lines: list[str] = []
    for start_index in range(0, len(entries), bucket_size):
        bucket = entries[start_index : start_index + bucket_size]
        lines.append(
            _format_older_entry_bucket(
                bucket,
                highlight_length=highlight_length,
            )
        )
    return "\n".join(lines)


# Generated by GitHub Copilot
def _format_older_entry_bucket(
    entries: list[Entry],
    *,
    highlight_length: int,
) -> str:
    """Format a single chronological bucket of older entries into one compact line."""
    representatives = [entries[0]]
    representative_ids = {entries[0].id}
    if len(entries) > 2:
        middle_entry = entries[len(entries) // 2]
        if middle_entry.id not in representative_ids:
            representatives.append(middle_entry)
            representative_ids.add(middle_entry.id)
    if len(entries) > 1 and entries[-1].id not in representative_ids:
        representatives.append(entries[-1])

    highlights = "; ".join(
        _format_entry_background_highlight(
            entry,
            max_summary_length=highlight_length,
        )
        for entry in representatives
    )
    entry_noun = "entry" if len(entries) == 1 else "entries"
    return (
        f"- {_format_entry_date_range(entries[0], entries[-1])} | {len(entries)} {entry_noun} | "
        f"highlights: {highlights}"
    )


# Generated by GitHub Copilot
def _format_entry_background_highlight(
    entry: Entry,
    *,
    max_summary_length: int,
) -> str:
    """Build a short title-and-preview highlight for summarized older entry context."""
    title = _normalize_text(entry.title) or "Untitled entry"
    summary = _normalize_text(
        preview_text(entry.final_text, max_length=max_summary_length)
    )
    if ". " in summary:
        first_sentence = summary.split(". ", 1)[0].strip()
        if first_sentence:
            summary = first_sentence if first_sentence.endswith(".") else f"{first_sentence}."
    if not summary or summary.casefold() == title.casefold():
        return title
    if summary.casefold().startswith(title.casefold()):
        return summary
    return f"{title}: {summary}"


# Generated by GitHub Copilot
def _format_entry_date_range(start_entry: Entry, end_entry: Entry) -> str:
    """Return a readable range label for a summarized chronological bucket."""
    start_label = _format_entry_date(start_entry)
    end_label = _format_entry_date(end_entry)
    if start_label == end_label:
        return start_label
    return f"{start_label} to {end_label}"


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
    content = _strip_structured_content(value)
    if not content:
        raise StoryGenerationError("The AI provider returned an empty story.")

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


def _parse_deck_generation_response(
    value: str,
    *,
    allowed_entry_ids: set[int],
) -> GeneratedExecutiveDeck:
    content = _strip_structured_content(value)
    if not content:
        raise StoryGenerationError("The AI provider returned an empty deck.")

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

    raw_slides = payload.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise StoryGenerationError(
            "The AI provider returned invalid structured output."
        )

    slides: list[GeneratedExecutiveDeckSlide] = []
    for raw_slide in raw_slides:
        if not isinstance(raw_slide, dict):
            continue

        headline = _normalize_text(str(raw_slide.get("headline", "")))
        purpose = _normalize_text(str(raw_slide.get("purpose", ""))).lower()
        body_points = _coerce_text_list(raw_slide.get("body_points"))
        callouts = _coerce_text_list(raw_slide.get("callouts"))
        visuals = _parse_deck_visuals(raw_slide.get("visuals"))
        citations = _parse_deck_citations(
            raw_slide.get("citations"),
            allowed_entry_ids=allowed_entry_ids,
        )
        if not headline:
            continue
        if purpose not in _ALLOWED_DECK_PURPOSES:
            purpose = "summary"
        _PURPOSES_WITHOUT_REQUIRED_BODY = {"title", "toc", "section_header", "quote", "close", "thank_you"}
        if not body_points and not callouts and not visuals and purpose not in _PURPOSES_WITHOUT_REQUIRED_BODY:
            continue

        slides.append(
            GeneratedExecutiveDeckSlide(
                slide_key=_normalize_slide_key(
                    str(raw_slide.get("slide_key", "")),
                    fallback=headline,
                ),
                headline=headline,
                purpose=purpose,
                body_points=body_points,
                callouts=callouts,
                visuals=visuals,
                citations=citations,
            )
        )

    if not slides:
        raise StoryGenerationError(
            "The AI provider returned a deck with no usable slides."
        )

    return GeneratedExecutiveDeck(
        title=_normalize_text(str(payload.get("title", ""))),
        subtitle=_normalize_optional_label_text(payload.get("subtitle")),
        slides=_normalize_deck_slide_order(slides),
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


def _strip_structured_content(value: str) -> str:
    """Remove optional markdown fences before JSON parsing."""
    content = value.strip()
    if not content:
        return ""
    if not content.startswith("```"):
        return content

    lines = content.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _coerce_text_list(value: object) -> list[str]:
    """Normalize a list of short text items from structured output."""
    if value is None:
        return []
    if not isinstance(value, list):
        if isinstance(value, str) and value.strip():
            return [_normalize_story_text(value)]
        return []
    normalized_items = [_normalize_story_text(str(item)) for item in value]
    return [item for item in normalized_items if item]


def _parse_deck_visuals(value: object) -> list[DeckVisualKind]:
    """Validate the requested visual kinds for a generated slide."""
    if value is None or not isinstance(value, list):
        return []

    visuals: list[DeckVisualKind] = []
    for raw_visual in value:
        if isinstance(raw_visual, dict):
            kind_value = raw_visual.get("kind")
        elif isinstance(raw_visual, str):
            kind_value = raw_visual
        else:
            continue

        normalized_kind = _normalize_text(str(kind_value or "")).lower()
        if normalized_kind in _ALLOWED_DECK_VISUAL_KINDS and normalized_kind not in visuals:
            visuals.append(cast(DeckVisualKind, normalized_kind))
    return visuals


def _parse_deck_citations(
    value: object,
    *,
    allowed_entry_ids: set[int],
) -> list[int]:
    """Validate and de-duplicate slide citations against scoped entries."""
    if value is None or not isinstance(value, list):
        return []

    citations: list[int] = []
    for raw_entry_id in value:
        entry_id = _coerce_required_int(raw_entry_id)
        if entry_id is not None and entry_id in allowed_entry_ids and entry_id not in citations:
            citations.append(entry_id)
    return citations


def _normalize_slide_key(value: str, *, fallback: str) -> str:
    """Create a stable slug for slide keys, even from imperfect model output."""
    candidate = _normalize_text(value) or _normalize_text(fallback) or "slide"
    parts: list[str] = []
    current: list[str] = []
    for character in candidate.lower():
        if character.isalnum():
            current.append(character)
            continue
        if current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return "-".join(parts) or "slide"


def _normalize_deck_slide_order(
    slides: list[GeneratedExecutiveDeckSlide],
) -> list[GeneratedExecutiveDeckSlide]:
    """Keep title slides first and closing slides last with unique slide keys."""
    _HEAD_PURPOSES = {"title", "toc"}
    _TAIL_PURPOSES = {"close", "thank_you"}
    title_slides = [slide for slide in slides if slide.purpose == "title"]
    toc_slides = [slide for slide in slides if slide.purpose == "toc"]
    body_slides = [
        slide for slide in slides if slide.purpose not in _HEAD_PURPOSES | _TAIL_PURPOSES
    ]
    close_slides = [slide for slide in slides if slide.purpose == "close"]
    thank_you_slides = [slide for slide in slides if slide.purpose == "thank_you"]
    ordered_slides = title_slides + toc_slides + body_slides + close_slides + thank_you_slides

    seen_keys: dict[str, int] = {}
    for slide in ordered_slides:
        seen_keys.setdefault(slide.slide_key, 0)
        seen_keys[slide.slide_key] += 1
        if seen_keys[slide.slide_key] > 1:
            slide.slide_key = f"{slide.slide_key}-{seen_keys[slide.slide_key]}"
    return ordered_slides


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


def _normalize_optional_label_text(value: object) -> str | None:
    """Normalize short label-style text such as titles and subtitles."""
    if value is None:
        return None
    normalized = _normalize_text(str(value))
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


def _default_deck_title(scope: TimelineStoryScope) -> str:
    """Build a fallback executive-deck title when the model omits one."""
    if scope.query_text:
        return f"Executive Deck: {_normalize_text(scope.query_text)}"
    if scope.year is not None and scope.month is not None:
        return f"Executive Deck: {_MONTH_NAMES[scope.month - 1]} {scope.year}"
    if scope.year is not None:
        return f"Executive Deck: {scope.year}"
    return "Executive Deck"


def _emit_story_event(
    event_sink: StoryEventSink,
    payload: Mapping[str, object],
) -> None:
    if event_sink is None:
        return
    event_sink(payload)


def _subscribe_to_story_session_events(
    session: copilot_runtime.CopilotSession,
    *,
    event_sink: StoryEventSink,
    phase: str,
) -> Callable[[], None]:
    if event_sink is None:
        return lambda: None

    def handle_event(event: object) -> None:
        try:
            payload = _build_story_session_event_payload(event, phase=phase)
            if payload is not None:
                _emit_story_event(event_sink, payload)
        except Exception:
            pass  # Never let event handling crash the Copilot session

    return copilot_runtime.subscribe_to_session_events(session, handle_event)


def _build_story_session_event_payload(
    event: object, *, phase: str
) -> Mapping[str, object] | None:
    event_type = _normalize_story_event_type(getattr(event, "type", None))
    if not event_type:
        return None

    return {
        "kind": "copilot_event",
        "phase": phase,
        "eventType": event_type,
        "message": _summarize_story_session_event(event),
        "raw": _serialize_story_event_value(event),
    }


def _normalize_story_event_type(value: object) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _summarize_story_session_event(event: object) -> str | None:
    data = getattr(event, "data", None)
    for attr_name in (
        "delta_content",
        "progress_message",
        "partial_output",
        "reasoning_text",
        "content",
        "message",
        "summary_content",
        "reason",
    ):
        value = getattr(data, attr_name, None)
        if value:
            return str(value)

    tool_name = getattr(data, "tool_name", None)
    if tool_name:
        return f"Tool event: {tool_name}"

    return None


def _serialize_story_event_value(
    value: object, *, depth: int = 0
) -> object:
    if depth >= 5:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _serialize_story_event_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _serialize_story_event_value(item, depth=depth + 1)
            for item in value
        ]

    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _serialize_story_event_value(to_dict(), depth=depth + 1)
        except Exception:
            return str(value)

    slots = getattr(value, "__slots__", None)
    if slots:
        return {
            slot: _serialize_story_event_value(
                getattr(value, slot, None), depth=depth + 1
            )
            for slot in slots
            if not slot.startswith("_")
        }

    value_dict = getattr(value, "__dict__", None)
    if isinstance(value_dict, dict):
        return {
            str(key): _serialize_story_event_value(item, depth=depth + 1)
            for key, item in value_dict.items()
            if not str(key).startswith("_")
        }

    return str(value)
