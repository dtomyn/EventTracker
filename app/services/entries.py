from __future__ import annotations

import base64
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
import hashlib
import json
import logging
import sqlite3
import time
from typing import Any, Literal, TypedDict, cast
from urllib.parse import urlparse

from app.models import Entry, EntryConnection, EntryLink, EntrySourceSnapshot, HeatmapData, TimelineGroup
from app.services.formatting import (
    format_plain_text as format_plain_text,
    plain_text_from_html as plain_text_from_html,
    preview_text as preview_text,
    render_source_snapshot_markdown as render_source_snapshot_markdown,
    sanitize_rich_text as sanitize_rich_text,
    sanitize_search_snippet as sanitize_search_snippet,
)
from app.services.groups import (
    TimelineGroupValidationError as TimelineGroupValidationError,
    clear_default_timeline_group as clear_default_timeline_group,
    create_timeline_group as create_timeline_group,
    delete_timeline_group as delete_timeline_group,
    get_default_timeline_group as get_default_timeline_group,
    get_timeline_group as get_timeline_group,
    list_timeline_groups as list_timeline_groups,
    normalize_timeline_group_name as normalize_timeline_group_name,
    normalize_timeline_group_web_search_query as normalize_timeline_group_web_search_query,
    rename_timeline_group as rename_timeline_group,
    set_default_timeline_group as set_default_timeline_group,
    MAX_TIMELINE_GROUP_WEB_SEARCH_QUERY_LENGTH,
)
from app.schemas import (
    EntryConnectionPayload,
    EntryFormState,
    EntryLinkPayload,
    EntryPayload,
    EntrySourceSnapshotPayload,
)
from app.services.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingError,
    EmbeddingIndexMismatchError,
    sync_entry_embedding,
)
from app.services.extraction import (
    DEFAULT_SOURCE_EXTRACTOR_NAME,
    DEFAULT_SOURCE_EXTRACTOR_VERSION,
)


MONTH_NAMES = [
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
]

logger = logging.getLogger(__name__)

EMPTY_LINK_ROW = {"url": "", "note": ""}
EMPTY_CONNECTION_ROW = {"entry_id": "", "entry_title": "", "note": ""}
DEFAULT_TIMELINE_PAGE_SIZE = 25
MAX_TIMELINE_PAGE_SIZE = 50
MAX_GENERATION_PREFERRED_TAGS = 50


class SavedUrlsCache(TypedDict):
    urls: set[str] | None
    ts: float


# Module-level TTL cache for saved entry URLs
_saved_urls_cache: SavedUrlsCache = {"urls": None, "ts": 0.0}
_SAVED_URLS_TTL_SECONDS = 30.0


def _invalidate_saved_urls_cache() -> None:
    _saved_urls_cache["urls"] = None
    _saved_urls_cache["ts"] = 0.0




class DuplicateEntrySourceUrlError(ValueError):
    def __init__(
        self,
        message: str = "This timeline group already has an entry with the same source URL.",
    ) -> None:
        super().__init__(message)


class TimelineEntryGroup(TypedDict):
    key: str
    label: str
    event_year: int
    event_month: int
    count: int
    focus_key: str
    playback_intro_delay_ms: int
    playback_entry_interval_ms: int
    playback_outro_delay_ms: int
    playback_burst_level: Literal["steady", "burst", "surge"]
    entries: list[Entry]


class TimelineYearBucket(TypedDict):
    key: str
    label: str
    event_year: int
    count: int
    focus_key: str
    drill_view: Literal["months"]
    drill_year: int
    drill_month: None


class TimelineMonthBucket(TypedDict):
    key: str
    label: str
    event_year: int
    event_month: int
    count: int
    focus_key: str
    drill_view: Literal["events"]
    drill_year: int
    drill_month: int


def blank_form_state() -> EntryFormState:
    return EntryFormState(
        values={
            "event_year": "",
            "event_month": "",
            "event_day": "",
            "group_id": "",
            "title": "",
            "source_url": "",
            "summary_instructions": "",
            "generated_text": "",
            "final_text": "",
            "tags": "",
            "source_snapshot_source_url": "",
            "source_snapshot_final_url": "",
            "source_snapshot_title": "",
            "source_snapshot_markdown": "",
            "source_snapshot_content_type": "",
            "source_snapshot_fetched_utc": "",
            "source_snapshot_http_etag": "",
            "source_snapshot_http_last_modified": "",
            "source_snapshot_extractor_name": "",
            "source_snapshot_extractor_version": "",
        },
        errors={},
        link_rows=[],
    )


def form_state_from_entry(entry: Entry) -> EntryFormState:
    return EntryFormState(
        values={
            "event_year": str(entry.event_year),
            "event_month": str(entry.event_month),
            "event_day": "" if entry.event_day is None else str(entry.event_day),
            "group_id": str(entry.group_id),
            "title": entry.title,
            "source_url": entry.source_url or "",
            "summary_instructions": "",
            "generated_text": entry.generated_text or "",
            "final_text": entry.final_text,
            "tags": ", ".join(entry.tags),
            "source_snapshot_source_url": "",
            "source_snapshot_final_url": "",
            "source_snapshot_title": "",
            "source_snapshot_markdown": "",
            "source_snapshot_content_type": "",
            "source_snapshot_fetched_utc": "",
            "source_snapshot_http_etag": "",
            "source_snapshot_http_last_modified": "",
            "source_snapshot_extractor_name": "",
            "source_snapshot_extractor_version": "",
        },
        errors={},
        link_rows=(
            [{"url": link.url, "note": link.note} for link in entry.links]
            or [EMPTY_LINK_ROW.copy()]
        ),
        connection_rows=(
            [
                {
                    "entry_id": str(c.connected_entry_id),
                    "entry_title": c.connected_entry_title,
                    "note": c.note,
                }
                for c in entry.connections
                if c.direction == "outgoing"
            ]
            or [EMPTY_CONNECTION_ROW.copy()]
        ),
    )


