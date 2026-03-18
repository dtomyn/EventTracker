from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
import asyncio
from datetime import datetime
import json
import logging
import os
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

from app.env import load_app_env
from app.services.ai_generate import (
    DraftGenerationConfigurationError,
    _extract_copilot_message_content,
    _instantiate_copilot_client,
    _prepare_copilot_client,
    _prepare_copilot_resource,
    _resolve_copilot_permission_handler,
    load_ai_provider,
    load_copilot_settings,
)


logger = logging.getLogger(__name__)

MAX_GROUP_WEB_RESULTS = 5
MIN_GROUP_WEB_RESULTS = 3
DEFAULT_GROUP_WEB_SEARCH_CACHE_TTL_SECONDS = 300
DEFAULT_GROUP_WEB_SEARCH_TIMEOUT_SECONDS = 60.0
DEFAULT_GROUP_WEB_SEARCH_BROADENED_TIMEOUT_SECONDS = 45.0
DEFAULT_GROUP_WEB_SEARCH_REQUEST_TIMEOUT_BUFFER_MS = 5000
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

    def to_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "items": [asdict(item) for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class _CachedGroupWebSearchResponse:
    response: GroupWebSearchResponse
    expires_at: float


_GROUP_WEB_SEARCH_CACHE: dict[str, _CachedGroupWebSearchResponse] = {}
GroupWebSearchEventSink = Callable[[dict[str, Any]], None]


async def search_group_web(
    query: str,
    *,
    force_refresh: bool = False,
    event_sink: GroupWebSearchEventSink | None = None,
) -> GroupWebSearchResponse:
    normalized_query = " ".join(query.strip().split())
    if not normalized_query:
        raise ValueError("Web search query is required.")

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
        _emit_group_web_search_event(
            event_sink,
            {
                "kind": "status",
                "phase": "cache",
                "message": "Using cached web search result.",
            },
        )
        return cached_response

    settings = load_copilot_settings()
    client = _instantiate_copilot_client(settings)
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
            active_client = await _prepare_copilot_client(exit_stack, client)
            session = await _create_search_session(
                active_client,
                settings.model_id,
                streaming=event_sink is not None,
            )
            active_session = await _prepare_copilot_resource(exit_stack, session)
            response = await _send_search_prompt(
                active_session,
                normalized_query,
                event_sink=event_sink,
                phase="initial",
            )
    except TimeoutError as exc:
        raise GroupWebSearchTimeoutError(
            "GitHub Copilot web search timed out before completing."
        ) from exc
    except asyncio.TimeoutError as exc:
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

    content = _extract_copilot_message_content(response)
    if not content:
        raise GroupWebSearchError(
            "The AI provider returned an empty web search response."
        )
    parsed_response = _parse_group_web_search_response(content, normalized_query)
    if len(parsed_response.items) < MIN_GROUP_WEB_RESULTS:
        _emit_group_web_search_event(
            event_sink,
            {
                "kind": "status",
                "phase": "broadened",
                "message": "Initial pass returned too few results. Starting broadened pass.",
            },
        )
        parsed_response = await _broaden_group_web_search(
            settings.model_id,
            normalized_query,
            parsed_response,
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
    client: Any, model_id: str, *, streaming: bool = False
) -> Any:
    method = getattr(client, "create_session", None)
    if method is None:
        raise GroupWebSearchConfigurationError(
            "The installed GitHub Copilot SDK does not expose create_session(...)."
        )

    config = {
        "model": model_id,
        "reasoning_effort": "low",
        "on_permission_request": _resolve_copilot_permission_handler(),
        "system_message": {
            "mode": "append",
            "content": GROUP_WEB_SEARCH_SYSTEM_PROMPT,
        },
    }
    if streaming:
        config["streaming"] = True
    return await method(config)


async def _send_search_prompt(
    session: Any,
    query: str,
    *,
    event_sink: GroupWebSearchEventSink | None = None,
    phase: str,
) -> Any:
    method = getattr(session, "send_and_wait", None)
    if method is None:
        raise GroupWebSearchConfigurationError(
            "The installed GitHub Copilot SDK does not expose send_and_wait(...)."
        )

    unsubscribe = _subscribe_to_group_web_search_events(
        session,
        event_sink=event_sink,
        phase=phase,
    )
    try:
        return await method(
            {
                "prompt": _build_search_prompt(query),
            },
            timeout=get_group_web_search_timeout_seconds(),
        )
    finally:
        unsubscribe()


async def _broaden_group_web_search(
    model_id: str,
    query: str,
    initial_response: GroupWebSearchResponse,
    *,
    event_sink: GroupWebSearchEventSink | None = None,
) -> GroupWebSearchResponse:
    settings = load_copilot_settings()
    client = _instantiate_copilot_client(settings)

    try:
        async with AsyncExitStack() as exit_stack:
            active_client = await _prepare_copilot_client(exit_stack, client)
            session = await _create_search_session(
                active_client,
                model_id,
                streaming=event_sink is not None,
            )
            active_session = await _prepare_copilot_resource(exit_stack, session)
            response = await _send_broadened_search_prompt(
                active_session,
                query,
                event_sink=event_sink,
                phase="broadened",
            )
    except Exception:
        logger.debug(
            "Broadened group web search failed; using initial results.", exc_info=True
        )
        return initial_response

    content = _extract_copilot_message_content(response)
    if not content:
        return initial_response

    broadened_response = _parse_group_web_search_response(content, query)
    merged_items: list[GroupWebSearchItem] = []
    seen_urls: set[str] = set()
    for item in [*initial_response.items, *broadened_response.items]:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        merged_items.append(item)
        if len(merged_items) >= MAX_GROUP_WEB_RESULTS:
            break

    return GroupWebSearchResponse(query=query, items=merged_items)


async def _send_broadened_search_prompt(
    session: Any,
    query: str,
    *,
    event_sink: GroupWebSearchEventSink | None = None,
    phase: str,
) -> Any:
    method = getattr(session, "send_and_wait", None)
    if method is None:
        raise GroupWebSearchConfigurationError(
            "The installed GitHub Copilot SDK does not expose send_and_wait(...)."
        )

    unsubscribe = _subscribe_to_group_web_search_events(
        session,
        event_sink=event_sink,
        phase=phase,
    )
    try:
        return await method(
            {
                "prompt": _build_broadened_search_prompt(query),
            },
            timeout=get_group_web_search_broadened_timeout_seconds(),
        )
    finally:
        unsubscribe()


def _subscribe_to_group_web_search_events(
    session: Any,
    *,
    event_sink: GroupWebSearchEventSink | None,
    phase: str,
) -> Callable[[], None]:
    if event_sink is None:
        return lambda: None

    on_method = getattr(session, "on", None)
    if not callable(on_method):
        return lambda: None

    def handle_event(event: Any) -> None:
        payload = _build_group_web_search_session_event_payload(event, phase=phase)
        if payload is not None:
            _emit_group_web_search_event(event_sink, payload)

    unsubscribe = on_method(handle_event)
    if callable(unsubscribe):
        return unsubscribe
    return lambda: None


def _build_group_web_search_session_event_payload(
    event: Any, *, phase: str
) -> dict[str, Any] | None:
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


def _normalize_group_web_search_event_type(value: Any) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _summarize_group_web_search_session_event(event: Any) -> str | None:
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


def _serialize_group_web_search_event_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
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

    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _serialize_group_web_search_event_value(
                value.to_dict(),
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
    event_sink: GroupWebSearchEventSink | None, payload: dict[str, Any]
) -> None:
    if event_sink is None:
        return
    event_sink(payload)


def _build_search_prompt(query: str) -> str:
    lines = [
        "Topic query: " + query,
        "Find up to 5 recent and relevant public-web items for this topic.",
        "Use direct source URLs, keep snippets short, and include article_date when it is clearly available.",
        "Prefer multiple distinct sources instead of repeating one publisher when credible alternatives exist.",
    ]
    focus_terms = _extract_query_focus_terms(query)
    if focus_terms:
        lines.append(
            "When the query names organizations, prefer coverage across those organizations where credible results exist."
        )
        lines.append("Named organizations or focus terms: " + ", ".join(focus_terms))
    lines.append(
        "If you cannot verify enough results quickly, return fewer items or an empty items array."
    )
    return "\n".join(lines)


def _build_broadened_search_prompt(query: str) -> str:
    lines = [
        "The first strict search likely returned too few results.",
        "Original topic query: " + query,
        "Broaden slightly to adjacent, recent agentic coding announcements that still fit the topic and timeframe.",
        "Aim for 3 to 5 distinct results from different credible sources when available.",
        "Use direct source URLs, keep snippets short, and include article_date when it is clearly available.",
    ]
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
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
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


def _parse_group_web_search_item(raw_item: Any) -> GroupWebSearchItem | None:
    if not isinstance(raw_item, dict):
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


def _normalize_article_date(value: Any) -> str | None:
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


def _normalize_http_url(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        logger.debug("Discarding non-http web search URL: %s", candidate)
        return None
    return candidate


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
