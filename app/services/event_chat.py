from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from functools import lru_cache
import logging
import os
import re
import sqlite3
from typing import Protocol, TypedDict

from app.env import load_app_env
from app.models import SearchResult
from app.services import copilot_runtime
from app.services.ai_generate import (
    CopilotSettings,
    DEFAULT_AI_PROVIDER,
    SUPPORTED_AI_PROVIDERS,
    load_copilot_settings,
)
from app.services.copilot_runtime import (
    COPILOT_CLIENT_SETTINGS_MESSAGE,
    COPILOT_SDK_REQUIRED_MESSAGE,
)
from app.services.entries import plain_text_from_html
from app.services.search import search_entries


logger = logging.getLogger(__name__)

MAX_EVENT_CHAT_QUESTION_LENGTH = 500
DEFAULT_EVENT_CHAT_RESULT_LIMIT = 8
DEFAULT_EVENT_CHAT_PREVIEW_CHARS = 280
EVENT_CHAT_PROVIDER_REQUIRED_MESSAGE = (
    "Event chat is currently available only when GitHub Copilot is the active AI provider."
)
EVENT_CHAT_GENERIC_ERROR_MESSAGE = "Could not generate a grounded answer right now."
EVENT_CHAT_NO_RESULTS_MESSAGE = (
    "I couldn't find relevant stored events for that question in this scope. Try a narrower group or different terms."
)
EVENT_CHAT_SYSTEM_PROMPT = (
    "You answer questions about stored timeline events using only the retrieved entry context provided to you. "
    "Answer in plain text with short paragraphs. "
    "Do not invent facts, dates, people, motives, or outcomes. "
    "If the retrieved entries do not contain enough evidence, say so plainly. "
    "When you reference evidence, cite the entry id inline using the format [Entry 123]."
)
_COPILOT_ANSWER_DELTA_EVENT_RE = re.compile(r"assistant[._].*delta", re.IGNORECASE)
_COPILOT_REASONING_EVENT_RE = re.compile(r"reason(?:ing)?", re.IGNORECASE)


class EventChatError(Exception):
    pass


class EventChatConfigurationError(EventChatError):
    pass


@dataclass(frozen=True, slots=True)
class EventChatCitation:
    entry_id: int
    title: str
    display_date: str
    group_name: str
    tags: list[str]
    preview_text: str
    rank: float


class EventChatCitationPayload(TypedDict):
    entry_id: int
    title: str
    display_date: str
    group_name: str
    tags: list[str]
    preview_text: str
    url: str


class EventChatStreamEventBase(TypedDict):
    kind: str


class EventChatStreamEvent(EventChatStreamEventBase, total=False):
    text: str
    items: list[EventChatCitationPayload]
    message: str
    ok: bool


class EventChatGenerator(Protocol):
    provider_name: str

    def stream_answer(
        self,
        question: str,
        citations: Sequence[EventChatCitation],
    ) -> AsyncGenerator[str, None]: ...


class CopilotEventChatGenerator:
    provider_name = "copilot"

    def __init__(self, settings: CopilotSettings) -> None:
        self._settings = settings

    async def stream_answer(
        self,
        question: str,
        citations: Sequence[EventChatCitation],
    ) -> AsyncGenerator[str, None]:
        prompt = build_event_chat_prompt(question, citations)
        try:
            client = copilot_runtime.instantiate_copilot_client(
                self._settings,
                configuration_error_type=EventChatConfigurationError,
                missing_sdk_message=COPILOT_SDK_REQUIRED_MESSAGE,
                invalid_settings_message=COPILOT_CLIENT_SETTINGS_MESSAGE,
            )
            async with AsyncExitStack() as exit_stack:
                active_client = await copilot_runtime.prepare_copilot_client(
                    exit_stack, client
                )
                session = await copilot_runtime.create_copilot_session(
                    active_client,
                    model_id=self._settings.model_id,
                    system_message=EVENT_CHAT_SYSTEM_PROMPT,
                    reasoning_effort="low",
                    streaming=True,
                )
                active_session = await copilot_runtime.prepare_copilot_resource(
                    exit_stack, session
                )
                async for chunk in _stream_copilot_answer(active_session, prompt):
                    yield chunk
        except EventChatError:
            raise
        except Exception as exc:
            raise EventChatConfigurationError(
                "GitHub Copilot event chat is not configured correctly. Install the "
                "GitHub Copilot SDK and ensure the Copilot CLI is available. If "
                "`copilot --version` already works, leave COPILOT_CLI_PATH and "
                "COPILOT_CLI_URL blank unless you intentionally need an override."
            ) from exc


