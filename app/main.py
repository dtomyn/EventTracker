from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
import asyncio
from html import escape
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, TypedDict, cast
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import connection_context, init_db, is_sqlite_vec_enabled
from app.env import load_app_env
from app.models import (
    Entry,
    SearchResult,
    StoryFormat,
    TimelineGroup,
    TimelineStoryCitation,
    TimelineStoryScope,
)
from app.schemas import (
    EntryFormState,
    TimelineStoryCitationPayload,
    TimelineStoryFormState,
    TimelineStorySavePayload,
)
from app.services.ai_generate import (
    DraftGenerationConfigurationError,
    DraftGenerationError,
    generate_entry_suggestion,
    load_ai_provider,
)
from app.services.ai_story_mode import (
    GeneratedTimelineStory,
    StoryGenerationConfigurationError,
    StoryGenerationError,
    generate_timeline_story,
)
from app.services.entries import (
    blank_form_state,
    build_timeline_groups,
    create_timeline_group,
    decode_timeline_cursor,
    delete_timeline_group,
    DEFAULT_TIMELINE_PAGE_SIZE,
    DuplicateEntrySourceUrlError,
    form_state_from_entry,
    format_plain_text,
    get_default_timeline_group,
    get_entry,
    get_timeline_group,
    list_timeline_entries_page,
    list_timeline_groups,
    list_timeline_entries,
    plain_text_from_html,
    list_saved_entry_urls,
    TimelineEntryGroup,
    list_timeline_month_buckets,
    list_timeline_summary_groups,
    list_timeline_year_buckets,
    normalize_timeline_group_name,
    paginate_entries_in_memory,
    rename_timeline_group,
    sanitize_rich_text,
    sanitize_search_snippet,
    save_entry,
    TimelineGroupValidationError,
    utc_now_iso,
    update_entry,
    validate_entry_form,
)
from app.services.extraction import extract_url_text
from app.services.group_web_search import (
    clear_group_web_search_cache,
    get_group_web_search_request_timeout_ms,
    GroupWebSearchConfigurationError,
    GroupWebSearchError,
    GroupWebSearchItemPayload,
    GroupWebSearchTimeoutError,
    search_group_web,
)
from app.services.search import (
    DEFAULT_SEARCH_PAGE_SIZE,
    decode_search_cursor,
    filter_timeline_entries,
    paginate_search_results,
    search_entries,
)
from app.services.topics import get_topic_clusters_from_cache
from app.services.story_mode import (
    get_story,
    list_story_entries,
    resolve_story_scope,
    save_story,
)


load_app_env()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["plain_text"] = format_plain_text
templates.env.filters["render_entry_html"] = sanitize_rich_text
templates.env.filters["render_search_snippet"] = sanitize_search_snippet

_ALLOWED_STORY_HTML_TAGS = {"a", "h2", "p", "section"}
_ALLOWED_STORY_HTML_ATTRIBUTES = {
    "a": {"href", "title", "class"},
    "h2": {"class"},
    "p": {"class"},
    "section": {"class"},
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Events", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


class TimelineWebSearchState(TypedDict):
    show_panel: bool
    provider_is_copilot: bool


class GroupScope(TypedDict):
    normalized_query: str
    selected_group_id: int | None
    selected_group_query_value: str
    timeline_filters: list[TimelineGroup]
    selected_group: TimelineGroup | None
    explicit_all_groups: bool
    scope_key: str


class TimelineScope(GroupScope):
    match_count: int | None
    timeline_web_search: TimelineWebSearchState


class TimelineWebSearchClientConfig(TypedDict):
    endpoint: str
    refreshEndpoint: str
    streamEndpoint: str
    requestTimeoutMs: int
    groupId: int | None
    hasQuery: bool
    providerIsCopilot: bool


class TimelineClientScope(TypedDict):
    scopeKey: str
    groupId: int | None
    query: str
    detailsEndpoint: str
    monthsEndpoint: str
    yearsEndpoint: str
    summariesEndpoint: str
    pageSize: int
    hasMore: bool
    nextCursor: str | None
    groupWebSearch: TimelineWebSearchClientConfig


class TimelinePageContext(TypedDict):
    request: Request
    page_title: str
    query: str
    is_filtered: bool
    match_count: int | None
    timeline_filters: list[TimelineGroup]
    selected_group_id: int | None
    selected_group_query_value: str
    selected_group_name: str
    timeline_groups: list[TimelineEntryGroup]
    details_has_more: bool
    details_next_cursor: str | None
    timeline_web_search: TimelineWebSearchState
    timeline_scope: TimelineClientScope
    embeddings_enabled: bool


class SearchClientScope(TypedDict):
    scopeKey: str
    groupId: int | None
    groupName: str
    query: str
    resultsEndpoint: str
    pageSize: int
    hasMore: bool
    nextCursor: str | None
    totalCount: int
    loadedCount: int


class SearchPageContext(TypedDict):
    request: Request
    page_title: str
    query: str
    search_results: list[SearchResult]
    has_query: bool
    result_count: int
    initial_result_count: int
    search_has_more: bool
    timeline_filters: list[TimelineGroup]
    selected_group_id: int | None
    selected_group_query_value: str
    selected_group_name: str
    search_scope: SearchClientScope


class TimelineDetailsPayload(TypedDict):
    scope_key: str
    items_html: str
    has_more: bool
    next_cursor: str | None
    loaded_count: int


class TimelineYearsPayload(TypedDict):
    view: str
    scope_key: str
    total_entries: int
    bucket_count: int
    items_html: str


class TimelineMonthsPayload(TypedDict):
    view: str
    scope_key: str
    year: int | None
    bucket_count: int
    items_html: str


class TimelineSummariesPayload(TypedDict):
    view: str
    scope_key: str
    year: int | None
    month: int | None
    group_count: int
    items_html: str


class SearchResultsPayload(TypedDict):
    scope_key: str
    items_html: str
    has_more: bool
    next_cursor: str | None
    loaded_count: int
    total_count: int


class StoryFormatOption(TypedDict):
    value: StoryFormat
    label: str
    selected: bool


class StoryScopeDetails(TypedDict):
    scope_type: str
    scope_label: str
    group_name: str
    query: str
    year: int | None
    month: int | None
    description: str


class StoryCitationContext(TypedDict):
    citation_order: int
    entry_id: int
    entry_title: str
    entry_url: str | None
    entry_date: str | None
    quote_text: str | None
    note: str | None


class StoryResultContext(TypedDict):
    story_id: int | None
    format: StoryFormat
    title: str
    narrative_html: str
    narrative_text: str | None
    generated_utc: str
    provider_name: str | None
    source_entry_count: int
    truncated_input: bool
    error_text: str | None
    is_saved: bool
    citations: list[StoryCitationContext]
    save_citations_json: str


class StoryPageContext(TypedDict, total=False):
    request: Request
    page_title: str
    query: str
    timeline_filters: list[TimelineGroup]
    selected_group_id: int | None
    selected_group_query_value: str
    selected_group_name: str
    story_form_state: TimelineStoryFormState
    story_formats: list[StoryFormatOption]
    story_scope: StoryScopeDetails
    source_entry_count: int
    feedback_message: str | None
    feedback_class: str
    story_result: StoryResultContext | None


class TimelineGroupWebSearchPayload(TypedDict):
    enabled: bool
    query: str | None
    items: list[GroupWebSearchItemPayload]
    message: str | None


class ExportedEntryLinkPayload(TypedDict):
    id: int
    url: str
    note: str
    created_utc: str


class ExportedEntryPayload(TypedDict):
    id: int
    event_year: int
    event_month: int
    event_day: int | None
    sort_key: int
    group_id: int
    group_name: str
    title: str
    source_url: str | None
    generated_text: str | None
    final_text: str
    created_utc: str
    updated_utc: str
    tags: list[str]
    links: list[ExportedEntryLinkPayload]
    display_date: str


class EntriesExportPayload(TypedDict):
    count: int
    entries: list[ExportedEntryPayload]


class EntryFormPageContext(TypedDict):
    page_title: str
    form_state: EntryFormState
    entry_id: int | None
    timeline_filters: list[TimelineGroup]


class EntryDetailPageContext(TypedDict):
    page_title: str
    entry: Entry


class AdminGroupEditValue(TypedDict):
    name: str
    web_search_query: str
    is_default: bool


class AdminGroupsPageContext(TypedDict):
    request: Request
    page_title: str
    timeline_filters: list[TimelineGroup]
    selected_group_id: int | None
    selected_group_query_value: str
    query: str
    notice: str | None
    create_group_name: str
    create_group_web_search_query: str
    create_group_is_default: bool
    create_group_errors: dict[str, str]
    edit_group_errors: dict[int, dict[str, str]]
    edit_group_values: dict[int, AdminGroupEditValue]
    delete_group_errors: dict[int, str]


class GeneratedPreviewContext(TypedDict, total=False):
    generated_text: str
    suggested_title: str
    suggested_event_year: str
    suggested_event_month: str
    suggested_event_day: str
    feedback_message: str
    feedback_class: str


class HtmlPreviewContext(TypedDict):
    preview_html: str
    empty_message: str


class DevExtractFailurePayload(TypedDict):
    ok: bool
    message: str


class DevExtractSuccessPayload(TypedDict):
    ok: bool
    title: str | None
    preview: str


_STORY_FORMAT_LABELS: dict[StoryFormat, str] = {
    "executive_summary": "Executive Summary",
    "detailed_chronology": "Detailed Chronology",
    "recent_changes": "What Changed Recently",
}


@app.get("/", response_class=HTMLResponse)
def timeline(request: Request, q: str = "", group_id: str = "") -> HTMLResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        entries, next_cursor, has_more = _list_timeline_details_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
            page_size=DEFAULT_TIMELINE_PAGE_SIZE,
        )

        timeline_scope = _build_timeline_client_scope(
            scope,
            has_more=has_more,
            next_cursor=next_cursor,
        )
        context: TimelinePageContext = {
            "request": request,
            "page_title": (
                f"{scope['selected_group'].name} Timeline"
                if scope["selected_group"]
                else "Timeline"
            ),
            "query": scope["normalized_query"],
            "is_filtered": bool(scope["normalized_query"]),
            "match_count": scope["match_count"],
            "timeline_filters": scope["timeline_filters"],
            "selected_group_id": scope["selected_group_id"],
            "selected_group_query_value": scope["selected_group_query_value"],
            "selected_group_name": (
                scope["selected_group"].name
                if scope["selected_group"]
                else "All groups"
            ),
            "timeline_groups": build_timeline_groups(entries),
            "details_has_more": has_more,
            "details_next_cursor": next_cursor,
            "timeline_web_search": scope["timeline_web_search"],
            "timeline_scope": timeline_scope,
            "embeddings_enabled": is_sqlite_vec_enabled(connection),
        }
    return templates.TemplateResponse(
        request,
        "timeline.html",
        cast(dict[str, object], context),
    )


