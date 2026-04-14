from __future__ import annotations

from dataclasses import dataclass, field

from app.models import StoryArtifactKind, StoryFormat, StoryScopeType


@dataclass(slots=True)
class EntryLinkPayload:
    url: str
    note: str


@dataclass(slots=True)
class EntryConnectionPayload:
    target_entry_id: int
    note: str


@dataclass(slots=True)
class EntrySourceSnapshotPayload:
    source_url: str
    final_url: str
    raw_title: str | None
    markdown: str
    fetched_utc: str
    content_type: str | None
    http_etag: str | None
    http_last_modified: str | None
    extractor_name: str
    extractor_version: str


@dataclass(slots=True)
class EntryPayload:
    event_year: int
    event_month: int
    event_day: int | None
    group_id: int
    title: str
    source_url: str | None
    generated_text: str | None
    final_text: str
    tags: list[str]
    links: list[EntryLinkPayload]
    connections: list[EntryConnectionPayload] = field(default_factory=list)
    source_snapshot: EntrySourceSnapshotPayload | None = None


@dataclass(slots=True)
class EntryFormState:
    values: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    link_rows: list[dict[str, str]] = field(default_factory=list)
    connection_rows: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class TimelineStoryScopePayload:
    scope_type: StoryScopeType
    group_id: int | None = None
    query_text: str | None = None
    year: int | None = None
    month: int | None = None


@dataclass(slots=True)
class TimelineStoryCitationPayload:
    entry_id: int
    citation_order: int
    quote_text: str | None = None
    note: str | None = None


@dataclass(slots=True)
class TimelineStorySavePayload(TimelineStoryScopePayload):
    format: StoryFormat = "executive_summary"
    title: str = ""
    narrative_html: str = ""
    narrative_text: str | None = None
    generated_utc: str = ""
    updated_utc: str = ""
    provider_name: str | None = None
    source_entry_count: int = 0
    truncated_input: bool = False
    error_text: str | None = None
    citations: list[TimelineStoryCitationPayload] = field(default_factory=list)


@dataclass(slots=True)
class TimelineStoryArtifactSavePayload:
    artifact_kind: StoryArtifactKind
    source_format: str
    source_text: str
    compiled_html: str = ""
    compiled_css: str = ""
    metadata_json: str = "{}"
    generated_utc: str = ""
    compiled_utc: str | None = None
    compiler_name: str | None = None
    compiler_version: str | None = None


@dataclass(slots=True)
class TimelineStoryFormState:
    values: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