def normalize_event_chat_question(raw_question: str) -> str:
    normalized_question = " ".join(raw_question.split())
    if not normalized_question:
        raise ValueError("Enter a question to ask about your events.")
    if len(normalized_question) > MAX_EVENT_CHAT_QUESTION_LENGTH:
        raise ValueError(
            f"Questions must be {MAX_EVENT_CHAT_QUESTION_LENGTH} characters or fewer."
        )
    return normalized_question


def retrieve_event_chat_citations(
    connection: sqlite3.Connection,
    question: str,
    *,
    group_id: int | None = None,
    limit: int = DEFAULT_EVENT_CHAT_RESULT_LIMIT,
) -> list[EventChatCitation]:
    normalized_question = normalize_event_chat_question(question)
    results = search_entries(connection, normalized_question, group_id=group_id)
    return build_event_chat_citations(results, limit=limit)


def build_event_chat_citations(
    results: Sequence[SearchResult],
    *,
    limit: int = DEFAULT_EVENT_CHAT_RESULT_LIMIT,
    preview_chars: int = DEFAULT_EVENT_CHAT_PREVIEW_CHARS,
) -> list[EventChatCitation]:
    citations: list[EventChatCitation] = []
    normalized_limit = max(1, limit)

    for result in results[:normalized_limit]:
        entry = result.entry
        preview_text = _build_citation_preview(result, preview_chars=preview_chars)
        citations.append(
            EventChatCitation(
                entry_id=entry.id,
                title=entry.title,
                display_date=entry.display_date,
                group_name=entry.group_name,
                tags=list(entry.tags),
                preview_text=preview_text,
                rank=float(result.rank),
            )
        )

    return citations


def build_event_chat_prompt(
    question: str,
    citations: Sequence[EventChatCitation],
) -> str:
    normalized_question = normalize_event_chat_question(question)
    lines = [
        "Question: " + normalized_question,
        "Use only the retrieved entries below to answer.",
        "If the evidence is incomplete, say what is missing instead of guessing.",
        "Do not use outside knowledge or hidden conversation state.",
        "Retrieved entries:",
    ]

    for citation in citations:
        tags_value = ", ".join(citation.tags) if citation.tags else "none"
        lines.extend(
            [
                f"Entry ID: {citation.entry_id}",
                f"Title: {citation.title}",
                f"Date: {citation.display_date}",
                f"Group: {citation.group_name}",
                f"Tags: {tags_value}",
                f"Preview: {citation.preview_text}",
                "",
            ]
        )

    lines.append("Answer in plain text with short paragraphs and cite entry ids inline.")
    return "\n".join(lines).strip()


def build_event_chat_citation_payloads(
    citations: Sequence[EventChatCitation],
) -> list[EventChatCitationPayload]:
    return [
        {
            "entry_id": citation.entry_id,
            "title": citation.title,
            "display_date": citation.display_date,
            "group_name": citation.group_name,
            "tags": list(citation.tags),
            "preview_text": citation.preview_text,
            "url": f"/entries/{citation.entry_id}/view",
        }
        for citation in citations
    ]


async def stream_event_chat_answer(
    question: str,
    citations: Sequence[EventChatCitation],
) -> AsyncGenerator[EventChatStreamEvent, None]:
    normalized_question = normalize_event_chat_question(question)
    if not citations:
        yield {
            "kind": "answer_chunk",
            "text": EVENT_CHAT_NO_RESULTS_MESSAGE,
        }
        yield {"kind": "citations", "items": []}
        yield {"kind": "complete", "ok": True}
        return

    try:
        generator = get_event_chat_generator()
        async for chunk in generator.stream_answer(normalized_question, citations):
            if chunk:
                yield {"kind": "answer_chunk", "text": chunk}
    except EventChatConfigurationError as exc:
        logger.warning("Event chat configuration issue: %s", exc)
        yield {"kind": "error", "message": str(exc)}
        yield {"kind": "complete", "ok": False}
        return
    except EventChatError:
        logger.exception("Event chat generation failed")
        yield {"kind": "error", "message": EVENT_CHAT_GENERIC_ERROR_MESSAGE}
        yield {"kind": "complete", "ok": False}
        return
    except Exception:
        logger.exception("Unexpected event chat failure")
        yield {"kind": "error", "message": EVENT_CHAT_GENERIC_ERROR_MESSAGE}
        yield {"kind": "complete", "ok": False}
        return

    yield {
        "kind": "citations",
        "items": build_event_chat_citation_payloads(citations),
    }
    yield {"kind": "complete", "ok": True}


