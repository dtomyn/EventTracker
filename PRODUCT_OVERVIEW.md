# EventTracker Product Overview

This document is the canonical product description for the current EventTracker application. It describes the product as implemented in the repository today so product scope, requirements, and implementation stay aligned.

EventTracker is a local-first timeline application for storing, browsing, searching, and enriching event entries. It is implemented as a single FastAPI service with server-rendered HTML, one SQLite database file, optional semantic search, optional AI-assisted draft generation, and an optional Copilot-backed group web search sidebar.

## Product goals

- Keep event data local in one SQLite database.
- Keep the application simple to run on one machine.
- Prioritize manual authoring, with AI assistance as an optional accelerator.
- Make browsing and search fast without turning the app into a SPA.
- Keep advanced capabilities fail-open so manual use still works when AI, embeddings, or sqlite-vec are unavailable.

## Current product scope

The shipped application includes:

- local SQLite persistence
- server-rendered UI with Jinja2 templates
- create, edit, and read-only entry views
- grouped timeline browsing on the root page
- client-side switching between detail, summary, month, and year views
- cursor-based pagination for timeline details and ranked search
- timeline filtering on the root page using a query string
- separate ranked search results on `/search`
- top-level timeline groups with admin management
- required titles and required final rich-text content
- optional source URL per entry
- optional additional links per entry, each with a required note
- optional AI draft generation from a title alone or a title plus extracted source content
- sanitized HTML preview rendering in the entry form
- JSON export of all entries
- import from legacy HTML or exported JSON
- optional semantic search using sqlite-vec
- optional OpenAI-compatible or GitHub Copilot draft generation
- optional Copilot-backed per-group web search results
- dark mode with system preference detection and manual toggle

## Architectural requirements

### Application shape

- The application must run as a single-process Python web app.
- The backend framework must be FastAPI.
- HTML must be rendered on the server with Jinja2.
- The browser layer must remain lightweight and use page-level JavaScript only.
- Dark mode must use the Bootstrap 5.3 `data-bs-theme` attribute with CSS custom properties for custom styles.
- Theme preference must be persisted in `localStorage` and must respect the operating system `prefers-color-scheme` setting when no explicit choice is stored.
- Static assets must be served from `app/static`.
- Database initialization must run on startup through `init_db()`.

### Persistence model

- The application must use one SQLite database file.
- The database path must be configurable through `EVENTTRACKER_DB_PATH`.
- If `EVENTTRACKER_DB_PATH` is not set, the default path must be `data/EventTracker.db`.
- Database access must use `connection_context()` so writes commit on success and roll back on failure.
- Schema upgrades must be additive where possible and keep existing data assigned to a valid group.

## Data model requirements

### `timeline_groups`

- Stores top-level collections for entries.
- Names must be unique case-insensitively.
- The app must seed a default group named `Agentic Coding` in a fresh database.
- A group may optionally store a `web_search_query` used by the timeline sidebar.
- Exactly one group can be marked default at a time.

Current persisted fields:

- `id INTEGER PRIMARY KEY`
- `name TEXT NOT NULL UNIQUE COLLATE NOCASE`
- `web_search_query TEXT NULL`
- `is_default INTEGER NOT NULL DEFAULT 0`

### `entries`

Current persisted fields:

- `id INTEGER PRIMARY KEY`
- `event_year INTEGER NOT NULL`
- `event_month INTEGER NOT NULL`
- `event_day INTEGER NULL`
- `sort_key INTEGER NOT NULL`
- `group_id INTEGER REFERENCES timeline_groups(id)`
- `title TEXT NOT NULL DEFAULT ''`
- `source_url TEXT NULL`
- `generated_text TEXT NULL`
- `final_text TEXT NOT NULL`
- `created_utc TEXT NOT NULL`
- `updated_utc TEXT NOT NULL`

### `tags` and `entry_tags`

- Tags must be normalized and deduplicated in application code.
- Tag associations must be replaced on every create or update.

### `entry_links`

- Stores additional websites related to an entry.
- Each saved link must include `url`, `note`, and `created_utc`.
- Link associations must be replaced on every create or update.

### `entries_fts`

- FTS5 virtual table indexing `entries.final_text` only.
- Must be rebuilt during initialization.
- Must be kept in sync with SQLite triggers on `entries`.

### `embedding_index_meta`

- Stores the current embedding model id and vector dimensions.

### `entry_embeddings`

- Must be created only when sqlite-vec is available and embedding metadata exists.
- Must store embeddings derived from `entries.final_text` only.

## Derived and validation rules

