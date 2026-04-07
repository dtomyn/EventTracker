from __future__ import annotations

import base64
import calendar
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
from html import escape
import json
import logging
import sqlite3
import time
from typing import Literal, TypedDict, cast
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.models import Entry, EntryLink, TimelineGroup
from app.schemas import EntryFormState, EntryLinkPayload, EntryPayload
from app.services.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingError,
    EmbeddingIndexMismatchError,
    sync_entry_embedding,
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
DEFAULT_TIMELINE_PAGE_SIZE = 25
MAX_TIMELINE_PAGE_SIZE = 50
MAX_TIMELINE_GROUP_WEB_SEARCH_QUERY_LENGTH = 400
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


ALLOWED_RICH_TEXT_TAGS = {
    "b",
    "blockquote",
    "br",
    "code",
    "em",
    "i",
    "li",
    "ol",
    "p",
    "strong",
    "u",
    "ul",
}
ALLOWED_SEARCH_SNIPPET_TAGS = ALLOWED_RICH_TEXT_TAGS | {"mark"}


class TimelineGroupValidationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


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
        },
        errors={},
        link_rows=(
            [{"url": link.url, "note": link.note} for link in entry.links]
            or [EMPTY_LINK_ROW.copy()]
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
    }
    link_rows = parse_link_rows(form_data)
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

    tags = normalize_tags(values["tags"])
    state = EntryFormState(
        values=values,
        errors=errors,
        link_rows=link_rows or [EMPTY_LINK_ROW.copy()],
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
    )
    return state, payload


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
    _sync_embedding_without_failing(connection, entry_id, payload.final_text)
    _invalidate_saved_urls_cache()
    return entry_id


