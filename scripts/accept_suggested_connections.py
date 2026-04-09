"""Bulk-accept all pending suggested connections."""

import argparse
import logging

from app.db import connection_context
from app.services.entries import utc_now_iso
from app.services.suggested_connections import accept_suggestion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(*, dry_run: bool = False) -> None:
    with connection_context() as connection:
        rows = connection.execute(
            """
            SELECT sc.id, sc.entry_id, e1.title AS source_title,
                   sc.suggested_entry_id, e2.title AS target_title,
                   sc.suggested_note
            FROM suggested_connections sc
            JOIN entries e1 ON e1.id = sc.entry_id
            JOIN entries e2 ON e2.id = sc.suggested_entry_id
            WHERE sc.status = 'pending'
            ORDER BY sc.entry_id, sc.distance ASC
            """,
        ).fetchall()

        total = len(rows)
        if total == 0:
            logger.info("No pending suggestions to accept.")
            return

        logger.info(
            "Found %d pending suggestion(s)%s.",
            total,
            " (dry run)" if dry_run else "",
        )

        accepted = 0
        for row in rows:
            note_preview = row["suggested_note"][:60] if row["suggested_note"] else ""
            logger.info(
                '  %s[%d] "%s" -> [%d] "%s"  note="%s"',
                "[DRY RUN] " if dry_run else "",
                row["entry_id"],
                row["source_title"],
                row["suggested_entry_id"],
                row["target_title"],
                note_preview,
            )
            if not dry_run:
                result = accept_suggestion(connection, row["id"], utc_now_iso())
                if result is not None:
                    accepted += 1

        if dry_run:
            logger.info("Dry run complete. %d suggestion(s) would be accepted.", total)
        else:
            logger.info("Done. Accepted %d of %d suggestion(s).", accepted, total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bulk-accept all pending suggested connections."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview which suggestions would be accepted without making changes.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
