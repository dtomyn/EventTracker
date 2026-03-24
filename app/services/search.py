from __future__ import annotations

import base64
import json
import re
import sqlite3

from app.models import Entry, SearchResult
from app.services.entries import entry_from_row, preview_text
from app.services.embeddings import SemanticMatch, search_semantic_matches


TOKEN_RE = re.compile(r"[\w-]+")
DEFAULT_SEARCH_PAGE_SIZE = 20
MAX_SEARCH_PAGE_SIZE = 50


def normalize_search_page_size(page_size: int | None) -> int:
    if page_size is None:
        return DEFAULT_SEARCH_PAGE_SIZE
    return max(1, min(page_size, MAX_SEARCH_PAGE_SIZE))


def encode_search_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_search_cursor(cursor: str) -> int:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            (cursor + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid search cursor.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Invalid search cursor.")

    offset = payload.get("offset")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("Invalid search cursor.")
    return offset


def paginate_search_results(
    results: list[SearchResult],
    *,
    page_size: int | None = None,
    cursor: int | None = None,
) -> tuple[list[SearchResult], str | None, bool]:
    normalized_page_size = normalize_search_page_size(page_size)
    offset = cursor or 0
    page_results = results[offset : offset + normalized_page_size]
    next_offset = offset + len(page_results)
    has_more = next_offset < len(results)
    next_cursor = encode_search_cursor(next_offset) if has_more else None
    return page_results, next_cursor, has_more


def search_entries(
    connection: sqlite3.Connection, raw_query: str, group_id: int | None = None
) -> list[SearchResult]:
    normalized_query = raw_query.strip()
    if not normalized_query:
        return []

    fts_rows = _search_fts_rows(connection, normalized_query, group_id=group_id)
    semantic_matches = _filter_semantic_matches_by_group(
        connection,
        search_semantic_matches(connection, normalized_query),
        group_id=group_id,
    )

    combined_scores: dict[int, float] = {}
    snippets_by_entry_id: dict[int, str] = {}

    for index, row in enumerate(fts_rows):
        entry_id = int(row["id"])
        combined_scores[entry_id] = combined_scores.get(entry_id, 0.0) + _rrf_score(
            index
        )
        snippets_by_entry_id[entry_id] = row["final_snippet"] or preview_text(
            row["final_text"]
        )

    for index, match in enumerate(semantic_matches):
        combined_scores[match.entry_id] = combined_scores.get(
            match.entry_id, 0.0
        ) + _rrf_score(index)

    if not combined_scores:
        return []

    rows_by_id = _get_entries_by_id(connection, list(combined_scores))
    sorted_ids = sorted(
        combined_scores,
        key=lambda entry_id: (
            combined_scores[entry_id],
            rows_by_id[entry_id]["sort_key"],
            rows_by_id[entry_id]["updated_utc"],
        ),
        reverse=True,
    )

    return [
        SearchResult(
            entry=entry_from_row(rows_by_id[entry_id]),
            snippet=snippets_by_entry_id.get(
                entry_id, preview_text(rows_by_id[entry_id]["final_text"])
            ),
            rank=combined_scores[entry_id],
        )
        for entry_id in sorted_ids
    ]


def filter_timeline_entries(
    connection: sqlite3.Connection, raw_query: str, group_id: int | None = None
) -> list[Entry]:
    normalized_query = raw_query.strip()
    if not normalized_query:
        return []

    tag_matched_ids = _find_exact_tag_entry_ids(
        connection,
        normalized_query,
        group_id=group_id,
    )
    if tag_matched_ids:
        rows_by_id = _get_entries_by_id(connection, tag_matched_ids)
        sorted_ids = sorted(
            tag_matched_ids,
            key=lambda entry_id: (
                rows_by_id[entry_id]["sort_key"],
                rows_by_id[entry_id]["updated_utc"],
            ),
            reverse=True,
        )
        return [entry_from_row(rows_by_id[entry_id]) for entry_id in sorted_ids]

    matched_ids = [
        result.entry.id
        for result in search_entries(connection, normalized_query, group_id=group_id)
    ]
    if not matched_ids:
        return []

    rows_by_id = _get_entries_by_id(connection, matched_ids)
    sorted_ids = sorted(
        set(matched_ids),
        key=lambda entry_id: (
            rows_by_id[entry_id]["sort_key"],
            rows_by_id[entry_id]["updated_utc"],
        ),
        reverse=True,
    )
    return [entry_from_row(rows_by_id[entry_id]) for entry_id in sorted_ids]


def build_fts_query(raw_query: str) -> str:
    tokens = TOKEN_RE.findall(raw_query)
    if not tokens:
        return ""
    return " ".join(f'"{token}"' for token in tokens)


def _search_fts_rows(
    connection: sqlite3.Connection, raw_query: str, group_id: int | None = None
) -> list[sqlite3.Row]:
    query = build_fts_query(raw_query)
    if not query:
        return []

    where_clauses = ["entries_fts MATCH ?"]
    parameters: list[str | int] = [query]
    if group_id is not None:
        where_clauses.append("e.group_id = ?")
        parameters.append(group_id)

    return connection.execute(
        f"""
        SELECT
            hits.*,
            hits.group_name,
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
        FROM (
            SELECT
                e.*,
                tg.name AS group_name,
                bm25(entries_fts) AS rank,
                snippet(entries_fts, 0, '<mark>', '</mark>', ' … ', 20) AS final_snippet
            FROM entries_fts
            JOIN entries e ON e.id = entries_fts.rowid
            JOIN timeline_groups tg ON tg.id = e.group_id
            WHERE {" AND ".join(where_clauses)}
            ORDER BY rank ASC, e.sort_key DESC
            LIMIT 50
        ) AS hits
        LEFT JOIN entry_tags et ON et.entry_id = hits.id
        LEFT JOIN tags t ON t.id = et.tag_id
        LEFT JOIN entry_links el ON el.entry_id = hits.id
        GROUP BY hits.id, hits.group_name
        ORDER BY hits.rank ASC, hits.sort_key DESC
        LIMIT 50
        """,
        parameters,
    ).fetchall()


def _get_entries_by_id(
    connection: sqlite3.Connection, entry_ids: list[int]
) -> dict[int, sqlite3.Row]:
    placeholders = ", ".join("?" for _ in entry_ids)
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
        WHERE e.id IN ({placeholders})
        GROUP BY e.id, tg.name
        """,
        entry_ids,
    ).fetchall()
    return {int(row["id"]): row for row in rows}


def _rrf_score(index: int, k: int = 60) -> float:
    return 1.0 / (k + index + 1)


def _filter_semantic_matches_by_group(
    connection: sqlite3.Connection,
    semantic_matches: list[SemanticMatch],
    group_id: int | None = None,
) -> list[SemanticMatch]:
    if group_id is None or not semantic_matches:
        return semantic_matches

    rows_by_id = _get_entries_by_id(
        connection,
        [match.entry_id for match in semantic_matches],
    )
    return [
        match
        for match in semantic_matches
        if match.entry_id in rows_by_id
        and rows_by_id[match.entry_id]["group_id"] == group_id
    ]


def _find_exact_tag_entry_ids(
    connection: sqlite3.Connection,
    query: str,
    *,
    group_id: int | None = None,
) -> list[int]:
    normalized_query = " ".join(query.split())
    if not normalized_query:
        return []

    where_clauses = ["LOWER(TRIM(t.name)) = LOWER(?)"]
    parameters: list[str | int] = [normalized_query]
    if group_id is not None:
        where_clauses.append("e.group_id = ?")
        parameters.append(group_id)

    rows = connection.execute(
        f"""
        SELECT DISTINCT e.id
        FROM entries e
        JOIN entry_tags et ON et.entry_id = e.id
        JOIN tags t ON t.id = et.tag_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY e.sort_key DESC, e.updated_utc DESC
        """,
        parameters,
    ).fetchall()
    return [int(row["id"]) for row in rows]
