from __future__ import annotations

import sqlite3

from app.models import TimelineGroup


MAX_TIMELINE_GROUP_WEB_SEARCH_QUERY_LENGTH = 400


class TimelineGroupValidationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def list_timeline_groups(connection: sqlite3.Connection) -> list[TimelineGroup]:
    rows = connection.execute(
        """
        SELECT
            tg.id,
            tg.name,
            tg.web_search_query,
            tg.is_default,
            COUNT(e.id) AS entry_count
        FROM timeline_groups tg
        LEFT JOIN entries e ON e.group_id = tg.id
        GROUP BY tg.id, tg.name, tg.web_search_query, tg.is_default
        ORDER BY tg.is_default DESC, LOWER(tg.name) ASC, tg.id ASC
        """
    ).fetchall()
    return [
        TimelineGroup(
            id=int(row["id"]),
            name=row["name"],
            web_search_query=row["web_search_query"],
            entry_count=int(row["entry_count"]),
            is_default=bool(row["is_default"]),
        )
        for row in rows
    ]


def get_timeline_group(
    connection: sqlite3.Connection, group_id: int
) -> TimelineGroup | None:
    row = connection.execute(
        "SELECT id, name, web_search_query, is_default FROM timeline_groups WHERE id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        return None
    return TimelineGroup(
        id=int(row["id"]),
        name=row["name"],
        web_search_query=row["web_search_query"],
        is_default=bool(row["is_default"]),
    )


def get_default_timeline_group(
    connection: sqlite3.Connection,
) -> TimelineGroup | None:
    row = connection.execute(
        "SELECT id, name, web_search_query, is_default FROM timeline_groups WHERE is_default = 1 ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return TimelineGroup(
        id=int(row["id"]),
        name=row["name"],
        web_search_query=row["web_search_query"],
        is_default=bool(row["is_default"]),
    )


def create_timeline_group(
    connection: sqlite3.Connection,
    raw_name: str,
    raw_web_search_query: str = "",
    *,
    is_default: bool = False,
) -> TimelineGroup:
    name = normalize_timeline_group_name(raw_name)
    web_search_query = normalize_timeline_group_web_search_query(raw_web_search_query)
    if not name:
        raise TimelineGroupValidationError("name", "Group name is required.")

    try:
        cursor = connection.execute(
            "INSERT INTO timeline_groups(name, web_search_query, is_default) VALUES (?, ?, 0)",
            (name, web_search_query),
        )
    except sqlite3.IntegrityError as exc:
        raise TimelineGroupValidationError(
            "name", "A group with that name already exists."
        ) from exc

    if cursor.lastrowid is None:
        raise RuntimeError("Failed to determine the new timeline group id.")
    group_id = int(cursor.lastrowid)
    if is_default:
        set_default_timeline_group(connection, group_id)

    return TimelineGroup(
        id=group_id,
        name=name,
        web_search_query=web_search_query,
        is_default=is_default,
    )


def rename_timeline_group(
    connection: sqlite3.Connection,
    group_id: int,
    raw_name: str,
    raw_web_search_query: str = "",
    *,
    is_default: bool | None = None,
) -> None:
    name = normalize_timeline_group_name(raw_name)
    web_search_query = normalize_timeline_group_web_search_query(raw_web_search_query)
    if not name:
        raise TimelineGroupValidationError("name", "Group name is required.")

    try:
        cursor = connection.execute(
            "UPDATE timeline_groups SET name = ?, web_search_query = ? WHERE id = ?",
            (name, web_search_query, group_id),
        )
    except sqlite3.IntegrityError as exc:
        raise TimelineGroupValidationError(
            "name", "A group with that name already exists."
        ) from exc

    if cursor.rowcount == 0:
        raise LookupError("Timeline group not found.")

    if is_default is True:
        set_default_timeline_group(connection, group_id)
    elif is_default is False:
        clear_default_timeline_group(connection, group_id)


def delete_timeline_group(connection: sqlite3.Connection, group_id: int) -> None:
    row = connection.execute(
        """
        SELECT
            tg.id,
            tg.name,
            tg.is_default,
            COUNT(e.id) AS entry_count
        FROM timeline_groups tg
        LEFT JOIN entries e ON e.group_id = tg.id
        WHERE tg.id = ?
        GROUP BY tg.id, tg.name, tg.is_default
        """,
        (group_id,),
    ).fetchone()
    if row is None:
        raise LookupError("Timeline group not found.")

    if bool(row["is_default"]):
        raise ValueError("The default timeline group cannot be deleted.")

    if int(row["entry_count"]) > 0:
        raise ValueError(
            "This group cannot be deleted while it still has entries. Move those entries first."
        )

    connection.execute("DELETE FROM timeline_groups WHERE id = ?", (group_id,))


def set_default_timeline_group(connection: sqlite3.Connection, group_id: int) -> None:
    row = connection.execute(
        "SELECT id FROM timeline_groups WHERE id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        raise LookupError("Timeline group not found.")

    connection.execute(
        "UPDATE timeline_groups SET is_default = 0 WHERE is_default <> 0"
    )
    connection.execute(
        "UPDATE timeline_groups SET is_default = 1 WHERE id = ?",
        (group_id,),
    )


def clear_default_timeline_group(connection: sqlite3.Connection, group_id: int) -> None:
    connection.execute(
        "UPDATE timeline_groups SET is_default = 0 WHERE id = ?",
        (group_id,),
    )


def normalize_timeline_group_name(value: str) -> str:
    return " ".join(value.strip().split())


def normalize_timeline_group_web_search_query(value: str) -> str | None:
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    if len(normalized) > MAX_TIMELINE_GROUP_WEB_SEARCH_QUERY_LENGTH:
        raise TimelineGroupValidationError(
            "web_search_query",
            "Web search query must be 400 characters or fewer.",
        )
    return normalized
