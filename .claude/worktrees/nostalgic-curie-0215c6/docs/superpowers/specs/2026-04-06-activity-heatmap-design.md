# Activity Heatmap — Design Spec

## Overview

Add a GitHub-style activity heatmap as a fifth visualization tab in the timeline toolbar. The heatmap displays entry density across a calendar year using event dates, with hover tooltips and click-to-filter interactivity. Rendered client-side with D3.js, data served via a new JSON API endpoint.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Date basis | Event date (`event_year`, `event_month`, `event_day`) | Shows when tracked events occurred, not when they were logged — more meaningful for a timeline app |
| Day-less entries | Distribute evenly across month days | Entries without `event_day` are round-robin assigned to days within their month to fill the grid naturally |
| Location | Fifth tab in visualization toolbar | Keeps all views unified in one place alongside Details, Summaries, Months, Years |
| Layout | GitHub-style horizontal grid | 53 week-columns x 7 day-rows per year. Instantly recognizable, high visual impact |
| Rendering | D3.js v7 + JSON API | Consistent with existing topic graph pattern. Clean data/presentation separation |
| Interaction | Tooltip on hover, click filters entries below | Turns the heatmap into a navigation tool, not just a passive visual |

## Data Layer

### Service function

New function in `app/services/entries.py`:

```python
def get_heatmap_counts(
    connection: sqlite3.Connection,
    year: int,
    group_id: int | None = None,
) -> HeatmapData:
```

**Query:** Select `event_year`, `event_month`, `event_day`, and count from the `entries` table, filtered by year and optional group_id. Group by (year, month, day).

**Day-less entry distribution:** For entries where `event_day IS NULL`, distribute them across the month using round-robin assignment. For example, 3 entries in March with no day get assigned to days 1, 11, and 21 (evenly spaced). This keeps the grid populated without clustering on day 1.

**Return type:** A dataclass (consistent with existing models in `app/models.py`):

```python
@dataclasses.dataclass
class HeatmapData:
    counts: dict[str, int]       # "YYYY-MM-DD" -> entry count
    total: int                   # total entries for the year
    year: int                    # displayed year
    years_available: list[int]   # all years with entries (for navigation)
```

### API endpoint

```
GET /api/heatmap?group_id={id}&year={year}
```

- `group_id` — optional, filters to a specific timeline group. Omit for all groups.
- `year` — required, the calendar year to display. Defaults to the most recent year with entries.

**Response:**

```json
{
  "counts": {"2025-01-15": 2, "2025-03-12": 3},
  "total": 142,
  "year": 2025,
  "years_available": [2023, 2024, 2025]
}
```

### Filtered entries endpoint

When a user clicks a heatmap cell, fetch entries for that date. A new endpoint returns an HTML partial of entry cards:

```
GET /timeline/heatmap/entries?group_id={id}&year={y}&month={m}&day={d}
```

This returns an HTML partial (entry cards) that gets inserted below the heatmap. The route handler filters entries by the exact date and renders them using the existing entry card partial template.

## Visualization (D3.js)

### Grid structure

- **Columns:** 53 (weeks in a year, with partial weeks at start/end)
- **Rows:** 7 (Monday through Sunday)
- **Cell size:** ~10px square with 2px gap, rounded corners (rx=2)
- **Total SVG width:** ~580px for the grid area, responsive within the container
- **Month labels:** Positioned along the top edge at the start of each month's first week
- **Day-of-week labels:** Mon, Wed, Fri on the left side (every other day, like GitHub)

### Color scale

5-step cyan gradient using the app's existing CSS custom properties:

