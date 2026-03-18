# EventTracker

EventTracker is a local-first timeline application built with FastAPI, SQLite, Jinja2, Bootstrap, SQLite FTS5, and optional sqlite-vec embeddings. It is a single-process, server-rendered app: entries are stored in one SQLite file, pages are rendered on the server, and the browser layer is limited to lightweight JavaScript for view switching, pagination, previews, and AI-assisted workflows.

## Quick start

Run these commands from the repository root:

```powershell
uv sync
uv run python -m scripts.init_db
uv run python -m scripts.run_dev --reload
```

Then open `http://127.0.0.1:35231/` in your browser.

## Playwright end-to-end tests

The browser E2E suite runs against a temporary copy of the SQLite database so the live `data/EventTracker.db` file is never modified.

Set up the test tooling once:

```powershell
uv sync --dev
uv run python -m playwright install chromium
```

Run the Playwright suite:

```powershell
uv run pytest tests/e2e
```

Useful environment variables:

- `EVENTTRACKER_PLAYWRIGHT_HEADLESS=0` launches Chromium visibly for debugging.
- `EVENTTRACKER_PLAYWRIGHT_SLOW_MO=250` slows browser actions by 250 ms.

The harness will:

- copy `data/EventTracker.db` to a temporary file when it exists
- launch EventTracker on an isolated local port
- give the suite a unique run id and dedicated Playwright group name
- discard the temporary database when the run completes

## What the application does today

- Creates and edits timeline entries with year, month, optional day, required group, required title, optional source URL, optional generated draft HTML, required final rich text, comma-separated tags, and optional additional links with required notes.
- Shows the main timeline at `/` with four views over the current scope: `Details`, `Summaries`, `Months`, and `Years`.
- Defaults the main timeline and ranked search to the current default timeline group. Users can switch to a specific group or `All groups`.
- Supports timeline filtering on `/` with the `q` query string. This keeps matches in timeline order instead of ranked order.
- Supports ranked search at `/search`, combining FTS matches with semantic matches when embeddings are available.
- Organizes entries into timeline groups, seeded with a default `Agentic Coding` group.
- Lets users create, rename, delete, and mark the default group at `/admin/groups`, and store an optional per-group web search query.
- Generates AI draft suggestions from either a title alone or a title plus temporary extracted URL content.
- Exports all saved entries as JSON from `/entries/export`.
- Imports legacy HTML lists or prior JSON exports with `scripts/import_entries.py`.
- Shows a Copilot-backed `On the web` sidebar for the selected group when that group has a stored web search query.

## Current architecture

### App shape

- Backend: FastAPI.
- Templates: Jinja2.
- Styling: Bootstrap plus custom CSS in `app/static/styles.css`.
- Persistence: SQLite in `data/EventTracker.db` by default.
- Search: SQLite FTS5 for keyword search and optional sqlite-vec for semantic recall.
- AI draft generation: provider abstraction in `app/services/ai_generate.py`.

### Database model

The app currently stores:

- `timeline_groups`: top-level collections for entries, with optional `web_search_query` and `is_default`.
- `entries`: the main event records.
- `tags` and `entry_tags`: normalized tag mapping.
- `entry_links`: additional per-entry websites with required notes.
- `entries_fts`: derived FTS5 index over `entries.final_text` only.
- `embedding_index_meta`: metadata for the embedding model and dimensions.
- `entry_embeddings`: sqlite-vec index when embeddings are configured and the extension is available.

Important implementation details:

- `title` is required.
- `final_text` is required.
- `group_id` is required when saving an entry.
- `sort_key` is derived as `YYYYMMDD`, using `00` when day is missing.
- Extracted URL content is not stored in the database.
- URL extraction fetches remote content server-side and is intended only for local or otherwise trusted deployments.
- Embeddings are derived only from `final_text`.
- FTS indexes `final_text` only.

## Routes and workflows

### Timeline

`GET /`

- Loads the selected timeline scope.
- When `group_id` is omitted, defaults to the current default group.
- Supports `group_id=all` to show all groups.
- If `q` is present, uses the search service to find matching entry ids and renders a filtered timeline.
- Groups entries by month and year.
- Sorts entries newest first by `sort_key DESC`, then `updated_utc DESC`, then `id DESC`.
- Exposes client-side metadata so the browser can switch between `Details`, `Summaries`, `Months`, and `Years`.

Additional timeline endpoints:

- `GET /timeline/details`: paginated HTML payload for the details view.
- `GET /timeline/summaries`: HTML payload for summary groups, optionally scoped by year and month.
- `GET /timeline/months`: month-bucket HTML payload, optionally scoped by year.
- `GET /timeline/years`: year-bucket HTML payload.

### Ranked search

`GET /search`