def validate_entry_form(
    form_data: Mapping[str, object],
) -> tuple[EntryFormState, EntryPayload | None]:
    values = {
        "event_year": str(form_data.get("event_year", "")).strip(),
        "event_month": str(form_data.get("event_month", "")).strip(),
        "event_day": str(form_data.get("event_day", "")).strip(),
        "group_id": str(form_data.get("group_id", "")).strip(),
        "title": str(form_data.get("title", "")).strip(),
        "source_url": str(form_data.get("source_url", "")).strip(),
        "summary_instructions": str(
            form_data.get("summary_instructions", "")
        ).strip(),
        "generated_text": str(form_data.get("generated_text", "")).strip(),
        "final_text": str(form_data.get("final_text", "")).strip(),
        "tags": str(form_data.get("tags", "")).strip(),
        "source_snapshot_source_url": str(
            form_data.get("source_snapshot_source_url", "")
        ).strip(),
        "source_snapshot_final_url": str(
            form_data.get("source_snapshot_final_url", "")
        ).strip(),
        "source_snapshot_title": str(
            form_data.get("source_snapshot_title", "")
        ).strip(),
        "source_snapshot_markdown": str(
            form_data.get("source_snapshot_markdown", "")
        ).replace("\r\n", "\n").strip(),
        "source_snapshot_content_type": str(
            form_data.get("source_snapshot_content_type", "")
        ).strip(),
        "source_snapshot_fetched_utc": str(
            form_data.get("source_snapshot_fetched_utc", "")
        ).strip(),
        "source_snapshot_http_etag": str(
            form_data.get("source_snapshot_http_etag", "")
        ).strip(),
        "source_snapshot_http_last_modified": str(
            form_data.get("source_snapshot_http_last_modified", "")
        ).strip(),
        "source_snapshot_extractor_name": str(
            form_data.get("source_snapshot_extractor_name", "")
        ).strip(),
        "source_snapshot_extractor_version": str(
            form_data.get("source_snapshot_extractor_version", "")
        ).strip(),
    }
    link_rows = parse_link_rows(form_data)
    connection_rows = parse_connection_rows(form_data)
    errors: dict[str, str] = {}

    event_year = _parse_int(
        values["event_year"], "event_year", errors, minimum=1900, maximum=2100
    )
    event_month = _parse_int(
        values["event_month"], "event_month", errors, minimum=1, maximum=12
    )
    event_day = None
    group_id = _parse_int(
        values["group_id"], "group_id", errors, minimum=1, maximum=1_000_000_000
    )
    if values["event_day"]:
        event_day = _parse_int(
            values["event_day"], "event_day", errors, minimum=1, maximum=31
        )

    if (
        event_year is not None
        and event_month is not None
        and event_day is not None
        and "event_day" not in errors
    ):
        try:
            date(event_year, event_month, event_day)
        except ValueError:
            errors["event_day"] = "Provide a valid calendar date."

    if not values["title"]:
        errors["title"] = "Title is required."

    if not values["final_text"]:
        errors["final_text"] = "Event summary is required."

    source_url = values["source_url"] or None
    if source_url and not _is_valid_url(source_url):
        errors["source_url"] = "Provide a valid http or https URL."

    links = validate_link_rows(link_rows, errors)
    connections = validate_connection_rows(connection_rows, errors)

    tags = normalize_tags(values["tags"])
    source_snapshot = _build_source_snapshot_payload(values, source_url=source_url)
    state = EntryFormState(
        values=values,
        errors=errors,
        link_rows=link_rows or [EMPTY_LINK_ROW.copy()],
        connection_rows=connection_rows or [EMPTY_CONNECTION_ROW.copy()],
    )
    if errors or event_year is None or event_month is None or group_id is None:
        return state, None

    payload = EntryPayload(
        event_year=event_year,
        event_month=event_month,
        event_day=event_day,
        group_id=group_id,
        title=values["title"],
        source_url=source_url,
        generated_text=values["generated_text"] or None,
        final_text=values["final_text"],
        tags=tags,
        links=links,
        connections=connections,
        source_snapshot=source_snapshot,
    )
    return state, payload


def _build_source_snapshot_payload(
    values: Mapping[str, str], *, source_url: str | None
) -> EntrySourceSnapshotPayload | None:
    markdown = values.get("source_snapshot_markdown", "").replace("\r\n", "\n").strip()
    snapshot_source_url = values.get("source_snapshot_source_url", "") or None
    if source_url is None or not markdown or snapshot_source_url != source_url:
        return None

    final_url = values.get("source_snapshot_final_url", "") or source_url
    if not _is_valid_url(final_url):
        final_url = source_url

    fetched_utc = values.get("source_snapshot_fetched_utc", "") or utc_now_iso()
    if not _looks_like_iso_datetime(fetched_utc):
        fetched_utc = utc_now_iso()

    extractor_name = (
        values.get("source_snapshot_extractor_name", "")
        or DEFAULT_SOURCE_EXTRACTOR_NAME
    )
    extractor_version = (
        values.get("source_snapshot_extractor_version", "")
        or DEFAULT_SOURCE_EXTRACTOR_VERSION
    )

    return EntrySourceSnapshotPayload(
        source_url=source_url,
        final_url=final_url,
        raw_title=values.get("source_snapshot_title", "") or None,
        markdown=markdown,
        fetched_utc=fetched_utc,
        content_type=values.get("source_snapshot_content_type", "") or None,
        http_etag=values.get("source_snapshot_http_etag", "") or None,
        http_last_modified=(
            values.get("source_snapshot_http_last_modified", "") or None
        ),
        extractor_name=extractor_name,
        extractor_version=extractor_version,
    )


def _looks_like_iso_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def parse_link_rows(form_data: Mapping[str, object]) -> list[dict[str, str]]:
    getlist = cast(
        Callable[[str], Iterable[object]] | None,
        getattr(form_data, "getlist", None),
    )
    raw_urls = list(getlist("link_url")) if getlist is not None else []
    raw_notes = list(getlist("link_note")) if getlist is not None else []
    row_count = max(len(raw_urls), len(raw_notes))
    rows: list[dict[str, str]] = []
    for index in range(row_count):
        url = str(raw_urls[index] if index < len(raw_urls) else "").strip()
        note = str(raw_notes[index] if index < len(raw_notes) else "").strip()
        rows.append({"url": url, "note": note})
    return rows


