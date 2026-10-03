"""Poster Board: a one-screen corkboard view of the most important entries.

The board ranks the entries in a timeline scope by a heuristic importance
score, keeps the top ``limit`` of them, and buckets them into four size tiers.
Poster size is the only visual signal of importance; placement on the board is
deliberately random (see ``app/templates/poster_board/board.js``).

The payload produced here is embedded as JSON into a self-contained HTML page,
so the same structure powers both the in-app board and the HTML export.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TypedDict
from urllib.parse import urlparse

from app.models import Entry
from app.services.entries import is_valid_url, list_connections_within
from app.services.formatting import preview_text, sanitize_rich_text

DEFAULT_BOARD_LIMIT = 30
BOARD_LIMIT_CHOICES = (20, 30, 45, 60)
MAX_CATEGORY_HUES = 9
OTHER_CATEGORY = "Other"
UNTAGGED_CATEGORY = "Untagged"
HEADLINE_FALLBACK_LENGTH = 90

# Share of the board that lands in each size tier, biggest first. Whatever is
# left after tiers 1-3 becomes tier 4. Mirrors the mix of the reference board.
_TIER_SHARES = (0.10, 0.30, 0.50)


class BoardSource(TypedDict):
    name: str
    url: str


class BoardConnection(TypedDict):
    id: int
    title: str
    date: str
    note: str


class BoardItem(TypedDict):
    id: int
    rank: int
    tier: int
    headline: str
    dek: str
    body_html: str
    category: str
    hue: int
    group: str
    date: str
    sort_key: int
    tags: list[str]
    sources: list[BoardSource]
    connections: list[BoardConnection]
    connection_count: int


class BoardCategory(TypedDict):
    name: str
    hue: int
    count: int


class BoardPayload(TypedDict):
    items: list[BoardItem]
    categories: list[BoardCategory]
    category_basis: str
    total_in_scope: int
    date_range: str


@dataclass(slots=True, frozen=True)
class ConnectionEdge:
    source_id: int
    target_id: int
    note: str


def normalize_board_limit(raw_value: str | int | None) -> int:
    """Clamp a requested poster count to one of the supported choices."""
    try:
        requested = int(raw_value) if raw_value not in (None, "") else DEFAULT_BOARD_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_BOARD_LIMIT
    if requested in BOARD_LIMIT_CHOICES:
        return requested
    return min(BOARD_LIMIT_CHOICES, key=lambda choice: abs(choice - requested))


def filter_entries_by_year(entries: Sequence[Entry], year: int | None) -> list[Entry]:
    if year is None:
        return list(entries)
    return [entry for entry in entries if entry.event_year == year]


def list_board_years(entries: Iterable[Entry]) -> list[int]:
    return sorted({entry.event_year for entry in entries}, reverse=True)


def build_poster_board(
    connection: sqlite3.Connection,
    entries: Sequence[Entry],
    *,
    limit: int = DEFAULT_BOARD_LIMIT,
) -> BoardPayload:
    """Rank *entries*, keep the top *limit*, and shape them for the board."""
    edges = _load_connection_edges(connection, [entry.id for entry in entries])
    return build_poster_board_payload(entries, edges=edges, limit=limit)


def build_poster_board_payload(
    entries: Sequence[Entry],
    *,
    edges: Sequence[ConnectionEdge] = (),
    limit: int = DEFAULT_BOARD_LIMIT,
) -> BoardPayload:
    if not entries:
        return {
            "items": [],
            "categories": [],
            "category_basis": "tag",
            "total_in_scope": 0,
            "date_range": "",
        }

    # Keyed by the other entry id so a pair stored in both directions (A->B and
    # B->A) counts as one connection; the first note seen wins.
    neighbors: defaultdict[int, dict[int, str]] = defaultdict(dict)
    for edge in edges:
        neighbors[edge.source_id].setdefault(edge.target_id, edge.note)
        neighbors[edge.target_id].setdefault(edge.source_id, edge.note)

    min_sort_key = min(entry.sort_key for entry in entries)
    max_sort_key = max(entry.sort_key for entry in entries)
    ranked = sorted(
        entries,
        key=lambda entry: (
            -_importance_score(
                entry,
                connection_count=len(neighbors[entry.id]),
                min_sort_key=min_sort_key,
                max_sort_key=max_sort_key,
            ),
            -entry.sort_key,
            entry.id,
        ),
    )
    selected = ranked[: max(1, limit)]
    selected_ids = {entry.id for entry in selected}

    category_basis = (
        "group" if len({entry.group_id for entry in selected}) > 1 else "tag"
    )
    categories, category_by_entry = _assign_categories(selected, category_basis)
    hue_by_category = {category["name"]: category["hue"] for category in categories}
    entries_by_id = {entry.id: entry for entry in entries}
    tiers = _assign_tiers(len(selected))

    items: list[BoardItem] = []
    for rank, (entry, tier) in enumerate(zip(selected, tiers, strict=True), start=1):
        category = category_by_entry[entry.id]
        items.append(
            {
                "id": entry.id,
                "rank": rank,
                "tier": tier,
                "headline": _headline(entry),
                "dek": entry.preview_text,
                "body_html": sanitize_rich_text(entry.final_text),
                "category": category,
                "hue": hue_by_category[category],
                "group": entry.group_name,
                "date": entry.display_date,
                "sort_key": entry.sort_key,
                "tags": list(entry.tags),
                "sources": _sources(entry),
                "connections": _connections_for(
                    neighbors[entry.id], entries_by_id, selected_ids
                ),
                "connection_count": len(neighbors[entry.id]),
            }
        )

    return {
        "items": items,
        "categories": categories,
        "category_basis": category_basis,
        "total_in_scope": len(entries),
        "date_range": _date_range(selected),
    }


def _importance_score(
    entry: Entry,
    *,
    connection_count: int,
    min_sort_key: int,
    max_sort_key: int,
) -> float:
    """Heuristic importance: how connected, sourced, documented, and recent."""
    text_length = len(entry.final_text)
    score = 3.0 * min(connection_count, 6)
    score += 1.5 * min(len(entry.links), 4)
    score += 1.0 if entry.source_url else 0.0
    score += min(text_length / 600.0, 3.0)
    score += 0.5 * min(len(entry.tags), 4)
    if max_sort_key > min_sort_key:
        score += 3.0 * (entry.sort_key - min_sort_key) / (max_sort_key - min_sort_key)
    return score


def _assign_tiers(count: int) -> list[int]:
    if count <= 0:
        return []
    tier_one = max(1, round(count * _TIER_SHARES[0]))
    tier_two = round(count * _TIER_SHARES[1])
    tier_three = round(count * _TIER_SHARES[2])
    tiers: list[int] = []
    for tier, size in ((1, tier_one), (2, tier_two), (3, tier_three)):
        tiers.extend([tier] * size)
    tiers = tiers[:count]
    tiers.extend([4] * (count - len(tiers)))
    return tiers


def _assign_categories(
    entries: Sequence[Entry], basis: str
) -> tuple[list[BoardCategory], dict[int, str]]:
    raw_category_by_entry: dict[int, str] = {}
    if basis == "group":
        for entry in entries:
            raw_category_by_entry[entry.id] = entry.group_name
    else:
        tag_counts = Counter(tag for entry in entries for tag in entry.tags)
        for entry in entries:
            if not entry.tags:
                raw_category_by_entry[entry.id] = UNTAGGED_CATEGORY
                continue
            raw_category_by_entry[entry.id] = max(
                entry.tags, key=lambda tag: (tag_counts[tag], tag)
            )

    counts = Counter(raw_category_by_entry.values())
    named = sorted(
        (name for name in counts if name != UNTAGGED_CATEGORY),
        key=lambda name: (-counts[name], name.lower()),
    )
    keep = set(named[: MAX_CATEGORY_HUES - 1]) if len(named) > MAX_CATEGORY_HUES else set(named)

    category_by_entry: dict[int, str] = {}
    for entry_id, name in raw_category_by_entry.items():
        if name == UNTAGGED_CATEGORY or name in keep:
            category_by_entry[entry_id] = name
        else:
            category_by_entry[entry_id] = OTHER_CATEGORY

    final_counts = Counter(category_by_entry.values())
    ordered = sorted(
        final_counts,
        key=lambda name: (
            name in (OTHER_CATEGORY, UNTAGGED_CATEGORY),
            -final_counts[name],
            name.lower(),
        ),
    )
    categories: list[BoardCategory] = []
    next_hue = 0
    for name in ordered:
        if name in (OTHER_CATEGORY, UNTAGGED_CATEGORY):
            hue = -1
        else:
            hue = next_hue
            next_hue += 1
        categories.append({"name": name, "hue": hue, "count": final_counts[name]})
    return categories, category_by_entry


def _headline(entry: Entry) -> str:
    title = entry.title.strip()
    if title:
        return title
    return preview_text(entry.final_text, HEADLINE_FALLBACK_LENGTH) or "Untitled event"


def _sources(entry: Entry) -> list[BoardSource]:
    sources: list[BoardSource] = []
    seen: set[str] = set()
    if entry.source_url and is_valid_url(entry.source_url):
        sources.append({"name": _host(entry.source_url), "url": entry.source_url})
        seen.add(entry.source_url)
    for link in entry.links:
        if link.url in seen or not is_valid_url(link.url):
            continue
        seen.add(link.url)
        sources.append({"name": link.note.strip() or _host(link.url), "url": link.url})
    return sources


def _connections_for(
    neighbors: dict[int, str],
    entries_by_id: dict[int, Entry],
    selected_ids: set[int],
) -> list[BoardConnection]:
    """Connections to other entries in scope, posters on the board first."""
    connections: list[BoardConnection] = []
    for other_id, note in neighbors.items():
        other = entries_by_id[other_id]
        connections.append(
            {
                "id": other_id,
                "title": _headline(other),
                "date": other.display_date,
                "note": note,
            }
        )
    connections.sort(key=lambda item: (item["id"] not in selected_ids, item["title"].lower()))
    return connections


def _date_range(entries: Sequence[Entry]) -> str:
    oldest = min(entries, key=lambda entry: entry.sort_key)
    newest = max(entries, key=lambda entry: entry.sort_key)
    if oldest.display_date == newest.display_date:
        return newest.display_date
    return f"{oldest.display_date} – {newest.display_date}"


def _load_connection_edges(
    connection: sqlite3.Connection, entry_ids: Sequence[int]
) -> list[ConnectionEdge]:
    """Connections whose two ends are both inside the scoped entry set."""
    if not entry_ids:
        return []
    return [
        ConnectionEdge(
            source_id=int(row["source_entry_id"]),
            target_id=int(row["target_entry_id"]),
            note=str(row["note"] or ""),
        )
        for row in list_connections_within(connection, entry_ids)
    ]


def _host(url: str) -> str:
    host = urlparse(url).hostname or url
    return host.removeprefix("www.")
