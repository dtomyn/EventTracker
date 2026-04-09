# Implementation Plan: Bidirectional Entry Linking & Knowledge Graph

This plan breaks down the implementation of bidirectional entry-to-entry connections, backlinks, and an interactive entry connection graph into five phases. Phases are ordered by dependency but internally parallelizable.

## Overview
- **Goal:** Allow entries to link to other entries with optional relationship notes. Surface backlinks automatically. Visualize the entry connection network as an interactive D3.js graph.
- **Inspiration:** Obsidian's bidirectional linking and graph view, adapted to a chronological timeline context.
- **Key Principle:** Connections are directional (source → target) but the UI treats them bidirectionally — viewing either side shows the relationship.

## Why This Feature
EventTracker currently links entries to external URLs but has no mechanism for entries to reference each other. Adding internal connections transforms the app from a flat timeline into a connected knowledge timeline where:
- Cause-and-effect chains become navigable ("GPT-4 Release" → "OpenAI Founded")
- Related events across groups are discoverable
- Story Mode can leverage connection paths for richer narratives
- The tag-based topic graph is complemented by an explicit relationship graph

---

## Phase 1: Database Schema & Models
**Focus:** Schema migration, model definitions, payload schemas.

### Tasks

1. **Schema — `entry_connections` table** (`app/db.py`):
   Add table creation in `bootstrap_schema()`, after the existing `entry_links` table (around line 128):
   ```sql
   CREATE TABLE IF NOT EXISTS entry_connections (
       id              INTEGER PRIMARY KEY,
       source_entry_id INTEGER NOT NULL,
       target_entry_id INTEGER NOT NULL,
       note            TEXT NOT NULL DEFAULT '',
       created_utc     TEXT NOT NULL,
       FOREIGN KEY (source_entry_id) REFERENCES entries(id) ON DELETE CASCADE,
       FOREIGN KEY (target_entry_id) REFERENCES entries(id) ON DELETE CASCADE,
       UNIQUE(source_entry_id, target_entry_id)
   );
   ```
   - `ON DELETE CASCADE` on both FKs ensures cleanup when either entry is deleted.
   - `UNIQUE` constraint prevents duplicate directional connections.
   - `note` stores an optional relationship description (e.g., "caused by", "follow-up to", "contradicts").

2. **Indexes** (add near existing index block, around line 151):
   ```sql
   CREATE INDEX IF NOT EXISTS idx_entry_connections_source
       ON entry_connections(source_entry_id);
   CREATE INDEX IF NOT EXISTS idx_entry_connections_target
       ON entry_connections(target_entry_id);
   ```
   Both directions need fast lookups — source for "outgoing connections" and target for "backlinks."

3. **Model — `EntryConnection`** (`app/models.py`):
   Add after the `EntryLink` dataclass (around line 20):
   ```python
   @dataclass(slots=True)
   class EntryConnection:
       id: int
       connected_entry_id: int    # The "other" entry (resolved from source/target)
       connected_entry_title: str  # Denormalized for display
       connected_entry_date: str   # Display date of connected entry
       connected_entry_group: str  # Group name of connected entry
       note: str
       direction: str             # "outgoing" | "incoming" (backlink)
       created_utc: str
   ```
   The `direction` field indicates whether this entry is the source or the target. The `connected_entry_*` fields are denormalized from a JOIN for rendering without extra queries.

4. **Extend `Entry` model** (`app/models.py`, around line 40):
   Add a new field to the Entry dataclass:
   ```python
   connections: list[EntryConnection] = field(default_factory=list)
   ```

5. **Schema — `EntryConnectionPayload`** (`app/schemas.py`):
   Add after `EntryLinkPayload` (around line 11):
   ```python
   @dataclass(slots=True)
   class EntryConnectionPayload:
       target_entry_id: int
       note: str
   ```

6. **Extend `EntryPayload`** (`app/schemas.py`, around line 40):
   Add field:
   ```python
   connections: list[EntryConnectionPayload] = field(default_factory=list)
   ```

7. **Extend `EntryFormState`** (`app/schemas.py`, around line 47):
   Add field:
   ```python
   connection_rows: list[dict[str, str]] = field(default_factory=list)
   ```

### Validation
- Run `uv run python -m scripts.init_db` on a fresh DB to confirm schema creation.
- Run `uv run pyright` to confirm model/schema type consistency.

---

## Phase 2: Service Layer — CRUD Operations
**Focus:** Reading, writing, and syncing entry connections in `app/services/entries.py`.

### Tasks

