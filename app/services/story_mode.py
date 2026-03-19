from __future__ import annotations

import sqlite3

from app.models import (
    Entry,
    TimelineStoryCitation,
    TimelineStoryScope,
    TimelineStorySnapshot,
)
from app.schemas import TimelineStorySavePayload
from app.services.entries import (
    get_default_timeline_group,
    get_timeline_group,
    list_timeline_entries,
    utc_now_iso,
)
from app.services.search import filter_timeline_entries


def resolve_story_scope(
    connection: sqlite3.Connection,
    *,
    q: str = "",
    group_id: str = "",
    year: int | str | None = None,
    month: int | str | None = None,
) -> TimelineStoryScope:
    normalized_query = q.strip()
    normalized_group_id = group_id.strip().lower()
    explicit_all_groups = normalized_group_id == "all"
    selected_group_id = _parse_story_group_id(group_id)

    if explicit_all_groups:
        selected_group_id = None
    elif selected_group_id is not None:
        if get_timeline_group(connection, selected_group_id) is None:
            raise ValueError("Timeline group not found.")
    else:
        default_group = get_default_timeline_group(connection)
        if default_group is not None:
            selected_group_id = default_group.id

    resolved_year = _parse_optional_int(
        year,
        field_name="year",
        minimum=1900,
        maximum=2100,
    )
    resolved_month = _parse_optional_int(
        month,
        field_name="month",
        minimum=1,
        maximum=12,
    )
    if resolved_month is not None and resolved_year is None:
        raise ValueError("Year is required when month is provided.")

    return TimelineStoryScope(
        scope_type="search" if normalized_query else "timeline",
        group_id=selected_group_id,
        query_text=normalized_query or None,
        year=resolved_year,
        month=resolved_month,
    )


def list_story_entries(
    connection: sqlite3.Connection, scope: TimelineStoryScope
) -> list[Entry]:
    if scope.scope_type == "search" and scope.query_text:
        entries = filter_timeline_entries(
            connection,
            scope.query_text,
            group_id=scope.group_id,
        )
    else:
        entries = list_timeline_entries(connection, group_id=scope.group_id)

    scoped_entries = [
        entry
        for entry in entries
        if (scope.year is None or entry.event_year == scope.year)
        and (scope.month is None or entry.event_month == scope.month)
    ]
    return order_story_entries(scoped_entries)


def order_story_entries(entries: list[Entry]) -> list[Entry]:
    return sorted(
        entries,
        key=lambda entry: (entry.sort_key, entry.updated_utc, entry.id),
    )


def prepare_story_input_entries(
    entries: list[Entry], *, max_entries: int | None = None
) -> tuple[list[Entry], bool]:
    ordered_entries = order_story_entries(entries)
    if max_entries is None or len(ordered_entries) <= max_entries:
        return ordered_entries, False
    if max_entries <= 0:
        raise ValueError("max_entries must be greater than zero.")
    return ordered_entries[-max_entries:], True


def save_story(
    connection: sqlite3.Connection, payload: TimelineStorySavePayload
) -> int:
    generated_utc = payload.generated_utc or utc_now_iso()
    updated_utc = payload.updated_utc or generated_utc
    cursor = connection.execute(
        """
        INSERT INTO timeline_stories (
            scope_type,
            group_id,
            query_text,
            year,
            month,
            format,
            title,
            narrative_html,
            narrative_text,
            generated_utc,
            updated_utc,
            provider_name,
            source_entry_count,
            truncated_input,
            error_text
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.scope_type,
            payload.group_id,
            payload.query_text,
            payload.year,
            payload.month,
            payload.format,
            payload.title,
            payload.narrative_html,
            payload.narrative_text,
            generated_utc,
            updated_utc,
            payload.provider_name,
            payload.source_entry_count,
            int(payload.truncated_input),
            payload.error_text,
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("Failed to determine the new story id.")

    story_id = int(cursor.lastrowid)
    for citation in sorted(payload.citations, key=lambda item: item.citation_order):
        connection.execute(
            """
            INSERT INTO timeline_story_entries (
                story_id,
                entry_id,
                citation_order,
                quote_text,
                note
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                story_id,
                citation.entry_id,
                citation.citation_order,
                citation.quote_text,
                citation.note,
            ),
        )
    return story_id


def get_story(
    connection: sqlite3.Connection, story_id: int
) -> TimelineStorySnapshot | None:
    row = connection.execute(
        """
        SELECT
            id,
            scope_type,
            group_id,
            query_text,
            year,
            month,
            format,
            title,
            narrative_html,
            narrative_text,
            generated_utc,
            updated_utc,
            provider_name,
            source_entry_count,
            truncated_input,
            error_text
        FROM timeline_stories
        WHERE id = ?
        """,
        (story_id,),
    ).fetchone()
    if row is None:
        return None

    return TimelineStorySnapshot(
        id=int(row["id"]),
        scope_type=row["scope_type"],
        format=row["format"],
        title=row["title"],
        narrative_html=row["narrative_html"],
        generated_utc=row["generated_utc"],
        updated_utc=row["updated_utc"],
        source_entry_count=int(row["source_entry_count"]),
        truncated_input=bool(row["truncated_input"]),
        group_id=row["group_id"],
        query_text=row["query_text"],
        year=row["year"],
        month=row["month"],
        narrative_text=row["narrative_text"],
        provider_name=row["provider_name"],
        error_text=row["error_text"],
        citations=list_story_citations(connection, story_id),
    )


def list_story_citations(
    connection: sqlite3.Connection, story_id: int
) -> list[TimelineStoryCitation]:
    rows = connection.execute(
        """
        SELECT story_id, entry_id, citation_order, quote_text, note
        FROM timeline_story_entries
        WHERE story_id = ?
        ORDER BY citation_order ASC
        """,
        (story_id,),
    ).fetchall()
    return [
        TimelineStoryCitation(
            story_id=int(row["story_id"]),
            entry_id=int(row["entry_id"]),
            citation_order=int(row["citation_order"]),
            quote_text=row["quote_text"],
            note=row["note"],
        )
        for row in rows
    ]


def _parse_story_group_id(raw_group_id: str) -> int | None:
    normalized = raw_group_id.strip()
    if not normalized or normalized.lower() == "all":
        return None
    try:
        value = int(normalized)
    except ValueError as exc:
        raise ValueError("Timeline group not found.") from exc
    if value <= 0:
        raise ValueError("Timeline group not found.")
    return value


def _parse_optional_int(
    raw_value: int | str | None,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        if not normalized:
            return None
        candidate = normalized
    else:
        candidate = raw_value

    try:
        value = int(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name.capitalize()} must be a valid number.") from exc

    if value < minimum or value > maximum:
        raise ValueError(
            f"{field_name.capitalize()} must be between {minimum} and {maximum}."
        )
    return value
