from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.env import load_app_env

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - dependency absence is handled at runtime.
    sqlite_vec = None


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "EventTracker.db"

logger = logging.getLogger(__name__)

DEFAULT_TIMELINE_GROUP_NAME = "Agentic Coding"

EXPECTED_TIMELINE_GROUP_COLUMNS = (
    "id",
    "name",
    "web_search_query",
    "is_default",
)


class SchemaDriftError(RuntimeError):
    pass


ENTRIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    event_year INTEGER NOT NULL,
    event_month INTEGER NOT NULL,
    event_day INTEGER NULL,
    sort_key INTEGER NOT NULL,
    group_id INTEGER REFERENCES timeline_groups(id),
    title TEXT NOT NULL DEFAULT '',
    source_url TEXT NULL,
    generated_text TEXT NULL,
    final_text TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    updated_utc TEXT NOT NULL
)
"""

EXPECTED_ENTRY_COLUMNS = (
    "id",
    "event_year",
    "event_month",
    "event_day",
    "sort_key",
    "group_id",
    "title",
    "source_url",
    "generated_text",
    "final_text",
    "created_utc",
    "updated_utc",
)

LEGACY_ENTRY_COLUMNS = (
    "id",
    "event_year",
    "event_month",
    "event_day",
    "sort_key",
    "title",
    "source_url",
    "generated_text",
    "final_text",
    "created_utc",
    "updated_utc",
)


PRE_ENTRY_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS timeline_groups (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        web_search_query TEXT NULL,
        is_default INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS embedding_index_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        model_id TEXT NOT NULL,
        dimensions INTEGER NOT NULL,
        updated_utc TEXT NOT NULL
    )
    """,
]

POST_ENTRY_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entry_tags (
        entry_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        PRIMARY KEY (entry_id, tag_id),
        FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entry_links (
        id INTEGER PRIMARY KEY,
        entry_id INTEGER NOT NULL,
        url TEXT NOT NULL,
        note TEXT NOT NULL,
        created_utc TEXT NOT NULL,
        FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_entries_sort_key ON entries(sort_key DESC)",
    "CREATE INDEX IF NOT EXISTS idx_entries_group_sort_key ON entries(group_id, sort_key DESC)",
    "CREATE INDEX IF NOT EXISTS idx_entry_tags_tag_id ON entry_tags(tag_id)",
    "CREATE INDEX IF NOT EXISTS idx_entry_links_entry_id ON entry_links(entry_id)",
]

FTS_TABLE_SQL = """
CREATE VIRTUAL TABLE entries_fts
USING fts5(
    final_text,
    content='entries',
    content_rowid='id'
)
"""

FTS_TRIGGER_STATEMENTS = [
    """
    CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
        INSERT INTO entries_fts(rowid, final_text)
        VALUES (new.id, new.final_text);
    END
    """,
    """
    CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
        INSERT INTO entries_fts(entries_fts, rowid, final_text)
        VALUES ('delete', old.id, old.final_text);
    END
    """,
    """
    CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
        INSERT INTO entries_fts(entries_fts, rowid, final_text)
        VALUES ('delete', old.id, old.final_text);
        INSERT INTO entries_fts(rowid, final_text)
        VALUES (new.id, new.final_text);
    END
    """,
]


def get_connection() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _load_sqlite_vec(connection)
    return connection


def get_db_path() -> Path:
    load_app_env()
    configured_path = os.getenv("EVENTTRACKER_DB_PATH", "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return DEFAULT_DB_PATH


@contextmanager
def connection_context() -> sqlite3.Connection:
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with connection_context() as connection:
        for statement in PRE_ENTRY_SCHEMA_STATEMENTS:
            connection.execute(statement)

        ensure_timeline_groups_schema(connection)

        default_group_id = ensure_default_timeline_group(connection)
        ensure_entries_schema(connection)
        ensure_entry_group_assignments(connection, default_group_id)

        for statement in POST_ENTRY_SCHEMA_STATEMENTS:
            connection.execute(statement)

        ensure_entries_fts_schema(connection)
        ensure_entry_embeddings_schema(connection)


def ensure_timeline_groups_schema(connection: sqlite3.Connection) -> None:
    existing_columns = tuple(
        row["name"]
        for row in connection.execute("PRAGMA table_info(timeline_groups)").fetchall()
    )

    if not existing_columns or existing_columns == EXPECTED_TIMELINE_GROUP_COLUMNS:
        return

    if "web_search_query" not in existing_columns:
        connection.execute(
            "ALTER TABLE timeline_groups ADD COLUMN web_search_query TEXT NULL"
        )

    if "is_default" not in existing_columns:
        connection.execute(
            "ALTER TABLE timeline_groups ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0"
        )


def ensure_entries_schema(connection: sqlite3.Connection) -> None:
    existing_columns = tuple(
        row["name"]
        for row in connection.execute("PRAGMA table_info(entries)").fetchall()
    )

    if not existing_columns:
        connection.execute(ENTRIES_TABLE_SQL)
        return

    if existing_columns == EXPECTED_ENTRY_COLUMNS:
        return

    if existing_columns == LEGACY_ENTRY_COLUMNS:
        connection.execute(
            "ALTER TABLE entries ADD COLUMN group_id INTEGER REFERENCES timeline_groups(id)"
        )
        return

    raise SchemaDriftError(
        "Unsupported entries schema detected. "
        f"Expected {EXPECTED_ENTRY_COLUMNS} or legacy {LEGACY_ENTRY_COLUMNS}, "
        f"found {existing_columns}. Run an explicit migration before starting EventTracker."
    )


def ensure_default_timeline_group(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM timeline_groups WHERE name = ?",
        (DEFAULT_TIMELINE_GROUP_NAME,),
    ).fetchone()

    if row is not None:
        default_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM timeline_groups WHERE is_default = 1"
            ).fetchone()[0]
        )
        if default_count == 0:
            connection.execute(
                "UPDATE timeline_groups SET is_default = 1 WHERE id = ?",
                (int(row["id"]),),
            )
        return int(row["id"])

    cursor = connection.execute(
        "INSERT INTO timeline_groups(name, is_default) VALUES (?, 1)",
        (DEFAULT_TIMELINE_GROUP_NAME,),
    )
    return int(cursor.lastrowid)