1. **`sync_entry_connections()`** — new function (follow pattern of `sync_entry_links()` at line 796):
   ```python
   def sync_entry_connections(
       connection: sqlite3.Connection,
       entry_id: int,
       connections: list[EntryConnectionPayload],
   ) -> None:
   ```
   - DELETE all existing connections where `source_entry_id = entry_id`.
   - INSERT new connections from the payload.
   - Skip connections where `target_entry_id == entry_id` (no self-links).
   - Use `INSERT OR IGNORE` to handle the UNIQUE constraint gracefully if duplicates slip through.

2. **`get_entry_connections()`** — new function to fetch both directions:
   ```python
   def get_entry_connections(
       connection: sqlite3.Connection,
       entry_id: int,
   ) -> list[EntryConnection]:
   ```
   Query pattern (UNION of both directions):
   ```sql
   SELECT ec.id, e.id, e.title, e.event_year, e.event_month, e.event_day,
          tg.name, ec.note, 'outgoing' AS direction, ec.created_utc
   FROM entry_connections ec
   JOIN entries e ON e.id = ec.target_entry_id
   JOIN timeline_groups tg ON tg.id = e.group_id
   WHERE ec.source_entry_id = ?

   UNION ALL

   SELECT ec.id, e.id, e.title, e.event_year, e.event_month, e.event_day,
          tg.name, ec.note, 'incoming' AS direction, ec.created_utc
   FROM entry_connections ec
   JOIN entries e ON e.id = ec.source_entry_id
   JOIN timeline_groups tg ON tg.id = e.group_id
   WHERE ec.target_entry_id = ?
   ```
   Order by direction (outgoing first), then by connected entry's title.

3. **`get_entry_connection_count()`** — lightweight count for card display:
   ```python
   def get_entry_connection_count(
       connection: sqlite3.Connection,
       entry_id: int,
   ) -> int:
   ```
   ```sql
   SELECT COUNT(*) FROM entry_connections
   WHERE source_entry_id = ? OR target_entry_id = ?
   ```

4. **`search_entries_for_connection()`** — entry search for the connection picker:
   ```python
   def search_entries_for_connection(
       connection: sqlite3.Connection,
       query: str,
       exclude_entry_id: int | None = None,
       group_id: int | None = None,
       limit: int = 10,
   ) -> list[dict]:
   ```
   - Uses FTS5 or LIKE-based title search.
   - Returns `[{id, title, display_date, group_name}]` for the picker UI.
   - Excludes the current entry (no self-links) and already-connected entries.

5. **Integrate into `save_entry()` and `update_entry()`**:
   - After existing `sync_entry_links()` call (lines 543 and 601), add:
     ```python
     sync_entry_connections(connection, entry_id, payload.connections)
     ```

6. **Integrate into entry reading**:
   - In the function that builds the full `Entry` model for detail/edit views, call `get_entry_connections()` and populate `entry.connections`.
   - For timeline card rendering, call `get_entry_connection_count()` and pass it as context (or add a `connection_count: int = 0` field to Entry).

7. **Form validation** — add to `validate_entry_form()` (around line 228):
   - Parse connection rows from form data: `connection_entry_id[]` and `connection_note[]` (same pattern as `parse_link_rows()`).
   - Validate each `target_entry_id` is a valid integer.
   - Validate target entry exists (optional — can defer to DB constraint).
   - Add `parse_connection_rows()` and `validate_connection_rows()` functions.

### Validation
- Unit tests: test sync, get, count, and search functions.
- Run `uv run pyright` after changes.

---

## Phase 3: API Routes & Form Handling
**Focus:** Route handlers in `app/main.py` for CRUD and the search API endpoint.

### Tasks

1. **Entry search API endpoint** — new route for the connection picker:
   ```
   GET /api/entries/search?q=...&exclude_id=...&group_id=...
   ```
   - Returns JSON array of `{id, title, display_date, group_name}`.
   - Used by the JavaScript typeahead/search picker in the form.
   - Limit to 10 results. Debounce expected on client side.

2. **Update `create_entry()` route** (line 1761):
   - Parse connection rows from form data alongside link rows.
   - Pass `connections` to `EntryPayload`.

3. **Update `update_entry_route()` route** (line 1838):
   - Same form parsing as create.
   - Pass connections through to `update_entry()`.

4. **Update `edit_entry_form()` route** (line 1815):
   - Fetch existing connections via `get_entry_connections()`.
   - Pass them as `connection_rows` in the form state for pre-population.

5. **Update `view_entry()` route** (line 1741):
   - Fetch connections via `get_entry_connections()`.
   - Pass to template context as `connections` (split into outgoing and incoming for the template).

