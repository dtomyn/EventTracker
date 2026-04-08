from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime
import json
import logging
import os
import re
import time
from typing import Callable, TypeAlias, TypedDict
from urllib.parse import urlparse

import httpx

from app.env import load_app_env
from app.services import copilot_runtime
from app.services.extraction import DEFAULT_HTTP_HEADERS
from app.services.ai_generate import (
    DraftGenerationConfigurationError,
    load_ai_provider,
    load_copilot_settings,
)
from app.services.copilot_runtime import COPILOT_CLIENT_SETTINGS_MESSAGE
from app.services.copilot_runtime import COPILOT_SDK_REQUIRED_MESSAGE


logger = logging.getLogger(__name__)

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


class GroupWebSearchItemPayload(TypedDict):
    title: str
    url: str
    snippet: str
    source: str | None
    article_date: str | None


class GroupWebSearchResponsePayload(TypedDict):
    query: str
    items: list[GroupWebSearchItemPayload]


class GroupWebSearchEventPayload(TypedDict, total=False):
    kind: str
    phase: str
    message: str | None
    eventType: str
    raw: JsonValue


MAX_GROUP_WEB_RESULTS = 5
MIN_GROUP_WEB_RESULTS = 3
DEFAULT_GROUP_WEB_SEARCH_CACHE_TTL_SECONDS = 300
DEFAULT_GROUP_WEB_SEARCH_TIMEOUT_SECONDS = 60.0
DEFAULT_GROUP_WEB_SEARCH_BROADENED_TIMEOUT_SECONDS = 45.0
DEFAULT_GROUP_WEB_SEARCH_REQUEST_TIMEOUT_BUFFER_MS = 5000
DEFAULT_GROUP_WEB_SEARCH_URL_CHECK_TIMEOUT_SECONDS = 5.0
CACHE_KEY_VERSION = "v3"
QUERY_DIVERSITY_STOPWORDS = {
    "agentic",
    "ai",
    "and",
    "announcement",
    "announcements",
    "are",
    "as",
    "be",
    "by",
    "coding",
    "companies",
    "company",
    "days",
    "directly",
    "during",
    "ensure",
    "find",
    "for",
    "from",
    "if",
    "in",
    "is",
    "keep",
    "last",
    "latest",
    "made",
    "march",
    "mention",
    "mentioned",
    "model",
    "models",
    "news",
    "only",
    "public",
    "query",
    "recent",
    "related",
    "relevant",
    "reliable",
    "result",
    "results",
    "search",
    "short",
    "sites",
    "such",
    "that",
    "the",
    "this",
    "tool",
    "tools",
    "topic",
    "trustworthy",
    "use",
    "when",
}
GROUP_WEB_SEARCH_SYSTEM_PROMPT = (
    "You compile concise, current web findings for a timeline sidebar. Use web search "
    "when needed. Return JSON only with this exact schema: "
    '{"query": string, "items": [{"title": string, "url": string, '
    '"snippet": string, "source": string|null, "article_date": string|null}]}. '
    "Do not wrap the JSON in markdown fences. Return at most 5 items. Prefer "
    "authoritative sources. Keep each snippet neutral, factual, and no more than two "
    "short sentences. If a publication date is clearly available, include article_date "
    "as YYYY-MM-DD when possible, otherwise YYYY-MM or YYYY, else null. Aim for 3 to "
    "5 distinct results when credible sources are available. Use at most 2 web "
    "fetches total, then stop and answer with the best results you have."
)


class GroupWebSearchError(Exception):
    pass


class GroupWebSearchConfigurationError(GroupWebSearchError):
    pass


class GroupWebSearchTimeoutError(GroupWebSearchError):
    pass


@dataclass(frozen=True, slots=True)
class GroupWebSearchItem:
    title: str
    url: str
    snippet: str
    source: str | None = None
    article_date: str | None = None


@dataclass(frozen=True, slots=True)
class GroupWebSearchResponse:
    query: str
    items: list[GroupWebSearchItem]

    def to_payload(self) -> GroupWebSearchResponsePayload:
        return {
            "query": self.query,
            "items": [
                {
                    "title": item.title,
                    "url": item.url,
                    "snippet": item.snippet,
                    "source": item.source,
                    "article_date": item.article_date,
                }
                for item in self.items
            ],
        }