def update_entry(
    connection: sqlite3.Connection, entry_id: int, payload: EntryPayload
) -> None:
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
    _sync_embedding_without_failing(connection, entry_id, payload.final_text)
    _invalidate_saved_urls_cache()


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

    Entries without ``event_day`` are distributed evenly across their month.
    """
    from app.models import HeatmapData

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

    # Entries without a specific day — distribute evenly across the month
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
        days_in_month = calendar.monthrange(year, month)[1]
        step = max(1, days_in_month // cnt) if cnt <= days_in_month else 1
        for i in range(cnt):
            day = (i * step) % days_in_month + 1
            key = f"{year}-{month:02d}-{day:02d}"
            counts[key] = counts.get(key, 0) + 1

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


def list_timeline_groups(connection: sqlite3.Connection) -> list[TimelineGroup]:
    rows = connection.execute(
        """
        SELECT
            tg.id,
            tg.name,
            tg.web_search_query,
            tg.is_default,
            COUNT(e.id) AS entry_count
        FROM timeline_groups tg
        LEFT JOIN entries e ON e.group_id = tg.id
        GROUP BY tg.id, tg.name, tg.web_search_query, tg.is_default
        ORDER BY tg.is_default DESC, LOWER(tg.name) ASC, tg.id ASC
        """
    ).fetchall()
    return [
        TimelineGroup(
            id=int(row["id"]),
            name=row["name"],
            web_search_query=row["web_search_query"],
            entry_count=int(row["entry_count"]),
            is_default=bool(row["is_default"]),
        )
        for row in rows
    ]


def get_timeline_group(
    connection: sqlite3.Connection, group_id: int
) -> TimelineGroup | None:
    row = connection.execute(
        "SELECT id, name, web_search_query, is_default FROM timeline_groups WHERE id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        return None
    return TimelineGroup(
        id=int(row["id"]),
        name=row["name"],
        web_search_query=row["web_search_query"],
        is_default=bool(row["is_default"]),
    )


def get_default_timeline_group(
    connection: sqlite3.Connection,
) -> TimelineGroup | None:
    row = connection.execute(
        "SELECT id, name, web_search_query, is_default FROM timeline_groups WHERE is_default = 1 ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return TimelineGroup(
        id=int(row["id"]),
        name=row["name"],
        web_search_query=row["web_search_query"],
        is_default=bool(row["is_default"]),
    )


def create_timeline_group(
    connection: sqlite3.Connection,
    raw_name: str,
    raw_web_search_query: str = "",
    *,
    is_default: bool = False,
) -> TimelineGroup:
    name = normalize_timeline_group_name(raw_name)
    web_search_query = normalize_timeline_group_web_search_query(raw_web_search_query)
    if not name:
        raise TimelineGroupValidationError("name", "Group name is required.")

    try:
        cursor = connection.execute(
            "INSERT INTO timeline_groups(name, web_search_query, is_default) VALUES (?, ?, 0)",
            (name, web_search_query),
        )
    except sqlite3.IntegrityError as exc:
        raise TimelineGroupValidationError(
            "name", "A group with that name already exists."
        ) from exc

    if cursor.lastrowid is None:
        raise RuntimeError("Failed to determine the new timeline group id.")
    group_id = int(cursor.lastrowid)
    if is_default:
        set_default_timeline_group(connection, group_id)

    return TimelineGroup(
        id=group_id,
        name=name,
        web_search_query=web_search_query,
        is_default=is_default,
    )


def rename_timeline_group(
    connection: sqlite3.Connection,
    group_id: int,
    raw_name: str,
    raw_web_search_query: str = "",
    *,
    is_default: bool | None = None,
) -> None:
    name = normalize_timeline_group_name(raw_name)
    web_search_query = normalize_timeline_group_web_search_query(raw_web_search_query)
    if not name:
        raise TimelineGroupValidationError("name", "Group name is required.")

    try:
        cursor = connection.execute(
            "UPDATE timeline_groups SET name = ?, web_search_query = ? WHERE id = ?",
            (name, web_search_query, group_id),
        )
    except sqlite3.IntegrityError as exc:
        raise TimelineGroupValidationError(
            "name", "A group with that name already exists."
        ) from exc

    if cursor.rowcount == 0:
        raise LookupError("Timeline group not found.")

    if is_default is True:
        set_default_timeline_group(connection, group_id)
    elif is_default is False:
        clear_default_timeline_group(connection, group_id)


def delete_timeline_group(connection: sqlite3.Connection, group_id: int) -> None:
    row = connection.execute(
        """
        SELECT
            tg.id,
            tg.name,
            tg.is_default,
            COUNT(e.id) AS entry_count
        FROM timeline_groups tg
        LEFT JOIN entries e ON e.group_id = tg.id
        WHERE tg.id = ?
        GROUP BY tg.id, tg.name, tg.is_default
        """,
        (group_id,),
    ).fetchone()
    if row is None:
        raise LookupError("Timeline group not found.")

    if bool(row["is_default"]):
        raise ValueError("The default timeline group cannot be deleted.")

    if int(row["entry_count"]) > 0:
        raise ValueError(
            "This group cannot be deleted while it still has entries. Move those entries first."
        )

    connection.execute("DELETE FROM timeline_groups WHERE id = ?", (group_id,))


def set_default_timeline_group(connection: sqlite3.Connection, group_id: int) -> None:
    row = connection.execute(
        "SELECT id FROM timeline_groups WHERE id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        raise LookupError("Timeline group not found.")

    connection.execute(
        "UPDATE timeline_groups SET is_default = 0 WHERE is_default <> 0"
    )
    connection.execute(
        "UPDATE timeline_groups SET is_default = 1 WHERE id = ?",
        (group_id,),
    )


def clear_default_timeline_group(connection: sqlite3.Connection, group_id: int) -> None:
    connection.execute(
        "UPDATE timeline_groups SET is_default = 0 WHERE id = ?",
        (group_id,),
    )


def normalize_timeline_group_name(value: str) -> str:
    return " ".join(value.strip().split())


def normalize_timeline_group_web_search_query(value: str) -> str | None:
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    if len(normalized) > MAX_TIMELINE_GROUP_WEB_SEARCH_QUERY_LENGTH:
        raise TimelineGroupValidationError(
            "web_search_query",
            "Web search query must be 400 characters or fewer.",
        )
    return normalized


def preview_text(value: str, max_length: int = 280) -> str:
    clean = " ".join(plain_text_from_html(value).split())
    if len(clean) <= max_length:
        return clean
    return clean[: max_length - 1].rstrip() + "\u2026"


def format_plain_text(value: str) -> str:
    escaped = escape(value)
    return escaped.replace("\n", "<br>")


def plain_text_from_html(value: str) -> str:
    if not value:
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def sanitize_rich_text(value: str) -> str:
    return _sanitize_html(value, ALLOWED_RICH_TEXT_TAGS)


def sanitize_search_snippet(value: str) -> str:
    return _sanitize_html(value, ALLOWED_SEARCH_SNIPPET_TAGS)


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


def _sanitize_html(value: str, allowed_tags: set[str]) -> str:
    if not value:
        return ""

    soup = BeautifulSoup(value, "html.parser")
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue
        if tag.name in {"script", "style"}:
            tag.decompose()
            continue
        if tag.name not in allowed_tags:
            tag.unwrap()
            continue
        tag.attrs = {}

    return str(soup)
