from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sqlite3
from collections.abc import Coroutine
from contextlib import AsyncExitStack
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionSystemMessageParam
from openai.types.chat import ChatCompletionUserMessageParam

from app.models import SuggestedConnection
from app.services import copilot_runtime
from app.services.ai_generate import (
    CopilotSettings,
    OpenAISettings,
    load_ai_provider,
    load_copilot_settings,
    load_openai_settings,
)

logger = logging.getLogger(__name__)

SUGGESTION_DISTANCE_THRESHOLD = 0.35
TEXT_QUERY_DISTANCE_THRESHOLD = 1.0
MAX_SUGGESTIONS_PER_ENTRY = 5

MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

_RELATIONSHIP_SYSTEM_PROMPT = (
    "You generate concise relationship phrases between pairs of timeline events. "
    "For each numbered pair, write a short phrase (3 to 8 words) describing how "
    "the two events might be related. Reply with one phrase per line, numbered to "
    "match the input. Example:\n"
    "1. Directly caused policy change\n"
    "2. Similar technology announced later\n"
    "Do not include any other text."
)


def find_similar_entries(
    connection: sqlite3.Connection,
    entry_id: int,
    limit: int = MAX_SUGGESTIONS_PER_ENTRY,
    distance_threshold: float = SUGGESTION_DISTANCE_THRESHOLD,
) -> list[dict[str, object]]:
    """Find semantically similar entries via embedding distance.

    Excludes the entry itself, already-connected entries (both directions),
    and previously dismissed suggestions.
    """
    try:
        rows = connection.execute(
            """
            SELECT
                b.rowid AS entry_id,
                vec_distance_cosine(a.embedding, b.embedding) AS distance,
                e.title,
                e.event_year,
                e.event_month,
                e.event_day,
                g.name AS group_name
            FROM entry_embeddings a
            JOIN entry_embeddings b ON a.rowid != b.rowid
            JOIN entries e ON e.id = b.rowid
            JOIN timeline_groups g ON g.id = e.group_id
            WHERE a.rowid = ?
              AND vec_distance_cosine(a.embedding, b.embedding) <= ?
              AND b.rowid NOT IN (
                  SELECT target_entry_id FROM entry_connections WHERE source_entry_id = ?
                  UNION
                  SELECT source_entry_id FROM entry_connections WHERE target_entry_id = ?
              )
              AND b.rowid NOT IN (
                  SELECT suggested_entry_id FROM suggested_connections
                  WHERE entry_id = ? AND status = 'dismissed'
              )
            ORDER BY distance ASC
            LIMIT ?
            """,
            (entry_id, distance_threshold, entry_id, entry_id, entry_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    results: list[dict[str, object]] = []
    for row in rows:
        year = int(row["event_year"])
        month = int(row["event_month"])
        day = row["event_day"]
        if day is not None:
            display_date = f"{MONTH_NAMES[month - 1]} {int(day)}, {year}"
        else:
            display_date = f"{MONTH_NAMES[month - 1]} {year}"
        results.append(
            {
                "entry_id": int(row["entry_id"]),
                "distance": float(row["distance"]),
                "title": str(row["title"]),
                "display_date": display_date,
                "group_name": str(row["group_name"]),
            }
        )
    return results


def save_suggestions(
    connection: sqlite3.Connection,
    entry_id: int,
    suggestions: list[dict[str, object]],
    now: str,
) -> int:
    """Delete existing pending suggestions for *entry_id* and insert new ones.

    Returns the number of suggestions saved.
    """
    connection.execute(
        "DELETE FROM suggested_connections WHERE entry_id = ? AND status = 'pending'",
        (entry_id,),
    )

    count = 0
    for s in suggestions:
        try:
            connection.execute(
                """
                INSERT OR IGNORE INTO suggested_connections
                    (entry_id, suggested_entry_id, distance, suggested_note,
                     status, created_utc, updated_utc)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    entry_id,
                    s["entry_id"],
                    s["distance"],
                    s.get("suggested_note", ""),
                    now,
                    now,
                ),
            )
            count += connection.total_changes and 1
        except sqlite3.IntegrityError:
            pass
    connection.commit()
    return count


def get_pending_suggestions(
    connection: sqlite3.Connection,
    entry_id: int,
) -> list[SuggestedConnection]:
    """Load pending suggestions for *entry_id* with display data."""
    rows = connection.execute(
        """
        SELECT
            sc.id,
            sc.suggested_entry_id,
            e.title AS suggested_entry_title,
            e.event_year,
            e.event_month,
            e.event_day,
            g.name AS suggested_entry_group,
            sc.distance,
            sc.suggested_note,
            sc.created_utc
        FROM suggested_connections sc
        JOIN entries e ON e.id = sc.suggested_entry_id
        JOIN timeline_groups g ON g.id = e.group_id
        WHERE sc.entry_id = ? AND sc.status = 'pending'
        ORDER BY sc.distance ASC
        """,
        (entry_id,),
    ).fetchall()

    results: list[SuggestedConnection] = []
    for row in rows:
        year = int(row["event_year"])
        month = int(row["event_month"])
        day = row["event_day"]
        if day is not None:
            display_date = f"{MONTH_NAMES[month - 1]} {int(day)}, {year}"
        else:
            display_date = f"{MONTH_NAMES[month - 1]} {year}"

        results.append(
            SuggestedConnection(
                id=int(row["id"]),
                suggested_entry_id=int(row["suggested_entry_id"]),
                suggested_entry_title=str(row["suggested_entry_title"]),
                suggested_entry_date=display_date,
                suggested_entry_group=str(row["suggested_entry_group"]),
                distance=float(row["distance"]),
                suggested_note=str(row["suggested_note"]),
                created_utc=str(row["created_utc"]),
            )
        )
    return results


def accept_suggestion(
    connection: sqlite3.Connection,
    suggestion_id: int,
    now: str,
) -> tuple[int, int] | None:
    """Accept a pending suggestion: create a real connection and mark accepted.

    Returns ``(entry_id, suggested_entry_id)`` or ``None`` if not found.
    """
    row = connection.execute(
        """
        SELECT id, entry_id, suggested_entry_id, suggested_note
        FROM suggested_connections
        WHERE id = ? AND status = 'pending'
        """,
        (suggestion_id,),
    ).fetchone()

    if row is None:
        return None

    entry_id = int(row["entry_id"])
    suggested_entry_id = int(row["suggested_entry_id"])
    note = str(row["suggested_note"])

    connection.execute(
        """
        INSERT OR IGNORE INTO entry_connections
            (source_entry_id, target_entry_id, note, created_utc)
        VALUES (?, ?, ?, ?)
        """,
        (entry_id, suggested_entry_id, note, now),
    )

    connection.execute(
        "UPDATE suggested_connections SET status = 'accepted', updated_utc = ? WHERE id = ?",
        (now, suggestion_id),
    )
    connection.commit()
    return (entry_id, suggested_entry_id)


def dismiss_suggestion(
    connection: sqlite3.Connection,
    suggestion_id: int,
    now: str,
) -> bool:
    """Mark a suggestion as dismissed. Returns True if updated."""
    cursor = connection.execute(
        "UPDATE suggested_connections SET status = 'dismissed', updated_utc = ? "
        "WHERE id = ? AND status = 'pending'",
        (now, suggestion_id),
    )
    connection.commit()
    return cursor.rowcount > 0


def generate_relationship_notes(
    pairs: list[tuple[str, str]],
) -> list[str]:
    """Use AI to generate short relationship phrases for entry title pairs.

    Returns a list of strings parallel to *pairs*. On any failure the
    corresponding element is an empty string (AI is optional).
    """
    if not pairs:
        return []

    empty: list[str] = [""] * len(pairs)

    try:
        provider = load_ai_provider()
    except Exception:
        return empty

    prompt_lines: list[str] = []
    for i, (source, target) in enumerate(pairs, start=1):
        prompt_lines.append(f"{i}. \"{source}\" and \"{target}\"")
    prompt = "\n".join(prompt_lines)

    try:
        if provider == "openai":
            return _generate_notes_openai(prompt, len(pairs))
        elif provider == "copilot":
            return _generate_notes_copilot(prompt, len(pairs))
    except Exception as exc:
        logger.warning("Relationship note generation failed: %s", exc)

    return empty


def _generate_notes_openai(prompt: str, count: int) -> list[str]:
    """Generate relationship notes via the OpenAI API (sync wrapper)."""
    settings = load_openai_settings()

    async def _call() -> str:
        client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url or None,
        )
        system_msg: ChatCompletionSystemMessageParam = {
            "role": "system",
            "content": _RELATIONSHIP_SYSTEM_PROMPT,
        }
        user_msg: ChatCompletionUserMessageParam = {
            "role": "user",
            "content": prompt,
        }
        messages: list[ChatCompletionMessageParam] = cast(
            list[ChatCompletionMessageParam], [system_msg, user_msg]
        )
        response = await client.chat.completions.create(
            model=settings.model_id,
            messages=messages,
        )
        content = response.choices[0].message.content if response.choices else None
        return content or ""

    raw = _run_async(_call())
    return _parse_numbered_lines(raw, count)


def _generate_notes_copilot(prompt: str, count: int) -> list[str]:
    """Generate relationship notes via GitHub Copilot (sync wrapper)."""
    settings = load_copilot_settings()

    async def _call() -> str:
        client = copilot_runtime.instantiate_copilot_client(
            settings,
            configuration_error_type=Exception,
            missing_sdk_message=copilot_runtime.COPILOT_SDK_REQUIRED_MESSAGE,
            invalid_settings_message=copilot_runtime.COPILOT_CLIENT_SETTINGS_MESSAGE,
        )
        async with AsyncExitStack() as exit_stack:
            active_client = await copilot_runtime.prepare_copilot_client(
                exit_stack, client
            )
            session = await copilot_runtime.create_copilot_session(
                active_client,
                model_id=settings.model_id,
                system_message=_RELATIONSHIP_SYSTEM_PROMPT,
            )
            active_session = await copilot_runtime.prepare_copilot_resource(
                exit_stack, session
            )
            response = await copilot_runtime.send_copilot_prompt(
                active_session, prompt, timeout=60.0
            )
            return copilot_runtime.extract_copilot_message_content(response)

    raw = _run_async(_call())
    return _parse_numbered_lines(raw, count)


def _run_async(coro: Coroutine[Any, Any, str]) -> str:
    """Run an async coroutine synchronously, handling existing event loops."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop -- safe to use asyncio.run().
        return asyncio.run(coro)

    # A loop is already running (e.g. inside a background task).  Execute in a
    # separate thread so we don't block the current loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=90.0)


def _parse_numbered_lines(text: str, count: int) -> list[str]:
    """Parse numbered response lines into a list of *count* phrases."""
    results: list[str] = [""] * count
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Expect lines like "1. Some phrase" or "1) Some phrase"
        parts = line.split(".", 1) if "." in line[:4] else line.split(")", 1)
        if len(parts) == 2:
            try:
                idx = int(parts[0].strip()) - 1
            except ValueError:
                continue
            if 0 <= idx < count:
                results[idx] = parts[1].strip()
    return results


def find_similar_entries_by_text(
    connection: sqlite3.Connection,
    query_text: str,
    exclude_entry_id: int | None = None,
    limit: int = MAX_SUGGESTIONS_PER_ENTRY,
    distance_threshold: float = TEXT_QUERY_DISTANCE_THRESHOLD,
) -> list[dict[str, object]]:
    """Find semantically similar entries by embedding a free-text query.

    Unlike *find_similar_entries*, this does not require the source entry to
    already exist in the database — it generates a one-off embedding from
    *query_text* and searches the index directly.  Useful for suggesting
    connections during entry creation before the entry is saved.

    Uses a higher default distance threshold than entry-to-entry comparison
    because short query text naturally produces larger cosine distances
    against full-text body embeddings.
    """
    from app.services.embeddings import search_semantic_matches

    matches = search_semantic_matches(connection, query_text, limit=limit + 5)
    if not matches:
        return []

    results: list[dict[str, object]] = []
    for m in matches:
        if m.distance > distance_threshold:
            continue
        if exclude_entry_id is not None and m.entry_id == exclude_entry_id:
            continue
        row = connection.execute(
            """
            SELECT e.id, e.title, e.event_year, e.event_month, e.event_day,
                   g.name AS group_name
            FROM entries e
            JOIN timeline_groups g ON g.id = e.group_id
            WHERE e.id = ?
            """,
            (m.entry_id,),
        ).fetchone()
        if row is None:
            continue
        year = int(row["event_year"])
        month = int(row["event_month"])
        day = row["event_day"]
        if day is not None:
            display_date = f"{MONTH_NAMES[month - 1]} {int(day)}, {year}"
        else:
            display_date = f"{MONTH_NAMES[month - 1]} {year}"
        results.append(
            {
                "entry_id": int(row["id"]),
                "title": str(row["title"]),
                "display_date": display_date,
                "group_name": str(row["group_name"]),
                "distance": m.distance,
            }
        )
        if len(results) >= limit:
            break
    return results


def compute_suggestions_for_entry(entry_id: int, entry_title: str) -> None:
    """Background task: compute and save suggested connections for an entry.

    Opens its own DB connection and catches all exceptions so the background
    worker is never crashed.
    """
    from app.db import connection_context, is_sqlite_vec_enabled
    from app.services.entries import utc_now_iso

    try:
        with connection_context() as connection:
            if not is_sqlite_vec_enabled(connection):
                logger.debug(
                    "sqlite-vec not available; skipping suggestions for entry %d",
                    entry_id,
                )
                return

            similar = find_similar_entries(connection, entry_id)
            if not similar:
                logger.debug(
                    "No similar entries found for entry %d", entry_id
                )
                return

            # Generate AI relationship notes (best-effort).
            pairs: list[tuple[str, str]] = [
                (entry_title, str(s["title"])) for s in similar
            ]
            notes = generate_relationship_notes(pairs)

            for s, note in zip(similar, notes):
                s["suggested_note"] = note

            now = utc_now_iso()
            saved = save_suggestions(connection, entry_id, similar, now)
            logger.info(
                "Saved %d suggested connections for entry %d (%s)",
                saved,
                entry_id,
                entry_title,
            )
    except Exception:
        logger.exception(
            "Failed to compute suggestions for entry %d (%s)",
            entry_id,
            entry_title,
        )