@dataclass(frozen=True, slots=True)
class _CachedGroupWebSearchResponse:
    response: GroupWebSearchResponse
    expires_at: float


_GROUP_WEB_SEARCH_CACHE: dict[str, _CachedGroupWebSearchResponse] = {}
GroupWebSearchEventSink = Callable[[GroupWebSearchEventPayload], None]


async def search_group_web(
    query: str,
    *,
    force_refresh: bool = False,
    existing_urls: set[str] | None = None,
    event_sink: GroupWebSearchEventSink | None = None,
) -> GroupWebSearchResponse:
    normalized_query = " ".join(query.strip().split())
    if not normalized_query:
        raise ValueError("Web search query is required.")
    normalized_existing_urls = _normalize_saved_urls_for_matching(existing_urls)

    provider = load_ai_provider()
    if provider != "copilot":
        raise GroupWebSearchConfigurationError(
            "Group web search is only available when EVENTTRACKER_AI_PROVIDER=copilot."
        )

    cache_key = _build_group_web_search_cache_key(normalized_query)
    if force_refresh:
        clear_group_web_search_cache(normalized_query)
        _emit_group_web_search_event(
            event_sink,
            {
                "kind": "status",
                "phase": "initial",
                "message": "Forced refresh requested. Clearing cached result before searching.",
            },
        )
    cached_response = _get_cached_group_web_search(cache_key)
    if cached_response is not None:
        filtered_cached_response, removed_cached_urls = _exclude_saved_urls(
            cached_response,
            saved_urls=normalized_existing_urls,
        )
        (
            filtered_cached_response,
            unreachable_cached_urls,
        ) = await _exclude_unreachable_urls(
            filtered_cached_response,
            event_sink=event_sink,
            phase="cache",
        )
        if not removed_cached_urls and not unreachable_cached_urls:
            _emit_group_web_search_event(
                event_sink,
                {
                    "kind": "status",
                    "phase": "cache",
                    "message": "Using cached web search result.",
                },
            )
            return filtered_cached_response
        cache_message = "Cached results included saved URLs. Requesting fresh links."
        if unreachable_cached_urls:
            cache_message = (
                "Cached results included unreachable URLs. Requesting fresh links."
            )
        _emit_group_web_search_event(
            event_sink,
            {
                "kind": "status",
                "phase": "cache",
                "message": cache_message,
            },
        )

    settings = load_copilot_settings()
    client = copilot_runtime.instantiate_copilot_client(
        settings,
        configuration_error_type=DraftGenerationConfigurationError,
        missing_sdk_message=COPILOT_SDK_REQUIRED_MESSAGE,
        invalid_settings_message=COPILOT_CLIENT_SETTINGS_MESSAGE,
    )
    _emit_group_web_search_event(
        event_sink,
        {
            "kind": "status",
            "phase": "initial",
            "message": "Starting initial web search pass.",
        },
    )

    try:
        async with AsyncExitStack() as exit_stack:
            active_client = await copilot_runtime.prepare_copilot_client(
                exit_stack, client
            )
            session = await _create_search_session(
                active_client,
                settings.model_id,
                streaming=event_sink is not None,
            )
            active_session = await copilot_runtime.prepare_copilot_resource(
                exit_stack, session
            )
            response = await _send_search_prompt(
                active_session,
                normalized_query,
                saved_urls=normalized_existing_urls,
                event_sink=event_sink,
                phase="initial",
            )
    except TimeoutError as exc:
        raise GroupWebSearchTimeoutError(
            "GitHub Copilot web search timed out before completing."
        ) from exc
    except DraftGenerationConfigurationError as exc:
        raise GroupWebSearchConfigurationError(str(exc)) from exc
    except GroupWebSearchError:
        raise
    except Exception as exc:
        raise GroupWebSearchConfigurationError(
            "GitHub Copilot web search is not configured correctly. Install the GitHub "
            "Copilot SDK and ensure the Copilot CLI is available."
        ) from exc

    content = copilot_runtime.extract_copilot_message_content(response)
    if not content:
        raise GroupWebSearchError(
            "The AI provider returned an empty web search response."
        )
    parsed_response = _parse_group_web_search_response(content, normalized_query)
    parsed_response, rejected_saved_urls = _exclude_saved_urls(
        parsed_response,
        saved_urls=normalized_existing_urls,
    )
    parsed_response, unreachable_urls = await _exclude_unreachable_urls(
        parsed_response,
        event_sink=event_sink,
        phase="initial",
    )
    should_request_alternatives = bool(rejected_saved_urls or unreachable_urls)
    if (
        len(parsed_response.items) < MIN_GROUP_WEB_RESULTS
        or should_request_alternatives
    ):
        status_message = (
            "Initial pass returned too few results. Starting broadened pass."
        )
        if unreachable_urls:
            status_message = (
                "Initial pass included unreachable URLs. Requesting different links."
            )
        elif rejected_saved_urls:
            status_message = (
                "Initial pass included URLs already saved in the database. "
                "Requesting different links."
            )
        _emit_group_web_search_event(
            event_sink,
            {
                "kind": "status",
                "phase": "broadened",
                "message": status_message,
            },
        )
        parsed_response = await _broaden_group_web_search(
            settings.model_id,
            normalized_query,
            parsed_response,
            saved_urls=normalized_existing_urls,
            rejected_saved_urls=rejected_saved_urls,
            unreachable_urls=unreachable_urls,
            event_sink=event_sink,
        )

    _store_cached_group_web_search(cache_key, parsed_response)
    _emit_group_web_search_event(
        event_sink,
        {
            "kind": "status",
            "phase": "complete",
            "message": "Web search complete.",
        },
    )
    return parsed_response