@app.get("/timeline/group-web-search")
async def timeline_group_web_search(group_id: str = "") -> JSONResponse:
    selected_group_id = _parse_group_id(group_id)
    if selected_group_id is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    with connection_context() as connection:
        group = get_timeline_group(connection, selected_group_id)
        saved_entry_urls = list_saved_entry_urls(connection)

    if group is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    if not group.web_search_query:
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=False,
                query=None,
                items=[],
                message="No related results found right now.",
            )
        )

    if not _is_copilot_provider():
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=False,
                query=group.web_search_query,
                items=[],
                message="Available when GitHub Copilot is the active AI provider.",
            )
        )

    try:
        result = await search_group_web(
            group.web_search_query,
            existing_urls=saved_entry_urls,
        )
    except GroupWebSearchConfigurationError:
        logging.getLogger(__name__).exception("Group web search configuration failed")
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=True,
                query=group.web_search_query,
                items=[],
                message="Could not load web results.",
            ),
            status_code=502,
        )
    except GroupWebSearchTimeoutError:
        logging.getLogger(__name__).warning("Group web search timed out")
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=True,
                query=group.web_search_query,
                items=[],
                message="Web search timed out. Try again.",
            ),
            status_code=504,
        )
    except GroupWebSearchError:
        logging.getLogger(__name__).exception("Group web search failed")
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=True,
                query=group.web_search_query,
                items=[],
                message="Could not load web results.",
            ),
            status_code=502,
        )
    except Exception:
        logging.getLogger(__name__).exception("Unexpected group web search failure")
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=True,
                query=group.web_search_query,
                items=[],
                message="Could not load web results.",
            ),
            status_code=500,
        )

    return JSONResponse(
        _build_timeline_group_web_search_payload(
            enabled=True,
            query=result.query,
            items=result.to_payload()["items"],
            message=None if result.items else "No related results found right now.",
        )
    )


@app.get("/timeline/group-web-search/stream")
async def timeline_group_web_search_stream(
    group_id: str = "", force_refresh: bool = False
) -> StreamingResponse:
    selected_group_id = _parse_group_id(group_id)
    if selected_group_id is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    with connection_context() as connection:
        group = get_timeline_group(connection, selected_group_id)
        saved_entry_urls = list_saved_entry_urls(connection)

    if group is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    web_search_query = group.web_search_query
    if web_search_query is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    async def stream_events() -> AsyncGenerator[str, None]:
        if not web_search_query:
            yield _encode_sse_event(
                "result",
                {
                    "query": None,
                    "items": [],
                    "message": "No related results found right now.",
                },
            )
            yield _encode_sse_event("complete", {"ok": True})
            return

        if not _is_copilot_provider():
            yield _encode_sse_event(
                "search_error",
                {
                    "message": "Available when GitHub Copilot is the active AI provider.",
                },
            )
            yield _encode_sse_event("complete", {"ok": False})
            return

        queue: asyncio.Queue[Mapping[str, object]] = asyncio.Queue()

        def on_event(payload: Mapping[str, object]) -> None:
            queue.put_nowait(payload)

        async def run_search() -> None:
            try:
                result = await search_group_web(
                    web_search_query,
                    force_refresh=force_refresh,
                    existing_urls=saved_entry_urls,
                    event_sink=on_event,
                )
            except GroupWebSearchConfigurationError:
                logging.getLogger(__name__).exception(
                    "Group web search configuration failed"
                )
                on_event(
                    {
                        "kind": "search_error",
                        "message": "Could not load web results.",
                    }
                )
            except GroupWebSearchTimeoutError:
                logging.getLogger(__name__).warning("Group web search timed out")
                on_event(
                    {
                        "kind": "search_error",
                        "message": "Web search timed out. Try again.",
                    }
                )
            except GroupWebSearchError:
                logging.getLogger(__name__).exception("Group web search failed")
                on_event(
                    {
                        "kind": "search_error",
                        "message": "Could not load web results.",
                    }
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Unexpected group web search failure"
                )
                on_event(
                    {
                        "kind": "search_error",
                        "message": "Could not load web results.",
                    }
                )
            else:
                on_event(
                    {
                        "kind": "result",
                        **result.to_payload(),
                        "message": None
                        if result.items
                        else "No related results found right now.",
                    }
                )
            finally:
                on_event({"kind": "complete", "ok": True})

        search_task = asyncio.create_task(run_search())
        try:
            while True:
                payload = await queue.get()
                event_name = str(payload.get("kind") or "message")
                yield _encode_sse_event(event_name, payload)
                if event_name == "complete":
                    break
        finally:
            if not search_task.done():
                search_task.cancel()
                try:
                    await search_task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/timeline/group-web-search/refresh")
