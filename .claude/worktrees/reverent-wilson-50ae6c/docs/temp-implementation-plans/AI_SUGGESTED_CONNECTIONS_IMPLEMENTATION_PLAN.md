# AI-Suggested Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an entry is saved, automatically discover semantically similar entries via embedding distance, generate AI relationship notes, and surface them as one-click accept/dismiss suggestions on the entry detail page.

**Architecture:** A background task triggered on entry save queries `vec_distance_cosine()` on the existing `entry_embeddings` table to find the top-N closest entries. An optional AI call generates a short relationship note for each pair. Results are persisted in a `suggested_connections` table. The entry detail page shows pending suggestions with accept (creates a real `entry_connection`) and dismiss buttons, both handled via lightweight API endpoints.

**Tech Stack:** SQLite + sqlite-vec (existing), OpenAI/Copilot AI providers (existing), FastAPI background tasks, Jinja2 templates, vanilla JavaScript.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `app/db.py` | Add `suggested_connections` table + indexes |
| Modify | `app/models.py` | Add `SuggestedConnection` dataclass |
| Create | `app/services/suggested_connections.py` | All suggestion logic: compute, accept, dismiss, AI notes |
| Modify | `app/main.py` | New API routes, update `view_entry`, wire background task |
| Modify | `app/templates/entry_detail.html` | Suggestions panel with accept/dismiss buttons |

---

## Task 1: Database Schema & Model

**Files:**
- Modify: `app/db.py` (POST_ENTRY_SCHEMA_STATEMENTS list, around line 160)
- Modify: `app/models.py` (after `EntryConnection` dataclass, around line 32)

- [ ] **Step 1: Add `suggested_connections` table to schema**

In `app/db.py`, add these statements to the `POST_ENTRY_SCHEMA_STATEMENTS` list, just before the closing `]` of the list (after the `topic_cluster_cache` table):

```python
    """
    CREATE TABLE IF NOT EXISTS suggested_connections (
        id                 INTEGER PRIMARY KEY,
        entry_id           INTEGER NOT NULL,
        suggested_entry_id INTEGER NOT NULL,
        distance           REAL NOT NULL,
        suggested_note     TEXT NOT NULL DEFAULT '',
        status             TEXT NOT NULL DEFAULT 'pending',
        created_utc        TEXT NOT NULL,
        updated_utc        TEXT NOT NULL,
        FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
        FOREIGN KEY (suggested_entry_id) REFERENCES entries(id) ON DELETE CASCADE,
        UNIQUE(entry_id, suggested_entry_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_suggested_connections_entry ON suggested_connections(entry_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_suggested_connections_suggested ON suggested_connections(suggested_entry_id)",
```

Column rationale:
- `entry_id`: the entry that was saved and triggered the suggestion
- `suggested_entry_id`: the similar entry being suggested
- `distance`: cosine distance (0.0 = identical, lower = more similar) — used for sorting
- `suggested_note`: AI-generated relationship note (e.g., "Related development in the same space")
- `status`: `'pending'` | `'accepted'` | `'dismissed'`
- Cascade deletes on both FKs ensure cleanup

- [ ] **Step 2: Add `SuggestedConnection` model**

In `app/models.py`, add after the `EntryConnection` dataclass:

```python
@dataclass(slots=True)
class SuggestedConnection:
    id: int
    suggested_entry_id: int
    suggested_entry_title: str
    suggested_entry_date: str
    suggested_entry_group: str
    distance: float
    suggested_note: str
    created_utc: str
```

