from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class EntryLink:
    id: int
    url: str
    note: str
    created_utc: str


@dataclass(slots=True)
class Entry:
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
    tags: list[str] = field(default_factory=list)
    links: list[EntryLink] = field(default_factory=list)
    display_date: str = ""
    preview_text: str = ""


@dataclass(slots=True)
class SearchResult:
    entry: Entry
    snippet: str
    rank: float


@dataclass(slots=True)
class TimelineGroup:
    id: int
    name: str
    web_search_query: str | None = None
    entry_count: int = 0
    is_default: bool = False