- Runs a ranked search only when `q` is present.
- Uses reciprocal rank fusion over:
  - FTS5 results from `entries.final_text`
  - semantic matches from sqlite-vec when enabled and configured
- Uses the same default-group and `All groups` scoping model as the timeline.
- Renders search-specific snippets with `<mark>` highlighting.

Additional search endpoint:

- `GET /search/results`: paginated HTML payload for additional ranked results.

### Entry create, view, and edit

`GET /entries/new`

- Renders a blank form.
- Preselects the first available timeline group.

`POST /entries/new`

- Validates year, month, optional day, group, title, source URL, additional links, and final text.
- Saves the entry, normalized tags, and additional links.
- Attempts embedding sync without failing the save path.
- Redirects to `/entries/{id}/view`.

`GET /entries/{id}/view`

- Renders a read-only event details page.
- Shows the saved final content, primary source URL, and additional links.

`GET /entries/{id}` and `POST /entries/{id}`

- Load an existing entry into the same form.
- Revalidate the same rules on update.
- Rewrite tags, replace additional links, and attempt best-effort embedding sync again.

### Draft generation and preview

`POST /entries/generate`

- Accepts `title`, `source_url`, and the current `generated_text` value.
- Requires either a title or a source URL.
- If a source URL is present, attempts extraction first.
- If extraction fails and a title exists, falls back to title-only generation.
- If extraction fails and there is no title, returns a server-rendered partial with an error.
- On success, returns a server-rendered partial containing generated draft HTML, a rendered preview, a suggested title, and suggested date fields.

`POST /entries/preview-html`

- Sanitizes arbitrary HTML entered in the form and returns the rendered preview partial used by the editor UI.

### Group administration

`GET /admin/groups`

- Lists all timeline groups with entry counts.
- Supports editing the group name, optional web search query, and default-group status.

`POST /admin/groups`

- Creates a new group if the normalized name is unique and non-empty.
- Can mark the new group as default.

`POST /admin/groups/{group_id}`

- Renames an existing group.
- Updates its optional web search query.
- Can set or clear its default-group status.
- Clears cached group web search results when the query changes.

`POST /admin/groups/{group_id}/delete`

- Deletes a group only when it is not the default group and has no entries.

### Group web search

These endpoints back the optional timeline sidebar for the selected group:

- `GET /timeline/group-web-search`
- `GET /timeline/group-web-search/stream`
- `POST /timeline/group-web-search/refresh`

Current behavior:

- Only available when `EVENTTRACKER_AI_PROVIDER=copilot`.
- Only active when the selected group has a stored `web_search_query`.
- Uses GitHub Copilot SDK to produce concise web results.
- Caches results in memory for a short TTL.
- Supports streaming status events over Server-Sent Events for the UI.

### Export and developer utilities

`GET /entries/export`

- Returns all entries as JSON.
- Includes tags and additional links in each exported entry payload.
- Uses a timestamped filename like `EventTracker-export-YYYY-MM-DD-HH-MM-SS.json`.

`GET /dev/extract`

- Fetches and extracts paragraph text from a source URL for debugging extraction behavior.
- Intended for localhost or similarly trusted environments because it performs server-side fetches of the provided URL.

`GET /visualization`

- Redirects to `/` for backward compatibility.

## Search behavior

The app has two distinct search modes:

- Timeline filter on `/`: returns matching entries but preserves timeline ordering and timeline presentation.
- Ranked search on `/search`: returns ranked result cards with snippets.

Keyword search behavior:

- FTS queries are built from tokenized quoted terms.
- FTS ranking uses `bm25(entries_fts)`.
- Search snippets are produced with SQLite `snippet(...)` and sanitized before rendering.

Semantic behavior:

- Semantic search only runs when sqlite-vec is available and embedding settings are configured.
- Query embeddings and entry embeddings both use the OpenAI embeddings API.
- Ranked search fuses keyword and semantic lists with reciprocal rank fusion.
- Timeline filtering reuses ranked match ids, then sorts matching entries back into timeline order.

If embeddings are unavailable or misconfigured, the app still works and search falls back to keyword-only behavior.

## AI generation behavior

Draft generation currently supports two providers:

- `openai` (default)
- `copilot`

OpenAI mode:

- Uses `AsyncOpenAI`.
- Requires `OPENAI_API_KEY` and `OPENAI_CHAT_MODEL_ID`.
- Supports an optional `OPENAI_BASE_URL` for OpenAI-compatible providers.

Copilot mode:

- Uses `github-copilot-sdk`.
- Requires `EVENTTRACKER_AI_PROVIDER=copilot`.
- Uses `COPILOT_CHAT_MODEL_ID`, defaulting to `gpt-5`.
- Supports optional `COPILOT_CLI_PATH` and `COPILOT_CLI_URL` overrides.
- Also powers the optional group web search panel.

Both providers are asked to return strict JSON with this shape:

```json
{
  "title": "string",
  "draft_html": "string",
  "event_year": 2026,
  "event_month": 3,
  "event_day": 16
}
```

The app normalizes that response, strips known stray Unicode characters from generated HTML, and rejects empty or invalid payloads.

## Rich text rules

Saved `final_text`, generated previews, and manual preview rendering are sanitized before display.

Allowed tags in rich text:

- `p`
- `b`
- `strong`
- `i`
- `em`
- `u`
- `ul`
- `ol`
- `li`
- `br`
- `blockquote`
- `code`

Search snippets additionally allow `mark`.

## Running locally

Install dependencies and initialize the database:

```powershell
uv sync
uv run python -m scripts.init_db
```

Start the development server:

```powershell
uv run python -m scripts.run_dev --reload
```

Then open `http://127.0.0.1:35231/` in your browser.

Default host and port:

- Host: `127.0.0.1`
- Port: `35231`

The run script loads `.env` with `override=False`, so explicit shell variables or `--host` and `--port` overrides take precedence over `.env` for that run.

On Windows, `--reload` also cleans up the previous EventTracker reload parent before starting a new one, which avoids the stale-listener state that can otherwise leave the port wedged after an interrupted dev session.

If you want a PowerShell session with the virtual environment activated:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Configuration

### Core settings

```env
EVENTTRACKER_HOST=127.0.0.1
EVENTTRACKER_PORT=35231
EVENTTRACKER_DB_PATH=data/EventTracker.db
```

`EVENTTRACKER_DB_PATH` is optional. If omitted, the app uses `data/EventTracker.db`.

### Draft generation with OpenAI-compatible chat

```env
EVENTTRACKER_AI_PROVIDER=openai
OPENAI_API_KEY=your-api-key
OPENAI_CHAT_MODEL_ID=your-chat-model
OPENAI_BASE_URL=https://your-provider-compatible-endpoint/v1
```

`OPENAI_BASE_URL` is optional.

### Draft generation and group web search with GitHub Copilot SDK

```env
EVENTTRACKER_AI_PROVIDER=copilot
COPILOT_CHAT_MODEL_ID=gpt-5
```

Optional advanced overrides:

```env
COPILOT_CLI_PATH=
COPILOT_CLI_URL=
EVENTTRACKER_GROUP_WEB_SEARCH_TIMEOUT_SECONDS=90
EVENTTRACKER_GROUP_WEB_SEARCH_BROADENED_TIMEOUT_SECONDS=75
EVENTTRACKER_GROUP_WEB_SEARCH_REQUEST_TIMEOUT_MS=95000
```

The Copilot-powered group web search can take a while because it may do an initial pass and then a broadened second pass if the first result set is too sparse. The browser request also has its own timeout.

### Semantic embeddings

```env
OPENAI_API_KEY=your-api-key
OPENAI_EMBEDDING_MODEL_ID=your-embedding-model
OPENAI_BASE_URL=https://your-provider-compatible-endpoint/v1
```

Embeddings are optional. If they are not configured, entry save still works and search stays keyword-only.

To rebuild embeddings for existing entries:

```powershell
uv run python -m scripts.init_db --reindex-embeddings
```

## Import, backup, and restore

### Import

The import script accepts either:

- legacy HTML list content with `<li>`, `<h4>`, and `<p>` blocks
- exported JSON from `/entries/export`

Run it with:

```powershell
uv run python -m scripts.import_entries path\to\input.html
uv run python -m scripts.import_entries path\to\export.json
```

By default it skips exact duplicates based on date, title, and final text. To allow duplicates:

```powershell
uv run python -m scripts.import_entries path\to\export.json --allow-duplicates
```

Current import behavior:

- imported rows are inserted without embedding generation
- FTS remains available immediately
- embeddings can be rebuilt later
- imported entries are assigned to group id `1`, which is the seeded default group in a fresh database

### Backup and restore

The app stores all primary data in a single SQLite file.

- Stop the app before copying the database file.
- Back up `data/EventTracker.db` or the file pointed to by `EVENTTRACKER_DB_PATH`.
- Restore by replacing that file, then restart the app.
- Rebuild embeddings afterward if the restored database is older than the current embedding configuration.

## Verification

Run the test suite with:

```powershell
uv run python -m unittest discover -s tests -p "test_*.py"
```

Current automated coverage includes:

- app startup
- entry create, view, and edit flow
- timeline filter and ranked search behavior
- group administration rules
- generation partial behavior
- import parsing
- group web search behavior

## Project layout

```text
app/
  db.py
  main.py
  models.py
  schemas.py
  services/
    ai_generate.py
    embeddings.py
    entries.py
    extraction.py
    group_web_search.py
    search.py
  static/
  templates/
scripts/
  import_entries.py
  init_db.py
  run_dev.py
tests/
```