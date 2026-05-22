"""Batch (re-)extract markdown snapshots for entries that have a source_url.

Usage:
    uv run python -m scripts.refresh_source_snapshots           # all entries with a source_url
    uv run python -m scripts.refresh_source_snapshots --missing  # only entries without a snapshot yet
    uv run python -m scripts.refresh_source_snapshots --group 3  # only entries in group 3
    uv run python -m scripts.refresh_source_snapshots --dry-run  # preview what would be processed
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys

from dotenv import load_dotenv

from app.db import connection_context, init_db
from app.schemas import EntrySourceSnapshotPayload
from app.services.entries import upsert_entry_source_snapshot
from app.services.extraction import extract_url_text

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)


def _fetch_candidates(
    connection: sqlite3.Connection,
    *,
    only_missing: bool,
    group_id: int | None,
) -> list[tuple[int, str, str]]:
    """Return (entry_id, title, source_url) for entries eligible for extraction."""
    clauses = ["e.source_url IS NOT NULL", "e.source_url != ''"]
    params: list[object] = []

    if only_missing:
        clauses.append(
            "e.id NOT IN (SELECT entry_id FROM entry_source_snapshots)"
        )
    if group_id is not None:
        clauses.append("e.group_id = ?")
        params.append(group_id)

    where = " AND ".join(clauses)
    rows = connection.execute(
        f"SELECT e.id, e.title, e.source_url FROM entries e WHERE {where} ORDER BY e.id",
        params,
    ).fetchall()
    return [(row["id"], row["title"] or "(untitled)", row["source_url"]) for row in rows]


async def _process_entry(
    entry_id: int,
    title: str,
    source_url: str,
) -> EntrySourceSnapshotPayload | None:
    result = await extract_url_text(source_url)
    if result is None:
        logger.warning("  FAILED  id=%d url=%s", entry_id, source_url)
        return None

    return EntrySourceSnapshotPayload(
        source_url=result.source_url,
        final_url=result.final_url,
        raw_title=result.title,
        markdown=result.markdown,
        fetched_utc=result.fetched_utc,
        content_type=result.content_type,
        http_etag=result.http_etag,
        http_last_modified=result.http_last_modified,
        extractor_name=result.extractor_name,
        extractor_version=result.extractor_version,
    )


async def run(
    *,
    only_missing: bool,
    group_id: int | None,
    dry_run: bool,
    concurrency: int,
) -> None:
    init_db()
    with connection_context() as connection:
        candidates = _fetch_candidates(
            connection, only_missing=only_missing, group_id=group_id
        )

    total = len(candidates)
    if total == 0:
        logger.info("No entries to process.")
        return

    logger.info("Found %d entries to process%s.", total, " (dry-run)" if dry_run else "")
    if dry_run:
        for entry_id, title, source_url in candidates:
            logger.info("  [DRY-RUN] id=%-5d %-60s %s", entry_id, title[:60], source_url)
        return

    semaphore = asyncio.Semaphore(concurrency)
    succeeded = 0
    failed = 0
    skipped = 0

    async def _process_one(idx: int, entry_id: int, title: str, source_url: str) -> tuple[int, EntrySourceSnapshotPayload | None]:
        async with semaphore:
            logger.info("[%d/%d] id=%-5d %s", idx, total, entry_id, source_url)
            payload = await _process_entry(entry_id, title, source_url)
            return entry_id, payload

    tasks = [
        _process_one(i + 1, eid, title, url)
        for i, (eid, title, url) in enumerate(candidates)
    ]
    results = await asyncio.gather(*tasks)

    with connection_context() as connection:
        for entry_id, payload in results:
            if payload is None:
                failed += 1
                continue
            if not payload.markdown.strip():
                skipped += 1
                logger.info("  SKIPPED id=%d (empty markdown)", entry_id)
                continue
            upsert_entry_source_snapshot(connection, entry_id, payload)
            succeeded += 1
            logger.info("  SAVED   id=%d (%d chars)", entry_id, len(payload.markdown))

    logger.info(
        "Done. %d succeeded, %d failed, %d skipped out of %d.",
        succeeded, failed, skipped, total,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch extract/refresh source markdown snapshots for entries."
    )
    parser.add_argument(
        "--missing",
        action="store_true",
        help="Only process entries that don't have a snapshot yet.",
    )
    parser.add_argument(
        "--group",
        type=int,
        default=None,
        metavar="GROUP_ID",
        help="Only process entries in this group.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List entries that would be processed without actually fetching.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        metavar="N",
        help="Max concurrent HTTP requests (default: 5).",
    )
    args = parser.parse_args()

    asyncio.run(
        run(
            only_missing=args.missing,
            group_id=args.group,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
        )
    )


if __name__ == "__main__":
    main()