async def stream_event_chat_events(
    connection: sqlite3.Connection,
    question: str,
    *,
    group_id: int | None = None,
    limit: int = DEFAULT_EVENT_CHAT_RESULT_LIMIT,
) -> AsyncGenerator[EventChatStreamEvent, None]:
    citations = retrieve_event_chat_citations(
        connection,
        question,
        group_id=group_id,
        limit=limit,
    )
    async for event in stream_event_chat_answer(question, citations):
        yield event


@lru_cache(maxsize=1)
def get_event_chat_generator() -> EventChatGenerator:
    provider = load_event_chat_ai_provider()
    if provider == "copilot":
        return CopilotEventChatGenerator(load_copilot_settings())
    raise EventChatConfigurationError(EVENT_CHAT_PROVIDER_REQUIRED_MESSAGE)


def load_event_chat_ai_provider() -> str:
    load_app_env()
    provider = os.getenv("EVENTTRACKER_AI_PROVIDER", DEFAULT_AI_PROVIDER).strip().lower()
    if provider not in SUPPORTED_AI_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_AI_PROVIDERS))
        raise EventChatConfigurationError(
            f"Unsupported EVENTTRACKER_AI_PROVIDER value: {provider}. Use one of: {allowed}."
        )
    return provider


async def _stream_copilot_answer(
    session: copilot_runtime.CopilotSession,
    prompt: str,
) -> AsyncGenerator[str, None]:
    queue: asyncio.Queue[object] = asyncio.Queue()
    unsubscribe = copilot_runtime.subscribe_to_session_events(
        session,
        lambda event: queue.put_nowait(event),
    )
    send_task: asyncio.Task[object] = asyncio.create_task(
        copilot_runtime.send_copilot_prompt(session, prompt, timeout=60.0)
    )
    emitted_chunks: list[str] = []
    response: object | None = None

    try:
        while True:
            if send_task.done() and queue.empty():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
            except TimeoutError:
                continue

            chunk = _extract_copilot_answer_chunk(event)
            if not chunk:
                continue
            emitted_chunks.append(chunk)
            yield chunk

        try:
            response = await send_task
        except TimeoutError as exc:
            raise EventChatError(
                "GitHub Copilot event chat timed out before completing."
            ) from exc
    finally:
        unsubscribe()
        if not send_task.done():
            send_task.cancel()
            with suppress(asyncio.CancelledError):
                await send_task

    content = copilot_runtime.extract_copilot_message_content(response)
    if not content and not emitted_chunks:
        raise EventChatError("The AI provider returned an empty answer.")
    if content and not emitted_chunks:
        yield content
        return

    emitted_text = "".join(emitted_chunks)
    if content.startswith(emitted_text):
        remainder = content[len(emitted_text) :]
        if remainder:
            yield remainder


def _extract_copilot_answer_chunk(event: object) -> str | None:
    event_type = _normalize_event_type(getattr(event, "type", None))
    if not event_type:
        return None
    if not _COPILOT_ANSWER_DELTA_EVENT_RE.search(event_type):
        return None
    if _COPILOT_REASONING_EVENT_RE.search(event_type):
        return None

    data = getattr(event, "data", None)
    for attribute_name in (
        "delta_content",
        "partial_output",
        "content",
        "message",
        "text",
    ):
        value = getattr(data, attribute_name, None)
        if value:
            return str(value)
    return None


def _normalize_event_type(value: object) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _build_citation_preview(
    result: SearchResult,
    *,
    preview_chars: int,
) -> str:
    raw_preview = result.snippet or result.entry.preview_text or result.entry.final_text
    normalized_preview = " ".join(plain_text_from_html(raw_preview).split())
    if not normalized_preview:
        normalized_preview = " ".join(plain_text_from_html(result.entry.final_text).split())
    return _truncate_text(normalized_preview, max_chars=preview_chars)


def _truncate_text(value: str, *, max_chars: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized

    truncated = normalized[: max_chars - 1].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return f"{truncated}…"