- `sort_key` must be computed as `YYYYMMDD`, using `00` when day is missing.
- `title` is required on create and update.
- `final_text` is required on create and update.
- `group_id` is required on create and update.
- `generated_text` is optional.
- `source_url` is optional.
- `source_url` must be a valid `http` or `https` URL when provided.
- `event_year` must be between `1900` and `2100`.
- `event_month` must be between `1` and `12`.
- `event_day` is optional, but when present it must be between `1` and `31`.
- Each additional link row is optional, but if either field is filled then both are required.
- Additional links must use valid `http` or `https` URLs.
- Additional links must include a non-empty note.
- Tags must be entered as comma-separated text, then normalized and deduplicated on save.
- Extracted article text must be transient and must not be persisted.
- Embeddings and FTS content are derived data, not primary source data.

## Route requirements

### `GET /`

The root page is the main timeline experience and the primary visualization surface.

Required behavior:

- Accept optional `q` and `group_id` query parameters.
- When `group_id` is omitted, default to the current default timeline group.
- Treat `group_id=all` as cross-group scope.
- With no `q`, render the full timeline for the selected scope.
- With `q`, render a filtered timeline based on ranked matching entry ids.
- Keep filtered results in timeline order, not ranked order.
- Render month-grouped detail sections.
- Expose enough client-side state for switching among `Details`, `Summaries`, `Months`, and `Years`.

### Timeline data endpoints

- `GET /timeline/details` must return paginated detail HTML for the current scope.
- `GET /timeline/summaries` must return summary-group HTML for the current scope, optionally narrowed by year and month.
- `GET /timeline/months` must return month-bucket HTML, optionally narrowed by year.
- `GET /timeline/years` must return year-bucket HTML.

### `GET /search`

The ranked search page is a separate search experience from timeline filtering.

Required behavior:

- Accept optional `q` and `group_id` query parameters.
- Use the same default-group and `All groups` scoping rules as `/`.
- Return ranked search cards instead of timeline sections.
- Show no results when there is no query.

### `GET /search/results`

- Must return paginated ranked result HTML for the current scope.

### `GET /visualization`

- Must remain as a backward-compatibility route.
- Must redirect to `/` with HTTP status `307`.

### `GET /entries/new`

- Must render a blank entry form.
- Must preselect the first available timeline group.

### `POST /entries/new`

- Must validate all entry form fields.
- Must reject non-existent timeline groups.
- Must save the entry, tags, and additional links when valid.
- Must attempt embedding sync without blocking the save path.
- Must redirect to `/entries/{id}/view` with HTTP status `303` after success.

### `GET /entries/{id}`

- Must render the edit form for an existing entry.
- Must return `404` when the entry does not exist.

### `POST /entries/{id}`

- Must revalidate all form rules.
- Must reject non-existent timeline groups.
- Must update the entry, tags, and additional links when valid.
- Must attempt embedding sync without blocking the save path.
- Must redirect to `/entries/{id}/view` with HTTP status `303` after success.

### `GET /entries/{id}/view`

- Must render a read-only event details page.
- Must show the saved final content, date, group name, tags, source URL, and additional links.
- Must return `404` when the entry does not exist.

### `POST /entries/generate`

Required behavior:

- Accept `title`, `source_url`, and current `generated_text`.
- Require at least one of `title` or `source_url`.
- Attempt source extraction when `source_url` is provided.
- If extraction fails and `title` exists, fall back to title-only generation.
- If extraction fails and no title is present, return a partial with an error.
- Return a server-rendered partial, not JSON.
- Never save the generated result automatically.
- Return suggested metadata alongside generated HTML.

Error handling requirements:

- Configuration or validation problems must return a partial with HTTP `400`.
- Provider errors must return a partial with HTTP `502`.
- Unexpected failures must return a partial with HTTP `500`.

### `POST /entries/preview-html`

- Must sanitize arbitrary HTML and return the preview partial used by the form.

### `GET /entries/export`

- Must return all entries as downloadable JSON.
- Must include a top-level `count` and `entries` array.
- Must include tags and additional links in each exported entry.
- Must use a timestamped filename prefixed with `EventTracker-export-`.

### `GET /admin/groups`

- Must render timeline-group administration.
- Must show entry counts.
- Must show create, rename, and delete notices when redirected with a success flag.

### `POST /admin/groups`

- Must create a new group when the normalized name is non-empty and unique.
- Must optionally allow the new group to become the default group.
- Must return inline validation errors when invalid.

### `POST /admin/groups/{group_id}`

- Must rename an existing group.
- Must update the optional web search query.
- Must optionally set or clear the default-group flag.
- Must clear cached web search results when the query changes.
- Must return inline validation errors when invalid.
- Must return `404` when the group does not exist.

### `POST /admin/groups/{group_id}/delete`

- Must allow deletion only when the group is not default and has no entries.
- Must return inline validation errors when deletion is not allowed.
- Must return `404` when the group does not exist.

### Group web search endpoints

- `GET /timeline/group-web-search`
- `GET /timeline/group-web-search/stream`
- `POST /timeline/group-web-search/refresh`

Required behavior:

- Require a concrete selected group.
- Return a disabled payload when the group has no web search query.
- Return a disabled payload when the active AI provider is not Copilot.
- Use cached results when possible.
- Support force-refresh.
- Support streaming progress and result events over Server-Sent Events.

### `GET /dev/extract`

- Must remain available as a developer utility.
- Must accept `source_url` and return extraction output for debugging.

## UI requirements

### Global navigation

- The navbar must include a link to `/`, a query field, a `Filter` action for timeline filtering, a `Search` action for ranked results, an `Admin` link, and a `New Entry` link.
- When a timeline group is selected, navbar actions must preserve the current `group_id`.

### Timeline page

Required behavior:

- Show group filter chips, including `All groups`.
- Show filtered-state copy and match counts when `q` is active.
- Show `Details`, `Summaries`, `Months`, and `Years` view toggles.
- Support drill-down from years to months and months to summaries.
- Support returning from summaries to detailed entries.
- Show `View` and `Edit` actions for entries.
- Show entry titles, sanitized final rich text, tags, group badge, source URL, and additional-link cues when present.
- Show a clear empty state when there are no entries or no matches.
- Show a Copilot-backed `On the web` panel only when the selected group has a stored web search query.

### Ranked search page

Required behavior:

- Keep ranked results distinct from the timeline filter experience.
- Show highlighted snippets and entry metadata.
- Support incremental result loading through `/search/results`.
- Show an empty state when there is no query or no matches.

### Entry form

Required behavior:

- Render inline validation errors.
- Support group selection.
- Support additional link rows with per-row validation.
- Render generated draft content and rendered preview side by side.
- Let the user request generation without saving.
- Let the user apply generated content into `final_text`.
- Render sanitized preview HTML as the user edits.
- Clarify that only the source URL is stored and fetched article text is transient.

### Entry detail page

- Must provide a read-only presentation of the saved event.
- Must show additional links and notes when present.
- Must provide a clear path back to editing.

### Group administration page

- Must list groups with entry counts.
- Must prevent deletion of default groups and groups that still contain entries.
- Must support editing name, default status, and web search query inline.

## Search requirements

### Keyword search

- Full-text search must operate on `entries.final_text` only.
- Queries must be tokenized and converted into a quoted FTS5 query string.
- Ranked results must use `bm25(entries_fts)`.
- Search snippets must be sanitized before rendering.

### Semantic search

- Semantic search must be optional.
- It must run only when sqlite-vec is available and embedding configuration exists.
- Query embeddings and entry embeddings must use the OpenAI embeddings configuration.
- Ranked results must combine keyword and semantic matches using reciprocal rank fusion.

### Timeline filtering

- Timeline filtering must reuse ranked match ids from the search service.
- Matching entries must be sorted back into timeline order before display.

## AI generation requirements

### Providers

- `EVENTTRACKER_AI_PROVIDER` must support `openai` and `copilot`.
- `openai` is the default provider.

### OpenAI mode

- Must use `AsyncOpenAI`.
- Must require `OPENAI_API_KEY` and `OPENAI_CHAT_MODEL_ID`.
- May use `OPENAI_BASE_URL` for OpenAI-compatible providers.

### Copilot mode

- Must use `github-copilot-sdk`.
- Must default `COPILOT_CHAT_MODEL_ID` to `gpt-5`.
- May accept `COPILOT_CLI_PATH` and `COPILOT_CLI_URL` overrides.
- Must also power group web search.

### Response contract

- Providers must be prompted to return JSON with `title`, `draft_html`, `event_year`, `event_month`, and `event_day`.
- The app must normalize and validate the response.
- The app must reject empty titles or empty draft payloads.
- Generated HTML must be limited to the app's safe rich-text subset when rendered.

## Rich text and sanitization requirements

Allowed rendered tags:

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

Additional rules:

- Search snippets may also render `mark`.
- Rich text must be sanitized before timeline, search, preview, and detail rendering.

## Import and export requirements

### Export

- Export must serialize the full entry payload used by the app, including tags and additional links.
- Export must omit display-only derived fields that are not part of the persisted payload contract.

### Import

- Import must accept either legacy HTML list input or exported JSON.
- Import must skip exact duplicates by default and allow them when explicitly requested.
- Import must insert rows without embedding generation.
- Imported entries currently target group id `1`, which corresponds to the seeded default group in a fresh database.

## Operational requirements

- The development server must run through `scripts.run_dev` and support `--reload`.
- `.env` must be loaded with override semantics by the run and import scripts.
- The app must remain usable when embeddings are unconfigured.
- The app must remain usable when AI generation is unconfigured.

## Current non-goals

The current repository does not implement:

- multi-user accounts or authentication
- cloud sync or remote persistence
- background jobs or worker processes
- a SPA frontend or API-first client architecture
- automatic persistence of extracted source article text
- semantic-only search without the existing SQLite application model