def validate_link_rows(
    link_rows: list[dict[str, str]], errors: dict[str, str]
) -> list[EntryLinkPayload]:
    validated: list[EntryLinkPayload] = []
    for index, row in enumerate(link_rows):
        url = row["url"]
        note = row["note"]
        has_value = bool(url or note)
        if not has_value:
            continue
        if not url:
            errors[f"link_url_{index}"] = "Provide a valid http or https URL."
            continue
        if not _is_valid_url(url):
            errors[f"link_url_{index}"] = "Provide a valid http or https URL."
        if not note:
            errors[f"link_note_{index}"] = "Add a brief note for this URL."
        if errors.get(f"link_url_{index}") or errors.get(f"link_note_{index}"):
            continue
        validated.append(EntryLinkPayload(url=url, note=note))
    return validated


def parse_connection_rows(form_data: Mapping[str, object]) -> list[dict[str, str]]:
    getlist = cast(
        Callable[[str], Iterable[object]] | None,
        getattr(form_data, "getlist", None),
    )
    raw_ids = list(getlist("connection_entry_id")) if getlist is not None else []
    raw_titles = list(getlist("connection_entry_title")) if getlist is not None else []
    raw_notes = list(getlist("connection_note")) if getlist is not None else []
    row_count = max(len(raw_ids), len(raw_notes))
    rows: list[dict[str, str]] = []
    for index in range(row_count):
        entry_id = str(raw_ids[index] if index < len(raw_ids) else "").strip()
        entry_title = str(raw_titles[index] if index < len(raw_titles) else "").strip()
        note = str(raw_notes[index] if index < len(raw_notes) else "").strip()
        rows.append({"entry_id": entry_id, "entry_title": entry_title, "note": note})
    return rows


def validate_connection_rows(
    connection_rows: list[dict[str, str]], errors: dict[str, str]
) -> list[EntryConnectionPayload]:
    validated: list[EntryConnectionPayload] = []
    seen_ids: set[int] = set()
    for index, row in enumerate(connection_rows):
        entry_id_str = row["entry_id"]
        note = row["note"]
        if not entry_id_str:
            continue
        try:
            target_id = int(entry_id_str)
        except ValueError:
            errors[f"connection_entry_id_{index}"] = "Invalid entry selection."
            continue
        if target_id in seen_ids:
            continue
        seen_ids.add(target_id)
        validated.append(EntryConnectionPayload(target_entry_id=target_id, note=note))
    return validated


def normalize_tags(raw_tags: str) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for part in raw_tags.split(","):
        value = " ".join(part.strip().split())
        if not value:
            continue
        lowered = value.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(value)
    return normalized


def list_group_tag_vocabulary(
    connection: sqlite3.Connection,
    group_id: int,
    *,
    limit: int = MAX_GENERATION_PREFERRED_TAGS,
) -> list[str]:
    """Return the most common distinct tags already used in a timeline group."""
    if limit <= 0:
        return []

    rows = connection.execute(
        """
        SELECT t.name, COUNT(*) AS usage_count
        FROM tags t
        JOIN entry_tags et ON et.tag_id = t.id
        JOIN entries e ON e.id = et.entry_id
        WHERE e.group_id = ?
        GROUP BY t.id, t.name
        ORDER BY usage_count DESC, lower(t.name) ASC
        LIMIT ?
        """,
        (group_id, limit),
    ).fetchall()
    return [str(row["name"]) for row in rows]


def compute_sort_key(year: int, month: int, day: int | None) -> int:
    return (year * 10000) + (month * 100) + (day or 0)


def _ensure_unique_source_url(
    connection: sqlite3.Connection,
    *,
    group_id: int,
    source_url: str | None,
    exclude_entry_id: int | None = None,
) -> None:
    if source_url is None:
        return

    parameters: list[int | str] = [group_id, source_url]
    where_sql = "WHERE group_id = ? AND source_url = ?"
    if exclude_entry_id is not None:
        where_sql += " AND id != ?"
        parameters.append(exclude_entry_id)

    duplicate_row = connection.execute(
        f"SELECT id FROM entries {where_sql} LIMIT 1",
        parameters,
    ).fetchone()
    if duplicate_row is not None:
        raise DuplicateEntrySourceUrlError()


