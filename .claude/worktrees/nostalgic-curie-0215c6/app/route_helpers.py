"""Shared TypedDicts and helper functions for route modules.

This module contains the data-structure definitions (TypedDicts) and pure or
near-pure helper functions that were originally defined in ``app/main.py`` and
are needed by multiple route modules after the split.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from dataclasses import asdict
from html import escape
import json
import sqlite3
from typing import Any, TypedDict, cast
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
from fastapi import HTTPException, Request

from app.db import connection_context, is_sqlite_vec_enabled
from app.models import (
    Entry,
    EntrySourceSnapshot,
    SearchResult,
    StoryArtifactKind,
    StoryFormat,
    TimelineGroup,
    TimelineStoryCitation,
    TimelineStoryScope,
)
from app.schemas import (
    EntryFormState,
    TimelineStoryArtifactSavePayload,
    TimelineStoryCitationPayload,
    TimelineStoryFormState,
    TimelineStorySavePayload,
)
from app.services.ai_generate import (
    DraftGenerationConfigurationError,
    load_ai_provider,
)
from app.services.ai_story_mode import GeneratedTimelineStory
from app.services.entries import (
    DEFAULT_TIMELINE_PAGE_SIZE,
    TimelineEntryGroup,
    build_timeline_groups,
    decode_timeline_cursor,
    get_default_timeline_group,
    get_entry,
    get_timeline_group,
    list_group_tag_vocabulary,
    list_timeline_entries,
    list_timeline_entries_page,
    list_timeline_groups,
    paginate_entries_in_memory,
    utc_now_iso,
)
from app.services.group_web_search import (
    get_group_web_search_request_timeout_ms,
    GroupWebSearchItemPayload,
)
from app.services.search import (
    DEFAULT_SEARCH_PAGE_SIZE,
    decode_search_cursor,
    filter_timeline_entries,
)
from app.services.story_mode import (
    list_story_entries,
    resolve_story_scope,
)
from app.templating import templates


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOWED_STORY_HTML_TAGS = {"a", "h2", "p", "section"}
_ALLOWED_STORY_HTML_ATTRIBUTES = {
    "a": {"href", "title", "class"},
    "h2": {"class"},
    "p": {"class"},
    "section": {"class"},
}


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------


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


class HeatmapPayload(TypedDict):
    counts: dict[str, int]
    total: int
    year: int
    years_available: list[int]


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
    presentation_ready: bool
    presentation_url: str | None
    presentation_artifact_json: str | None
    presentation_warning: str | None


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
    story_view_mode: str


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


class ExportedEntrySourceSnapshotPayload(TypedDict):
    entry_id: int
    source_url: str
    final_url: str
    raw_title: str | None
    markdown: str
    fetched_utc: str
    content_type: str | None
    http_etag: str | None
    http_last_modified: str | None
    content_sha256: str
    extractor_name: str
    extractor_version: str
    markdown_char_count: int


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
    source_snapshot: ExportedEntrySourceSnapshotPayload | None


class EntriesExportPayload(TypedDict):
    count: int
    entries: list[ExportedEntryPayload]


class EntryFormPageContext(TypedDict):
    page_title: str
    form_state: EntryFormState
    entry_id: int | None
    timeline_filters: list[TimelineGroup]
    source_snapshot: EntrySourceSnapshot | None


class EntryDetailPageContext(TypedDict):
    page_title: str
    entry: Entry
    source_snapshot: EntrySourceSnapshot | None


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
    suggested_tags: list[str]
    suggested_tags_csv: str
    suggested_connections: list[dict[str, object]]
    feedback_message: str
    feedback_class: str
    source_snapshot_source_url: str
    source_snapshot_final_url: str
    source_snapshot_title: str
    source_snapshot_markdown: str
    source_snapshot_content_type: str
    source_snapshot_fetched_utc: str
    source_snapshot_http_etag: str
    source_snapshot_http_last_modified: str
    source_snapshot_extractor_name: str
    source_snapshot_extractor_version: str


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


# ---------------------------------------------------------------------------
# Story format labels
# ---------------------------------------------------------------------------

_STORY_FORMAT_LABELS: dict[StoryFormat, str] = {
    "executive_summary": "Executive Summary",
    "detailed_chronology": "Detailed Chronology",
    "recent_changes": "What Changed Recently",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


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
    include_deck: bool = False,
    errors: dict[str, str] | None = None,
) -> TimelineStoryFormState:
    return TimelineStoryFormState(
        values={
            "q": q,
            "group_id": group_id,
            "year": "" if year is None else str(year),
            "month": "" if month is None else str(month),
            "format": story_format,
            "include_deck": "true" if include_deck else "",
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
    include_deck: bool = False,
    story_view_mode: str = "narrative",
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
            include_deck=include_deck,
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
        "story_view_mode": story_view_mode,
    }
    return context


def _build_generated_story_result(
    story: GeneratedTimelineStory,
    *,
    entries: list[Entry],
    generated_utc: str,
    presentation_ready: bool = False,
    presentation_artifact_json: str | None = None,
    presentation_warning: str | None = None,
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
        "presentation_ready": presentation_ready,
        "presentation_url": None,
        "presentation_artifact_json": presentation_artifact_json,
        "presentation_warning": presentation_warning,
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
    presentation_artifact_json: str,
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
        "presentation_ready": bool(presentation_artifact_json.strip()),
        "presentation_url": None,
        "presentation_artifact_json": presentation_artifact_json or None,
        "presentation_warning": None,
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


def _serialize_story_artifact_payload(
    payload: TimelineStoryArtifactSavePayload,
) -> str:
    """Serialize a generated presentation artifact for Story Mode form reuse."""
    return json.dumps(asdict(payload), separators=(",", ":"), sort_keys=True)


def _parse_story_artifact_payload(
    raw_value: str,
) -> TimelineStoryArtifactSavePayload | None:
    """Parse the hidden presentation artifact payload when saving a story."""
    normalized = raw_value.strip()
    if not normalized:
        return None

    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ValueError("Generated presentation artifact could not be parsed.") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Generated presentation artifact could not be parsed.")

    try:
        payload = TimelineStoryArtifactSavePayload(
            artifact_kind=cast(StoryArtifactKind, str(parsed["artifact_kind"])),
            source_format=str(parsed["source_format"]),
            source_text=str(parsed["source_text"]),
            compiled_html=str(parsed.get("compiled_html", "")),
            compiled_css=str(parsed.get("compiled_css", "")),
            metadata_json=str(parsed.get("metadata_json", "{}")),
            generated_utc=str(parsed.get("generated_utc", "")),
            compiled_utc=(
                str(parsed["compiled_utc"])
                if parsed.get("compiled_utc") is not None
                else None
            ),
            compiler_name=(
                str(parsed["compiler_name"])
                if parsed.get("compiler_name") is not None
                else None
            ),
            compiler_version=(
                str(parsed["compiler_version"])
                if parsed.get("compiler_version") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Generated presentation artifact could not be parsed.") from exc

    if payload.artifact_kind != "executive_deck":
        raise ValueError("Generated presentation artifact could not be parsed.")
    return payload


def _parse_story_view_mode(raw_value: str, *, has_presentation: bool) -> str:
    """Resolve the saved story view mode with a narrative fallback."""
    if has_presentation and raw_value.strip().lower() == "presentation":
        return "presentation"
    return "narrative"


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


def _month_name(month: int) -> str:
    return calendar.month_name[month]


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