async def _create_search_session(
    client: copilot_runtime.CopilotClient, model_id: str, *, streaming: bool = False
) -> copilot_runtime.CopilotSession:
    return await copilot_runtime.create_copilot_session(
        client,
        model_id=model_id,
        system_message=GROUP_WEB_SEARCH_SYSTEM_PROMPT,
        reasoning_effort="low",
        streaming=streaming,
    )


async def _send_search_prompt(
    session: copilot_runtime.CopilotSession,
    query: str,
    *,
    saved_urls: set[str],
    event_sink: GroupWebSearchEventSink | None = None,
    phase: str,
) -> object:
    unsubscribe = _subscribe_to_group_web_search_events(
        session,
        event_sink=event_sink,
        phase=phase,
    )
    try:
        return await copilot_runtime.send_copilot_prompt(
            session,
            _build_search_prompt(query, saved_url_count=len(saved_urls)),
            timeout=get_group_web_search_timeout_seconds(),
        )
    finally:
        unsubscribe()


async def _broaden_group_web_search(
    model_id: str,
    query: str,
    initial_response: GroupWebSearchResponse,
    *,
    saved_urls: set[str],
    rejected_saved_urls: list[str],
    unreachable_urls: list[str],
    event_sink: GroupWebSearchEventSink | None = None,
) -> GroupWebSearchResponse:
    settings = load_copilot_settings()
    client = copilot_runtime.instantiate_copilot_client(
        settings,
        configuration_error_type=DraftGenerationConfigurationError,
        missing_sdk_message=COPILOT_SDK_REQUIRED_MESSAGE,
        invalid_settings_message=COPILOT_CLIENT_SETTINGS_MESSAGE,
    )

    try:
        async with AsyncExitStack() as exit_stack:
            active_client = await copilot_runtime.prepare_copilot_client(
                exit_stack, client
            )
            session = await _create_search_session(
                active_client,
                model_id,
                streaming=event_sink is not None,
            )
            active_session = await copilot_runtime.prepare_copilot_resource(
                exit_stack, session
            )
            response = await _send_broadened_search_prompt(
                active_session,
                query,
                existing_item_urls=[item.url for item in initial_response.items],
                rejected_saved_urls=rejected_saved_urls,
                unreachable_urls=unreachable_urls,
                event_sink=event_sink,
                phase="broadened",
            )
    except Exception:
        logger.debug(
            "Broadened group web search failed; using initial results.", exc_info=True
        )
        return initial_response

    content = copilot_runtime.extract_copilot_message_content(response)
    if not content:
        return initial_response

    broadened_response = _parse_group_web_search_response(content, query)
    broadened_response, _ = _exclude_saved_urls(
        broadened_response,
        saved_urls=saved_urls,
    )
    broadened_response, _ = await _exclude_unreachable_urls(
        broadened_response,
        event_sink=event_sink,
        phase="broadened",
    )
    merged_items: list[GroupWebSearchItem] = []
    seen_urls: set[str] = set()
    for item in [*initial_response.items, *broadened_response.items]:
        dedup_key = _canonicalize_url_for_matching(item.url) or item.url
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)
        merged_items.append(item)
        if len(merged_items) >= MAX_GROUP_WEB_RESULTS:
            break

    return GroupWebSearchResponse(query=query, items=merged_items)


