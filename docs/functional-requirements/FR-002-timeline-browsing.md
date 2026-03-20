# FR-002 Timeline Browsing

- Category: Functional
- Status: Baseline
- Scope: Root timeline behavior, scoping, timeline views, drill-down, and progressive loading.
- Primary Sources: `README.md`, `app/main.py`, `app/services/entries.py`, `app/templates/timeline.html`, `app/templates/partials/timeline_*.html`, `tests/test_smoke.py`, `tests/e2e/test_core_workflows.py`

## Requirement Statements

- FR-002-01 The system shall serve the root route `/` as the primary timeline experience.
- FR-002-02 The system shall accept optional `q` and `group_id` query parameters for the timeline.
- FR-002-03 The system shall default the timeline to the current default timeline group when `group_id` is omitted.
- FR-002-04 The system shall treat `group_id=all` as cross-group scope in the timeline.
- FR-002-05 The system shall show timeline entries newest first by `sort_key DESC`, then `updated_utc DESC`, then `id DESC`.
- FR-002-06 The system shall expose four user-selectable timeline views: `Details`, `Summaries`, `Months`, and `Years`.
- FR-002-07 The system shall support timeline drill-down from years to months and from months to summary or event views within the current scope.
- FR-002-08 The system shall provide server-rendered pagination for detailed timeline entry loading through `/timeline/details`.
- FR-002-09 The system shall return server-rendered partial payloads for detail, summary, month, and year timeline views through dedicated timeline endpoints.
- FR-002-10 The system shall show clear empty states in the timeline when no entries exist in the selected scope or when the active filter yields no matches.
- FR-002-11 The system shall expose `View` and `Edit` entry actions from the detailed timeline presentation.
- FR-002-12 The system shall expose replay and playback controls for the current summarized scope in non-detail timeline views.
- FR-002-13 The system shall render the full selected scope in the root timeline instead of ranked search results when no `q` query is provided.
- FR-002-14 The system shall redirect `GET /visualization` to `/` with HTTP status `307` for backward compatibility.

## Acceptance Notes

- The detailed timeline is grouped into month sections for presentation.
- Client-side JavaScript switches among pre-defined server-backed views rather than constructing timeline data independently in the browser.
- Timeline Story Mode launch points are part of the current timeline browsing surface.