async def refresh_timeline_group_web_search(group_id: str = "") -> JSONResponse:
    selected_group_id = _parse_group_id(group_id)
    if selected_group_id is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    with connection_context() as connection:
        group = get_timeline_group(connection, selected_group_id)
        saved_entry_urls = list_saved_entry_urls(connection)

    if group is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    if not group.web_search_query:
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=False,
                query=None,
                items=[],
                message="No related results found right now.",
            )
        )

    if not _is_copilot_provider():
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=False,
                query=group.web_search_query,
                items=[],
                message="Available when GitHub Copilot is the active AI provider.",
            )
        )

    try:
        result = await search_group_web(
            group.web_search_query,
            force_refresh=True,
            existing_urls=saved_entry_urls,
        )
    except GroupWebSearchConfigurationError:
        logging.getLogger(__name__).exception("Group web search configuration failed")
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=True,
                query=group.web_search_query,
                items=[],
                message="Could not load web results.",
            ),
            status_code=502,
        )
    except GroupWebSearchTimeoutError:
        logging.getLogger(__name__).warning("Group web search timed out")
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=True,
                query=group.web_search_query,
                items=[],
                message="Web search timed out. Try again.",
            ),
            status_code=504,
        )
    except GroupWebSearchError:
        logging.getLogger(__name__).exception("Group web search failed")
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=True,
                query=group.web_search_query,
                items=[],
                message="Could not load web results.",
            ),
            status_code=502,
        )
    except Exception:
        logging.getLogger(__name__).exception("Unexpected group web search failure")
        return JSONResponse(
            _build_timeline_group_web_search_payload(
                enabled=True,
                query=group.web_search_query,
                items=[],
                message="Could not load web results.",
            ),
            status_code=500,
        )

    return JSONResponse(
        _build_timeline_group_web_search_payload(
            enabled=True,
            query=result.query,
            items=result.to_payload()["items"],
            message=None if result.items else "No related results found right now.",
        )
    )


@app.get("/timeline/details")
def timeline_details(
    q: str = "",
    group_id: str = "",
    cursor: str = "",
    page_size: int | None = None,
) -> JSONResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        parsed_cursor = _parse_timeline_cursor(cursor)
        entries, next_cursor, has_more = _list_timeline_details_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
            page_size=page_size,
            cursor=parsed_cursor,
        )

    payload: TimelineDetailsPayload = {
        "scope_key": scope["scope_key"],
        "items_html": _render_partial(
            "partials/timeline_detail_groups.html",
            timeline_groups=build_timeline_groups(entries),
        ),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "loaded_count": len(entries),
    }
    return JSONResponse(payload)


@app.get("/timeline/years")
def timeline_years(q: str = "", group_id: str = "") -> JSONResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        scoped_entries = _list_entries_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
        )
        buckets = list_timeline_year_buckets(scoped_entries)

    payload: TimelineYearsPayload = {
        "view": "years",
        "scope_key": scope["scope_key"],
        "total_entries": len(scoped_entries),
        "bucket_count": len(buckets),
        "items_html": _render_partial(
            "partials/timeline_bucket_cards.html",
            buckets=buckets,
            query=scope["normalized_query"],
            selected_group_query_value=scope["selected_group_query_value"],
        ),
    }
    return JSONResponse(payload)


@app.get("/timeline/months")
def timeline_months(
    q: str = "",
    group_id: str = "",
    year: int | None = None,
) -> JSONResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        scoped_entries = _list_entries_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
        )
        buckets = list_timeline_month_buckets(scoped_entries, year=year)

    payload: TimelineMonthsPayload = {
        "view": "months",
        "scope_key": scope["scope_key"],
        "year": year,
        "bucket_count": len(buckets),
        "items_html": _render_partial(
            "partials/timeline_bucket_cards.html",
            buckets=buckets,
            query=scope["normalized_query"],
            selected_group_query_value=scope["selected_group_query_value"],
        ),
    }
    return JSONResponse(payload)


@app.get("/timeline/summaries")
def timeline_summaries(
    q: str = "",
    group_id: str = "",
    year: int | None = None,
    month: int | None = None,
) -> JSONResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        scoped_entries = _list_entries_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
        )
        groups = list_timeline_summary_groups(scoped_entries, year=year, month=month)

    payload: TimelineSummariesPayload = {
        "view": "events",
        "scope_key": scope["scope_key"],
        "year": year,
        "month": month,
        "group_count": len(groups),
        "items_html": _render_partial(
            "partials/timeline_summary_groups.html",
            timeline_groups=groups,
        ),
    }
    return JSONResponse(payload)


@app.get("/search", response_class=HTMLResponse)
def ranked_search(request: Request, q: str = "", group_id: str = "") -> HTMLResponse:
    with connection_context() as connection:
        scope = _load_group_scope(connection, q=q, group_id=group_id)
        all_results = (
            search_entries(
                connection,
                scope["normalized_query"],
                group_id=scope["selected_group_id"],
            )
            if scope["normalized_query"]
            else []
        )
        search_results, next_cursor, has_more = paginate_search_results(
            all_results,
            page_size=DEFAULT_SEARCH_PAGE_SIZE,
        )
        search_scope = _build_search_client_scope(
            scope,
            has_more=has_more,
            next_cursor=next_cursor,
            total_count=len(all_results),
            loaded_count=len(search_results),
        )
        context: SearchPageContext = {
            "request": request,
            "page_title": (
                f"{scope['selected_group'].name} Search"
                if scope["selected_group"]
                else "Search"
            ),
            "query": scope["normalized_query"],
            "search_results": search_results,
            "has_query": bool(scope["normalized_query"]),
            "result_count": len(all_results),
            "initial_result_count": len(search_results),
            "search_has_more": has_more,
            "timeline_filters": scope["timeline_filters"],
            "selected_group_id": scope["selected_group_id"],
            "selected_group_query_value": scope["selected_group_query_value"],
            "selected_group_name": (
                scope["selected_group"].name
                if scope["selected_group"]
                else "All groups"
            ),
            "search_scope": search_scope,
        }
    return templates.TemplateResponse(
        request,
        "search.html",
        cast(dict[str, object], context),
    )


@app.get("/search/results")
def ranked_search_results(
    q: str = "",
    group_id: str = "",
    cursor: str = "",
    page_size: int | None = None,
) -> JSONResponse:
    with connection_context() as connection:
        scope = _load_group_scope(connection, q=q, group_id=group_id)
        parsed_cursor = _parse_search_cursor(cursor)
        all_results = (
            search_entries(
                connection,
                scope["normalized_query"],
                group_id=scope["selected_group_id"],
            )
            if scope["normalized_query"]
            else []
        )
        search_results, next_cursor, has_more = paginate_search_results(
            all_results,
            page_size=page_size,
            cursor=parsed_cursor,
        )

    payload: SearchResultsPayload = {
        "scope_key": scope["scope_key"],
        "items_html": _render_partial(
            "partials/search_results.html",
            search_results=search_results,
            show_empty_state=False,
        ),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "loaded_count": len(search_results),
        "total_count": len(all_results),
    }
    return JSONResponse(payload)


@app.get("/story", response_class=HTMLResponse)
def story_page(
    request: Request,
    q: str = "",
    group_id: str = "",
    year: str = "",
    month: str = "",
    format: str = "executive_summary",
) -> HTMLResponse:
    story_format = _parse_story_format(format)
    with connection_context() as connection:
        group_scope, story_scope = _load_story_page_scope(
            connection,
            q=q,
            group_id=group_id,
            year=year,
            month=month,
        )
        entries = list_story_entries(connection, story_scope)

    feedback_message: str | None = None
    feedback_class = "warning"
    if not entries:
        feedback_message = (
            "No entries match this scope yet. Adjust the current filters or add entries, "
            "then generate a story."
        )

    context = _build_story_page_context(
        request,
        group_scope=group_scope,
        story_scope=story_scope,
        story_format=story_format,
        source_entry_count=len(entries),
        feedback_message=feedback_message,
        feedback_class=feedback_class,
    )
    return templates.TemplateResponse(
        request,
        "story.html",
        cast(dict[str, object], context),
    )