async def _send_broadened_search_prompt(
    session: copilot_runtime.CopilotSession,
    query: str,
    *,
    existing_item_urls: list[str],
    rejected_saved_urls: list[str],
    unreachable_urls: list[str],
    event_sink: GroupWebSearchEventSink | None = None,
    phase: str,
) -> object:
    unsubscribe = _subscribe_to_group_web_search_events(
        session,
        event_sink=event_sink,
        phase=phase,
    )
    try:
        return await copilot_runtime.send_copilot_prompt(
            session,
            _build_broadened_search_prompt(
                query,
                existing_item_urls=existing_item_urls,
                rejected_saved_urls=rejected_saved_urls,
                unreachable_urls=unreachable_urls,
            ),
            timeout=get_group_web_search_broadened_timeout_seconds(),
        )
    finally:
        unsubscribe()


def _subscribe_to_group_web_search_events(
    session: copilot_runtime.CopilotSession,
    *,
    event_sink: GroupWebSearchEventSink | None,
    phase: str,
) -> Callable[[], None]:
    if event_sink is None:
        return lambda: None

    def handle_event(event: object) -> None:
        payload = _build_group_web_search_session_event_payload(event, phase=phase)
        if payload is not None:
            _emit_group_web_search_event(event_sink, payload)

    return copilot_runtime.subscribe_to_session_events(session, handle_event)


def _build_group_web_search_session_event_payload(
    event: object, *, phase: str
) -> GroupWebSearchEventPayload | None:
    event_type = _normalize_group_web_search_event_type(getattr(event, "type", None))
    if not event_type:
        return None

    return {
        "kind": "copilot_event",
        "phase": phase,
        "eventType": event_type,
        "message": _summarize_group_web_search_session_event(event),
        "raw": _serialize_group_web_search_event_value(event),
    }


def _normalize_group_web_search_event_type(value: object) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _summarize_group_web_search_session_event(event: object) -> str | None:
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


def _serialize_group_web_search_event_value(
    value: object, *, depth: int = 0
) -> JsonValue:
    if depth >= 5:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _serialize_group_web_search_event_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _serialize_group_web_search_event_value(item, depth=depth + 1)
            for item in value
        ]

    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _serialize_group_web_search_event_value(
                to_dict(),
                depth=depth + 1,
            )
        except Exception:
            return str(value)

    slots = getattr(value, "__slots__", None)
    if slots:
        return {
            slot: _serialize_group_web_search_event_value(
                getattr(value, slot, None),
                depth=depth + 1,
            )
            for slot in slots
            if not slot.startswith("_")
        }

    value_dict = getattr(value, "__dict__", None)
    if isinstance(value_dict, dict):
        return {
            str(key): _serialize_group_web_search_event_value(item, depth=depth + 1)
            for key, item in value_dict.items()
            if not str(key).startswith("_")
        }

    return str(value)


def _emit_group_web_search_event(
    event_sink: GroupWebSearchEventSink | None,
    payload: GroupWebSearchEventPayload,
) -> None:
    if event_sink is None:
        return
    event_sink(payload)


def _build_search_prompt(query: str, *, saved_url_count: int = 0) -> str:
    lines = [
        "Topic query: " + query,
        "Find up to 5 recent and relevant public-web items for this topic.",
        "Use direct source URLs, keep snippets short, and include article_date when it is clearly available.",
        "Prefer multiple distinct sources instead of repeating one publisher when credible alternatives exist.",
    ]
    if saved_url_count > 0:
        lines.append(
            "Avoid URLs that are already saved in the local timeline database; return different source links."
        )
    focus_terms = _extract_query_focus_terms(query)
    if focus_terms:
        lines.append(
            "When the query names organizations, prefer coverage across those organizations where credible results exist."
        )
        lines.append("Named organizations or focus terms: " + ", ".join(focus_terms))
    lines.append(
        "If you cannot verify enough results quickly, return fewer items or an empty items array."
    )
    lines.append(
        "Only return URLs that resolve successfully on the public web; do not return dead or 404 links."
    )
    return "\n".join(lines)