| Level | Light mode | Dark mode | Meaning |
|-------|-----------|-----------|---------|
| 0 (empty) | `--et-surface-bg` (#f8fafc) | Card bg (#1e293b) | No entries |
| 1 | `--et-primary-bg` (rgba cyan 0.08) | #164e63 | 1 entry |
| 2 | `--et-primary` at 40% opacity | #0e7490 | 2 entries |
| 3 | `--et-primary` at 70% opacity | #0891b2 | 3-4 entries |
| 4 | `--et-primary` full | #06b6d4 | 5+ entries |

Scale thresholds are computed dynamically based on the max count in the dataset using `d3.scaleQuantile()` with 5 buckets. The fixed examples above are for a typical distribution.

Colors are read from CSS custom properties via `getComputedStyle()` so the heatmap automatically adapts when the user toggles dark/light mode (matching the pattern in `topic_graph.html`).

### Legend

"Less / More" label with 5 colored squares, positioned bottom-right of the heatmap grid. Same pattern as GitHub's contribution graph legend.

### Summary stat

Text line below the grid: "{total} events in {year}" in `--et-text-secondary` color.

## Interactions

### Hover tooltip

- Appears on `mouseenter` for any cell
- Content: "{count} entries on {formatted date}" (e.g., "3 entries on March 12, 2025")
- For zero-count cells: "No entries on {date}"
- Style: dark card (`--et-card-bg` / `#334155` in dark mode) with subtle border, positioned above the cell
- Dismisses on `mouseleave`

### Click to filter

- Clicking a cell with entries > 0 fetches the HTML partial from `/timeline/heatmap/entries`
- The response (entry cards) is inserted into a container div below the heatmap
- A header shows the selected date and entry count
- Clicking the same cell again (or a "clear" button) removes the filter
- Clicking a different cell replaces the current filter
- The selected cell gets a visible highlight ring (`--et-primary` border)

### Year navigation

- Left/right arrow buttons flanking the year label (e.g., "< 2025 >")
- Clicking fetches new heatmap data for the adjacent year via the API
- Arrows are disabled/hidden when at the boundary of `years_available`
- Smooth transition: old grid fades out, new grid fades in (CSS opacity transition)

### Group filtering

- The heatmap respects the existing group selector dropdown in the timeline hero section
- When the group changes, the heatmap re-fetches data for the new group
- This uses the same group_id parameter already passed to other visualization views

## Template Integration

### Toolbar button

Add a "Heatmap" button to the segmented controls in `timeline.html`:

```html
<button class="visualization-view-button" data-zoom-target="heatmap"
        aria-pressed="false">Heatmap</button>
```

### View panel

New panel alongside existing ones:

```html
<div id="heatmap-view" data-view-panel="heatmap" hidden>
  <div id="heatmap-container"></div>
  <div id="heatmap-entries"></div>
</div>
```

- `#heatmap-container` — D3 renders the SVG here
- `#heatmap-entries` — filtered entry cards are inserted here on cell click

### Script loading

D3 v7 script tag (already used by topic graph) is loaded when the heatmap tab is activated. The heatmap rendering code lives in an inline `<script>` block within the heatmap panel or in a dedicated JS section, following the same pattern as the timeline playback code.

### Route

```
GET /timeline/heatmap
```

Returns the timeline page with the heatmap tab pre-selected. Query params: `group_id`, `year`. This follows the same pattern as `/timeline/details`, `/timeline/months`, etc.

## Dark/Light Mode

The heatmap reads all colors from CSS custom properties at render time. When the user toggles the theme:

- A `MutationObserver` on `<html data-bs-theme>` detects the change
- The color scale is recomputed from the new CSS variable values
- Cell fills are updated in-place (no full re-render needed)

This matches the approach used by the existing topic graph visualization.

## Error Handling

- **No entries for year:** Show an empty grid with all cells at level 0, with a centered message: "No events tracked in {year}"
- **API failure:** Show a brief error message in the heatmap container; don't break the rest of the page
- **No years available:** Hide year navigation arrows, show current year as empty

## Testing

### Python tests

- `test_heatmap_counts` — verify `get_heatmap_counts` returns correct date-count mapping
- `test_heatmap_dayless_distribution` — verify entries without `event_day` are distributed evenly
- `test_heatmap_api_response` — verify API endpoint returns correct JSON structure
- `test_heatmap_group_filter` — verify group_id filtering works
- `test_heatmap_entries_partial` — verify filtered entries endpoint returns correct HTML

### E2E tests (TypeScript Playwright)

- Heatmap tab is visible and clickable in the toolbar
- Heatmap grid renders with correct number of cells for the year
- Hovering a cell shows a tooltip with date and count
- Clicking a cell shows filtered entries below
- Year navigation arrows work and update the grid
- Dark/light mode toggle updates heatmap colors