@app.post("/story/generate", response_class=HTMLResponse)
async def generate_story_page(
    request: Request,
    q: str = Form(""),
    group_id: str = Form(""),
    year: str = Form(""),
    month: str = Form(""),
    format: str = Form("executive_summary"),
) -> HTMLResponse:
    story_format = _parse_story_format(format)
    with connection_context() as connection:
        group_scope, story_scope = _load_story_page_scope(
            connection,
            q=q,
            group_id=group_id,
            year=year,
            month=month,
        )
        entries = list_story_entries(connection, story_scope)

    if not entries:
        context = _build_story_page_context(
            request,
            group_scope=group_scope,
            story_scope=story_scope,
            story_format=story_format,
            source_entry_count=0,
            feedback_message=(
                "No entries match this scope yet. Adjust the current filters or add "
                "entries, then generate a story."
            ),
            feedback_class="warning",
        )
        return templates.TemplateResponse(
            request,
            "story.html",
            cast(dict[str, object], context),
        )

    try:
        generated_story = await generate_timeline_story(
            story_scope, story_format, entries
        )
    except StoryGenerationConfigurationError as exc:
        context = _build_story_page_context(
            request,
            group_scope=group_scope,
            story_scope=story_scope,
            story_format=story_format,
            source_entry_count=len(entries),
            feedback_message=str(exc),
            feedback_class="danger",
        )
        return templates.TemplateResponse(
            request,
            "story.html",
            cast(dict[str, object], context),
            status_code=400,
        )
    except ValueError as exc:
        context = _build_story_page_context(
            request,
            group_scope=group_scope,
            story_scope=story_scope,
            story_format=story_format,
            source_entry_count=len(entries),
            feedback_message=str(exc),
            feedback_class="danger",
        )
        return templates.TemplateResponse(
            request,
            "story.html",
            cast(dict[str, object], context),
            status_code=400,
        )
    except StoryGenerationError as exc:
        context = _build_story_page_context(
            request,
            group_scope=group_scope,
            story_scope=story_scope,
            story_format=story_format,
            source_entry_count=len(entries),
            feedback_message=str(exc),
            feedback_class="danger",
        )
        return templates.TemplateResponse(
            request,
            "story.html",
            cast(dict[str, object], context),
            status_code=502,
        )
    except Exception:
        logging.getLogger(__name__).exception("Story generation failed")
        context = _build_story_page_context(
            request,
            group_scope=group_scope,
            story_scope=story_scope,
            story_format=story_format,
            source_entry_count=len(entries),
            feedback_message="Story generation failed. You can adjust the scope and try again.",
            feedback_class="danger",
        )
        return templates.TemplateResponse(
            request,
            "story.html",
            cast(dict[str, object], context),
            status_code=500,
        )

    generated_utc = utc_now_iso()
    story_result = _build_generated_story_result(
        generated_story,
        entries=entries,
        generated_utc=generated_utc,
    )
    context = _build_story_page_context(
        request,
        group_scope=group_scope,
        story_scope=story_scope,
        story_format=story_format,
        source_entry_count=len(entries),
        feedback_message="Story generated for the current scope.",
        feedback_class="success",
        story_result=story_result,
    )
    return templates.TemplateResponse(
        request,
        "story.html",
        cast(dict[str, object], context),
    )


@app.post("/story/save", response_model=None)
def save_story_page(
    request: Request,
    q: str = Form(""),
    group_id: str = Form(""),
    year: str = Form(""),
    month: str = Form(""),
    format: str = Form("executive_summary"),
    title: str = Form(""),
    narrative_html: str = Form(""),
    narrative_text: str = Form(""),
    generated_utc: str = Form(""),
    provider_name: str = Form(""),
    source_entry_count: str = Form("0"),
    truncated_input: str = Form("false"),
    error_text: str = Form(""),
    citations_json: str = Form("[]"),
) -> RedirectResponse | HTMLResponse:
    story_format = _parse_story_format(format)
    with connection_context() as connection:
        group_scope, story_scope = _load_story_page_scope(
            connection,
            q=q,
            group_id=group_id,
            year=year,
            month=month,
        )
        current_entries = list_story_entries(connection, story_scope)

        try:
            source_entry_count_value = _parse_story_source_entry_count(
                source_entry_count
            )
            citations = _parse_story_citation_payloads(citations_json)
            payload = TimelineStorySavePayload(
                scope_type=story_scope.scope_type,
                group_id=story_scope.group_id,
                query_text=story_scope.query_text,
                year=story_scope.year,
                month=story_scope.month,
                format=story_format,
                title=title.strip(),
                narrative_html=_sanitize_story_html(narrative_html),
                narrative_text=narrative_text.strip() or None,
                generated_utc=generated_utc.strip(),
                provider_name=provider_name.strip() or None,
                source_entry_count=source_entry_count_value,
                truncated_input=_parse_story_bool_value(truncated_input),
                error_text=error_text.strip() or None,
                citations=citations,
            )
            if not payload.title:
                raise ValueError("A generated story title is required before saving.")
            if not payload.narrative_html:
                raise ValueError("A generated story is required before saving.")
            story_id = save_story(connection, payload)
        except ValueError as exc:
            story_result = _build_posted_story_result(
                story_format=story_format,
                title=title,
                narrative_html=narrative_html,
                narrative_text=narrative_text,
                generated_utc=generated_utc,
                provider_name=provider_name,
                source_entry_count=source_entry_count,
                truncated_input=truncated_input,
                error_text=error_text,
                citations_json=citations_json,
                entries=current_entries,
            )
            context = _build_story_page_context(
                request,
                group_scope=group_scope,
                story_scope=story_scope,
                story_format=story_format,
                source_entry_count=len(current_entries),
                feedback_message=str(exc),
                feedback_class="danger",
                story_result=story_result,
            )
            return templates.TemplateResponse(
                request,
                "story.html",
                cast(dict[str, object], context),
                status_code=400,
            )

    return RedirectResponse(url=f"/story/{story_id}", status_code=303)


@app.get("/story/{story_id:int}", response_class=HTMLResponse)
def saved_story_page(request: Request, story_id: int) -> HTMLResponse:
    with connection_context() as connection:
        story = get_story(connection, story_id)
        if story is None:
            raise HTTPException(status_code=404, detail="Story not found")

        timeline_filters = list_timeline_groups(connection)
        selected_group = (
            get_timeline_group(connection, story.group_id)
            if story.group_id is not None
            else None
        )
        selected_group_name = (
            selected_group.name
            if selected_group is not None
            else (
                f"Group {story.group_id}"
                if story.group_id is not None
                else "All groups"
            )
        )
        citations = _build_story_citation_contexts(
            story.citations,
            {
                citation.entry_id: get_entry(connection, citation.entry_id)
                for citation in story.citations
            },
        )

    context: StoryPageContext = {
        "request": request,
        "page_title": story.title,
        "query": story.query_text or "",
        "timeline_filters": timeline_filters,
        "selected_group_id": story.group_id,
        "selected_group_query_value": (
            str(story.group_id) if story.group_id is not None else ""
        ),
        "selected_group_name": selected_group_name,
        "story_form_state": _build_story_form_state(
            q=story.query_text or "",
            group_id=(str(story.group_id) if story.group_id is not None else ""),
            year=story.year,
            month=story.month,
            story_format=story.format,
        ),
        "story_formats": _build_story_format_options(story.format),
        "story_scope": _build_story_scope_details(
            story_scope=TimelineStoryScope(
                scope_type=story.scope_type,
                group_id=story.group_id,
                query_text=story.query_text,
                year=story.year,
                month=story.month,
            ),
            selected_group_name=selected_group_name,
        ),
        "source_entry_count": story.source_entry_count,
        "story_result": {
            "story_id": story.id,
            "format": story.format,
            "title": story.title,
            "narrative_html": story.narrative_html,
            "narrative_text": story.narrative_text,
            "generated_utc": story.generated_utc,
            "provider_name": story.provider_name,
            "source_entry_count": story.source_entry_count,
            "truncated_input": story.truncated_input,
            "error_text": story.error_text,
            "is_saved": True,
            "citations": citations,
            "save_citations_json": json.dumps(
                [
                    {
                        "entry_id": citation.entry_id,
                        "citation_order": citation.citation_order,
                        "quote_text": citation.quote_text,
                        "note": citation.note,
                    }
                    for citation in story.citations
                ]
            ),
        },
    }
    return templates.TemplateResponse(
        request,
        "story.html",
        cast(dict[str, object], context),
    )