def _build_broadened_search_prompt(
    query: str,
    *,
    existing_item_urls: list[str],
    rejected_saved_urls: list[str],
    unreachable_urls: list[str],
) -> str:
    lines = [
        "The first strict search likely returned too few usable results.",
        "Original topic query: " + query,
        "Broaden slightly to adjacent, recent agentic coding announcements that still fit the topic and timeframe.",
        "Aim for 3 to 5 distinct results from different credible sources when available.",
        "Use direct source URLs, keep snippets short, and include article_date when it is clearly available.",
        "Return different URLs than the links that were already saved in the local timeline database.",
        "Only return URLs that resolve successfully on the public web; do not return dead or 404 links.",
    ]
    if existing_item_urls:
        lines.append(
            "Do not repeat URLs already returned in this run: "
            + ", ".join(existing_item_urls[:5])
        )
    if rejected_saved_urls:
        lines.append(
            "These URLs were rejected because they already exist in the local timeline database: "
            + ", ".join(rejected_saved_urls[:5])
        )
    if unreachable_urls:
        lines.append(
            "These URLs were rejected because they did not resolve successfully when checked: "
            + ", ".join(unreachable_urls[:5])
        )
    focus_terms = _extract_query_focus_terms(query)
    if focus_terms:
        lines.append(
            "Prefer one strong result per named organization before adding multiple results from the same publisher."
        )
        lines.append("Named organizations or focus terms: " + ", ".join(focus_terms))
    return "\n".join(lines)


def _parse_group_web_search_response(
    value: str, requested_query: str
) -> GroupWebSearchResponse:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise GroupWebSearchError(
                "The AI provider returned invalid web search JSON."
            )
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError as exc:
            raise GroupWebSearchError(
                "The AI provider returned invalid web search JSON."
            ) from exc

    if not isinstance(parsed, dict):
        raise GroupWebSearchError(
            "The AI provider returned an invalid web search payload."
        )

    raw_items = parsed.get("items")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise GroupWebSearchError(
            "The AI provider returned an invalid web search payload."
        )

    normalized_query = " ".join(str(parsed.get("query") or requested_query).split())
    items: list[GroupWebSearchItem] = []
    seen_urls: set[str] = set()

    for raw_item in raw_items:
        item = _parse_group_web_search_item(raw_item)
        if item is None:
            continue
        dedup_key = _canonicalize_url_for_matching(item.url) or item.url
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)
        items.append(item)
        if len(items) >= MAX_GROUP_WEB_RESULTS:
            break

    diverse_items = _select_diverse_group_web_search_items(
        items,
        query=normalized_query or requested_query,
    )
    return GroupWebSearchResponse(
        query=normalized_query or requested_query,
        items=diverse_items,
    )


def _parse_group_web_search_item(raw_item: object) -> GroupWebSearchItem | None:
    if not isinstance(raw_item, Mapping):
        return None

    title = " ".join(str(raw_item.get("title") or "").split())
    url = _normalize_http_url(raw_item.get("url"))
    snippet = " ".join(str(raw_item.get("snippet") or "").split())
    source = " ".join(str(raw_item.get("source") or "").split()) or None
    article_date = _normalize_article_date(
        raw_item.get("article_date") or raw_item.get("date")
    )

    if not title or not url:
        return None

    return GroupWebSearchItem(
        title=title,
        url=url,
        snippet=snippet,
        source=source,
        article_date=article_date,
    )


def _normalize_article_date(value: object) -> str | None:
    candidate = " ".join(str(value or "").strip().split())
    if not candidate:
        return None

    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        return candidate

    return None


def _normalize_http_url(value: object) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        logger.debug("Discarding non-http web search URL: %s", candidate)
        return None
    return candidate


def _canonicalize_url_for_matching(value: object) -> str | None:
    normalized = _normalize_http_url(value)
    if normalized is None:
        return None

    parsed = urlparse(normalized)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold().strip()
    if not host:
        return None

    port = parsed.port
    netloc = host
    if port is not None:
        is_default_port = (scheme == "http" and port == 80) or (
            scheme == "https" and port == 443
        )
        if not is_default_port:
            netloc = f"{host}:{port}"

    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    canonical = f"{scheme}://{netloc}{path}"
    if parsed.query:
        canonical = f"{canonical}?{parsed.query}"
    return canonical