This mirrors `EntryConnection` but includes `distance` for ranking and drops `direction` (suggestions are always from the current entry's perspective).

- [ ] **Step 3: Verify schema creation**

Run:
```bash
uv run pyright app/db.py app/models.py
```
Expected: 0 errors

---

## Task 2: Suggestion Service — Core Functions

**Files:**
- Create: `app/services/suggested_connections.py`

This is the core service containing all suggestion logic. It does NOT contain AI note generation (that's Task 3).

- [ ] **Step 1: Create the service file with imports and constants**

Create `app/services/suggested_connections.py`:

```python
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from app.models import SuggestedConnection

logger = logging.getLogger(__name__)

# Cosine distance threshold: entries closer than this are suggested.
# 0.0 = identical, 2.0 = opposite. 0.35 is moderately similar.
SUGGESTION_DISTANCE_THRESHOLD = 0.35

# Maximum suggestions to generate per entry.
MAX_SUGGESTIONS_PER_ENTRY = 5

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
```

- [ ] **Step 2: Add `find_similar_entries()` — the core similarity query**

This function queries the embedding table for the closest entries. Append to the service file:

```python
def find_similar_entries(
    connection: sqlite3.Connection,
    entry_id: int,
    limit: int = MAX_SUGGESTIONS_PER_ENTRY,
    distance_threshold: float = SUGGESTION_DISTANCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Find entries most similar to the given entry by embedding distance.

    Returns a list of dicts with keys: entry_id, distance, title,
    display_date, group_name. Excludes the entry itself, already-connected
    entries, and previously dismissed suggestions.
    """
    rows = connection.execute(
        """
        SELECT
            b.rowid AS suggested_id,
            vec_distance_cosine(a.embedding, b.embedding) AS distance,
            e.title,
            e.event_year,
            e.event_month,
            e.event_day,
            tg.name AS group_name
        FROM entry_embeddings a
        JOIN entry_embeddings b ON b.rowid != a.rowid
        JOIN entries e ON e.id = b.rowid
        JOIN timeline_groups tg ON tg.id = e.group_id
        WHERE a.rowid = ?
          AND vec_distance_cosine(a.embedding, b.embedding) < ?
          AND b.rowid NOT IN (
              SELECT target_entry_id FROM entry_connections
              WHERE source_entry_id = ?
              UNION
              SELECT source_entry_id FROM entry_connections
              WHERE target_entry_id = ?
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

    results: list[dict[str, Any]] = []
    for row in rows:
        day = row["event_day"]
        month_name = MONTH_NAMES[row["event_month"] - 1]
        display_date = (
            f"{month_name} {day}, {row['event_year']}"
            if day
            else f"{month_name} {row['event_year']}"
        )
        results.append({
            "entry_id": row["suggested_id"],
            "distance": row["distance"],
            "title": row["title"] or "",
            "display_date": display_date,
            "group_name": row["group_name"],
        })
    return results
```

- [ ] **Step 3: Add `save_suggestions()` — persist computed suggestions**

```python
def save_suggestions(
    connection: sqlite3.Connection,
    entry_id: int,
    suggestions: list[dict[str, Any]],
    now: str,
) -> int:
    """Persist a batch of suggestions for an entry.

    Replaces any existing pending suggestions for this entry.
    Returns the number of suggestions saved.
    """
    connection.execute(
        "DELETE FROM suggested_connections WHERE entry_id = ? AND status = 'pending'",
        (entry_id,),
    )
    count = 0
    for s in suggestions:
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
        count += 1
    return count
```

- [ ] **Step 4: Add `get_pending_suggestions()` — load for display**

```python
def get_pending_suggestions(
    connection: sqlite3.Connection,
    entry_id: int,
) -> list[SuggestedConnection]:
    """Load pending suggestions for an entry, ordered by distance (best first)."""
    rows = connection.execute(
        """
        SELECT sc.id, sc.suggested_entry_id, e.title, e.event_year,
               e.event_month, e.event_day, tg.name AS group_name,
               sc.distance, sc.suggested_note, sc.created_utc
        FROM suggested_connections sc
        JOIN entries e ON e.id = sc.suggested_entry_id
        JOIN timeline_groups tg ON tg.id = e.group_id
        WHERE sc.entry_id = ? AND sc.status = 'pending'
        ORDER BY sc.distance ASC
        """,
        (entry_id,),
    ).fetchall()
    results: list[SuggestedConnection] = []
    for row in rows:
        day = row["event_day"]
        month_name = MONTH_NAMES[row["event_month"] - 1]
        display_date = (
            f"{month_name} {day}, {row['event_year']}"
            if day
            else f"{month_name} {row['event_year']}"
        )
        results.append(SuggestedConnection(
            id=row["id"],
            suggested_entry_id=row["suggested_entry_id"],
            suggested_entry_title=row["title"] or "",
            suggested_entry_date=display_date,
            suggested_entry_group=row["group_name"],
            distance=row["distance"],
            suggested_note=row["suggested_note"] or "",
            created_utc=row["created_utc"],
        ))
    return results
```

- [ ] **Step 5: Add `accept_suggestion()` and `dismiss_suggestion()`**

```python
def accept_suggestion(
    connection: sqlite3.Connection,
    suggestion_id: int,
    now: str,
) -> tuple[int, int] | None:
    """Accept a suggestion: create a real entry_connection and mark accepted.

    Returns (entry_id, suggested_entry_id) on success, or None if not found.
    """
    row = connection.execute(
        "SELECT entry_id, suggested_entry_id, suggested_note "
        "FROM suggested_connections WHERE id = ? AND status = 'pending'",
        (suggestion_id,),
    ).fetchone()
    if row is None:
        return None
    entry_id = row["entry_id"]
    suggested_entry_id = row["suggested_entry_id"]
    note = row["suggested_note"] or ""
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
    return entry_id, suggested_entry_id


def dismiss_suggestion(
    connection: sqlite3.Connection,
    suggestion_id: int,
    now: str,
) -> bool:
    """Dismiss a suggestion so it won't be shown again.

    Returns True if a row was updated, False if not found.
    """
    cursor = connection.execute(
        "UPDATE suggested_connections SET status = 'dismissed', updated_utc = ? "
        "WHERE id = ? AND status = 'pending'",
        (now, suggestion_id),
    )
    return cursor.rowcount > 0
```

- [ ] **Step 6: Verify types**

Run:
```bash
uv run pyright app/services/suggested_connections.py
```
Expected: 0 errors

- [ ] **Step 7: Commit**

```bash
git add app/db.py app/models.py app/services/suggested_connections.py
git commit -m "feat: add suggested_connections schema and service layer"
```

---

## Task 3: AI Relationship Note Generation

**Files:**
- Modify: `app/services/suggested_connections.py`

This adds an optional AI call that generates short relationship notes for each suggestion. It gracefully degrades — if AI is unavailable, suggestions are saved without notes.

- [ ] **Step 1: Add the AI note generation function**

Add these imports at the top of `app/services/suggested_connections.py`:

```python
from app.services.ai_generate import (
    DraftGenerationConfigurationError,
    load_ai_provider,
    load_copilot_settings,
    load_openai_settings,
)
```

Then add the function:

```python
RELATIONSHIP_NOTE_SYSTEM_PROMPT = (
    "You are a concise assistant. Given two timeline entry titles, "
    "produce a short relationship phrase (3-8 words) describing how they relate. "
    "Examples: 'follow-up announcement', 'earlier background context', "
    "'competing approach', 'same product line'. "
    "Respond with ONLY the phrase, no quotes, no punctuation at the end."
)


def generate_relationship_notes(
    pairs: list[tuple[str, str]],
) -> list[str]:
    """Generate short relationship notes for (source_title, target_title) pairs.

    Returns a list of notes in the same order as the input pairs.
    On any failure, returns empty strings for all pairs.
    """
    if not pairs:
        return []

    try:
        provider = load_ai_provider()
    except DraftGenerationConfigurationError:
        return [""] * len(pairs)

    prompt_lines = []
    for i, (src, tgt) in enumerate(pairs, 1):
        prompt_lines.append(f"{i}. \"{src}\" -> \"{tgt}\"")
    user_prompt = (
        "For each numbered pair, write a short relationship phrase "
        "(3-8 words) on its own line, numbered to match:\n\n"
        + "\n".join(prompt_lines)
    )

    try:
        if provider == "copilot":
            raw = _generate_notes_copilot(user_prompt)
        else:
            raw = _generate_notes_openai(user_prompt)
    except Exception:
        logger.warning("AI relationship note generation failed", exc_info=True)
        return [""] * len(pairs)

    return _parse_numbered_lines(raw, len(pairs))


def _generate_notes_openai(user_prompt: str) -> str:
    from openai import OpenAI

    settings = load_openai_settings()
    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url or None)
    response = client.chat.completions.create(
        model=settings.model_id,
        messages=[
            {"role": "system", "content": RELATIONSHIP_NOTE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=200,
        temperature=0.3,
    )
    return (response.choices[0].message.content or "").strip()


def _generate_notes_copilot(user_prompt: str) -> str:
    import asyncio
    from app.services import copilot_runtime

    settings = load_copilot_settings()

    async def _run() -> str:
        client = copilot_runtime.instantiate_copilot_client(
            model_id=settings.model_id,
            cli_path=settings.cli_path,
            cli_url=settings.cli_url,
        )
        session = await copilot_runtime.create_copilot_session(client)
        prompt = f"{RELATIONSHIP_NOTE_SYSTEM_PROMPT}\n\n{user_prompt}"
        response = await copilot_runtime.send_copilot_prompt(
            session, prompt, timeout=30.0
        )
        return copilot_runtime.extract_copilot_message_content(response).strip()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, _run()).result(timeout=35)
    return asyncio.run(_run())


def _parse_numbered_lines(raw: str, expected_count: int) -> list[str]:
    """Parse numbered lines like '1. follow-up announcement' into a list."""
    lines = raw.strip().splitlines()
    results: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Strip leading number and punctuation: "1. text" or "1) text"
        for sep in (". ", ") ", ": ", "- "):
            if len(line) > 2 and line[0].isdigit() and sep in line[:5]:
                line = line.split(sep, 1)[-1]
                break
        line = line.strip().strip('"').strip("'").rstrip(".")
        results.append(line)
    # Pad or truncate to match expected count
    while len(results) < expected_count:
        results.append("")
    return results[:expected_count]
```

- [ ] **Step 2: Verify types**

Run:
```bash
uv run pyright app/services/suggested_connections.py
```
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add app/services/suggested_connections.py
git commit -m "feat: add AI relationship note generation for suggested connections"
```

---

## Task 4: Background Task Integration

**Files:**
- Modify: `app/services/suggested_connections.py` (add orchestrator function)
- Modify: `app/main.py` (wire background task into save/update routes)

- [ ] **Step 1: Add `compute_suggestions_for_entry()` orchestrator**

Add to the end of `app/services/suggested_connections.py`:

```python
def compute_suggestions_for_entry(
    entry_id: int,
    entry_title: str,
) -> None:
    """Background task: find similar entries and generate AI relationship notes.

    Designed to be called via FastAPI BackgroundTasks. Opens its own DB
    connection and commits independently of the request lifecycle.
    """
    from app.db import connection_context, is_sqlite_vec_enabled
    from app.services.entries import utc_now_iso

    try:
        with connection_context() as conn:
            if not is_sqlite_vec_enabled(conn):
                return

            similar = find_similar_entries(conn, entry_id)
            if not similar:
                # No similar entries found; clear any stale pending suggestions.
                conn.execute(
                    "DELETE FROM suggested_connections "
                    "WHERE entry_id = ? AND status = 'pending'",
                    (entry_id,),
                )
                return

            # Generate AI relationship notes (best-effort).
            pairs = [(entry_title, s["title"]) for s in similar]
            notes = generate_relationship_notes(pairs)
            for s, note in zip(similar, notes):
                s["suggested_note"] = note

            now = utc_now_iso()
            save_suggestions(conn, entry_id, similar, now)
            logger.info(
                "Computed %d suggestions for entry %d", len(similar), entry_id
            )
    except Exception:
        logger.warning(
            "Suggestion computation failed for entry %d",
            entry_id,
            exc_info=True,
        )
```

- [ ] **Step 2: Wire background task into `create_entry` route**

In `app/main.py`, add the import at the top with the other imports from `app.services`:

```python
from app.services.suggested_connections import (
    accept_suggestion,
    compute_suggestions_for_entry,
    dismiss_suggestion,
    get_pending_suggestions,
)
```

Then in the `create_entry` route (search for `def create_entry`), find the block that dispatches the topic clusters background task:

```python
    if payload.tags:
        background_tasks.add_task(_refresh_topic_clusters_bg, payload.group_id)
    return RedirectResponse(url=f"/entries/{entry_id}/view", status_code=303)
```

Add the suggestion task **before** the return, unconditionally (suggestions depend on embeddings, not tags):

```python
    if payload.tags:
        background_tasks.add_task(_refresh_topic_clusters_bg, payload.group_id)
    background_tasks.add_task(
        compute_suggestions_for_entry, entry_id, payload.title
    )
    return RedirectResponse(url=f"/entries/{entry_id}/view", status_code=303)
```

- [ ] **Step 3: Wire background task into `update_entry_route`**

Find the same pattern in `update_entry_route` (search for `def update_entry_route`). Find the block:

```python
    if payload.tags:
        background_tasks.add_task(_refresh_topic_clusters_bg, payload.group_id)
    return RedirectResponse(url=f"/entries/{entry_id}/view", status_code=303)
```

Add the suggestion task:

```python
    if payload.tags:
        background_tasks.add_task(_refresh_topic_clusters_bg, payload.group_id)
    background_tasks.add_task(
        compute_suggestions_for_entry, entry_id, payload.title
    )
    return RedirectResponse(url=f"/entries/{entry_id}/view", status_code=303)
```

- [ ] **Step 4: Verify types**

Run:
```bash
uv run pyright app/main.py app/services/suggested_connections.py
```
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add app/services/suggested_connections.py app/main.py
git commit -m "feat: compute suggested connections as background task on entry save"
```

---

## Task 5: API Routes — Accept, Dismiss, and View Route Update

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Update `view_entry` route to load suggestions**

Find the `view_entry` function. It currently loads connections. Add suggestion loading inside the `with connection_context()` block, after the connections line:

Current code:
```python
    with connection_context() as connection:
        entry = get_entry(connection, entry_id)
        source_snapshot = get_entry_source_snapshot(connection, entry_id)
        connections = get_entry_connections(connection, entry_id) if entry else []
```

Change to:
```python
    with connection_context() as connection:
        entry = get_entry(connection, entry_id)
        source_snapshot = get_entry_source_snapshot(connection, entry_id)
        connections = get_entry_connections(connection, entry_id) if entry else []
        suggestions = get_pending_suggestions(connection, entry_id) if entry else []
```

Then pass `suggestions` in the template context. Find the context dict:

```python
    context: EntryDetailPageContext = {
        "page_title": entry.title or "Entry",
        "entry": entry,
        "source_snapshot": source_snapshot,
    }
```

Change to:
```python
    context = {
        "page_title": entry.title or "Entry",
        "entry": entry,
        "source_snapshot": source_snapshot,
        "suggested_connections": suggestions,
    }
```

Note: We switch from the `EntryDetailPageContext` TypedDict to a plain dict since we're adding a new key. Alternatively, update the TypedDict — but a plain dict is simpler and matches how other routes work.

- [ ] **Step 2: Add accept endpoint**

Add this route near the other connection-related routes (after `api_group_connections`):

```python
@app.post("/api/suggestions/{suggestion_id}/accept")
def api_accept_suggestion(suggestion_id: int) -> JSONResponse:
    with connection_context() as connection:
        result = accept_suggestion(
            connection, suggestion_id, utc_now_iso()
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    entry_id, suggested_entry_id = result
    return JSONResponse({"ok": True, "entry_id": entry_id, "suggested_entry_id": suggested_entry_id})
```

- [ ] **Step 3: Add dismiss endpoint**

```python
@app.post("/api/suggestions/{suggestion_id}/dismiss")
def api_dismiss_suggestion(suggestion_id: int) -> JSONResponse:
    with connection_context() as connection:
        updated = dismiss_suggestion(
            connection, suggestion_id, utc_now_iso()
        )
    if not updated:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: Verify types**

Run:
```bash
uv run pyright app/main.py
```
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add app/main.py
git commit -m "feat: add accept/dismiss API routes and load suggestions in view"
```

---

## Task 6: Entry Detail UI — Suggestions Panel

**Files:**
- Modify: `app/templates/entry_detail.html`

- [ ] **Step 1: Add the Suggested Connections panel**

In `entry_detail.html`, find the end of the Connections section. It ends with:
```html
                    {% endif %}
```
(after the incoming connections block, before the closing `</article>` tag)

Add a new section after it, before `</article>`:

```html
                    {% if suggested_connections %}
                    <section class="mt-4" id="suggested-connections-section">
                        <h2 class="h5 mb-3 d-flex align-items-center gap-2">
                            Suggested Connections
                            <span class="badge text-bg-info" id="suggestion-count">{{ suggested_connections | length }}</span>
                        </h2>
                        <p class="text-body-secondary small mb-3">Based on content similarity. Accept to create a connection, or dismiss.</p>
                        <ul class="list-group list-group-flush" id="suggestions-list">
                            {% for s in suggested_connections %}
                            <li class="list-group-item d-flex align-items-start gap-3 px-0" data-suggestion-id="{{ s.id }}">
                                <div class="flex-grow-1">
                                    <a href="/entries/{{ s.suggested_entry_id }}/view" class="text-decoration-none fw-medium">{{ s.suggested_entry_title }}</a>
                                    <div class="text-body-secondary small">{{ s.suggested_entry_date }} &middot; {{ s.suggested_entry_group }}</div>
                                    {% if s.suggested_note %}
                                    <div class="text-body-secondary small fst-italic mt-1">{{ s.suggested_note }}</div>
                                    {% endif %}
                                </div>
                                <div class="d-flex gap-1 flex-shrink-0">
                                    <button class="btn btn-sm btn-outline-success" data-accept-suggestion="{{ s.id }}" title="Accept connection">
                                        &#10003;
                                    </button>
                                    <button class="btn btn-sm btn-outline-secondary" data-dismiss-suggestion="{{ s.id }}" title="Dismiss">
                                        &#10005;
                                    </button>
                                </div>
                            </li>
                            {% endfor %}
                        </ul>
                    </section>
                    {% endif %}
```

- [ ] **Step 2: Add JavaScript for accept/dismiss**

Add a `<script>` block at the end of the template (before `{% endblock %}`):

```html
{% if suggested_connections %}
<script>
(function () {
    const section = document.getElementById('suggested-connections-section');
    if (!section) return;

    section.addEventListener('click', async function (event) {
        const acceptBtn = event.target.closest('[data-accept-suggestion]');
        const dismissBtn = event.target.closest('[data-dismiss-suggestion]');
        const btn = acceptBtn || dismissBtn;
        if (!btn) return;

        const suggestionId = btn.dataset.acceptSuggestion || btn.dataset.dismissSuggestion;
        const action = acceptBtn ? 'accept' : 'dismiss';
        const url = `/api/suggestions/${suggestionId}/${action}`;

        btn.disabled = true;
        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content
                || document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith('csrf_token='))?.split('=')[1]
                || '';
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'X-CSRF-Token': csrfToken,
                    'Content-Type': 'application/json',
                },
            });
            if (!response.ok) throw new Error('Request failed');

            const listItem = btn.closest('[data-suggestion-id]');
            if (listItem) {
                listItem.style.transition = 'opacity 0.3s';
                listItem.style.opacity = '0';
                setTimeout(() => {
                    listItem.remove();
                    // Update count badge
                    const remaining = document.querySelectorAll('[data-suggestion-id]').length;
                    const badge = document.getElementById('suggestion-count');
                    if (badge) badge.textContent = String(remaining);
                    // Hide section if empty
                    if (remaining === 0) section.style.display = 'none';
                }, 300);
            }

            if (action === 'accept') {
                // Reload page to show the new connection in the connections list
                setTimeout(() => window.location.reload(), 400);
            }
        } catch {
            btn.disabled = false;
        }
    });
})();
</script>
{% endif %}
```

- [ ] **Step 3: Handle CSRF for the API endpoints**

Check how other POST API routes handle CSRF in the codebase. The app uses CSRF middleware. The JavaScript above sends the CSRF token from the cookie via the `X-CSRF-Token` header, which matches the app's existing CSRF pattern.

If the app's CSRF middleware checks `X-CSRF-Token` header (which it does based on the existing codebase pattern), this will work. If the endpoints need to be exempt for API use, the `TESTING=1` env var bypasses CSRF (already used by tests).

- [ ] **Step 4: Verify by running tests**

Run:
```bash
uv run pytest tests/ --ignore=tests/e2e -x -q
```
Expected: All tests pass (no regressions)

- [ ] **Step 5: Commit**

```bash
git add app/templates/entry_detail.html
git commit -m "feat: add suggested connections panel with accept/dismiss on entry detail"
```

---

## Task Summary

| Task | Scope | Key Files | Depends On |
|------|-------|-----------|------------|
| 1 | Schema & Model | `db.py`, `models.py` | — |
| 2 | Core Service | `suggested_connections.py` | Task 1 |
| 3 | AI Notes | `suggested_connections.py` | Task 2 |
| 4 | Background Task | `suggested_connections.py`, `main.py` | Tasks 2, 3 |
| 5 | API Routes | `main.py` | Tasks 2, 4 |
| 6 | Detail UI | `entry_detail.html` | Task 5 |

Tasks 2 and 3 can be merged into a single subagent. Tasks 4 and 5 can also be merged. Task 6 depends on Task 5 completing (needs routes to exist for the JS).
