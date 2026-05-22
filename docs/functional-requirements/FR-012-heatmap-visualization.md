# FR-012 Heatmap Visualization

- Category: Functional
- Status: Baseline
- Scope: Calendar-based heatmap visualization of entry density by day, year-level filtering, and click-through to day-scoped entries.
- Primary Sources: `app/main.py`, `app/services/entries.py`, `app/templates/timeline.html`, `tests/test_heatmap_api.py`

## Requirement Statements

- FR-012-01 The system shall provide a heatmap view alongside the existing timeline views (Details, Summaries, Months, Years).
- FR-012-02 The system shall expose `/api/heatmap` to return entry density counts by day for a specified year and optional group.
- FR-012-03 The system shall return heatmap counts as a dictionary mapping "YYYY-MM-DD" formatted date strings to entry counts for that day.
- FR-012-04 The system shall include year-level availability data in the heatmap response so users can navigate to years with entries.
- FR-012-05 The system shall default to the current year when no year is specified in the heatmap request.
- FR-012-06 The system shall respect the selected timeline group scope when rendering heatmap data, filtering to group entries when a specific group is selected.
- FR-012-07 The system shall provide a `/timeline/heatmap/entries` endpoint to return a server-rendered HTML list of entries for a specific date (year, month, day).
- FR-012-08 The system shall accept an optional `group_id` parameter for the heatmap-entries endpoint to scope entries to a single group.
- FR-012-09 The system shall include month-only and day-only entries when retrieving entries for day `1` of a month (combining exact-day and month-scoped entries).
- FR-012-10 The system shall render entry detail cards for the date with date label (e.g., "January 1, 2025") in the heatmap-entries view.
- FR-012-11 The system shall display an empty state when a requested date has no entries.

## Acceptance Notes

- The heatmap view is included in the client-side view-switching UI alongside Details, Summaries, Months, and Years views.
- Heatmap colors typically reflect entry density, with darker colors indicating higher entry counts.
- The heatmap visualization is rendered on the client side using calendar/heatmap visualization libraries.
- Day-level filtering for entries respects the same group scoping rules as the timeline view.