def _normalize_saved_urls_for_matching(saved_urls: set[str] | None) -> set[str]:
    if not saved_urls:
        return set()

    normalized: set[str] = set()
    for url in saved_urls:
        canonical = _canonicalize_url_for_matching(url)
        if canonical:
            normalized.add(canonical)
    return normalized


def _exclude_saved_urls(
    response: GroupWebSearchResponse,
    *,
    saved_urls: set[str],
) -> tuple[GroupWebSearchResponse, list[str]]:
    if not response.items or not saved_urls:
        return response, []

    filtered_items: list[GroupWebSearchItem] = []
    rejected: list[str] = []

    for item in response.items:
        canonical = _canonicalize_url_for_matching(item.url)
        if canonical is not None and canonical in saved_urls:
            rejected.append(item.url)
            continue
        filtered_items.append(item)

    deduped_rejected = list(dict.fromkeys(rejected))
    return GroupWebSearchResponse(
        query=response.query, items=filtered_items
    ), deduped_rejected


async def _exclude_unreachable_urls(
    response: GroupWebSearchResponse,
    *,
    event_sink: GroupWebSearchEventSink | None,
    phase: str,
) -> tuple[GroupWebSearchResponse, list[str]]:
    if not response.items:
        return response, []

    _emit_group_web_search_event(
        event_sink,
        {
            "kind": "status",
            "phase": phase,
            "message": "Verifying returned URLs are reachable.",
        },
    )

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=get_group_web_search_url_check_timeout_seconds(),
        headers=DEFAULT_HTTP_HEADERS,
    ) as client:
        checks = await _check_group_web_search_items(client, response.items)

    reachable_items = [item for item, is_reachable in checks if is_reachable]
    unreachable_urls = [item.url for item, is_reachable in checks if not is_reachable]
    deduped_unreachable_urls = list(dict.fromkeys(unreachable_urls))

    return (
        GroupWebSearchResponse(query=response.query, items=reachable_items),
        deduped_unreachable_urls,
    )


async def _check_group_web_search_items(
    client: httpx.AsyncClient,
    items: list[GroupWebSearchItem],
) -> list[tuple[GroupWebSearchItem, bool]]:
    checks = [_check_group_web_search_item_url(client, item) for item in items]
    return await asyncio.gather(*checks)


async def _check_group_web_search_item_url(
    client: httpx.AsyncClient,
    item: GroupWebSearchItem,
) -> tuple[GroupWebSearchItem, bool]:
    return item, await _is_group_web_search_url_reachable(client, item.url)


async def _is_group_web_search_url_reachable(
    client: httpx.AsyncClient,
    url: str,
) -> bool:
    try:
        response = await client.head(url)
        if response.status_code < 400:
            return True
        if response.status_code in {403, 405, 429} or response.status_code >= 500:
            response = await client.get(url)
            return response.status_code < 400
        return False
    except httpx.HTTPError:
        logger.debug(
            "Group web search URL reachability check failed",
            extra={"url": url},
            exc_info=True,
        )
        return False


def _select_diverse_group_web_search_items(
    items: list[GroupWebSearchItem], *, query: str
) -> list[GroupWebSearchItem]:
    focus_terms = _extract_query_focus_terms(query)
    remaining = list(items)
    selected: list[GroupWebSearchItem] = []
    used_focus_terms: set[str] = set()
    used_domains: set[str] = set()

    while remaining and len(selected) < MAX_GROUP_WEB_RESULTS:
        best_index = 0
        best_score: tuple[int, int, int, int, int] | None = None
        for index, item in enumerate(remaining):
            item_focus_terms = _matching_focus_terms(item, focus_terms)
            new_focus_terms = item_focus_terms - used_focus_terms
            domain = _extract_item_domain(item)
            domain_is_new = 1 if domain and domain not in used_domains else 0
            score = (
                1 if new_focus_terms else 0,
                len(new_focus_terms),
                domain_is_new,
                len(item_focus_terms),
                -index,
            )
            if best_score is None or score > best_score:
                best_score = score
                best_index = index

        chosen = remaining.pop(best_index)
        selected.append(chosen)
        used_focus_terms.update(_matching_focus_terms(chosen, focus_terms))
        domain = _extract_item_domain(chosen)
        if domain:
            used_domains.add(domain)

    return selected


