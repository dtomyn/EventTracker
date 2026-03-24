from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from openai import OpenAI

from app.db import is_sqlite_vec_enabled
from app.env import load_app_env

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - dependency absence is handled at runtime.
    sqlite_vec = None


logger = logging.getLogger(__name__)

INDEX_TABLE_NAME = "entry_embeddings"


class EmbeddingError(Exception):
    pass


class EmbeddingConfigurationError(EmbeddingError):
    pass


class EmbeddingIndexMismatchError(EmbeddingError):
    pass


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingSettings:
    api_key: str
    model_id: str
    base_url: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingIndexState:
    model_id: str
    dimensions: int
    updated_utc: str


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    entry_id: int
    distance: float


def sync_entry_embedding(
    connection: sqlite3.Connection, entry_id: int, final_text: str
) -> bool:
    if (
        not final_text.strip()
        or not is_sqlite_vec_enabled(connection)
        or sqlite_vec is None
    ):
        return False

    settings = load_embedding_settings()
    embedding = _generate_embedding(final_text, settings)
    state = get_embedding_index_state(connection)
    dimensions = len(embedding)

    if state is None:
        _recreate_embedding_index(connection, settings.model_id, dimensions)
    elif state.model_id != settings.model_id or state.dimensions != dimensions:
        raise EmbeddingIndexMismatchError(
            "Embedding index does not match the configured model. "
            "Run `uv run python -m scripts.init_db --reindex-embeddings` to rebuild it."
        )

    _store_embedding(connection, entry_id, embedding)
    _touch_embedding_index_state(connection, settings.model_id, dimensions)
    return True


def search_semantic_matches(
    connection: sqlite3.Connection, raw_query: str, limit: int = 25
) -> list[SemanticMatch]:
    query = raw_query.strip()
    if not query or not is_sqlite_vec_enabled(connection) or sqlite_vec is None:
        return []

    try:
        settings = load_embedding_settings()
    except EmbeddingConfigurationError:
        return []

    state = get_embedding_index_state(connection)
    if state is None or state.model_id != settings.model_id:
        return []

    try:
        query_embedding = _generate_embedding(query, settings)
    except EmbeddingError:
        logger.warning(
            "Semantic search skipped because query embedding generation failed."
        )
        return []

    if len(query_embedding) != state.dimensions:
        logger.warning(
            "Semantic search skipped because embedding dimensions do not match the index."
        )
        return []

    rows = connection.execute(
        f"""
        SELECT rowid, distance
        FROM {INDEX_TABLE_NAME}
        WHERE embedding MATCH ?
        ORDER BY distance ASC
        LIMIT ?
        """,
        (sqlite_vec.serialize_float32(query_embedding), limit),
    ).fetchall()

    return [
        SemanticMatch(entry_id=int(row["rowid"]), distance=float(row["distance"]))
        for row in rows
    ]


def reindex_all_embeddings(connection: sqlite3.Connection) -> tuple[int, str]:
    if not is_sqlite_vec_enabled(connection) or sqlite_vec is None:
        return (
            0,
            "sqlite-vec is unavailable on this machine; semantic indexing was skipped.",
        )

    settings = load_embedding_settings()
    rows = connection.execute(
        "SELECT id, final_text FROM entries ORDER BY id ASC"
    ).fetchall()
    if not rows:
        connection.execute(f"DROP TABLE IF EXISTS {INDEX_TABLE_NAME}")
        connection.execute("DELETE FROM embedding_index_meta")
        return 0, "No entries found; cleared any existing embedding index state."

    embeddings: list[tuple[int, list[float]]] = []
    for row in rows:
        embeddings.append(
            (int(row["id"]), _generate_embedding(str(row["final_text"]), settings))
        )

    dimensions = len(embeddings[0][1])
    _recreate_embedding_index(connection, settings.model_id, dimensions)
    for entry_id, embedding in embeddings:
        _store_embedding(connection, entry_id, embedding)

    _touch_embedding_index_state(connection, settings.model_id, dimensions)
    return len(embeddings), f"Rebuilt embeddings for {len(embeddings)} entries."


def get_embedding_index_state(
    connection: sqlite3.Connection,
) -> EmbeddingIndexState | None:
    row = connection.execute(
        "SELECT model_id, dimensions, updated_utc FROM embedding_index_meta WHERE singleton = 1"
    ).fetchone()
    if row is None:
        return None
    return EmbeddingIndexState(
        model_id=str(row["model_id"]),
        dimensions=int(row["dimensions"]),
        updated_utc=str(row["updated_utc"]),
    )


@lru_cache(maxsize=1)
def load_embedding_settings() -> OpenAIEmbeddingSettings:
    load_app_env()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model_id = os.getenv("OPENAI_EMBEDDING_MODEL_ID", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None

    missing = [
        name
        for name, value in (
            ("OPENAI_API_KEY", api_key),
            ("OPENAI_EMBEDDING_MODEL_ID", model_id),
        )
        if not value
    ]
    if missing:
        names = ", ".join(missing)
        raise EmbeddingConfigurationError(
            f"Embeddings are not configured. Set {names} in your environment."
        )

    return OpenAIEmbeddingSettings(
        api_key=api_key, model_id=model_id, base_url=base_url
    )


def _generate_embedding(text: str, settings: OpenAIEmbeddingSettings) -> list[float]:
    normalized = " ".join(text.split())
    if not normalized:
        raise EmbeddingError("Cannot embed empty text.")

    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url or None)
    try:
        response = client.embeddings.create(model=settings.model_id, input=normalized)
    except Exception as exc:  # pragma: no cover - network/provider failures.
        raise EmbeddingError("Embedding generation failed.") from exc

    if not response.data or not response.data[0].embedding:
        raise EmbeddingError("The embedding provider returned an empty vector.")
    return [float(value) for value in response.data[0].embedding]


def _validate_dimensions(dimensions: int) -> int:
    """Validate that *dimensions* is a positive integer before DDL interpolation."""
    if not isinstance(dimensions, int) or dimensions <= 0:
        raise ValueError(
            f"Embedding dimensions must be a positive integer, got {dimensions!r}"
        )
    return dimensions


def _recreate_embedding_index(
    connection: sqlite3.Connection, model_id: str, dimensions: int
) -> None:
    safe_dims = _validate_dimensions(dimensions)
    connection.execute(f"DROP TABLE IF EXISTS {INDEX_TABLE_NAME}")
    connection.execute(
        f"CREATE VIRTUAL TABLE {INDEX_TABLE_NAME} USING vec0(embedding float[{safe_dims}])"
    )
    _touch_embedding_index_state(connection, model_id, dimensions)


def _store_embedding(
    connection: sqlite3.Connection, entry_id: int, embedding: list[float]
) -> None:
    assert sqlite_vec is not None
    connection.execute(f"DELETE FROM {INDEX_TABLE_NAME} WHERE rowid = ?", (entry_id,))
    connection.execute(
        f"INSERT INTO {INDEX_TABLE_NAME}(rowid, embedding) VALUES (?, ?)",
        (entry_id, sqlite_vec.serialize_float32(embedding)),
    )


def _touch_embedding_index_state(
    connection: sqlite3.Connection, model_id: str, dimensions: int
) -> None:
    connection.execute(
        """
        INSERT INTO embedding_index_meta(singleton, model_id, dimensions, updated_utc)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(singleton) DO UPDATE SET
            model_id = excluded.model_id,
            dimensions = excluded.dimensions,
            updated_utc = excluded.updated_utc
        """,
        (model_id, dimensions, _utc_now_iso()),
    )


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