@app.get("/visualization", response_class=HTMLResponse)
def timeline_visualization(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/", status_code=307)


@app.get("/api/groups/{group_id}/topics")
def api_group_topics(group_id: int) -> JSONResponse:
    with connection_context() as connection:
        group = get_timeline_group(connection, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Timeline group not found")

        graph = get_topic_clusters_from_cache(connection, group_id)
        return JSONResponse(asdict(graph))

@app.get("/groups/{group_id}/topics/graph", response_class=HTMLResponse)
async def group_topics_graph(request: Request, group_id: int) -> HTMLResponse:
    with connection_context() as connection:
        group = get_timeline_group(connection, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Timeline group not found")
            
    context = {
        "request": request,
        "page_title": f"{group.name} Tag Clusters",
        "group": group,
        "selected_group_id": group.id,
        "selected_group_query_value": str(group.id),
        "query": "",
    }
    return templates.TemplateResponse(
        request,
        "topic_graph.html",
        cast(dict[str, object], context)
    )


@app.get("/entries/export")
def export_entries() -> JSONResponse:
    with connection_context() as connection:
        entries = list_timeline_entries(connection)

    exported_entries: list[ExportedEntryPayload] = []
    for entry in entries:
        data = cast(ExportedEntryPayload, asdict(entry))
        data.pop("preview_text", None)
        exported_entries.append(data)

    export_timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    file_name = f"EventTracker-export-{export_timestamp}.json"

    payload: EntriesExportPayload = {
        "count": len(exported_entries),
        "entries": exported_entries,
    }
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@app.get("/entries/new", response_class=HTMLResponse)
def new_entry_form(request: Request) -> HTMLResponse:
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)

    form_state = blank_form_state()
    if timeline_filters:
        form_state.values["group_id"] = str(timeline_filters[0].id)

    context: EntryFormPageContext = {
        "page_title": "New Entry",
        "form_state": form_state,
        "entry_id": None,
        "timeline_filters": timeline_filters,
    }
    return templates.TemplateResponse(
        request,
        "entry_form.html",
        cast(dict[str, object], context),
    )


@app.get("/entries/{entry_id:int}/view", response_class=HTMLResponse)
def view_entry(request: Request, entry_id: int) -> HTMLResponse:
    with connection_context() as connection:
        entry = get_entry(connection, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    context: EntryDetailPageContext = {
        "page_title": entry.title or "Entry",
        "entry": entry,
    }
    return templates.TemplateResponse(
        request,
        "entry_detail.html",
        cast(dict[str, object], context),
    )


@app.post("/entries/new", response_model=None)
async def create_entry(request: Request) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    form_state, payload = validate_entry_form(form)
    timeline_filters: list[TimelineGroup] = []
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)
        if (
            payload is not None
            and get_timeline_group(connection, payload.group_id) is None
        ):
            form_state.errors["group_id"] = "Select an existing timeline group."
            payload = None

    if payload is None:
        context: EntryFormPageContext = {
            "page_title": "New Entry",
            "form_state": form_state,
            "entry_id": None,
            "timeline_filters": timeline_filters,
        }
        return templates.TemplateResponse(
            request,
            "entry_form.html",
            cast(dict[str, object], context),
            status_code=400,
        )

    try:
        with connection_context() as connection:
            entry_id = save_entry(connection, payload)
    except DuplicateEntrySourceUrlError as exc:
        form_state.errors["source_url"] = str(exc)
        context = {
            "page_title": "New Entry",
            "form_state": form_state,
            "entry_id": None,
            "timeline_filters": timeline_filters,
        }
        return templates.TemplateResponse(
            request,
            "entry_form.html",
            cast(dict[str, object], context),
            status_code=400,
        )
    return RedirectResponse(url=f"/entries/{entry_id}/view", status_code=303)


@app.get("/entries/{entry_id:int}", response_class=HTMLResponse)
def edit_entry_form(request: Request, entry_id: int) -> HTMLResponse:
    with connection_context() as connection:
        entry = get_entry(connection, entry_id)
        timeline_filters = list_timeline_groups(connection)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    context: EntryFormPageContext = {
        "page_title": "Edit Entry",
        "form_state": form_state_from_entry(entry),
        "entry_id": entry_id,
        "timeline_filters": timeline_filters,
    }
    return templates.TemplateResponse(
        request,
        "entry_form.html",
        cast(dict[str, object], context),
    )


@app.post("/entries/{entry_id:int}", response_model=None)
async def update_entry_route(
    request: Request, entry_id: int
) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    form_state, payload = validate_entry_form(form)
    timeline_filters: list[TimelineGroup] = []
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)
        if (
            payload is not None
            and get_timeline_group(connection, payload.group_id) is None
        ):
            form_state.errors["group_id"] = "Select an existing timeline group."
            payload = None

    if payload is None:
        context: EntryFormPageContext = {
            "page_title": "Edit Entry",
            "form_state": form_state,
            "entry_id": entry_id,
            "timeline_filters": timeline_filters,
        }
        return templates.TemplateResponse(
            request,
            "entry_form.html",
            cast(dict[str, object], context),
            status_code=400,
        )

    try:
        with connection_context() as connection:
            existing = get_entry(connection, entry_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="Entry not found")
            update_entry(connection, entry_id, payload)
    except DuplicateEntrySourceUrlError as exc:
        form_state.errors["source_url"] = str(exc)
        context = {
            "page_title": "Edit Entry",
            "form_state": form_state,
            "entry_id": entry_id,
            "timeline_filters": timeline_filters,
        }
        return templates.TemplateResponse(
            request,
            "entry_form.html",
            cast(dict[str, object], context),
            status_code=400,
        )
    return RedirectResponse(url=f"/entries/{entry_id}/view", status_code=303)


@app.get("/admin/groups", response_class=HTMLResponse)
def manage_groups(
    request: Request,
    notice: str = "",
    create_group_name: str = "",
    create_group_web_search_query: str = "",
    create_group_is_default: bool = False,
) -> HTMLResponse:
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)

    context = _admin_groups_context(
        request,
        timeline_filters,
        notice=_notice_message(notice),
        create_group_name=create_group_name,
        create_group_web_search_query=create_group_web_search_query,
        create_group_is_default=create_group_is_default,
    )
    return templates.TemplateResponse(
        request,
        "admin_groups.html",
        cast(dict[str, object], context),
    )


@app.post("/admin/groups", response_model=None)
async def create_group_route(request: Request) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    raw_name = str(form.get("name", ""))
    raw_web_search_query = str(form.get("web_search_query", ""))
    is_default = form.get("is_default") is not None
    normalized_name = normalize_timeline_group_name(raw_name)
    normalized_query_value = _normalize_group_form_value(raw_web_search_query)

    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)
        try:
            create_timeline_group(
                connection,
                raw_name,
                raw_web_search_query,
                is_default=is_default,
            )
        except TimelineGroupValidationError as exc:
            context = _admin_groups_context(
                request,
                timeline_filters,
                create_group_name=normalized_name,
                create_group_web_search_query=normalized_query_value,
                create_group_is_default=is_default,
                create_group_errors={exc.field: str(exc)},
            )
            return templates.TemplateResponse(
                request,
                "admin_groups.html",
                cast(dict[str, object], context),
                status_code=400,
            )

    return RedirectResponse(url="/admin/groups?notice=created", status_code=303)


