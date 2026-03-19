from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from app.db import connection_context, init_db
from app.services.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingError,
    reindex_all_embeddings,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reindex-embeddings",
        action="store_true",
        help="Rebuild sqlite-vec embeddings for all saved entries.",
    )
    return parser.parse_args()


def main() -> None:
    workspace_root = Path(__file__).resolve().parents[1]
    load_dotenv(workspace_root / ".env", override=True)

    args = parse_args()

    init_db()
    print("Database initialized.")
    if args.reindex_embeddings:
        try:
            with connection_context() as connection:
                count, message = reindex_all_embeddings(connection)
        except EmbeddingConfigurationError as exc:
            raise SystemExit(str(exc)) from exc
        except EmbeddingError as exc:
            raise SystemExit(f"Embedding reindex failed: {exc}") from exc

        print(message)
        print(f"Indexed entries: {count}")


if __name__ == "__main__":
    main()