6. **Connection graph page route** — new route:
   ```
   GET /groups/{group_id}/connections/graph
   ```
   - Validates group exists.
   - Renders `connection_graph.html` template with group context.

7. **Connection graph data API** — new route:
   ```
   GET /api/groups/{group_id}/connections
   ```
   - Returns JSON `{nodes: [...], edges: [...]}` for D3.js.
   - Nodes: entries in the group that have at least one connection.
   - Edges: connections between entries, with note as label.
   - Include implicit edges from shared tags (lower weight) as an option via `?include_tags=1` query param.

### Validation
- Smoke test the search API with curl/httpie.
- Test create/edit round-trip preserves connections.
- Run existing test suite to confirm no regressions.

---

## Phase 4: Frontend — Entry Form & Detail View
**Focus:** UI for managing connections on the entry form and displaying them on entry detail.

### Tasks

#### Entry Form (`app/templates/entry_form.html`)

1. **New "Connected Entries" section** — add after the "Additional Links" section (after line 183):
   - Collapsible section matching the existing pattern (`data-collapse-toggle`).
   - Section label: "Connected Entries"
   - Description text: "Link this entry to other entries in your timeline."

2. **Connection row template**:
   Each row contains:
   - A search input with typeahead/autocomplete for finding entries by title.
   - A hidden `connection_entry_id[]` field holding the selected entry ID.
   - A display element showing the selected entry title + date + group.
   - A `connection_note[]` text input for the relationship note.
   - A remove button (same pattern as link row removal).

3. **JavaScript — Entry search picker**:
   - On input in the search field, debounced fetch to `GET /api/entries/search?q=...&exclude_id={currentEntryId}`.
   - Render dropdown results below the input.
   - On selection: populate hidden ID field, show entry title/date as a read-only badge/chip, hide the search input.
   - On remove: clear hidden field, restore search input.
   - Pattern: Follow the existing `createLinkRow()` approach but with async search.

4. **Pre-populate on edit**:
   - When editing an existing entry, render connection rows for existing connections.
   - Each pre-populated row shows the connected entry as a read-only badge with a remove button.

5. **Form submission**:
   - Connection data submitted as `connection_entry_id[]` and `connection_note[]` arrays (same pattern as `link_url[]` / `link_note[]`).

#### Entry Detail View (`app/templates/entry_detail.html`)

6. **Connected Entries section** — add after "Additional Links" (after line 128):
   - Split into two subsections:
     - **"Connected Entries"** (outgoing): entries this entry links to.
     - **"Backlinks"** (incoming): entries that link to this entry.
   - Each connection rendered as a row:
     - Entry title as a clickable link to `/entries/{id}/view`.
     - Date and group name as secondary text.
     - Relationship note (if present) in muted text.
   - Empty states: "No connected entries" / "No entries link to this one."
   - Count badges in section headers.

#### Entry Card (`app/templates/entry_card.html`)

7. **Connection count indicator** — add near the tags section (around line 25):
   - Small badge showing connection count, e.g., `🔗 3` or a simple `3 connections` text.
   - Only render if count > 0.
   - Links to the entry detail view.

### Validation
- Manual testing: create entry, add connections, save, verify they appear on detail view.
- Edit entry, verify connections pre-populate correctly.
- Delete a connected entry, verify cascade removes the connection.
- Test backlinks appear on the target entry's detail page.

---

## Phase 5: Frontend — Connection Graph Visualization
**Focus:** Interactive D3.js graph showing entry connections within a group.

### Tasks

1. **Template — `connection_graph.html`** (`app/templates/`):
   Create new template closely following `topic_graph.html` structure:
   - Extends `base.html`.
   - Breadcrumb: Home → Group Name → Connection Graph.
   - Card container with SVG, loading spinner, zoom controls.
   - Page title: "Entry Connections" or "Knowledge Graph".

