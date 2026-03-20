# FR-002 Timeline Browsing

- Category: Functional
- Status: Baseline
- Scope: Root timeline behavior, scoping, timeline views, drill-down, and progressive loading.
- Primary Sources: `PRODUCT_OVERVIEW.md`, `app/main.py`, `app/services/entries.py`, `app/templates/timeline.html`, `app/templates/partials/timeline_*.html`, `tests/test_smoke.py`, `tests/e2e/test_core_workflows.py`

## Requirement Statements

- FR-002-01 The root route `/` shall serve as the primary timeline experience.
- FR-002-02 The timeline shall accept optional `q` and `group_id` query parameters.
- FR-002-03 The timeline shall default to the current default timeline group when `group_id` is omitted.
- FR-002-04 The timeline shall treat `group_id=all` as cross-group scope.
- FR-002-05 The timeline shall show entries newest first by `sort_key DESC`, then `updated_utc DESC`, then `id DESC`.
- FR-002-06 The timeline shall expose four user-selectable views: `Details`, `Summaries`, `Months`, and `Years`.
- FR-002-07 The timeline shall support drill-down from years to months and from months to summary or event views within the current scope.
- FR-002-08 The timeline shall provide server-rendered pagination for detailed-entry loading through `/timeline/details`.
- FR-002-09 The timeline shall return server-rendered partial payloads for detail, summary, month, and year views through dedicated timeline endpoints.
- FR-002-10 The timeline shall show clear empty states when no entries exist in the selected scope or when the active filter yields no matches.
- FR-002-11 The timeline shall expose `View` and `Edit` entry actions from the detailed timeline presentation.
- FR-002-12 The non-detail views shall expose replay and playback controls for the current summarized scope.

## Acceptance Notes

- The detailed timeline is grouped into month sections for presentation.
- Client-side JavaScript switches among pre-defined server-backed views rather than constructing timeline data independently in the browser.
- Timeline Story Mode launch points are part of the current timeline browsing surface.