@app.post("/admin/groups/{group_id:int}", response_model=None)
async def rename_group_route(
    request: Request, group_id: int
) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    raw_name = str(form.get("name", ""))
    raw_web_search_query = str(form.get("web_search_query", ""))
    is_default = form.get("is_default") is not None

    with connection_context() as connection:
        existing_group = get_timeline_group(connection, group_id)
        timeline_filters = list_timeline_groups(connection)
        try:
            rename_timeline_group(
                connection,
                group_id,
                raw_name,
                raw_web_search_query,
                is_default=is_default,
            )
        except TimelineGroupValidationError as exc:
            context = _admin_groups_context(
                request,
                timeline_filters,
                edit_group_errors={group_id: {exc.field: str(exc)}},
                edit_group_values={
                    group_id: {
                        "name": normalize_timeline_group_name(raw_name),
                        "web_search_query": _normalize_group_form_value(
                            raw_web_search_query
                        ),
                        "is_default": is_default,
                    }
                },
            )
            return templates.TemplateResponse(
                request,
                "admin_groups.html",
                cast(dict[str, object], context),
                status_code=400,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    updated_query = _normalize_group_form_value(raw_web_search_query)
    previous_query = existing_group.web_search_query if existing_group else None
    if previous_query != updated_query:
        clear_group_web_search_cache(previous_query)
        clear_group_web_search_cache(updated_query)

    return RedirectResponse(url="/admin/groups?notice=updated", status_code=303)


@app.post("/admin/groups/{group_id:int}/delete", response_model=None)
async def delete_group_route(
    request: Request, group_id: int
) -> RedirectResponse | HTMLResponse:
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)
        try:
            delete_timeline_group(connection, group_id)
        except ValueError as exc:
            context = _admin_groups_context(
                request,
                timeline_filters,
                delete_group_errors={group_id: str(exc)},
            )
            return templates.TemplateResponse(
                request,
                "admin_groups.html",
                cast(dict[str, object], context),
                status_code=400,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RedirectResponse(url="/admin/groups?notice=deleted", status_code=303)


@app.post("/entries/generate")
async def generate_entry_preview(
    request: Request,
    title: str = Form(""),
    source_url: str = Form(""),
    generated_text: str = Form(""),
) -> HTMLResponse:
    prompt_title = title.strip()
    cleaned_source_url = source_url.strip()
    if not prompt_title and not cleaned_source_url:
        context: GeneratedPreviewContext = {
            "generated_text": generated_text,
            "feedback_message": "Title or source URL is required to generate a summary.",
            "feedback_class": "text-danger",
        }
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            cast(dict[str, object], context),
            status_code=400,
        )

    extraction = None
    extraction_error = None
    if cleaned_source_url:
        extraction = await extract_url_text(cleaned_source_url)
        if extraction is None:
            extraction_error = (
                "Source extraction failed. Summary generation used title-only mode."
            )
            if not prompt_title:
                context: GeneratedPreviewContext = {
                    "generated_text": generated_text,
                    "feedback_message": (
                        "Source extraction failed. Enter a title or try a different URL."
                    ),
                    "feedback_class": "text-danger",
                }
                return templates.TemplateResponse(
                    request,
                    "partials/generated_preview.html",
                    cast(dict[str, object], context),
                    status_code=400,
                )

    try:
        suggestion = await generate_entry_suggestion(prompt_title, extraction)
        generated_text = suggestion.draft_html
    except DraftGenerationConfigurationError as exc:
        context: GeneratedPreviewContext = {
            "generated_text": generated_text,
            "feedback_message": str(exc),
            "feedback_class": "text-danger",
        }
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            cast(dict[str, object], context),
            status_code=400,
        )
    except ValueError as exc:
        context: GeneratedPreviewContext = {
            "generated_text": generated_text,
            "feedback_message": str(exc),
            "feedback_class": "text-danger",
        }
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            cast(dict[str, object], context),
            status_code=400,
        )
    except DraftGenerationError as exc:
        context: GeneratedPreviewContext = {
            "generated_text": generated_text,
            "feedback_message": str(exc),
            "feedback_class": "text-danger",
        }
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            cast(dict[str, object], context),
            status_code=502,
        )
    except Exception:
        logging.getLogger(__name__).exception("Draft generation failed")
        context: GeneratedPreviewContext = {
            "generated_text": generated_text,
            "feedback_message": "Summary generation failed. You can still write manually.",
            "feedback_class": "text-danger",
        }
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            cast(dict[str, object], context),
            status_code=500,
        )

    context: GeneratedPreviewContext = {
        "generated_text": generated_text,
        "suggested_title": suggestion.title,
        "suggested_event_year": ""
        if suggestion.event_year is None
        else str(suggestion.event_year),
        "suggested_event_month": ""
        if suggestion.event_month is None
        else str(suggestion.event_month),
        "suggested_event_day": ""
        if suggestion.event_day is None
        else str(suggestion.event_day),
        "feedback_message": (
            extraction_error
            or (
                "Summary, title, and date suggestions generated with source context."
                if extraction is not None
                else "Summary, title, and date suggestions generated from the current input."
            )
        ),
        "feedback_class": "text-warning" if extraction_error else "text-success",
    }
    return templates.TemplateResponse(
        request,
        "partials/generated_preview.html",
        cast(dict[str, object], context),
    )


@app.post("/entries/preview-html")
async def preview_entry_html(
    request: Request,
    raw_html: str = Form(""),
) -> HTMLResponse:
    context: HtmlPreviewContext = {
        "preview_html": sanitize_rich_text(raw_html),
        "empty_message": "Rendered preview updates here as you type.",
    }
    return templates.TemplateResponse(
        request,
        "partials/html_preview_content.html",
        cast(dict[str, object], context),
    )


@app.get("/dev/extract")
async def dev_extract(source_url: str) -> JSONResponse:
    extraction = await extract_url_text(source_url)
    if extraction is None:
        error_payload: DevExtractFailurePayload = {
            "ok": False,
            "message": "Extraction failed.",
        }
        return JSONResponse(error_payload, status_code=400)
    success_payload: DevExtractSuccessPayload = {
        "ok": True,
        "title": extraction.title,
        "preview": extraction.text[:500],
    }
    return JSONResponse(success_payload)