def ensure_entry_group_assignments(
    connection: sqlite3.Connection, default_group_id: int
) -> None:
    connection.execute(
        "UPDATE entries SET group_id = ? WHERE group_id IS NULL",
        (default_group_id,),
    )


def ensure_entries_fts_schema(connection: sqlite3.Connection) -> None:
    current_table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'entries_fts'"
    ).fetchone()
    current_sql = (current_table["sql"] if current_table else "") or ""
    needs_rebuild = not current_sql

    for trigger_name in ("entries_ai", "entries_ad", "entries_au"):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    if needs_rebuild:
        connection.execute("DROP TABLE IF EXISTS entries_fts")
        connection.execute(FTS_TABLE_SQL)

    for statement in FTS_TRIGGER_STATEMENTS:
        connection.execute(statement)

    connection.execute("INSERT INTO entries_fts(entries_fts) VALUES ('rebuild')")


def ensure_entry_embeddings_schema(connection: sqlite3.Connection) -> None:
    if not is_sqlite_vec_enabled(connection):
        return

    current_table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'entry_embeddings'"
    ).fetchone()
    state = connection.execute(
        "SELECT model_id, dimensions FROM embedding_index_meta WHERE singleton = 1"
    ).fetchone()

    if state is None:
        if current_table is not None:
            connection.execute("DROP TABLE IF EXISTS entry_embeddings")
        return

    current_sql = (current_table["sql"] if current_table else "") or ""
    normalized_sql = " ".join(current_sql.lower().split())
    expected_fragment = f"float[{int(state['dimensions'])}]"
    if "using vec0" not in normalized_sql or expected_fragment not in normalized_sql:
        connection.execute("DROP TABLE IF EXISTS entry_embeddings")
        connection.execute(
            f"CREATE VIRTUAL TABLE entry_embeddings USING vec0(embedding float[{int(state['dimensions'])}])"
        )


def is_sqlite_vec_enabled(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("SELECT vec_version()").fetchone()
    except sqlite3.DatabaseError:
        return False
    return True


def _load_sqlite_vec(connection: sqlite3.Connection) -> None:
    if sqlite_vec is None:
        return

    try:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
    except (AttributeError, OSError, sqlite3.DatabaseError) as exc:
        logger.warning(
            "sqlite-vec could not be loaded; semantic search disabled: %s", exc
        )
    finally:
        try:
            connection.enable_load_extension(False)
        except sqlite3.DatabaseError:
            pass
