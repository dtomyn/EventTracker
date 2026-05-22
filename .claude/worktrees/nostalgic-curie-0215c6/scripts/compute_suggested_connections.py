"""Batch-compute suggested connections for all entries that have embeddings."""

import logging
import time

from app.db import connection_context, is_sqlite_vec_enabled
from app.services.entries import utc_now_iso
from app.services.suggested_connections import (
    find_similar_entries,
    generate_relationship_notes,
    save_suggestions,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run() -> None:
    with connection_context() as connection:
        if not is_sqlite_vec_enabled(connection):
            logger.error("sqlite-vec is not available — cannot compute suggestions.")
            return

        rows = connection.execute(
            """
            SELECT e.id, e.title
            FROM entries e
            JOIN entry_embeddings ee ON ee.rowid = e.id
            ORDER BY e.sort_key DESC
            """
        ).fetchall()

        total = len(rows)
        logger.info("Found %d entries with embeddings.", total)
        if total == 0:
            return

        computed = 0
        skipped = 0
        total_suggestions = 0
        start = time.monotonic()

        for i, row in enumerate(rows, 1):
            entry_id = row["id"]
            title = row["title"] or ""

            similar = find_similar_entries(connection, entry_id)
            if not similar:
                skipped += 1
                if i % 50 == 0:
                    logger.info("Progress: %d/%d entries processed...", i, total)
                continue

            pairs = [(title, s["title"]) for s in similar]
            notes = generate_relationship_notes(pairs)
            for s, note in zip(similar, notes):
                s["suggested_note"] = note

            now = utc_now_iso()
            count = save_suggestions(connection, entry_id, similar, now)
            total_suggestions += count
            computed += 1

            if i % 10 == 0:
                logger.info(
                    "Progress: %d/%d entries processed (%d suggestions so far)...",
                    i, total, total_suggestions,
                )

        elapsed = time.monotonic() - start
        logger.info(
            "Done. %d entries processed, %d had suggestions (%d total suggestions), "
            "%d skipped (no similar entries). Took %.1fs.",
            total, computed, total_suggestions, skipped, elapsed,
        )


if __name__ == "__main__":
    run()
