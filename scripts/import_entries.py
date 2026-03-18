from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from app.db import connection_context, init_db
from app.schemas import EntryLinkPayload, EntryPayload
from app.services.entries import (
    compute_sort_key,
    sync_entry_links,
    sync_entry_tags,
    utc_now_iso,
)


MONTH_NUMBERS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

DATE_AND_TITLE_PATTERN = re.compile(
    r"^(?P<month>[A-Za-z]+)\s+(?:(?P<day>\d{1,2}),\s+)?(?P<year>\d{4})(?:\s*\([^)]*\))?:\s+(?P<title>.+)$"
)


@dataclass(slots=True)
class ParsedEntry:
    payload: EntryPayload
    source_heading: str
    created_utc: str | None = None
    updated_utc: str | None = None


def normalize_whitespace(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").replace("\u202f", " ").split())


def parse_date_and_title(raw_heading: str) -> tuple[int, int, int | None, str]:
    heading = normalize_whitespace(raw_heading)
    match = DATE_AND_TITLE_PATTERN.match(heading)
    if match is None:
        raise ValueError(f"Unsupported heading format: {raw_heading!r}")

    month_token = match.group("month").casefold().rstrip(".")
    month = MONTH_NUMBERS.get(month_token)
    if month is None:
        raise ValueError(f"Unsupported month in heading: {raw_heading!r}")

    day_text = match.group("day")
    day = int(day_text) if day_text else None
    year = int(match.group("year"))
    title = normalize_title_text(match.group("title"))
    return year, month, day, title


def normalize_title_text(value: str) -> str:
    return normalize_whitespace(value.replace("**", "")).strip()


def parse_entries_document(raw_html: str) -> list[ParsedEntry]:
    soup = BeautifulSoup(f"<ul>{raw_html}</ul>", "html.parser")
    container = soup.find("ul")
    if container is None:
        return []

    parsed_entries: list[ParsedEntry] = []
    for item in container.find_all("li", recursive=False):
        heading_tag = item.find("h4")
        paragraph_tag = item.find("p")
        if heading_tag is None or paragraph_tag is None:
            continue

        heading_text = heading_tag.get_text(" ", strip=True)
        event_year, event_month, event_day, title = parse_date_and_title(heading_text)
        final_text = str(paragraph_tag).strip()

        parsed_entries.append(
            ParsedEntry(
                payload=EntryPayload(
                    event_year=event_year,
                    event_month=event_month,
                    event_day=event_day,
                    group_id=1,
                    title=title,
                    source_url=None,
                    generated_text=None,
                    final_text=final_text,
                    tags=[],
                    links=[],
                ),
                source_heading=heading_text,
            )
        )

    return parsed_entries


def parse_entries_export(raw_json: str) -> list[ParsedEntry]:
    document = json.loads(raw_json)
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("Export JSON must contain an 'entries' array.")

    parsed_entries: list[ParsedEntry] = []
    for index, item in enumerate(raw_entries, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Export entry #{index} must be an object.")

        tags = item.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f"Export entry #{index} has invalid tags.")

        raw_links = item.get("links") or []
        if not isinstance(raw_links, list):
            raise ValueError(f"Export entry #{index} has invalid links.")

        links: list[EntryLinkPayload] = []
        for raw_link in raw_links:
            if not isinstance(raw_link, dict):
                raise ValueError(f"Export entry #{index} has invalid links.")
            url = str(raw_link.get("url") or "").strip()
            note = str(raw_link.get("note") or "").strip()
            if not url or not note:
                raise ValueError(
                    f"Export entry #{index} contains a link without both url and note."
                )
            links.append(EntryLinkPayload(url=url, note=note))

        parsed_entries.append(
            ParsedEntry(
                payload=EntryPayload(
                    event_year=int(item["event_year"]),
                    event_month=int(item["event_month"]),
                    event_day=(
                        None
                        if item.get("event_day") in (None, "")
                        else int(item["event_day"])
                    ),
                    group_id=1,
                    title=str(item.get("title") or "").strip(),
                    source_url=_optional_string(item.get("source_url")),
                    generated_text=_optional_string(item.get("generated_text")),
                    final_text=str(item.get("final_text") or "").strip(),
                    tags=tags,
                    links=links,
                ),
                source_heading=str(item.get("title") or f"entry-{index}"),
                created_utc=_optional_string(item.get("created_utc")),
                updated_utc=_optional_string(item.get("updated_utc")),
            )
        )

    return parsed_entries


def entry_exists(connection: object, payload: EntryPayload) -> bool:
    if payload.event_day is None:
        row = connection.execute(
            """
            SELECT 1
            FROM entries
            WHERE event_year = ?
              AND event_month = ?
              AND event_day IS NULL
              AND title = ?
              AND final_text = ?
            LIMIT 1
            """,
            (
                payload.event_year,
                payload.event_month,
                payload.title,
                payload.final_text,
            ),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT 1
            FROM entries
            WHERE event_year = ?
              AND event_month = ?
              AND event_day = ?
              AND title = ?
              AND final_text = ?
            LIMIT 1
            """,
            (
                payload.event_year,
                payload.event_month,
                payload.event_day,
                payload.title,
                payload.final_text,
            ),
        ).fetchone()
    return row is not None


def import_entries(input_path: Path, *, skip_existing: bool) -> tuple[int, int]:
    raw_input = input_path.read_text(encoding="utf-8")
    parsed_entries = _parse_input_file(input_path, raw_input)

    inserted = 0
    skipped = 0
    with connection_context() as connection:
        for parsed_entry in parsed_entries:
            if skip_existing and entry_exists(connection, parsed_entry.payload):
                skipped += 1
                continue

            insert_entry_without_embeddings(
                connection,
                parsed_entry.payload,
                created_utc=parsed_entry.created_utc,
                updated_utc=parsed_entry.updated_utc,
            )
            inserted += 1

    return inserted, skipped


def insert_entry_without_embeddings(
    connection: object,
    payload: EntryPayload,
    *,
    created_utc: str | None = None,
    updated_utc: str | None = None,
) -> int:
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
            created_utc or now,
            updated_utc or created_utc or now,
        ),
    )
    entry_id = int(cursor.lastrowid)
    sync_entry_tags(connection, entry_id, payload.tags)
    sync_entry_links(connection, entry_id, payload.links)
    return entry_id


def _parse_input_file(input_path: Path, raw_input: str) -> list[ParsedEntry]:
    if input_path.suffix.casefold() == ".json":
        return parse_entries_export(raw_input)
    return parse_entries_document(raw_input)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def main() -> None:
    workspace_root = Path(__file__).resolve().parents[1]
    load_dotenv(workspace_root / ".env", override=True)

    parser = argparse.ArgumentParser(
        description="Import HTML list entries or exported JSON entries into the EventTracker SQLite database."
    )
    parser.add_argument(
        "input_path", type=Path, help="Path to the source HTML or JSON file."
    )
    parser.add_argument(
        "--allow-duplicates",
        action="store_true",
        help="Insert rows even when an exact matching entry already exists.",
    )
    args = parser.parse_args()

    if not args.input_path.exists():
        raise SystemExit(f"Input file not found: {args.input_path}")

    init_db()
    inserted, skipped = import_entries(
        args.input_path, skip_existing=not args.allow_duplicates
    )
    print(f"Imported entries: {inserted}")
    print(f"Skipped duplicates: {skipped}")


if __name__ == "__main__":
    main()