2. **D3.js graph rendering**:
   Follow the pattern established in `topic_graph.html` (lines 46-284):

   **Data fetch:**
   - Endpoint: `GET /api/groups/{groupId}/connections`
   - Returns `{nodes: [{id, label, size, display_date, group_name}], edges: [{source, target, weight, note, type}]}`

   **Node rendering:**
   - Nodes are entries (not clusters). Use entry title as label.
   - Size based on connection count (entries with more connections are larger).
   - Color scale: use a different palette from topic graph to visually distinguish.
   - Tooltip on hover: entry title, date, connection count.

   **Edge rendering:**
   - Explicit connections: solid lines, higher opacity.
   - Shared-tag implicit edges (if enabled): dashed lines, lower opacity, lighter color.
   - Edge labels: show relationship note on hover or as small text.
   - Weight: explicit connections = 1.0, tag edges = co-occurrence count / max.

   **Interactions:**
   - **Single click** on node: highlight node and its direct connections (dim others).
   - **Double click** on node: navigate to `/entries/{id}/view`.
   - **Hover** on node: show tooltip, brighten connected edges.
   - **Hover** on edge: show relationship note.
   - **Drag**: reposition nodes (same D3 drag behavior as topic graph).
   - **Zoom/pan**: same `d3.zoom()` pattern.

   **Force simulation:**
   ```javascript
   simulation = d3.forceSimulation(data.nodes)
       .force("link", d3.forceLink(data.edges).id(d => d.id).distance(120).strength(0.7))
       .force("charge", d3.forceManyBody().strength(-200))
       .force("center", d3.forceCenter(width / 2, height / 2))
       .force("collide", d3.forceCollide().radius(d => radiusScale(d.size) + 8))
   ```

3. **Navigation entry point**:
   - Add a "Connection Graph" button/link on the timeline page, near the existing "Tag Clusters" button.
   - Only show if the group has entries with connections (or always show, with empty state in the graph).

4. **Graph data service** — `build_connection_graph()` in `app/services/entries.py` or new `app/services/connections.py`:
   ```python
   def build_connection_graph(
       connection: sqlite3.Connection,
       group_id: int,
       include_tag_edges: bool = False,
   ) -> dict:
   ```
   - Query all entries in the group that have at least one connection.
   - Query all connections between entries in the group.
   - Optionally query shared tags to build implicit edges.
   - Return `{nodes: [...], edges: [...]}` dict.
   - Nodes: `{id: entry_id, label: title, size: connection_count, display_date, group_name}`
   - Edges: `{source: entry_id, target: entry_id, weight, note, type: "explicit"|"tag"}`

5. **Empty state**:
   - If no connections exist in the group, show a friendly message: "No connections yet. Connect entries from the entry form to build your knowledge graph."

### Validation
- Test with entries that have 0, 1, and many connections.
- Test cross-group connections (entries in different groups that connect — decide if these appear or are filtered).
- Test performance with larger datasets (50+ entries, 100+ connections).
- Verify zoom, drag, click, and double-click interactions.

---

## Cross-Cutting Concerns

### Performance
- Connection count queries should be efficient with the two indexes.
- The entry search API (`/api/entries/search`) should use FTS5 for fast typeahead.
- Graph data for large groups should be bounded (e.g., max 200 nodes).

### Testing Strategy
- **Unit tests** (`tests/`): Test `sync_entry_connections`, `get_entry_connections`, `get_entry_connection_count`, `search_entries_for_connection`, `build_connection_graph`.
- **E2E tests** (`tstests/e2e/`): Test creating an entry with connections, verifying backlinks appear, editing connections, deleting entries and verifying cascade.
- **Type checking**: Add new files to pyright include list in `pyproject.toml` if needed.

### Migration Safety
- All schema changes use `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` — safe for existing databases.
- No changes to existing tables. The `Entry` model's new `connections` field has `default_factory=list` so existing code that doesn't populate it continues to work.
- The `EntryPayload.connections` field has `default_factory=list` so existing form submissions without connection data still validate.

### Design Decisions
1. **Directional vs. bidirectional storage:** Connections are stored directionally (source → target) but displayed bidirectionally. This allows for relationship notes that read naturally in one direction ("caused by", "led to") while still showing the reverse as a backlink.
2. **Separate table vs. extending entry_links:** A new `entry_connections` table is cleaner than overloading `entry_links` (which stores external URLs with a fundamentally different schema). Separation of concerns.
3. **Cross-group connections allowed:** Entries in different groups can connect. The graph view filters to one group at a time but the detail view shows all connections regardless of group.
4. **No reciprocal auto-creation:** Creating A → B does not auto-create B → A. The backlinks UI makes the reverse discoverable without duplicating data.

---

## Phase Summary

| Phase | Scope | Key Files | Depends On |
|-------|-------|-----------|------------|
| 1 | Schema & Models | `db.py`, `models.py`, `schemas.py` | — |
| 2 | Service Layer | `services/entries.py` | Phase 1 |
| 3 | Routes & API | `main.py` | Phase 1, 2 |
| 4 | Form & Detail UI | `entry_form.html`, `entry_detail.html`, `entry_card.html` | Phase 1, 2, 3 |
| 5 | Connection Graph | `connection_graph.html`, `services/entries.py` or `services/connections.py` | Phase 1, 2, 3 |

Phases 4 and 5 are independent of each other and can be worked in parallel once Phase 3 is complete.
