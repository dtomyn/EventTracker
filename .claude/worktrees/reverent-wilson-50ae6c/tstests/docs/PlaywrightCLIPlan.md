# Playwright CLI E2E Plan

## Goal

Create an initial Playwright end-to-end test suite for the running EventTracker app that validates the core user journeys with stable, observable UI behavior before expanding into provider-dependent AI flows.

This plan is based on:

- the route and API inventory in `tstests/docs/PageAndAPIInfo.md`
- live inspection of `http://127.0.0.1:35231`
- existing repository notes about the Playwright harness and Windows behavior

## Confirmed core functionality

The integrated browser confirmed these core product behaviors:

1. The landing page at `/` is the main application hub.
   - It exposes the global navigation, shared search/filter input, group context, story-mode entry point, tag-cluster entry point, and timeline view controls.
2. The timeline page supports multiple in-page views.
   - `Details`, `Summaries`, `Months`, and `Years` are presented as button-driven states on the same route.
   - Switching to `Months` loads bucket cards with drill-in buttons and `Start story` links.
3. Search is driven from the shared navbar input and lands on `/search`.
   - A real query produced ranked results, highlighted matches, and the `Create story`, `Filter Timeline`, and `Clear search` actions described in the page inventory.
4. Entry creation is a first-class workflow.
   - `/entries/new` includes labeled fields for group, date parts, title, source URL, additional links, summary generation, live preview, tags, cancel, and save.
5. Group administration is a stable CRUD-style surface.
   - `/admin/groups` exposes create-group controls, rename/default controls, visible entry counts, and save/delete buttons with clear disabled states.

## Initial scope recommendation

Start with deterministic browser journeys that do not require mocking LLM providers or SSE behavior:

- timeline browsing and view switching
- search and search-driven navigation
- entry lifecycle from create to read to update

Defer these to a second wave:

- `Recent Developments` web-search streaming
- story generation success/failure paths tied to provider configuration
- topic graph empty/error-state mocking
- export/download assertions

## Three actionable test scenarios

### Scenario 1: Timeline navigation and view switching

**Why this matters**

The timeline is the landing experience and the richest client-side page. It validates that the app shell, group context, and fetch-backed timeline views work together.

**Primary routes**

- `GET /`
- `GET /timeline/months`
- `GET /timeline/years`
- `GET /timeline/summaries`

**Suggested flow**

1. Open `/`.
2. Assert the page title, main heading, and current group label are visible.
3. Assert the timeline view controls are present and `Details` is active by default.
4. Switch to `Months` and assert the current-view label changes to `Months`.
5. Assert at least one month bucket card is rendered with an `Open ...` drill-in button and a `Start story` link.
6. Switch back to `Details` or to `Summaries` and assert the visible view state changes again.

**Key assertions**

- Navigation shell is present: `Events`, `Export`, `Admin`, `New Entry`.
- Timeline heading reflects the active group.
- View buttons toggle pressed state correctly.
- The main timeline region updates after the view change.
- The month bucket view renders event counts and story-entry links.

**Implementation notes for later**

- Prefer role-based selectors for the view buttons and visible text for the current-view summary.
- Wait on the timeline region content change rather than arbitrary sleeps.
- Keep this scenario read-only.

### Scenario 2: Search query and result navigation

**Why this matters**

Search is a core retrieval workflow and reuses the shared navbar input, so it also validates the global shell behavior.

**Primary routes**

- `GET /search`
- `GET /search/results`
- `GET /?group_id=...&q=...`
- `GET /story?group_id=...&q=...`

**Suggested flow**

1. Open `/search`.
2. Assert the empty-state message is shown when there is no query.
3. Enter a real query in the navbar search box and trigger `Search`.
4. Assert the URL includes `q=` and the results summary reflects the searched term.
5. Assert at least one result card appears with `View` and `Edit` actions.
6. Assert the result page exposes `Create story`, `Filter Timeline`, and `Clear search` actions.
7. Follow `Filter Timeline` and assert the filtered timeline route preserves the query string.

**Key assertions**

- Empty-state text renders before searching.
- Query state is preserved in the URL.
- Ranked results render with visible highlights or excerpts.
- Search-to-timeline handoff works through `Filter Timeline`.

**Implementation notes for later**

- Use a query that already returns seeded results in the current dataset. `Copilot` worked during live inspection.
- Favor assertions on visible result summary text over exact result counts if the seed data may evolve.

### Scenario 3: Entry lifecycle from create to read to edit

**Why this matters**

This is the highest-value state-changing workflow. It validates form input, save behavior, redirect behavior, detail rendering, and edit persistence in one scenario.

**Primary routes**

- `GET /entries/new`
- `POST /entries`
- `GET /entries/{entry_id}/view`
- `GET /entries/{entry_id}`
- `POST /entries/{entry_id}`

**Suggested flow**

1. Open `/entries/new`.
2. Fill the required fields with unique test data.
3. Add one additional link row with a note.
4. Enter tags and a simple summary directly rather than depending on AI generation.
5. Save the entry.
6. Assert redirect to the entry detail page and verify the title, date, tags, and link are visible.
7. Click `Edit`.
8. Change the title or tags and save again.
9. Assert the detail page reflects the updated value.

**Key assertions**

- Required fields accept valid values and submit successfully.
- Save redirects to `/entries/{id}/view`.
- Entry detail shows the newly created content.
- Edit updates persist after the second save.

**Implementation notes for later**

- Use unique titles to avoid collisions with seeded content.
- Do not depend on `/entries/generate` for the first-pass suite.
- Include CSRF-aware submission expectations because the app protects entry POST routes.
- Cleanup can be skipped if the existing test harness isolates each run with a temporary database copy.

## Recommended order of implementation

1. Scenario 1: fastest read-only confidence on the main page.
2. Scenario 2: validates shared search behavior and cross-page navigation.
3. Scenario 3: exercises the main write path and gives the broadest regression coverage.

## Test data and harness guidance

- Use the TypeScript Playwright scaffold under `tstests/e2e` noted in repository memory.
- Prefer isolated database-backed runs rather than reusing the long-running dev session for eventual automated execution.
- On Windows, keep in mind the repository note that Playwright collection/runtime has been more reliable under Python 3.12 than Python 3.14 for the existing Python harness.
- Use deterministic seeded data for search assertions and unique generated titles for create/edit assertions.

## Out of scope for the first pass

- AI story generation success cases
- Recent Developments SSE refresh flows
- topic graph API mocking and graph rendering assertions
- export/download verification
- admin group mutation coverage

Those are valid next targets, but they are less suitable for the first three CLI-authored scenarios than the flows above.