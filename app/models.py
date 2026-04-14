from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias


StoryScopeType: TypeAlias = Literal["timeline", "search"]
StoryFormat: TypeAlias = Literal[
    "executive_summary",
    "detailed_chronology",
    "recent_changes",
]
StoryArtifactKind: TypeAlias = Literal["executive_deck"]
DeckSlidePurpose: TypeAlias = Literal[
    "title",
    "toc",
    "summary",
    "section_header",
    "turning_point",
    "highlight",
    "trajectory",
    "quote",
    "close",
    "thank_you",
]
DeckVisualKind: TypeAlias = Literal[
    "kpi_strip",
    "phase_timeline",
    "pull_quote",
    "bar_chart",
    "stat_card",
    "icon_grid",
]


@dataclass(slots=True)
class EntryLink:
    id: int
    url: str
    note: str
    created_utc: str


@dataclass(slots=True)
class EntryConnection:
    id: int
    connected_entry_id: int
    connected_entry_title: str
    connected_entry_date: str
    connected_entry_group: str
    note: str
    direction: str  # "outgoing" | "incoming"
    created_utc: str


@dataclass(slots=True)
class SuggestedConnection:
    id: int
    suggested_entry_id: int
    suggested_entry_title: str
    suggested_entry_date: str
    suggested_entry_group: str
    distance: float
    suggested_note: str
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
    connections: list[EntryConnection] = field(default_factory=list)
    display_date: str = ""
    preview_text: str = ""


@dataclass(slots=True)
class EntrySourceSnapshot:
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


@dataclass(slots=True)
class TimelineStoryScope:
    scope_type: StoryScopeType
    group_id: int | None = None
    query_text: str | None = None
    year: int | None = None
    month: int | None = None


@dataclass(slots=True)
class TimelineStoryCitation:
    story_id: int
    entry_id: int
    citation_order: int
    quote_text: str | None = None
    note: str | None = None


@dataclass(slots=True)
class TimelineStoryArtifact:
    id: int
    story_id: int
    artifact_kind: StoryArtifactKind
    source_format: str
    source_text: str
    compiled_html: str
    compiled_css: str
    metadata_json: str
    generated_utc: str
    compiled_utc: str | None = None
    compiler_name: str | None = None
    compiler_version: str | None = None


@dataclass(slots=True)
class TimelineStorySnapshot:
    id: int
    scope_type: StoryScopeType
    format: StoryFormat
    title: str
    narrative_html: str
    generated_utc: str
    updated_utc: str
    source_entry_count: int
    truncated_input: bool
    group_id: int | None = None
    query_text: str | None = None
    year: int | None = None
    month: int | None = None
    narrative_text: str | None = None
    provider_name: str | None = None
    error_text: str | None = None
    citations: list[TimelineStoryCitation] = field(default_factory=list)


@dataclass(slots=True)
class GeneratedExecutiveDeckSlide:
    slide_key: str
    headline: str
    purpose: DeckSlidePurpose
    body_points: list[str] = field(default_factory=list)
    callouts: list[str] = field(default_factory=list)
    visuals: list[DeckVisualKind] = field(default_factory=list)
    citations: list[int] = field(default_factory=list)


@dataclass(slots=True)
class GeneratedExecutiveDeck:
    title: str
    subtitle: str | None
    slides: list[GeneratedExecutiveDeckSlide]
    provider_name: str | None = None
    source_entry_count: int = 0
    truncated_input: bool = False


@dataclass(slots=True)
class HeatmapData:
    counts: dict[str, int]
    total: int
    year: int
    years_available: list[int]