def _extract_query_focus_terms(query: str) -> list[str]:
    seen: set[str] = set()
    focus_terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9.-]{2,}", query.casefold()):
        if token in QUERY_DIVERSITY_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        focus_terms.append(token)
    return focus_terms


def _matching_focus_terms(item: GroupWebSearchItem, focus_terms: list[str]) -> set[str]:
    if not focus_terms:
        return set()
    haystack = " ".join(
        part
        for part in (
            item.title,
            item.snippet,
            item.source or "",
            item.url,
        )
        if part
    ).casefold()
    return {term for term in focus_terms if term in haystack}


def _extract_item_domain(item: GroupWebSearchItem) -> str | None:
    parsed = urlparse(item.url)
    hostname = parsed.netloc.casefold().strip()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname or None


def _get_group_web_search_cache_ttl_seconds() -> int:
    load_app_env()
    raw_value = os.getenv("EVENTTRACKER_GROUP_WEB_SEARCH_CACHE_TTL_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_GROUP_WEB_SEARCH_CACHE_TTL_SECONDS
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_GROUP_WEB_SEARCH_CACHE_TTL_SECONDS
    return max(parsed, 0)


def _get_positive_float_env(name: str, default: float) -> float:
    load_app_env()
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        parsed = float(raw_value)
    except ValueError:
        return default
    if parsed <= 0:
        return default
    return parsed


def _get_positive_int_env(name: str, default: int) -> int:
    load_app_env()
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    if parsed <= 0:
        return default
    return parsed


def get_group_web_search_timeout_seconds() -> float:
    return _get_positive_float_env(
        "EVENTTRACKER_GROUP_WEB_SEARCH_TIMEOUT_SECONDS",
        DEFAULT_GROUP_WEB_SEARCH_TIMEOUT_SECONDS,
    )


def get_group_web_search_broadened_timeout_seconds() -> float:
    return _get_positive_float_env(
        "EVENTTRACKER_GROUP_WEB_SEARCH_BROADENED_TIMEOUT_SECONDS",
        DEFAULT_GROUP_WEB_SEARCH_BROADENED_TIMEOUT_SECONDS,
    )


def get_group_web_search_url_check_timeout_seconds() -> float:
    return _get_positive_float_env(
        "EVENTTRACKER_GROUP_WEB_SEARCH_URL_CHECK_TIMEOUT_SECONDS",
        DEFAULT_GROUP_WEB_SEARCH_URL_CHECK_TIMEOUT_SECONDS,
    )


def get_group_web_search_request_timeout_ms() -> int:
    default_request_timeout_ms = (
        int(
            max(
                get_group_web_search_timeout_seconds(),
                get_group_web_search_broadened_timeout_seconds(),
            )
            * 1000
        )
        + DEFAULT_GROUP_WEB_SEARCH_REQUEST_TIMEOUT_BUFFER_MS
    )
    return _get_positive_int_env(
        "EVENTTRACKER_GROUP_WEB_SEARCH_REQUEST_TIMEOUT_MS",
        default_request_timeout_ms,
    )


def _build_group_web_search_cache_key(query: str) -> str:
    return f"{CACHE_KEY_VERSION}:{query}"


def _get_cached_group_web_search(cache_key: str) -> GroupWebSearchResponse | None:
    cached = _GROUP_WEB_SEARCH_CACHE.get(cache_key)
    if cached is None:
        return None
    if cached.expires_at <= time.monotonic():
        _GROUP_WEB_SEARCH_CACHE.pop(cache_key, None)
        return None
    return cached.response


def _store_cached_group_web_search(
    cache_key: str, response: GroupWebSearchResponse
) -> None:
    ttl_seconds = _get_group_web_search_cache_ttl_seconds()
    if ttl_seconds <= 0:
        return
    _GROUP_WEB_SEARCH_CACHE[cache_key] = _CachedGroupWebSearchResponse(
        response=response,
        expires_at=time.monotonic() + ttl_seconds,
    )


def clear_group_web_search_cache(query: str | None = None) -> None:
    if query is None:
        _GROUP_WEB_SEARCH_CACHE.clear()
        return

    normalized_query = " ".join(query.strip().split())
    if not normalized_query:
        return

    _GROUP_WEB_SEARCH_CACHE.pop(
        _build_group_web_search_cache_key(normalized_query),
        None,
    )


def _clear_group_web_search_cache() -> None:
    clear_group_web_search_cache()