def _parse_group_id(raw_group_id: str) -> int | None:
    normalized = raw_group_id.strip()
    if not normalized:
        return None
    if normalized.lower() == "all":
        return None
    try:
        value = int(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Timeline group not found") from exc
    if value <= 0:
        raise HTTPException(status_code=404, detail="Timeline group not found")
    return value


def _parse_timeline_cursor(cursor: str) -> tuple[int, str, int] | None:
    normalized_cursor = cursor.strip()
    if not normalized_cursor:
        return None
    try:
        return decode_timeline_cursor(normalized_cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid timeline cursor") from exc


def _parse_search_cursor(cursor: str) -> int | None:
    normalized_cursor = cursor.strip()
    if not normalized_cursor:
        return None
    try:
        return decode_search_cursor(normalized_cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid search cursor") from exc


def _load_group_scope(
    connection: sqlite3.Connection, *, q: str, group_id: str
) -> GroupScope:
    normalized_query = q.strip()
    normalized_group_id = group_id.strip().lower()
    explicit_all_groups = normalized_group_id == "all"
    selected_group_id = _parse_group_id(group_id)
    timeline_filters = list_timeline_groups(connection)
    selected_group = None
    if explicit_all_groups:
        selected_group_id = None
    elif selected_group_id is not None:
        selected_group = get_timeline_group(connection, selected_group_id)
        if selected_group is None:
            raise HTTPException(status_code=404, detail="Timeline group not found")
    else:
        selected_group = get_default_timeline_group(connection)
        if selected_group is not None:
            selected_group_id = selected_group.id

    return {
        "normalized_query": normalized_query,
        "selected_group_id": selected_group_id,
        "selected_group_query_value": (
            "all"
            if explicit_all_groups
            else (str(selected_group_id) if selected_group_id is not None else "")
        ),
        "timeline_filters": timeline_filters,
        "selected_group": selected_group,
        "explicit_all_groups": explicit_all_groups,
        "scope_key": _build_timeline_scope_key(selected_group_id, normalized_query),
    }


def _load_timeline_scope(
    connection: sqlite3.Connection, *, q: str, group_id: str
) -> TimelineScope:
    group_scope = _load_group_scope(connection, q=q, group_id=group_id)

    match_count = None
    if group_scope["normalized_query"]:
        match_count = len(
            filter_timeline_entries(
                connection,
                group_scope["normalized_query"],
                group_id=group_scope["selected_group_id"],
            )
        )

    timeline_web_search: TimelineWebSearchState = {
        "show_panel": bool(
            group_scope["selected_group"]
            and group_scope["selected_group"].web_search_query
        ),
        "provider_is_copilot": _is_copilot_provider(),
    }
    return {
        **group_scope,
        "match_count": match_count,
        "timeline_web_search": timeline_web_search,
    }


def _build_timeline_scope_key(group_id: int | None, query: str) -> str:
    return json.dumps(
        {"group_id": group_id, "query": query},
        sort_keys=True,
        separators=(",", ":"),
    )


def _build_timeline_client_scope(
    scope: TimelineScope,
    *,
    has_more: bool,
    next_cursor: str | None,
) -> TimelineClientScope:
    return {
        "scopeKey": scope["scope_key"],
        "groupId": scope["selected_group_id"],
        "query": scope["normalized_query"],
        "detailsEndpoint": "/timeline/details",
        "monthsEndpoint": "/timeline/months",
        "yearsEndpoint": "/timeline/years",
        "summariesEndpoint": "/timeline/summaries",
        "pageSize": DEFAULT_TIMELINE_PAGE_SIZE,
        "hasMore": has_more,
        "nextCursor": next_cursor,
        "groupWebSearch": {
            "endpoint": "/timeline/group-web-search",
            "refreshEndpoint": "/timeline/group-web-search/refresh",
            "streamEndpoint": "/timeline/group-web-search/stream",
            "requestTimeoutMs": get_group_web_search_request_timeout_ms(),
            "groupId": scope["selected_group_id"],
            "hasQuery": scope["timeline_web_search"]["show_panel"],
            "providerIsCopilot": scope["timeline_web_search"]["provider_is_copilot"],
        },
    }


def _build_search_client_scope(
    scope: GroupScope,
    *,
    has_more: bool,
    next_cursor: str | None,
    total_count: int,
    loaded_count: int,
) -> SearchClientScope:
    return {
        "scopeKey": scope["scope_key"],
        "groupId": scope["selected_group_id"],
        "groupName": (
            scope["selected_group"].name if scope["selected_group"] else "All groups"
        ),
        "query": scope["normalized_query"],
        "resultsEndpoint": "/search/results",
        "pageSize": DEFAULT_SEARCH_PAGE_SIZE,
        "hasMore": has_more,
        "nextCursor": next_cursor,
        "totalCount": total_count,
        "loadedCount": loaded_count,
    }


def _load_story_page_scope(
    connection: sqlite3.Connection,
    *,
    q: str,
    group_id: str,
    year: str,
    month: str,
) -> tuple[GroupScope, TimelineStoryScope]:
    group_scope = _load_group_scope(connection, q=q, group_id=group_id)
    try:
        story_scope = resolve_story_scope(
            connection,
            q=q,
            group_id=group_id,
            year=year,
            month=month,
        )
    except ValueError as exc:
        message = str(exc)
        if (
            message == "Timeline group not found."
            or message == "Timeline group not found"
        ):
            raise HTTPException(
                status_code=404, detail="Timeline group not found"
            ) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    return group_scope, story_scope


def _parse_story_format(raw_value: str) -> StoryFormat:
    normalized = raw_value.strip() or "executive_summary"
    if normalized not in _STORY_FORMAT_LABELS:
        raise HTTPException(status_code=400, detail="Invalid story format")
    return cast(StoryFormat, normalized)


def _build_story_format_options(
    selected_format: StoryFormat,
) -> list[StoryFormatOption]:
    return [
        {
            "value": value,
            "label": label,
            "selected": value == selected_format,
        }
        for value, label in _STORY_FORMAT_LABELS.items()
    ]


def _build_story_form_state(
    *,
    q: str,
    group_id: str,
    year: int | str | None,
    month: int | str | None,
    story_format: StoryFormat,
    errors: dict[str, str] | None = None,
) -> TimelineStoryFormState:
    return TimelineStoryFormState(
        values={
            "q": q,
            "group_id": group_id,
            "year": "" if year is None else str(year),
            "month": "" if month is None else str(month),
            "format": story_format,
        },
        errors=errors or {},
    )


def _build_story_scope_details(
    *, story_scope: TimelineStoryScope, selected_group_name: str
) -> StoryScopeDetails:
    parts = [selected_group_name]
    if story_scope.query_text:
        parts.append(f'Search query: "{story_scope.query_text}"')
    if story_scope.year is not None and story_scope.month is not None:
        parts.append(f"Month: {story_scope.year}-{story_scope.month:02d}")
    elif story_scope.year is not None:
        parts.append(f"Year: {story_scope.year}")

    return {
        "scope_type": story_scope.scope_type,
        "scope_label": "Search scope"
        if story_scope.scope_type == "search"
        else "Timeline scope",
        "group_name": selected_group_name,
        "query": story_scope.query_text or "",
        "year": story_scope.year,
        "month": story_scope.month,
        "description": " | ".join(parts),
    }


def _build_story_page_context(
    request: Request,
    *,
    group_scope: GroupScope,
    story_scope: TimelineStoryScope,
    story_format: StoryFormat,
    source_entry_count: int,
    feedback_message: str | None = None,
    feedback_class: str = "warning",
    story_result: StoryResultContext | None = None,
) -> StoryPageContext:
    selected_group_name = (
        group_scope["selected_group"].name
        if group_scope["selected_group"]
        else "All groups"
    )
    context: StoryPageContext = {
        "request": request,
        "page_title": "Story Mode",
        "query": group_scope["normalized_query"],
        "timeline_filters": group_scope["timeline_filters"],
        "selected_group_id": group_scope["selected_group_id"],
        "selected_group_query_value": group_scope["selected_group_query_value"],
        "selected_group_name": selected_group_name,
        "story_form_state": _build_story_form_state(
            q=group_scope["normalized_query"],
            group_id=group_scope["selected_group_query_value"],
            year=story_scope.year,
            month=story_scope.month,
            story_format=story_format,
        ),
        "story_formats": _build_story_format_options(story_format),
        "story_scope": _build_story_scope_details(
            story_scope=story_scope,
            selected_group_name=selected_group_name,
        ),
        "source_entry_count": source_entry_count,
        "feedback_message": feedback_message,
        "feedback_class": feedback_class,
        "story_result": story_result,
    }
    return context


def _build_generated_story_result(
    story: GeneratedTimelineStory,
    *,
    entries: list[Entry],
    generated_utc: str,
) -> StoryResultContext:
    entry_lookup = {entry.id: entry for entry in entries}
    citations = _build_story_citation_contexts(
        [
            TimelineStoryCitation(
                story_id=0,
                entry_id=citation.entry_id,
                citation_order=citation.citation_order,
                quote_text=citation.quote_text,
                note=citation.note,
            )
            for citation in story.citations
        ],
        entry_lookup,
    )
    narrative_html, narrative_text = _render_generated_story(story, citations)
    return {
        "story_id": None,
        "format": story.format,
        "title": story.title,
        "narrative_html": narrative_html,
        "narrative_text": narrative_text,
        "generated_utc": generated_utc,
        "provider_name": story.provider_name,
        "source_entry_count": story.source_entry_count,
        "truncated_input": story.truncated_input,
        "error_text": None,
        "is_saved": False,
        "citations": citations,
        "save_citations_json": json.dumps(
            [
                {
                    "entry_id": citation.entry_id,
                    "citation_order": citation.citation_order,
                    "quote_text": citation.quote_text,
                    "note": citation.note,
                }
                for citation in story.citations
            ]
        ),
    }


def _build_posted_story_result(
    *,
    story_format: StoryFormat,
    title: str,
    narrative_html: str,
    narrative_text: str,
    generated_utc: str,
    provider_name: str,
    source_entry_count: str,
    truncated_input: str,
    error_text: str,
    citations_json: str,
    entries: list[Entry],
) -> StoryResultContext | None:
    if not title.strip() and not narrative_html.strip():
        return None
    entry_lookup = {entry.id: entry for entry in entries}
    citations = _build_story_citation_contexts(
        [
            TimelineStoryCitation(
                story_id=0,
                entry_id=item.entry_id,
                citation_order=item.citation_order,
                quote_text=item.quote_text,
                note=item.note,
            )
            for item in _parse_story_citation_payloads(
                citations_json, fail_silently=True
            )
        ],
        entry_lookup,
    )
    return {
        "story_id": None,
        "format": story_format,
        "title": title.strip(),
        "narrative_html": _sanitize_story_html(narrative_html),
        "narrative_text": narrative_text.strip() or None,
        "generated_utc": generated_utc.strip() or utc_now_iso(),
        "provider_name": provider_name.strip() or None,
        "source_entry_count": _parse_story_source_entry_count(
            source_entry_count, default=0
        ),
        "truncated_input": _parse_story_bool_value(truncated_input),
        "error_text": error_text.strip() or None,
        "is_saved": False,
        "citations": citations,
        "save_citations_json": citations_json,
    }


def _build_story_citation_contexts(
    citations: list[TimelineStoryCitation],
    entry_lookup: Mapping[int, Entry | None],
) -> list[StoryCitationContext]:
    contexts: list[StoryCitationContext] = []
    for citation in citations:
        entry = entry_lookup.get(citation.entry_id)
        contexts.append(
            {
                "citation_order": citation.citation_order,
                "entry_id": citation.entry_id,
                "entry_title": entry.title
                if entry is not None
                else f"Entry #{citation.entry_id}",
                "entry_url": (
                    f"/entries/{citation.entry_id}/view" if entry is not None else None
                ),
                "entry_date": entry.display_date if entry is not None else None,
                "quote_text": citation.quote_text,
                "note": citation.note,
            }
        )
    return contexts


def _render_generated_story(
    story: GeneratedTimelineStory,
    citations: list[StoryCitationContext],
) -> tuple[str, str]:
    citation_lookup = {citation["citation_order"]: citation for citation in citations}
    html_parts: list[str] = []
    text_parts: list[str] = []
    for section in story.sections:
        html_parts.append('<section class="story-section mb-4">')
        html_parts.append(f'<h2 class="h5 mb-2">{escape(section.heading)}</h2>')
        for paragraph in _split_story_paragraphs(section.body):
            html_parts.append(f"<p>{escape(paragraph)}</p>")
        if section.citation_orders:
            citation_links = " ".join(
                _render_story_inline_citation_link(order, citation_lookup.get(order))
                for order in section.citation_orders
            )
            html_parts.append(
                '<p class="small text-body-secondary mb-0">Sources '
                f"{citation_links}</p>"
            )
        html_parts.append("</section>")

        text_parts.append(section.heading)
        text_parts.append(section.body.strip())

    return _sanitize_story_html("".join(html_parts)), "\n\n".join(
        part for part in text_parts if part
    )


def _render_story_inline_citation_link(
    citation_order: int,
    citation: StoryCitationContext | None,
) -> str:
    label = f"[{citation_order}]"
    fallback_href = f"#citation-{citation_order}"
    if citation is None:
        return f'<a href="{fallback_href}" class="story-inline-citation">{label}</a>'

    title_parts = [citation["entry_title"]]
    if citation["entry_date"]:
        title_parts.append(citation["entry_date"])
    title = " | ".join(part for part in title_parts if part)
    return (
        f'<a href="{fallback_href}" class="story-inline-citation" '
        f'title="{escape(title)}">{label}</a>'
    )


def _sanitize_story_html(value: str) -> str:
    if not value:
        return ""

    soup = BeautifulSoup(value, "html.parser")
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue
        if tag.name in {"script", "style"}:
            tag.decompose()
            continue
        if tag.name not in _ALLOWED_STORY_HTML_TAGS:
            tag.unwrap()
            continue

        allowed_attributes = _ALLOWED_STORY_HTML_ATTRIBUTES.get(tag.name, set())
        sanitized_attributes: dict[str, str | list[str]] = {}
        for attribute_name, attribute_value in tag.attrs.items():
            if attribute_name not in allowed_attributes:
                continue
            if tag.name == "a" and attribute_name == "href":
                href = str(attribute_value).strip()
                if _is_safe_story_href(href):
                    sanitized_attributes[attribute_name] = href
                continue
            sanitized_attributes[attribute_name] = cast(
                str | list[str],
                attribute_value,
            )
        tag.attrs = cast(Any, sanitized_attributes)

    return str(soup)


def _is_safe_story_href(value: str) -> bool:
    if value.startswith("#"):
        return True
    if value.startswith("/"):
        parsed = urlparse(value)
        return parsed.scheme == "" and parsed.netloc == ""
    return False


def _split_story_paragraphs(value: str) -> list[str]:
    paragraphs = [
        paragraph.strip() for paragraph in value.split("\n\n") if paragraph.strip()
    ]
    if paragraphs:
        return paragraphs
    stripped = value.strip()
    return [stripped] if stripped else []


def _parse_story_source_entry_count(
    raw_value: str, *, default: int | None = None
) -> int:
    normalized = raw_value.strip()
    if not normalized:
        if default is not None:
            return default
        raise ValueError("Source entry count is required.")
    try:
        value = int(normalized)
    except ValueError as exc:
        if default is not None:
            return default
        raise ValueError("Source entry count must be a valid number.") from exc
    if value < 0:
        if default is not None:
            return default
        raise ValueError("Source entry count must be zero or greater.")
    return value


def _parse_story_bool_value(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _parse_story_citation_payloads(
    raw_value: str, *, fail_silently: bool = False
) -> list[TimelineStoryCitationPayload]:
    try:
        parsed = json.loads(raw_value or "[]")
    except json.JSONDecodeError as exc:
        if fail_silently:
            return []
        raise ValueError("Generated citations could not be parsed.") from exc

    if not isinstance(parsed, list):
        if fail_silently:
            return []
        raise ValueError("Generated citations could not be parsed.")

    citations: list[TimelineStoryCitationPayload] = []
    for item in parsed:
        if not isinstance(item, dict):
            if fail_silently:
                return []
            raise ValueError("Generated citations could not be parsed.")
        try:
            citations.append(
                TimelineStoryCitationPayload(
                    entry_id=int(item["entry_id"]),
                    citation_order=int(item["citation_order"]),
                    quote_text=(
                        str(item["quote_text"])
                        if item.get("quote_text") is not None
                        else None
                    ),
                    note=str(item["note"]) if item.get("note") is not None else None,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            if fail_silently:
                return []
            raise ValueError("Generated citations could not be parsed.") from exc
    return citations


def _encode_sse_event(event_name: str, payload: Mapping[str, object]) -> str:
    body = json.dumps(payload, separators=(",", ":"), default=str)
    return f"event: {event_name}\ndata: {body}\n\n"


def _list_entries_for_scope(
    connection: sqlite3.Connection,
    *,
    normalized_query: str,
    selected_group_id: int | None,
) -> list[Entry]:
    if normalized_query:
        return filter_timeline_entries(
            connection,
            normalized_query,
            group_id=selected_group_id,
        )
    return list_timeline_entries(connection, group_id=selected_group_id)


def _list_timeline_details_for_scope(
    connection: sqlite3.Connection,
    *,
    normalized_query: str,
    selected_group_id: int | None,
    page_size: int | None,
    cursor: tuple[int, str, int] | None = None,
) -> tuple[list[Entry], str | None, bool]:
    if normalized_query:
        scoped_entries = filter_timeline_entries(
            connection,
            normalized_query,
            group_id=selected_group_id,
        )
        return paginate_entries_in_memory(
            scoped_entries,
            page_size=page_size,
            cursor=cursor,
        )

    return list_timeline_entries_page(
        connection,
        group_id=selected_group_id,
        page_size=page_size,
        cursor=cursor,
    )


def _render_partial(template_name: str, **context: object) -> str:
    template = templates.get_template(template_name)
    return template.render(**context)


def _build_timeline_group_web_search_payload(
    *,
    enabled: bool,
    query: str | None,
    items: list[GroupWebSearchItemPayload],
    message: str | None,
) -> TimelineGroupWebSearchPayload:
    return {
        "enabled": enabled,
        "query": query,
        "items": items,
        "message": message,
    }


def _notice_message(notice: str) -> str | None:
    if notice == "created":
        return "Timeline group created."
    if notice == "updated":
        return "Timeline group updated."
    if notice == "deleted":
        return "Timeline group deleted."
    return None


def _admin_groups_context(
    request: Request,
    timeline_filters: list[TimelineGroup],
    *,
    notice: str | None = None,
    create_group_name: str = "",
    create_group_web_search_query: str = "",
    create_group_is_default: bool = False,
    create_group_errors: dict[str, str] | None = None,
    edit_group_errors: dict[int, dict[str, str]] | None = None,
    edit_group_values: dict[int, AdminGroupEditValue] | None = None,
    delete_group_errors: dict[int, str] | None = None,
) -> AdminGroupsPageContext:
    return {
        "request": request,
        "page_title": "Admin",
        "timeline_filters": timeline_filters,
        "selected_group_id": None,
        "selected_group_query_value": "",
        "query": "",
        "notice": notice,
        "create_group_name": create_group_name,
        "create_group_web_search_query": create_group_web_search_query,
        "create_group_is_default": create_group_is_default,
        "create_group_errors": create_group_errors or {},
        "edit_group_errors": edit_group_errors or {},
        "edit_group_values": edit_group_values or {},
        "delete_group_errors": delete_group_errors or {},
    }


def _normalize_group_form_value(value: str) -> str:
    return " ".join(value.strip().split())


def _is_copilot_provider() -> bool:
    try:
        return load_ai_provider() == "copilot"
    except DraftGenerationConfigurationError:
        return False
