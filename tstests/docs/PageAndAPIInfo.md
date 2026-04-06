# EventTracker Page And API Info

This project is a server-rendered FastAPI app. The main route definitions are in [app/main.py](../../app/main.py), the HTML pages are rendered from [app/templates](../../app/templates), and shared styling is served from [app/static/styles.css](../../app/static/styles.css).

## How the app is organized

- FastAPI app setup and static mount live in [app/main.py#L127](../../app/main.py#L127).
- All HTTP routes are defined in [app/main.py](../../app/main.py).
- Primary full-page templates live in [app/templates](../../app/templates).
- Partial templates used by client-side fetch flows live in [app/templates/partials](../../app/templates/partials).
- The main navigation that exposes the core pages is in [app/templates/base.html#L31](../../app/templates/base.html#L31).

## Full browser pages worth covering with Playwright

### 1. Timeline landing page

- URL: `/`
- Handler: [app/main.py#L557](../../app/main.py#L557)
- Template: [app/templates/timeline.html](../../app/templates/timeline.html)
- Purpose: Main landing page. Shows timeline entries, group filter flyout, story-mode link, optional tag cluster link, and the multi-view timeline UI.
- Important interactions:
  - Group switching from the flyout.
  - Search/filter state via `q` and `group_id` query parameters.
  - View switching between Details, Summaries, Months, and Years.
  - Infinite loading of detail cards.
  - Optional “Recent Developments” panel for Copilot-backed group web search.
- Supporting partials:
  - [app/templates/partials/timeline_detail_groups.html](../../app/templates/partials/timeline_detail_groups.html)
  - [app/templates/partials/timeline_summary_groups.html](../../app/templates/partials/timeline_summary_groups.html)
  - [app/templates/partials/timeline_bucket_cards.html](../../app/templates/partials/timeline_bucket_cards.html)
  - [app/templates/partials/entry_card.html](../../app/templates/partials/entry_card.html)

### 2. Search page

- URL: `/search`
- Handler: [app/main.py#L1040](../../app/main.py#L1040)
- Template: [app/templates/search.html](../../app/templates/search.html)
- Purpose: Ranked keyword and semantic search across entries, optionally scoped to a group.
- Important interactions:
  - Query parameter `q`.
  - Optional group scoping with `group_id`.
  - “Create story”, “Filter Timeline”, and “Clear search” actions.
  - Incremental result loading via fetch.
- Supporting partials:
  - [app/templates/partials/search_results.html](../../app/templates/partials/search_results.html)

### 3. Story mode page

- URL: `/story`
- Handler: [app/main.py#L1134](../../app/main.py#L1134)
- Template: [app/templates/story.html](../../app/templates/story.html)
- Purpose: Generate a narrative from the currently selected scope.
- Important interactions:
  - Scope controls: group, search query, year, month.
  - Format selection.
  - “Generate story” submit flow.
  - “Save snapshot” flow after generation.
  - Reset scope and reset result flows.
- Related page states:
  - Empty-scope warning when no entries match.
  - Validation/configuration/provider failures rendered back into the same page.

### 4. Saved story detail page

- URL: `/story/{story_id}`
- Handler: [app/main.py#L1400](../../app/main.py#L1400)
- Template: [app/templates/story.html](../../app/templates/story.html)
- Purpose: Read-only view of a saved story snapshot using the same template as story generation.
- Important interactions:
  - Verify saved badge/state.
  - Citation rendering.
  - Scope metadata display.

### 5. Topic graph page

- URL: `/groups/{group_id}/topics/graph`
- Handler: [app/main.py#L1507](../../app/main.py#L1507)
- Template: [app/templates/topic_graph.html](../../app/templates/topic_graph.html)
- Purpose: Visualize tag clusters for a specific group.
- Important interactions:
  - Graph loads client-side from `/api/groups/{group_id}/topics`.
  - Empty or error rendering when graph data cannot be loaded.
  - Back-to-timeline navigation.

### 6. New entry page

- URL: `/entries/new`
- Handler: [app/main.py#L1569](../../app/main.py#L1569)
- Template: [app/templates/entry_form.html](../../app/templates/entry_form.html)
- Purpose: Create a new timeline entry.
- Important interactions:
  - Required form validation.
  - Additional links rows.
  - AI-assisted summary generation.
  - Live HTML preview.
  - CSRF-protected form submission.
- Supporting partials:
  - [app/templates/partials/generated_preview.html](../../app/templates/partials/generated_preview.html)
  - [app/templates/partials/html_preview_content.html](../../app/templates/partials/html_preview_content.html)

### 7. Entry detail page

- URL: `/entries/{entry_id}/view`
- Handler: [app/main.py#L1591](../../app/main.py#L1591)
- Template: [app/templates/entry_detail.html](../../app/templates/entry_detail.html)
- Purpose: Read-only view of a single entry, including tags, summary HTML, and links.
- Important interactions:
  - Back to timeline.
  - Edit action.

### 8. Edit entry page

- URL: `/entries/{entry_id}`
- Handler: [app/main.py#L1661](../../app/main.py#L1661)
- Template: [app/templates/entry_form.html](../../app/templates/entry_form.html)
- Purpose: Edit an existing entry using the same template as create.
- Important interactions:
  - Pre-populated values.
  - Validation errors.
  - Duplicate source URL handling.
  - Save redirect back to entry detail.

### 9. Admin groups page

- URL: `/admin/groups`
- Handler: [app/main.py#L1737](../../app/main.py#L1737)
- Template: [app/templates/admin_groups.html](../../app/templates/admin_groups.html)
- Purpose: Manage timeline groups.
- Important interactions:
  - Create group.
  - Rename group.
  - Change default group.
  - Delete empty non-default groups.
  - Success notices rendered with `role="status"`.

### 10. Visualization redirect

- URL: `/visualization`
- Handler: [app/main.py#L1492](../../app/main.py#L1492)
- Purpose: Redirect-only compatibility route back to `/`.
- Playwright value: Low, but worth one basic redirect assertion if legacy links matter.

## API and helper endpoints that drive page behavior

These are not standalone pages, but they are important for E2E coverage because the visible UI depends on them.

### Timeline fetch endpoints

- `/timeline/details` -> [app/main.py#L923](../../app/main.py#L923)
  - Returns JSON with rendered HTML from [app/templates/partials/timeline_detail_groups.html](../../app/templates/partials/timeline_detail_groups.html).
  - Used for lazy-loading more timeline cards on the main page.

- `/timeline/years` -> [app/main.py#L954](../../app/main.py#L954)
  - Returns JSON with rendered HTML from [app/templates/partials/timeline_bucket_cards.html](../../app/templates/partials/timeline_bucket_cards.html).
  - Used when the timeline switches to Years view.

- `/timeline/months` -> [app/main.py#L980](../../app/main.py#L980)
  - Returns JSON with the same bucket-card partial.
  - Used when drilling from Years to Months.

- `/timeline/summaries` -> [app/main.py#L1010](../../app/main.py#L1010)
  - Returns JSON with rendered HTML from [app/templates/partials/timeline_summary_groups.html](../../app/templates/partials/timeline_summary_groups.html).
  - Used for the Summaries view and playback mode.

### Search fetch endpoint

- `/search/results` -> [app/main.py#L1094](../../app/main.py#L1094)
  - Returns JSON with rendered HTML from [app/templates/partials/search_results.html](../../app/templates/partials/search_results.html).
  - Used for incremental search pagination.

### Story form actions

- `POST /story/generate` -> [app/main.py#L1178](../../app/main.py#L1178)
  - Re-renders [app/templates/story.html](../../app/templates/story.html) with generated output or an error state.

- `POST /story/save` -> [app/main.py#L1310](../../app/main.py#L1310)
  - Saves a story and redirects to `/story/{story_id}`.

### Entry form helper endpoints

- `POST /entries/generate` -> [app/main.py#L1879](../../app/main.py#L1879)
  - Returns the partial [app/templates/partials/generated_preview.html](../../app/templates/partials/generated_preview.html).
  - Used by the “Generate” button on the entry form.

- `POST /entries/preview-html` -> [app/main.py#L2006](../../app/main.py#L2006)
  - Returns the partial [app/templates/partials/html_preview_content.html](../../app/templates/partials/html_preview_content.html).
  - Used by the live preview on the entry form.

### Group web search endpoints

- `GET /timeline/group-web-search` -> [app/main.py#L605](../../app/main.py#L605)
  - Returns JSON results or a disabled/error payload.

- `GET /timeline/group-web-search/stream` -> [app/main.py#L698](../../app/main.py#L698)
  - Server-sent events endpoint used by the “Recent Developments” panel.
  - Good candidate for mocked or provider-specific E2E coverage.

- `POST /timeline/group-web-search/refresh` -> [app/main.py#L829](../../app/main.py#L829)
  - Forces a refreshed web-search result set.

### Topic graph data endpoint

- `GET /api/groups/{group_id}/topics` -> [app/main.py#L1497](../../app/main.py#L1497)
  - Returns JSON graph data consumed by [app/templates/topic_graph.html](../../app/templates/topic_graph.html).

### Export and dev helper endpoints

- `GET /entries/export` -> [app/main.py#L1529](../../app/main.py#L1529)
  - Returns exported JSON with an attachment header.
  - Better suited to download/assertion testing than DOM testing.

- `GET /dev/extract` -> [app/main.py#L2022](../../app/main.py#L2022)
  - Dev-only JSON extractor endpoint.
  - Probably not a core user journey, but useful for diagnostics.

## Page definitions by template

- [app/templates/base.html](../../app/templates/base.html): shared shell and navigation.
- [app/templates/timeline.html](../../app/templates/timeline.html): timeline landing experience.
- [app/templates/search.html](../../app/templates/search.html): ranked search page.
- [app/templates/story.html](../../app/templates/story.html): story generation and saved story display.
- [app/templates/topic_graph.html](../../app/templates/topic_graph.html): topic cluster visualization.
- [app/templates/entry_form.html](../../app/templates/entry_form.html): new and edit entry flows.
- [app/templates/entry_detail.html](../../app/templates/entry_detail.html): single entry display.
- [app/templates/admin_groups.html](../../app/templates/admin_groups.html): group administration.

## Good first Playwright coverage candidates

If the goal is onboarding plus fast value, these pages are the highest-yield starting points:

1. Timeline page `/` because it is the landing page and contains the richest client behavior.
2. New entry page `/entries/new` because it covers validation, generation, preview, and the create flow.
3. Entry detail and edit pages because they validate the create-to-read-to-update journey.
4. Admin groups page `/admin/groups` because it has CRUD behavior and stable selectors/forms.
5. Search page `/search` because it validates query scoping and pagination.
6. Story page `/story` because it exercises the AI-assisted narrative flow.
7. Topic graph `/groups/{id}/topics/graph` because it depends on a separate API payload and error handling.

## Notes for test design

- There is no separate SPA router. Most user-visible pages are server-rendered from [app/main.py](../../app/main.py).
- Several “sub-views” on the timeline are not separate URLs; they are fetch-driven states within `/`.
- The entry form and story page reuse templates across create/edit and generated/saved states, so test assertions should key off visible state, form action, and URL.
- The group web search feature is provider-dependent, so those tests may need mocking or environment-aware expectations.