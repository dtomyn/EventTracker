from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class EntryLinkPayload:
    url: str
    note: str


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


@dataclass(slots=True)
class EntryFormState:
    values: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    link_rows: list[dict[str, str]] = field(default_factory=list)