def save_entry(connection: sqlite3.Connection, payload: EntryPayload) -> int:
    _ensure_unique_source_url(
        connection,
        group_id=payload.group_id,
        source_url=payload.source_url,
    )
    now = utc_now_iso()
    cursor = connection.execute(
        """
        INSERT INTO entries (
            event_year,
            event_month,
            event_day,
            sort_key,
            group_id,
            title,
            source_url,
            generated_text,
            final_text,
            created_utc,
            updated_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.event_year,
            payload.event_month,
            payload.event_day,
            compute_sort_key(
                payload.event_year, payload.event_month, payload.event_day
            ),
            payload.group_id,
            payload.title,
            payload.source_url,
            payload.generated_text,
            payload.final_text,
            now,
            now,
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("Failed to determine the new entry id.")
    entry_id = int(cursor.lastrowid)
    sync_entry_tags(connection, entry_id, payload.tags)
    sync_entry_links(connection, entry_id, payload.links)
    sync_entry_connections(connection, entry_id, payload.connections)
    if payload.source_snapshot is not None:
        upsert_entry_source_snapshot(connection, entry_id, payload.source_snapshot)
    _sync_embedding_without_failing(connection, entry_id, payload.final_text)
    _invalidate_saved_urls_cache()
    return entry_id


def update_entry(
    connection: sqlite3.Connection, entry_id: int, payload: EntryPayload
) -> None:
    existing_row = connection.execute(
        "SELECT source_url FROM entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    previous_source_url = (
        str(existing_row["source_url"])
        if existing_row is not None and existing_row["source_url"] is not None
        else None
    )
    _ensure_unique_source_url(
        connection,
        group_id=payload.group_id,
        source_url=payload.source_url,
        exclude_entry_id=entry_id,
    )
    connection.execute(
        """
        UPDATE entries
        SET event_year = ?,
            event_month = ?,
            event_day = ?,
            sort_key = ?,
            group_id = ?,
            title = ?,
            source_url = ?,
            generated_text = ?,
            final_text = ?,
            updated_utc = ?
        WHERE id = ?
        """,
        (
            payload.event_year,
            payload.event_month,
            payload.event_day,
            compute_sort_key(
                payload.event_year, payload.event_month, payload.event_day
            ),
            payload.group_id,
            payload.title,
            payload.source_url,
            payload.generated_text,
            payload.final_text,
            utc_now_iso(),
            entry_id,
        ),
    )
    sync_entry_tags(connection, entry_id, payload.tags)
    sync_entry_links(connection, entry_id, payload.links)
    sync_entry_connections(connection, entry_id, payload.connections)
    if payload.source_snapshot is not None:
        upsert_entry_source_snapshot(connection, entry_id, payload.source_snapshot)
    elif previous_source_url != payload.source_url:
        delete_entry_source_snapshot(connection, entry_id)
    _sync_embedding_without_failing(connection, entry_id, payload.final_text)
    _invalidate_saved_urls_cache()


def upsert_entry_source_snapshot(
    connection: sqlite3.Connection,
    entry_id: int,
    payload: EntrySourceSnapshotPayload,
) -> None:
    normalized_markdown = payload.markdown.replace("\r\n", "\n").strip()
    if not normalized_markdown:
        delete_entry_source_snapshot(connection, entry_id)
        return

    markdown_bytes = normalized_markdown.encode("utf-8")
    connection.execute(
        """
        INSERT INTO entry_source_snapshots (
            entry_id,
            source_url,
            final_url,
            raw_title,
            source_markdown,
            fetched_utc,
            content_type,
            http_etag,
            http_last_modified,
            content_sha256,
            extractor_name,
            extractor_version,
            markdown_char_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
            source_url = excluded.source_url,
            final_url = excluded.final_url,
            raw_title = excluded.raw_title,
            source_markdown = excluded.source_markdown,
            fetched_utc = excluded.fetched_utc,
            content_type = excluded.content_type,
            http_etag = excluded.http_etag,
            http_last_modified = excluded.http_last_modified,
            content_sha256 = excluded.content_sha256,
            extractor_name = excluded.extractor_name,
            extractor_version = excluded.extractor_version,
            markdown_char_count = excluded.markdown_char_count
        """,
        (
            entry_id,
            payload.source_url,
            payload.final_url,
            payload.raw_title,
            normalized_markdown,
            payload.fetched_utc,
            payload.content_type,
            payload.http_etag,
            payload.http_last_modified,
            hashlib.sha256(markdown_bytes).hexdigest(),
            payload.extractor_name,
            payload.extractor_version,
            len(normalized_markdown),
        ),
    )


def delete_entry_source_snapshot(connection: sqlite3.Connection, entry_id: int) -> None:
    connection.execute(
        "DELETE FROM entry_source_snapshots WHERE entry_id = ?",
        (entry_id,),
    )


def get_entry_source_snapshot(
    connection: sqlite3.Connection, entry_id: int
) -> EntrySourceSnapshot | None:
    row = connection.execute(
        """
        SELECT
            entry_id,
            source_url,
            final_url,
            raw_title,
            source_markdown AS markdown,
            fetched_utc,
            content_type,
            http_etag,
            http_last_modified,
            content_sha256,
            extractor_name,
            extractor_version,
            markdown_char_count
        FROM entry_source_snapshots
        WHERE entry_id = ?
        """,
        (entry_id,),
    ).fetchone()
    if row is None:
        return None
    return entry_source_snapshot_from_row(row)


def list_entry_source_snapshots(
    connection: sqlite3.Connection, entry_ids: Iterable[int] | None = None
) -> dict[int, EntrySourceSnapshot]:
    parameters: list[int] = []
    where_sql = ""
    if entry_ids is not None:
        parameters = [int(entry_id) for entry_id in entry_ids]
        if not parameters:
            return {}
        placeholders = ",".join("?" for _ in parameters)
        where_sql = f"WHERE entry_id IN ({placeholders})"

    rows = connection.execute(
        f"""
        SELECT
            entry_id,
            source_url,
            final_url,
            raw_title,
            source_markdown AS markdown,
            fetched_utc,
            content_type,
            http_etag,
            http_last_modified,
            content_sha256,
            extractor_name,
            extractor_version,
            markdown_char_count
        FROM entry_source_snapshots
        {where_sql}
        """,
        parameters,
    ).fetchall()
    return {
        snapshot.entry_id: snapshot
        for snapshot in (entry_source_snapshot_from_row(row) for row in rows)
    }


def sync_entry_tags(
    connection: sqlite3.Connection, entry_id: int, tags: list[str]
) -> None:
    connection.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
    if not tags:
        return
    connection.executemany(
        "INSERT OR IGNORE INTO tags(name) VALUES (?)",
        [(t,) for t in tags],
    )
    placeholders = ",".join("?" for _ in tags)
    tag_rows = connection.execute(
        f"SELECT id FROM tags WHERE name IN ({placeholders})", tags
    ).fetchall()
    if tag_rows:
        connection.executemany(
            "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id) VALUES (?, ?)",
            [(entry_id, row["id"]) for row in tag_rows],
        )


def merge_entry_tags(
    connection: sqlite3.Connection, entry_id: int, new_tags: list[str]
) -> None:
    """Add tags to an entry without removing existing ones. Skips tags already present (case-insensitive)."""
    existing = {
        row["name"].casefold()
        for row in connection.execute(
            "SELECT t.name FROM tags t JOIN entry_tags et ON t.id = et.tag_id WHERE et.entry_id = ?",
            (entry_id,),
        ).fetchall()
    }
    tags_to_add = [t for t in new_tags if t.casefold() not in existing]
    if not tags_to_add:
        return
    connection.executemany(
        "INSERT OR IGNORE INTO tags(name) VALUES (?)",
        [(t,) for t in tags_to_add],
    )
    placeholders = ",".join("?" for _ in tags_to_add)
    tag_rows = connection.execute(
        f"SELECT id FROM tags WHERE name IN ({placeholders})", tags_to_add
    ).fetchall()
    if tag_rows:
        connection.executemany(
            "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id) VALUES (?, ?)",
            [(entry_id, row["id"]) for row in tag_rows],
        )


def sync_entry_links(
    connection: sqlite3.Connection,
    entry_id: int,
    links: list[EntryLinkPayload],
) -> None:
    connection.execute("DELETE FROM entry_links WHERE entry_id = ?", (entry_id,))
    now = utc_now_iso()
    for link in links:
        connection.execute(
            """
            INSERT INTO entry_links(entry_id, url, note, created_utc)
            VALUES (?, ?, ?, ?)
            """,
            (entry_id, link.url, link.note, now),
        )


def sync_entry_connections(
    connection: sqlite3.Connection,
    entry_id: int,
    connections: list[EntryConnectionPayload],
) -> None:
    connection.execute(
        "DELETE FROM entry_connections WHERE source_entry_id = ?", (entry_id,)
    )
    now = utc_now_iso()
    for conn_payload in connections:
        if conn_payload.target_entry_id == entry_id:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO entry_connections(
                source_entry_id, target_entry_id, note, created_utc
            )
            VALUES (?, ?, ?, ?)
            """,
            (entry_id, conn_payload.target_entry_id, conn_payload.note, now),
        )


def get_entry_connections(
    connection: sqlite3.Connection,
    entry_id: int,
) -> list[EntryConnection]:
    rows = connection.execute(
        """
        SELECT ec.id, e.id AS connected_id, e.title, e.event_year, e.event_month,
               e.event_day, tg.name AS group_name, ec.note,
               'outgoing' AS direction, ec.created_utc
        FROM entry_connections ec
        JOIN entries e ON e.id = ec.target_entry_id
        JOIN timeline_groups tg ON tg.id = e.group_id
        WHERE ec.source_entry_id = ?

        UNION ALL

        SELECT ec.id, e.id AS connected_id, e.title, e.event_year, e.event_month,
               e.event_day, tg.name AS group_name, ec.note,
               'incoming' AS direction, ec.created_utc
        FROM entry_connections ec
        JOIN entries e ON e.id = ec.source_entry_id
        JOIN timeline_groups tg ON tg.id = e.group_id
        WHERE ec.target_entry_id = ?

        ORDER BY direction, title
        """,
        (entry_id, entry_id),
    ).fetchall()
    results: list[EntryConnection] = []
    for row in rows:
        day = row["event_day"]
        month_name = MONTH_NAMES[row["event_month"] - 1]
        display_date = (
            f"{month_name} {day}, {row['event_year']}"
            if day
            else f"{month_name} {row['event_year']}"
        )
        results.append(
            EntryConnection(
                id=row["id"],
                connected_entry_id=row["connected_id"],
                connected_entry_title=row["title"] or "",
                connected_entry_date=display_date,
                connected_entry_group=row["group_name"],
                note=row["note"] or "",
                direction=row["direction"],
                created_utc=row["created_utc"],
            )
        )
    return results


def get_entry_connection_count(
    connection: sqlite3.Connection,
    entry_id: int,
) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS cnt FROM entry_connections
        WHERE source_entry_id = ? OR target_entry_id = ?
        """,
        (entry_id, entry_id),
    ).fetchone()
    return int(row["cnt"]) if row else 0


def search_entries_for_connection(
    connection: sqlite3.Connection,
    query: str,
    exclude_entry_id: int | None = None,
    group_id: int | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    like_pattern = f"%{query}%"
    sql = """
        SELECT e.id, e.title, e.event_year, e.event_month, e.event_day,
               tg.name AS group_name
        FROM entries e
        JOIN timeline_groups tg ON tg.id = e.group_id
        WHERE e.title LIKE ?
    """
    params: list[Any] = [like_pattern]
    if exclude_entry_id is not None:
        sql += " AND e.id != ?"
        params.append(exclude_entry_id)
    if group_id is not None:
        sql += " AND e.group_id = ?"
        params.append(group_id)
    sql += " ORDER BY e.sort_key DESC LIMIT ?"
    params.append(limit)
    rows = connection.execute(sql, params).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        day = row["event_day"]
        month_name = MONTH_NAMES[row["event_month"] - 1]
        display_date = (
            f"{month_name} {day}, {row['event_year']}"
            if day
            else f"{month_name} {row['event_year']}"
        )
        results.append(
            {
                "id": row["id"],
                "title": row["title"] or "",
                "display_date": display_date,
                "group_name": row["group_name"],
            }
        )
    return results


def build_connection_graph(
    connection: sqlite3.Connection,
    group_id: int,
    include_tag_edges: bool = False,
) -> dict[str, Any]:
    entry_rows = connection.execute(
        """
        SELECT e.id, e.title, e.event_year, e.event_month, e.event_day,
               tg.name AS group_name
        FROM entries e
        JOIN timeline_groups tg ON tg.id = e.group_id
        WHERE e.group_id = ?
          AND (
              e.id IN (SELECT source_entry_id FROM entry_connections)
              OR e.id IN (SELECT target_entry_id FROM entry_connections)
          )
        ORDER BY e.sort_key DESC
        """,
        (group_id,),
    ).fetchall()

    entry_ids = {row["id"] for row in entry_rows}
    if not entry_ids:
        return {"nodes": [], "edges": []}

    conn_rows = connection.execute(
        """
        SELECT ec.source_entry_id, ec.target_entry_id, ec.note
        FROM entry_connections ec
        WHERE ec.source_entry_id IN ({placeholders})
          AND ec.target_entry_id IN ({placeholders})
        """.format(
            placeholders=",".join("?" for _ in entry_ids)
        ),
        list(entry_ids) + list(entry_ids),
    ).fetchall()

    count_map: dict[int, int] = {}
    for cr in conn_rows:
        s, t = cr["source_entry_id"], cr["target_entry_id"]
        count_map[s] = count_map.get(s, 0) + 1
        count_map[t] = count_map.get(t, 0) + 1

    nodes: list[dict[str, Any]] = []
    for row in entry_rows:
        day = row["event_day"]
        month_name = MONTH_NAMES[row["event_month"] - 1]
        display_date = (
            f"{month_name} {day}, {row['event_year']}"
            if day
            else f"{month_name} {row['event_year']}"
        )
        nodes.append(
            {
                "id": row["id"],
                "label": row["title"] or f"Entry #{row['id']}",
                "size": count_map.get(row["id"], 1),
                "display_date": display_date,
                "group_name": row["group_name"],
            }
        )

    edges: list[dict[str, Any]] = [
        {
            "source": cr["source_entry_id"],
            "target": cr["target_entry_id"],
            "weight": 1.0,
            "note": cr["note"] or "",
            "type": "explicit",
        }
        for cr in conn_rows
    ]

    if include_tag_edges:
        tag_rows = connection.execute(
            """
            SELECT et1.entry_id AS id1, et2.entry_id AS id2, COUNT(*) AS shared
            FROM entry_tags et1
            JOIN entry_tags et2 ON et1.tag_id = et2.tag_id
                AND et1.entry_id < et2.entry_id
            WHERE et1.entry_id IN ({p}) AND et2.entry_id IN ({p})
            GROUP BY et1.entry_id, et2.entry_id
            HAVING shared >= 2
            """.format(p=",".join("?" for _ in entry_ids)),
            list(entry_ids) + list(entry_ids),
        ).fetchall()

        explicit_pairs = {
            (cr["source_entry_id"], cr["target_entry_id"]) for cr in conn_rows
        }
        max_shared = max((r["shared"] for r in tag_rows), default=1)
        for tr in tag_rows:
            pair = (min(tr["id1"], tr["id2"]), max(tr["id1"], tr["id2"]))
            if pair in explicit_pairs or (pair[1], pair[0]) in explicit_pairs:
                continue
            edges.append(
                {
                    "source": tr["id1"],
                    "target": tr["id2"],
                    "weight": tr["shared"] / max_shared,
                    "note": "",
                    "type": "tag",
                }
            )
            for eid in (tr["id1"], tr["id2"]):
                if eid not in entry_ids:
                    entry_ids.add(eid)

    return {"nodes": nodes, "edges": edges}


def _sync_embedding_without_failing(
    connection: sqlite3.Connection, entry_id: int, final_text: str
) -> None:
    try:
        sync_entry_embedding(connection, entry_id, final_text)
    except EmbeddingConfigurationError:
        return
    except EmbeddingIndexMismatchError as exc:
        logger.warning("Embedding sync skipped for entry %s: %s", entry_id, exc)
    except EmbeddingError as exc:
        logger.warning("Embedding sync failed for entry %s: %s", entry_id, exc)


def list_timeline_entries(
    connection: sqlite3.Connection, group_id: int | None = None
) -> list[Entry]:
    rows = connection.execute(
        f"""
        SELECT
            e.*,
            tg.name AS group_name,
            COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags_csv,
            COALESCE(
                json_group_array(
                    DISTINCT CASE
                        WHEN el.id IS NOT NULL THEN json_object(
                            'id', el.id,
                            'url', el.url,
                            'note', el.note,
                            'created_utc', el.created_utc
                        )
                    END
                ),
                '[]'
            ) AS links_json
        FROM entries e
        JOIN timeline_groups tg ON tg.id = e.group_id
        LEFT JOIN entry_tags et ON et.entry_id = e.id
        LEFT JOIN tags t ON t.id = et.tag_id
        LEFT JOIN entry_links el ON el.entry_id = e.id
        {"WHERE e.group_id = ?" if group_id is not None else ""}
        GROUP BY e.id, tg.name
        ORDER BY e.sort_key DESC, e.updated_utc DESC
        """,
        (group_id,) if group_id is not None else (),
    ).fetchall()
    return [entry_from_row(row) for row in rows]


def normalize_timeline_page_size(page_size: int | None) -> int:
    if page_size is None:
        return DEFAULT_TIMELINE_PAGE_SIZE
    return max(1, min(page_size, MAX_TIMELINE_PAGE_SIZE))


def encode_timeline_cursor(entry: Entry) -> str:
    payload = {
        "sort_key": entry.sort_key,
        "updated_utc": entry.updated_utc,
        "id": entry.id,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_timeline_cursor(cursor: str) -> tuple[int, str, int]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            (cursor + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid timeline cursor.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Invalid timeline cursor.")

    sort_key = payload.get("sort_key")
    updated_utc = payload.get("updated_utc")
    entry_id = payload.get("id")

    if (
        not isinstance(sort_key, int)
        or not isinstance(updated_utc, str)
        or not updated_utc
        or not isinstance(entry_id, int)
    ):
        raise ValueError("Invalid timeline cursor.")

    return sort_key, updated_utc, entry_id


def list_timeline_entries_page(
    connection: sqlite3.Connection,
    *,
    group_id: int | None = None,
    page_size: int | None = None,
    cursor: tuple[int, str, int] | None = None,
) -> tuple[list[Entry], str | None, bool]:
    normalized_page_size = normalize_timeline_page_size(page_size)
    parameters: list[int | str] = []
    where_clauses: list[str] = []

    if group_id is not None:
        where_clauses.append("e.group_id = ?")
        parameters.append(group_id)

    if cursor is not None:
        where_clauses.append(
            "(e.sort_key < ? OR (e.sort_key = ? AND (e.updated_utc < ? OR (e.updated_utc = ? AND e.id < ?))))"
        )
        parameters.extend([cursor[0], cursor[0], cursor[1], cursor[1], cursor[2]])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    rows = connection.execute(
        f"""
        SELECT
            e.*,
            tg.name AS group_name,
            COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags_csv,
            COALESCE(
                json_group_array(
                    DISTINCT CASE
                        WHEN el.id IS NOT NULL THEN json_object(
                            'id', el.id,
                            'url', el.url,
                            'note', el.note,
                            'created_utc', el.created_utc
                        )
                    END
                ),
                '[]'
            ) AS links_json
        FROM entries e
        JOIN timeline_groups tg ON tg.id = e.group_id
        LEFT JOIN entry_tags et ON et.entry_id = e.id
        LEFT JOIN tags t ON t.id = et.tag_id
        LEFT JOIN entry_links el ON el.entry_id = e.id
        {where_sql}
        GROUP BY e.id, tg.name
        ORDER BY e.sort_key DESC, e.updated_utc DESC, e.id DESC
        LIMIT ?
        """,
        [*parameters, normalized_page_size + 1],
    ).fetchall()

    page_entries = [entry_from_row(row) for row in rows[:normalized_page_size]]
    has_more = len(rows) > normalized_page_size
    next_cursor = (
        encode_timeline_cursor(page_entries[-1]) if has_more and page_entries else None
    )
    return page_entries, next_cursor, has_more


def paginate_entries_in_memory(
    entries: list[Entry],
    *,
    page_size: int | None = None,
    cursor: tuple[int, str, int] | None = None,
) -> tuple[list[Entry], str | None, bool]:
    normalized_page_size = normalize_timeline_page_size(page_size)
    filtered_entries = entries
    if cursor is not None:
        cursor_sort_key, cursor_updated_utc, cursor_id = cursor
        filtered_entries = [
            entry
            for entry in entries
            if (
                entry.sort_key < cursor_sort_key
                or (
                    entry.sort_key == cursor_sort_key
                    and (
                        entry.updated_utc < cursor_updated_utc
                        or (
                            entry.updated_utc == cursor_updated_utc
                            and entry.id < cursor_id
                        )
                    )
                )
            )
        ]

    page_entries = filtered_entries[:normalized_page_size]
    has_more = len(filtered_entries) > normalized_page_size
    next_cursor = (
        encode_timeline_cursor(page_entries[-1]) if has_more and page_entries else None
    )
    return page_entries, next_cursor, has_more


def get_entry(connection: sqlite3.Connection, entry_id: int) -> Entry | None:
    row = connection.execute(
        """
        SELECT
            e.*,
            tg.name AS group_name,
            COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags_csv,
            COALESCE(
                json_group_array(
                    DISTINCT CASE
                        WHEN el.id IS NOT NULL THEN json_object(
                            'id', el.id,
                            'url', el.url,
                            'note', el.note,
                            'created_utc', el.created_utc
                        )
                    END
                ),
                '[]'
            ) AS links_json
        FROM entries e
        JOIN timeline_groups tg ON tg.id = e.group_id
        LEFT JOIN entry_tags et ON et.entry_id = e.id
        LEFT JOIN tags t ON t.id = et.tag_id
        LEFT JOIN entry_links el ON el.entry_id = e.id
        WHERE e.id = ?
        GROUP BY e.id, tg.name
        """,
        (entry_id,),
    ).fetchone()
    if row is None:
        return None
    return entry_from_row(row)


def list_saved_entry_urls(connection: sqlite3.Connection) -> set[str]:
    cached = _saved_urls_cache["urls"]
    if cached is not None and (time.monotonic() - _saved_urls_cache["ts"]) < _SAVED_URLS_TTL_SECONDS:
        return set(cached)

    rows = connection.execute(
        """
        SELECT source_url AS url
        FROM entries
        WHERE source_url IS NOT NULL AND TRIM(source_url) != ''
        UNION
        SELECT url AS url
        FROM entry_links
        WHERE url IS NOT NULL AND TRIM(url) != ''
        """
    ).fetchall()

    saved_urls: set[str] = set()
    for row in rows:
        value = str(row["url"] if "url" in row.keys() else row[0]).strip()
        if value:
            saved_urls.add(value)

    _saved_urls_cache["urls"] = saved_urls
    _saved_urls_cache["ts"] = time.monotonic()
    return saved_urls


def timeline_playback_profile(
    entry_count: int,
) -> tuple[int, int, int, Literal["steady", "burst", "surge"]]:
    normalized_count = max(1, entry_count)
    entry_interval_ms = max(70, int(round(320 / (normalized_count**0.5))))
    intro_delay_ms = max(160, 360 - (min(normalized_count, 6) - 1) * 35)
    outro_delay_ms = 240 if normalized_count >= 4 else 320
    burst_level: Literal["steady", "burst", "surge"]
    if normalized_count >= 6:
        burst_level = "surge"
    elif normalized_count >= 3:
        burst_level = "burst"
    else:
        burst_level = "steady"
    return intro_delay_ms, entry_interval_ms, outro_delay_ms, burst_level


def build_timeline_groups(entries: list[Entry]) -> list[TimelineEntryGroup]:
    groups: list[TimelineEntryGroup] = []
    current_key: tuple[int, int] | None = None
    current_group: TimelineEntryGroup | None = None

    for entry in entries:
        key = (entry.event_year, entry.event_month)
        if key != current_key:
            current_key = key
            current_group = {
                "key": f"{entry.event_year}-{entry.event_month:02d}",
                "label": f"{MONTH_NAMES[entry.event_month - 1]} {entry.event_year}",
                "event_year": entry.event_year,
                "event_month": entry.event_month,
                "count": 0,
                "focus_key": f"month-{entry.event_year}-{entry.event_month:02d}",
                "playback_intro_delay_ms": 0,
                "playback_entry_interval_ms": 0,
                "playback_outro_delay_ms": 0,
                "playback_burst_level": "steady",
                "entries": [],
            }
            groups.append(current_group)
        assert current_group is not None
        current_group["entries"].append(entry)
        current_group["count"] += 1

    for group in groups:
        (
            group["playback_intro_delay_ms"],
            group["playback_entry_interval_ms"],
            group["playback_outro_delay_ms"],
            group["playback_burst_level"],
        ) = timeline_playback_profile(group["count"])

    return groups


def list_timeline_year_buckets(entries: list[Entry]) -> list[TimelineYearBucket]:
    buckets: list[TimelineYearBucket] = []
    bucket_map: dict[int, TimelineYearBucket] = {}

    for entry in entries:
        bucket = bucket_map.get(entry.event_year)
        if bucket is None:
            bucket = TimelineYearBucket(
                key=str(entry.event_year),
                label=str(entry.event_year),
                event_year=entry.event_year,
                count=0,
                focus_key=f"year-{entry.event_year}",
                drill_view="months",
                drill_year=entry.event_year,
                drill_month=None,
            )
            bucket_map[entry.event_year] = bucket
            buckets.append(bucket)
        bucket["count"] += 1

    return buckets


def list_timeline_month_buckets(
    entries: list[Entry], *, year: int | None = None
) -> list[TimelineMonthBucket]:
    buckets: list[TimelineMonthBucket] = []
    bucket_map: dict[tuple[int, int], TimelineMonthBucket] = {}

    for entry in entries:
        if year is not None and entry.event_year != year:
            continue

        key = (entry.event_year, entry.event_month)
        bucket = bucket_map.get(key)
        if bucket is None:
            bucket = TimelineMonthBucket(
                key=f"{entry.event_year}-{entry.event_month:02d}",
                label=f"{MONTH_NAMES[entry.event_month - 1]} {entry.event_year}",
                event_year=entry.event_year,
                event_month=entry.event_month,
                count=0,
                focus_key=f"month-{entry.event_year}-{entry.event_month:02d}",
                drill_view="events",
                drill_year=entry.event_year,
                drill_month=entry.event_month,
            )
            bucket_map[key] = bucket
            buckets.append(bucket)
        bucket["count"] += 1

    return buckets


def get_heatmap_counts(
    connection: sqlite3.Connection,
    year: int,
    group_id: int | None = None,
) -> HeatmapData:
    """Return per-day entry counts for a calendar year.

    Entries without ``event_day`` are counted on the first day of their month.
    """
    group_filter = "AND e.group_id = ?" if group_id is not None else ""
    base_params: tuple[object, ...] = (year,) if group_id is None else (year, group_id)

    # Entries with a specific day
    rows_with_day = connection.execute(
        f"""
        SELECT event_month, event_day, COUNT(*) AS cnt
        FROM entries e
        WHERE e.event_year = ? {group_filter}
          AND e.event_day IS NOT NULL
        GROUP BY event_month, event_day
        """,
        base_params,
    ).fetchall()

    counts: dict[str, int] = {}
    for row in rows_with_day:
        month, day, cnt = row[0], row[1], row[2]
        key = f"{year}-{month:02d}-{day:02d}"
        counts[key] = counts.get(key, 0) + cnt

    # Entries without a specific day count toward the first of the month.
    rows_without_day = connection.execute(
        f"""
        SELECT event_month, COUNT(*) AS cnt
        FROM entries e
        WHERE e.event_year = ? {group_filter}
          AND e.event_day IS NULL
        GROUP BY event_month
        """,
        base_params,
    ).fetchall()

    for row in rows_without_day:
        month, cnt = row[0], row[1]
        key = f"{year}-{month:02d}-01"
        counts[key] = counts.get(key, 0) + cnt

    # All years with entries (for year navigation)
    years_query = "SELECT DISTINCT event_year FROM entries"
    if group_id is not None:
        years_query += " WHERE group_id = ?"
        years_rows = connection.execute(years_query, (group_id,)).fetchall()
    else:
        years_rows = connection.execute(years_query).fetchall()
    years_available = sorted(row[0] for row in years_rows)

    total = sum(counts.values())
    return HeatmapData(counts=counts, total=total, year=year, years_available=years_available)


def list_timeline_summary_groups(
    entries: list[Entry],
    *,
    year: int | None = None,
    month: int | None = None,
) -> list[TimelineEntryGroup]:
    scoped_entries = [
        entry
        for entry in entries
        if (year is None or entry.event_year == year)
        and (month is None or entry.event_month == month)
    ]
    return build_timeline_groups(scoped_entries)




def entry_from_row(row: sqlite3.Row) -> Entry:
    tags_csv = row["tags_csv"] or ""
    tags = [tag for tag in tags_csv.split(",") if tag]
    links = parse_links_json(row["links_json"] if "links_json" in row.keys() else None)
    day = row["event_day"]
    if day:
        display_date = (
            f"{MONTH_NAMES[row['event_month'] - 1]} {day}, {row['event_year']}"
        )
    else:
        display_date = f"{MONTH_NAMES[row['event_month'] - 1]} {row['event_year']}"
    return Entry(
        id=row["id"],
        event_year=row["event_year"],
        event_month=row["event_month"],
        event_day=day,
        sort_key=row["sort_key"],
        group_id=row["group_id"],
        group_name=row["group_name"],
        title=row["title"] or "",
        source_url=row["source_url"],
        generated_text=row["generated_text"],
        final_text=row["final_text"],
        created_utc=row["created_utc"],
        updated_utc=row["updated_utc"],
        tags=tags,
        links=links,
        display_date=display_date,
        preview_text=preview_text(row["final_text"]),
    )


def entry_source_snapshot_from_row(row: sqlite3.Row) -> EntrySourceSnapshot:
    return EntrySourceSnapshot(
        entry_id=row["entry_id"],
        source_url=row["source_url"],
        final_url=row["final_url"],
        raw_title=row["raw_title"],
        markdown=row["markdown"],
        fetched_utc=row["fetched_utc"],
        content_type=row["content_type"],
        http_etag=row["http_etag"],
        http_last_modified=row["http_last_modified"],
        content_sha256=row["content_sha256"],
        extractor_name=row["extractor_name"],
        extractor_version=row["extractor_version"],
        markdown_char_count=row["markdown_char_count"],
    )


def parse_links_json(raw_value: str | None) -> list[EntryLink]:
    if not raw_value:
        return []
    parsed = json.loads(raw_value)
    links: list[EntryLink] = []
    for item in parsed:
        if item is None:
            continue
        links.append(
            EntryLink(
                id=int(item["id"]),
                url=str(item["url"]),
                note=str(item["note"]),
                created_utc=str(item["created_utc"]),
            )
        )
    return links


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _parse_int(
    raw_value: str,
    field_name: str,
    errors: dict[str, str],
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if not raw_value:
        errors[field_name] = "This field is required."
        return None
    try:
        value = int(raw_value)
    except ValueError:
        errors[field_name] = "Enter a valid number."
        return None
    if value < minimum or value > maximum:
        errors[field_name] = f"Enter a value between {minimum} and {maximum}."
        return None
    return value


def _is_valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


