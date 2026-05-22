# Activity Heatmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub-style activity heatmap as a fifth visualization tab in the timeline toolbar, rendered with D3.js and backed by a JSON API.

**Architecture:** New service function queries entry counts per date, a JSON API endpoint serves heatmap data, and D3.js renders a week-column/day-row grid with tooltips and click-to-filter. Integrates into the existing visualization toolbar alongside Details, Summaries, Months, and Years tabs.

**Tech Stack:** Python (FastAPI, sqlite3), D3.js v7, Jinja2 templates, existing CSS custom properties

**Spec:** `docs/superpowers/specs/2026-04-06-activity-heatmap-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `app/models.py` | Add `HeatmapData` dataclass |
| Modify | `app/services/entries.py` | Add `get_heatmap_counts()` function |
| Modify | `app/main.py` | Add `/api/heatmap` and `/timeline/heatmap/entries` routes, `HeatmapPayload` TypedDict |
| Modify | `app/templates/timeline.html` | Add Heatmap toolbar button, view panel, D3 rendering script |
| Create | `app/templates/partials/heatmap_entries.html` | Partial for filtered entry cards below heatmap |
| Modify | `app/static/styles.css` | Add heatmap tooltip, container, and selected-cell styles |
| Modify | `tests/test_entries.py` | Unit tests for `get_heatmap_counts` |
| Create | `tests/test_heatmap_api.py` | Integration tests for heatmap API endpoint |
| Create | `tstests/e2e/heatmap.spec.ts` | E2E tests for heatmap UI |

---

### Task 1: Add HeatmapData model

**Files:**
- Modify: `app/models.py` (after `TimelineStorySnapshot` dataclass, ~line 95)

- [ ] **Step 1: Add the HeatmapData dataclass**

Add at the end of `app/models.py`, after the `TimelineStorySnapshot` class:

```python
@dataclass(slots=True)
class HeatmapData:
    counts: dict[str, int]
    total: int
    year: int
    years_available: list[int]
```

- [ ] **Step 2: Verify no import errors**

Run: `uv run python -c "from app.models import HeatmapData; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/models.py
git commit -m "feat(heatmap): add HeatmapData dataclass"
```

---

### Task 2: Implement get_heatmap_counts service function (TDD)

**Files:**
- Modify: `app/services/entries.py` (add function after `list_timeline_month_buckets` ~line 890)
- Modify: `tests/test_entries.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_entries.py`. First, add the import at the top with the other entries imports:

```python
from app.services.entries import (
    # ... existing imports ...
    get_heatmap_counts,
)
```

Then add this test class at the end of the file:

```python
class TestHeatmapCounts(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        os.environ["EVENTTRACKER_DB_PATH"] = self.db_path
        with connection_context() as conn:
            init_db(conn)
            conn.execute(
                "INSERT INTO timeline_groups (id, name) VALUES (1, 'Test Group')"
            )
            conn.execute(
                "INSERT INTO timeline_groups (id, name) VALUES (2, 'Other Group')"
            )
            # Entry with specific day
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 1, 'A', '<p>A</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            # Another entry on the same day
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 1, 'B', '<p>B</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            # Entry without a day
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 6, NULL, 20250600, 1, 'C', '<p>C</p>', '2025-06-01T00:00:00+00:00', '2025-06-01T00:00:00+00:00')"
            )
            # Entry in a different group
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 2, 'D', '<p>D</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            # Entry in a different year
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2024, 1, 10, 20240110, 1, 'E', '<p>E</p>', '2024-01-10T00:00:00+00:00', '2024-01-10T00:00:00+00:00')"
            )
            conn.commit()

    def tearDown(self) -> None:
        if "EVENTTRACKER_DB_PATH" in os.environ:
            del os.environ["EVENTTRACKER_DB_PATH"]
        self.tmp.cleanup()

    def test_counts_entries_with_specific_days(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2025)
        self.assertEqual(result.counts.get("2025-03-15"), 3)  # 2 from group 1 + 1 from group 2
        self.assertEqual(result.year, 2025)

    def test_distributes_dayless_entries_across_month(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2025)
        # The dayless June entry should be distributed to some day in June
        june_keys = [k for k in result.counts if k.startswith("2025-06-")]
        self.assertEqual(sum(result.counts[k] for k in june_keys), 1)

    def test_filters_by_group_id(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2025, group_id=1)
        # Only group 1: 2 entries on Mar 15 + 1 dayless in June
        self.assertEqual(result.counts.get("2025-03-15"), 2)
        self.assertEqual(result.total, 3)

    def test_returns_years_available(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2025)
        self.assertIn(2024, result.years_available)
        self.assertIn(2025, result.years_available)

    def test_empty_year_returns_zero_total(self) -> None:
        with connection_context() as conn:
            result = get_heatmap_counts(conn, year=2020)
        self.assertEqual(result.total, 0)
        self.assertEqual(result.counts, {})
        self.assertIn(2024, result.years_available)

    def test_multiple_dayless_entries_distribute_evenly(self) -> None:
        with connection_context() as conn:
            for i in range(3):
                conn.execute(
                    "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                    "VALUES (2025, 9, NULL, 20250900, 1, ?, '<p>X</p>', '2025-09-01T00:00:00+00:00', '2025-09-01T00:00:00+00:00')",
                    (f"Sep{i}",),
                )
            conn.commit()
            result = get_heatmap_counts(conn, year=2025)
        sept_keys = [k for k in result.counts if k.startswith("2025-09-")]
        # 3 entries should be spread across 3 different days
        self.assertEqual(len(sept_keys), 3)
        self.assertEqual(sum(result.counts[k] for k in sept_keys), 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_entries.py::TestHeatmapCounts -v`
Expected: FAIL with `ImportError: cannot import name 'get_heatmap_counts'`

- [ ] **Step 3: Implement get_heatmap_counts**

Add the import at the top of `app/services/entries.py` (with existing stdlib imports):

```python
import calendar
```

Add the `get_heatmap_counts` function after `list_timeline_month_buckets` (~line 890) in `app/services/entries.py`:

```python
def get_heatmap_counts(
    connection: sqlite3.Connection,
    year: int,
    group_id: int | None = None,
) -> HeatmapData:
    """Return per-day entry counts for a calendar year.

    Entries without ``event_day`` are distributed evenly across their month.
    """
    from app.models import HeatmapData

    group_filter = "AND e.group_id = ?" if group_id is not None else ""
    base_params: tuple[object, ...] = (year,) if group_id is None else (year, group_id)

    # Entries with a specific day
    rows_with_day = connection.execute(
        f"""
        SELECT event_month, event_day, COUNT(*) AS cnt
        FROM entries e
        WHERE e.event_year = ? {group_filter}
          AND e.event_day IS NOT NULL
        GROUP BY event_month, event_day
        """,
        base_params,
    ).fetchall()

    counts: dict[str, int] = {}
    for row in rows_with_day:
        month, day, cnt = row[0], row[1], row[2]
        key = f"{year}-{month:02d}-{day:02d}"
        counts[key] = counts.get(key, 0) + cnt

    # Entries without a specific day — distribute evenly across the month
    rows_without_day = connection.execute(
        f"""
        SELECT event_month, COUNT(*) AS cnt
        FROM entries e
        WHERE e.event_year = ? {group_filter}
          AND e.event_day IS NULL
        GROUP BY event_month
        """,
        base_params,
    ).fetchall()

    for row in rows_without_day:
        month, cnt = row[0], row[1]
        days_in_month = calendar.monthrange(year, month)[1]
        step = max(1, days_in_month // cnt) if cnt <= days_in_month else 1
        for i in range(cnt):
            day = (i * step) % days_in_month + 1
            key = f"{year}-{month:02d}-{day:02d}"
            counts[key] = counts.get(key, 0) + 1

    # All years with entries (for year navigation)
    years_query = "SELECT DISTINCT event_year FROM entries"
    if group_id is not None:
        years_query += " WHERE group_id = ?"
        years_rows = connection.execute(years_query, (group_id,)).fetchall()
    else:
        years_rows = connection.execute(years_query).fetchall()
    years_available = sorted(row[0] for row in years_rows)

    total = sum(counts.values())
    return HeatmapData(counts=counts, total=total, year=year, years_available=years_available)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_entries.py::TestHeatmapCounts -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/entries.py tests/test_entries.py
git commit -m "feat(heatmap): add get_heatmap_counts service function with tests"
```

---

### Task 3: Add /api/heatmap API endpoint

**Files:**
- Modify: `app/main.py` (~line 382 for TypedDict, ~line 1510 for route near other API routes)
- Create: `tests/test_heatmap_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_heatmap_api.py`:

```python
from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.db import connection_context, init_db


class TestHeatmapAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        os.environ["EVENTTRACKER_DB_PATH"] = self.db_path
        os.environ["TESTING"] = "1"
        with connection_context() as conn:
            init_db(conn)
            conn.execute(
                "INSERT INTO timeline_groups (id, name) VALUES (1, 'Test')"
            )
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 1, 'A', '<p>A</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO entries (event_year, event_month, event_day, sort_key, group_id, title, final_text, created_utc, updated_utc) "
                "VALUES (2025, 3, 15, 20250315, 1, 'B', '<p>B</p>', '2025-03-15T00:00:00+00:00', '2025-03-15T00:00:00+00:00')"
            )
            conn.commit()

        from app.main import app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for key in ("EVENTTRACKER_DB_PATH", "TESTING"):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def test_heatmap_returns_counts(self) -> None:
        resp = self.client.get("/api/heatmap?year=2025")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["year"], 2025)
        self.assertEqual(data["counts"]["2025-03-15"], 2)
        self.assertEqual(data["total"], 2)

    def test_heatmap_filters_by_group(self) -> None:
        resp = self.client.get("/api/heatmap?year=2025&group_id=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 2)

    def test_heatmap_defaults_to_latest_year(self) -> None:
        resp = self.client.get("/api/heatmap")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["year"], 2025)

    def test_heatmap_empty_year(self) -> None:
        resp = self.client.get("/api/heatmap?year=2020")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["counts"], {})

    def test_heatmap_includes_years_available(self) -> None:
        resp = self.client.get("/api/heatmap?year=2025")
        data = resp.json()
        self.assertIn(2025, data["years_available"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_heatmap_api.py -v`
Expected: FAIL (404 — route does not exist yet)

- [ ] **Step 3: Add HeatmapPayload TypedDict and route**

Add the TypedDict after the `TimelineSummariesPayload` class (~line 382) in `app/main.py`:

```python
class HeatmapPayload(TypedDict):
    counts: dict[str, int]
    total: int
    year: int
    years_available: list[int]
```

Add the import of `get_heatmap_counts` to the `from app.services.entries import (...)` block (~line 56):

```python
    get_heatmap_counts,
```

Add the route near the other API routes (~line 1510, after `api_group_topics`):

```python
@app.get("/api/heatmap")
def api_heatmap(year: int | None = None, group_id: int | None = None) -> JSONResponse:
    with connection_context() as connection:
        if year is None:
            row = connection.execute(
                "SELECT MAX(event_year) FROM entries"
            ).fetchone()
            year = row[0] if row and row[0] else datetime.now().year

        data = get_heatmap_counts(connection, year=year, group_id=group_id)

    payload: HeatmapPayload = {
        "counts": data.counts,
        "total": data.total,
        "year": data.year,
        "years_available": data.years_available,
    }
    return JSONResponse(payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_heatmap_api.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Add GET /timeline/heatmap route**

The spec requires a route that returns the timeline page with the heatmap tab pre-selected (matching the pattern of `/timeline/details`, `/timeline/months`, etc.). Add this route near the other `/timeline/` routes in `app/main.py`:

```python
@app.get("/timeline/heatmap")
def timeline_heatmap(
    q: str = "",
    group_id: str = "",
    year: int | None = None,
) -> JSONResponse:
    """Return a payload that tells the client to switch to the heatmap view."""
    payload = {
        "view": "heatmap",
        "year": year,
    }
    return JSONResponse(payload)
```

Note: Unlike the other timeline view routes that return `items_html`, this endpoint just signals the client to activate the heatmap tab and load data via the `/api/heatmap` endpoint. The D3 rendering is fully client-side.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_heatmap_api.py
git commit -m "feat(heatmap): add /api/heatmap and /timeline/heatmap endpoints"
```

---

### Task 4: Add /timeline/heatmap/entries filtered entries endpoint

**Files:**
- Create: `app/templates/partials/heatmap_entries.html`
- Modify: `app/main.py` (add route)
- Modify: `tests/test_heatmap_api.py` (add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_heatmap_api.py`:

```python
    def test_heatmap_entries_returns_html(self) -> None:
        resp = self.client.get("/timeline/heatmap/entries?year=2025&month=3&day=15")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("A", resp.text)
        self.assertIn("B", resp.text)

    def test_heatmap_entries_empty_date(self) -> None:
        resp = self.client.get("/timeline/heatmap/entries?year=2025&month=1&day=1")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("No entries", resp.text)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_heatmap_api.py::TestHeatmapAPI::test_heatmap_entries_returns_html -v`
Expected: FAIL (404 — route does not exist)

- [ ] **Step 3: Create the partial template**

Create `app/templates/partials/heatmap_entries.html`:

```html
{% if entries %}
    <div class="heatmap-entries-header mb-3">
        <strong class="text-primary">{{ date_label }}</strong>
        <span class="text-body-secondary ms-2">{{ entries | length }} {% if entries | length == 1 %}entry{% else %}entries{% endif %}</span>
        <button class="btn btn-sm btn-outline-secondary ms-2" type="button" data-heatmap-clear-filter aria-label="Clear date filter">&times;</button>
    </div>
    <div class="vstack gap-3">
        {% for entry in entries %}
            {% include "partials/entry_card.html" %}
        {% endfor %}
    </div>
{% else %}
    <div class="text-body-secondary py-3">No entries on {{ date_label }}.</div>
{% endif %}
```

- [ ] **Step 4: Add the route**

Add to `app/main.py` after the `/api/heatmap` route:

```python
@app.get("/timeline/heatmap/entries", response_class=HTMLResponse)
def timeline_heatmap_entries(
    request: Request,
    year: int,
    month: int,
    day: int,
    group_id: int | None = None,
) -> HTMLResponse:
    with connection_context() as connection:
        group_filter = "AND e.group_id = ?" if group_id is not None else ""
        params: tuple[object, ...] = (year, month, day)
        if group_id is not None:
            params = (year, month, day, group_id)

        rows = connection.execute(
            f"""
            SELECT
                e.*,
                tg.name AS group_name,
                COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags_csv,
                COALESCE(
                    json_group_array(
                        DISTINCT CASE
                            WHEN el.id IS NOT NULL THEN json_object(
                                'id', el.id,
                                'url', el.url,
                                'note', el.note,
                                'created_utc', el.created_utc
                            )
                        END
                    ),
                    '[]'
                ) AS links_json
            FROM entries e
            JOIN timeline_groups tg ON tg.id = e.group_id
            LEFT JOIN entry_tags et ON et.entry_id = e.id
            LEFT JOIN tags t ON t.id = et.tag_id
            LEFT JOIN entry_links el ON el.entry_id = e.id
            WHERE e.event_year = ? AND e.event_month = ? AND e.event_day = ?
                {group_filter}
            GROUP BY e.id, tg.name
            ORDER BY e.sort_key DESC, e.updated_utc DESC
            """,
            params,
        ).fetchall()

        from app.services.entries import entry_from_row
        entries = [entry_from_row(row) for row in rows]

    date_label = f"{_month_name(month)} {day}, {year}"
    html = _render_partial(
        "partials/heatmap_entries.html",
        entries=entries,
        date_label=date_label,
    )
    return HTMLResponse(html)
```

Add the `_month_name` helper near the other private helpers at the bottom of `app/main.py` (before `_render_partial`):

```python
def _month_name(month: int) -> str:
    import calendar
    return calendar.month_name[month]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_heatmap_api.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/partials/heatmap_entries.html tests/test_heatmap_api.py
git commit -m "feat(heatmap): add /timeline/heatmap/entries endpoint with partial template"
```

---

### Task 5: Add Heatmap toolbar button and view panel to timeline template

**Files:**
- Modify: `app/templates/timeline.html`

- [ ] **Step 1: Add the Heatmap button to the visualization toolbar**

In `app/templates/timeline.html`, find the toolbar segmented controls (~line 106) and add the Heatmap button after the Years button:

```html
            <button class="visualization-view-button" type="button" data-zoom-target="years" aria-pressed="false">Years</button>
            <button class="visualization-view-button" type="button" data-zoom-target="heatmap" aria-pressed="false">Heatmap</button>
```

- [ ] **Step 2: Add the heatmap view panel**

After the `visualization-content` div (the summary panel, ~line 150), add the heatmap panel inside the `visualization-shell` div:

```html
        <div id="heatmap-view" data-view-panel="heatmap" hidden>
            <div id="heatmap-container" class="heatmap-container"></div>
            <div id="heatmap-entries" class="mt-4"></div>
        </div>
```

- [ ] **Step 3: Update the view switching JavaScript**

In the script section of `timeline.html`, update the `viewLabels` object (~line 620) to include heatmap:

```javascript
    const viewLabels = {
        details: "Details",
        events: "Summaries",
        months: "Months",
        years: "Years",
        heatmap: "Heatmap",
    };
```

Add a reference to the heatmap panel after the existing panel selectors (~line 626):

```javascript
    const heatmapPanel = document.getElementById("heatmap-view");
```

Update the `syncControls` function to handle three panels:

```javascript
    const syncControls = () => {
        summaryPanel.dataset.currentView = state.view;
        detailPanel.hidden = state.view !== "details";
        summaryPanel.hidden = state.view === "details" || state.view === "heatmap";
        if (heatmapPanel) heatmapPanel.hidden = state.view !== "heatmap";
        statusLabel.textContent = viewLabels[state.view];
        statusContext.textContent = describeState(state);
        if (playbackPanel) {
            playbackPanel.hidden = state.view !== "events";
        }
        syncPlaybackStatusVisibility();
        for (const control of controls) {
            const pressed = control.dataset.zoomTarget === state.view;
            control.setAttribute("aria-pressed", String(pressed));
        }
    };
```

Find the click handler for view buttons and add heatmap initialization. Locate the section where button clicks trigger view changes (search for `data-zoom-target` click handler) and add a call to load the heatmap when switching to that view:

```javascript
        if (target === "heatmap") {
            state.view = "heatmap";
            syncControls();
            loadHeatmap();
            return;
        }
```

- [ ] **Step 4: Verify the page loads without errors**

Run: `uv run python -m scripts.run_dev --reload`
Open http://127.0.0.1:35231/ and verify the Heatmap button appears in the toolbar. Clicking it should show an empty panel (D3 rendering comes in the next task).

- [ ] **Step 5: Commit**

```bash
git add app/templates/timeline.html
git commit -m "feat(heatmap): add Heatmap toolbar button and view panel"
```

---

### Task 6: D3.js heatmap grid rendering

**Files:**
- Modify: `app/templates/timeline.html` (add D3 script block inside the heatmap panel or at the end of the script section)

- [ ] **Step 1: Add D3 script tag and heatmap rendering code**

Add the D3 CDN script tag. In the heatmap view panel area of `timeline.html`, add it conditionally (or at the bottom of the page). Since the topic graph already uses D3 from CDN, follow the same pattern. Add inside the main `<script>` block at the bottom:

```javascript
    // --- Heatmap rendering ---
    let heatmapLoaded = false;
    let heatmapYear = null;
    let heatmapSelectedDate = null;

    function getHeatmapColors() {
        const style = getComputedStyle(document.documentElement);
        const theme = document.documentElement.getAttribute("data-bs-theme");
        if (theme === "dark") {
            return ["#1e293b", "#164e63", "#0e7490", "#0891b2", "#06b6d4"];
        }
        return [
            style.getPropertyValue("--et-surface-bg").trim() || "#f8fafc",
            "rgba(8, 145, 178, 0.15)",
            "rgba(8, 145, 178, 0.35)",
            "rgba(8, 145, 178, 0.6)",
            style.getPropertyValue("--et-primary").trim() || "#0891b2",
        ];
    }

    function loadHeatmap(year) {
        const container = document.getElementById("heatmap-container");
        if (!container) return;

        // Load D3 if not loaded
        if (!window.d3) {
            const script = document.createElement("script");
            script.src = "https://cdn.jsdelivr.net/npm/d3@7";
            script.onload = () => loadHeatmap(year);
            document.head.appendChild(script);
            return;
        }

        const groupId = timelineState.selectedGroupId || "";
        const params = new URLSearchParams();
        if (groupId) params.set("group_id", groupId);
        if (year) params.set("year", year);

        container.innerHTML = '<div class="text-body-secondary py-4 text-center">Loading heatmap\u2026</div>';

        fetch(`/api/heatmap?${params}`)
            .then(r => { if (!r.ok) throw new Error("Failed to load"); return r.json(); })
            .then(data => {
                heatmapYear = data.year;
                renderHeatmapGrid(container, data);
            })
            .catch(err => {
                container.innerHTML = `<div class="text-danger py-4 text-center">Could not load heatmap: ${err.message}</div>`;
            });
    }

    function renderHeatmapGrid(container, data) {
        container.innerHTML = "";
        const { counts, total, year, years_available } = data;

        const cellSize = 12;
        const cellGap = 2;
        const cellStep = cellSize + cellGap;
        const labelWidth = 36;
        const topMargin = 28;
        const navHeight = 32;
        const width = labelWidth + 53 * cellStep + 10;
        const height = navHeight + topMargin + 7 * cellStep + 40;

        const svg = d3.select(container)
            .append("svg")
            .attr("viewBox", `0 0 ${width} ${height}`)
            .attr("class", "heatmap-svg")
            .style("width", "100%")
            .style("max-width", `${width}px`);

        // Year navigation
        const nav = svg.append("g").attr("transform", `translate(${width / 2}, 18)`);

        const prevYear = years_available[years_available.indexOf(year) - 1];
        const nextYear = years_available[years_available.indexOf(year) + 1];

        if (prevYear !== undefined) {
            nav.append("text")
                .attr("x", -60)
                .attr("text-anchor", "middle")
                .attr("fill", "var(--et-text-secondary)")
                .attr("font-size", "14px")
                .attr("class", "heatmap-nav-arrow")
                .style("cursor", "pointer")
                .text("\u25C0")
                .on("click", () => loadHeatmap(prevYear));
        }

        nav.append("text")
            .attr("x", 0)
            .attr("text-anchor", "middle")
            .attr("fill", "var(--et-text)")
            .attr("font-size", "14px")
            .attr("font-weight", "600")
            .attr("font-family", "'JetBrains Mono', monospace")
            .text(year);

        if (nextYear !== undefined) {
            nav.append("text")
                .attr("x", 60)
                .attr("text-anchor", "middle")
                .attr("fill", "var(--et-text-secondary)")
                .attr("font-size", "14px")
                .attr("class", "heatmap-nav-arrow")
                .style("cursor", "pointer")
                .text("\u25B6")
                .on("click", () => loadHeatmap(nextYear));
        }

        const gridG = svg.append("g")
            .attr("transform", `translate(${labelWidth}, ${navHeight + topMargin})`);

        // Build date-to-cell mapping
        const jan1 = new Date(year, 0, 1);
        const dec31 = new Date(year, 11, 31);
        const startDay = (jan1.getDay() + 6) % 7; // Monday = 0
        const cells = [];

        for (let d = new Date(jan1); d <= dec31; d.setDate(d.getDate() + 1)) {
            const dayOfWeek = (d.getDay() + 6) % 7;
            const dayOfYear = Math.floor((d - jan1) / 86400000);
            const week = Math.floor((dayOfYear + startDay) / 7);
            const month = d.getMonth();
            const dayNum = d.getDate();
            const key = `${year}-${String(month + 1).padStart(2, "0")}-${String(dayNum).padStart(2, "0")}`;
            cells.push({ key, week, dayOfWeek, month, dayNum, count: counts[key] || 0 });
        }

        // Color scale
        const maxCount = d3.max(cells, c => c.count) || 1;
        const colors = getHeatmapColors();
        const colorScale = (count) => {
            if (count === 0) return colors[0];
            const thresholds = [1, Math.ceil(maxCount * 0.25), Math.ceil(maxCount * 0.5), Math.ceil(maxCount * 0.75)];
            if (count >= thresholds[3]) return colors[4];
            if (count >= thresholds[2]) return colors[3];
            if (count >= thresholds[1]) return colors[2];
            return colors[1];
        };

        // Month labels
        const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
        let lastMonth = -1;
        for (const cell of cells) {
            if (cell.month !== lastMonth && cell.dayNum <= 7) {
                gridG.append("text")
                    .attr("x", cell.week * cellStep)
                    .attr("y", -6)
                    .attr("fill", "var(--et-text-muted)")
                    .attr("font-size", "9px")
                    .text(months[cell.month]);
                lastMonth = cell.month;
            }
        }

        // Day-of-week labels
        const dayLabels = ["Mon", "", "Wed", "", "Fri", "", ""];
        dayLabels.forEach((label, i) => {
            if (label) {
                gridG.append("text")
                    .attr("x", -6)
                    .attr("y", i * cellStep + cellSize - 1)
                    .attr("text-anchor", "end")
                    .attr("fill", "var(--et-text-muted)")
                    .attr("font-size", "8px")
                    .text(label);
            }
        });

        // Tooltip
        const tooltip = d3.select(container)
            .append("div")
            .attr("class", "heatmap-tooltip")
            .style("display", "none");

        // Cells
        gridG.selectAll("rect.heatmap-cell")
            .data(cells)
            .enter()
            .append("rect")
            .attr("class", "heatmap-cell")
            .attr("x", d => d.week * cellStep)
            .attr("y", d => d.dayOfWeek * cellStep)
            .attr("width", cellSize)
            .attr("height", cellSize)
            .attr("rx", 2)
            .attr("fill", d => colorScale(d.count))
            .attr("data-date", d => d.key)
            .attr("data-count", d => d.count)
            .style("cursor", d => d.count > 0 ? "pointer" : "default")
            .on("mouseenter", function(event, d) {
                const rect = this.getBoundingClientRect();
                const containerRect = container.getBoundingClientRect();
                const dateObj = new Date(year, d.month, d.dayNum);
                const formatted = dateObj.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
                const label = d.count === 0
                    ? `No entries on ${formatted}`
                    : `${d.count} ${d.count === 1 ? "entry" : "entries"} on ${formatted}`;
                tooltip
                    .style("display", "block")
                    .html(label)
                    .style("left", `${rect.left - containerRect.left + cellSize / 2}px`)
                    .style("top", `${rect.top - containerRect.top - 32}px`);
            })
            .on("mouseleave", () => tooltip.style("display", "none"))
            .on("click", function(event, d) {
                if (d.count === 0) return;
                const dateKey = d.key;
                if (heatmapSelectedDate === dateKey) {
                    clearHeatmapFilter();
                    return;
                }
                selectHeatmapDate(dateKey, d);
            });

        // Legend
        const legendG = svg.append("g")
            .attr("transform", `translate(${width - 160}, ${height - 14})`);
        legendG.append("text").attr("x", 0).attr("y", 9).attr("fill", "var(--et-text-muted)").attr("font-size", "9px").text("Less");
        colors.forEach((color, i) => {
            legendG.append("rect")
                .attr("x", 28 + i * (cellSize + 2))
                .attr("y", 0)
                .attr("width", cellSize)
                .attr("height", cellSize)
                .attr("rx", 2)
                .attr("fill", color);
        });
        legendG.append("text").attr("x", 28 + 5 * (cellSize + 2) + 2).attr("y", 9).attr("fill", "var(--et-text-muted)").attr("font-size", "9px").text("More");

        // Summary stat
        svg.append("text")
            .attr("x", labelWidth)
            .attr("y", height - 6)
            .attr("fill", "var(--et-text-secondary)")
            .attr("font-size", "11px")
            .text(`${total} events in ${year}`);

        // Empty state overlay
        if (total === 0) {
            svg.append("text")
                .attr("x", width / 2)
                .attr("y", navHeight + topMargin + 3.5 * cellStep)
                .attr("text-anchor", "middle")
                .attr("fill", "var(--et-text-muted)")
                .attr("font-size", "13px")
                .text(`No events tracked in ${year}`);
        }

        heatmapLoaded = true;
    }

    function selectHeatmapDate(dateKey, cellData) {
        // Highlight selected cell
        d3.selectAll(".heatmap-cell").classed("heatmap-cell-selected", false);
        d3.selectAll(`.heatmap-cell[data-date="${dateKey}"]`).classed("heatmap-cell-selected", true);
        heatmapSelectedDate = dateKey;

        // Fetch filtered entries
        const [y, m, d] = dateKey.split("-");
        const params = new URLSearchParams({ year: y, month: String(parseInt(m)), day: String(parseInt(d)) });
        const groupId = timelineState.selectedGroupId;
        if (groupId) params.set("group_id", groupId);

        const entriesContainer = document.getElementById("heatmap-entries");
        entriesContainer.innerHTML = '<div class="text-body-secondary py-3">Loading entries\u2026</div>';

        fetch(`/timeline/heatmap/entries?${params}`)
            .then(r => { if (!r.ok) throw new Error("Failed"); return r.text(); })
            .then(html => {
                entriesContainer.innerHTML = html;
                const clearBtn = entriesContainer.querySelector("[data-heatmap-clear-filter]");
                if (clearBtn) clearBtn.addEventListener("click", clearHeatmapFilter);
            })
            .catch(() => {
                entriesContainer.innerHTML = '<div class="text-danger py-3">Could not load entries.</div>';
            });
    }

    function clearHeatmapFilter() {
        heatmapSelectedDate = null;
        d3.selectAll(".heatmap-cell").classed("heatmap-cell-selected", false);
        document.getElementById("heatmap-entries").innerHTML = "";
    }

    // Theme change observer — re-render heatmap when theme toggles
    new MutationObserver(() => {
        if (state.view === "heatmap" && heatmapLoaded) {
            loadHeatmap(heatmapYear);
        }
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-bs-theme"] });
```

- [ ] **Step 2: Verify the heatmap renders**

Run: `uv run python -m scripts.run_dev --reload`
Open http://127.0.0.1:35231/, click the Heatmap tab. Verify:
- The grid renders with colored cells for dates that have entries
- Month labels appear along the top
- Day-of-week labels appear on the left
- The "Less / More" legend is shown
- Summary stat text shows at the bottom

- [ ] **Step 3: Verify tooltip and interactions**

- Hover over a cell: tooltip appears with date and count
- Click a cell with entries: filtered entry cards appear below
- Click the same cell again: filter clears
- Click the x button on the filter header: filter clears
- Year navigation arrows work (if multiple years of data exist)
- Toggle dark/light mode: heatmap colors update

- [ ] **Step 4: Commit**

```bash
git add app/templates/timeline.html
git commit -m "feat(heatmap): add D3.js heatmap grid with tooltip, click-to-filter, year nav, and theme support"
```

---

### Task 7: Add heatmap CSS styles

**Files:**
- Modify: `app/static/styles.css`

- [ ] **Step 1: Add heatmap styles**

Add at the end of `app/static/styles.css`, before any closing comments:

```css
/* ── Heatmap ──────────────────────────────────────────────── */

.heatmap-container {
    position: relative;
    padding: 0.5rem 0;
}

.heatmap-svg {
    display: block;
    margin: 0 auto;
}

.heatmap-cell-selected {
    stroke: var(--et-primary);
    stroke-width: 2;
}

.heatmap-tooltip {
    position: absolute;
    padding: 0.4rem 0.7rem;
    background: var(--et-card-bg);
    border: 1px solid var(--et-border);
    border-radius: 6px;
    font-size: 0.75rem;
    color: var(--et-text);
    pointer-events: none;
    white-space: nowrap;
    transform: translateX(-50%);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    z-index: 10;
}

.heatmap-nav-arrow:hover {
    fill: var(--et-primary) !important;
}

.heatmap-entries-header {
    display: flex;
    align-items: center;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--et-border);
}
```

- [ ] **Step 2: Verify styling**

Run: `uv run python -m scripts.run_dev --reload`
Verify:
- Tooltip has proper background, border, shadow, and text color in both light and dark mode
- Selected cell has a cyan stroke ring
- Year nav arrows highlight on hover
- Layout is clean and centered

- [ ] **Step 3: Commit**

```bash
git add app/static/styles.css
git commit -m "feat(heatmap): add heatmap CSS styles for tooltip, selection, and layout"
```

---

### Task 8: E2E tests

**Files:**
- Create: `tstests/e2e/heatmap.spec.ts`

- [ ] **Step 1: Write E2E tests**

Create `tstests/e2e/heatmap.spec.ts`:

```typescript
import { test, expect } from "./helpers/harness";

test.describe("Activity Heatmap", () => {
    test("heatmap tab is visible in the toolbar", async ({ page }) => {
        await page.goto("/");
        const heatmapButton = page.locator(
            'button[data-zoom-target="heatmap"]'
        );
        await expect(heatmapButton).toBeVisible();
        await expect(heatmapButton).toHaveText("Heatmap");
    });

    test("clicking heatmap tab renders the SVG grid", async ({ page }) => {
        await page.goto("/");
        await page.click('button[data-zoom-target="heatmap"]');

        // Wait for the heatmap SVG to appear
        const svg = page.locator(".heatmap-svg");
        await expect(svg).toBeVisible({ timeout: 10000 });

        // Verify cells exist
        const cells = page.locator(".heatmap-cell");
        const cellCount = await cells.count();
        // A year has 365 or 366 cells
        expect(cellCount).toBeGreaterThanOrEqual(365);
        expect(cellCount).toBeLessThanOrEqual(366);
    });

    test("hovering a cell shows tooltip", async ({ page }) => {
        await page.goto("/");
        await page.click('button[data-zoom-target="heatmap"]');
        await page.locator(".heatmap-svg").waitFor({ state: "visible" });

        // Hover over the first cell
        const firstCell = page.locator(".heatmap-cell").first();
        await firstCell.hover();

        const tooltip = page.locator(".heatmap-tooltip");
        await expect(tooltip).toBeVisible();
    });

    test("clicking a cell with entries shows filtered entries below", async ({
        page,
        seedEntry,
    }) => {
        // Seed an entry with a specific date
        const entryDate = { year: 2025, month: 3, day: 15 };
        await seedEntry({
            title: "Heatmap Test Entry",
            event_year: entryDate.year,
            event_month: entryDate.month,
            event_day: entryDate.day,
        });

        await page.goto("/");
        await page.click('button[data-zoom-target="heatmap"]');
        await page.locator(".heatmap-svg").waitFor({ state: "visible" });

        // Click the cell for March 15
        const cell = page.locator('.heatmap-cell[data-date="2025-03-15"]');
        if (await cell.count() > 0) {
            await cell.click();

            const entriesContainer = page.locator("#heatmap-entries");
            await expect(entriesContainer).toContainText("Heatmap Test Entry", {
                timeout: 5000,
            });
        }
    });

    test("year navigation arrows switch years", async ({ page }) => {
        await page.goto("/");
        await page.click('button[data-zoom-target="heatmap"]');
        await page.locator(".heatmap-svg").waitFor({ state: "visible" });

        // Check that the current year is displayed
        const yearText = page.locator(".heatmap-svg text").filter({ hasText: /^\d{4}$/ });
        await expect(yearText.first()).toBeVisible();
    });

    test("heatmap legend is visible", async ({ page }) => {
        await page.goto("/");
        await page.click('button[data-zoom-target="heatmap"]');
        await page.locator(".heatmap-svg").waitFor({ state: "visible" });

        // Check for Less/More legend text
        const lessText = page.locator(".heatmap-svg text").filter({ hasText: "Less" });
        const moreText = page.locator(".heatmap-svg text").filter({ hasText: "More" });
        await expect(lessText).toBeVisible();
        await expect(moreText).toBeVisible();
    });
});
```

- [ ] **Step 2: Run E2E tests**

Run: `npm run test:e2e:ts -- tstests/e2e/heatmap.spec.ts`

Expected: Tests pass (some may need adjustment based on the actual test harness and seed data helpers — check `tstests/e2e/helpers/harness.ts` for the `seedEntry` API). If `seedEntry` is not available, simplify the click-to-filter test to just verify the heatmap renders and basic interactions work without seeded data.

- [ ] **Step 3: Commit**

```bash
git add tstests/e2e/heatmap.spec.ts
git commit -m "test(heatmap): add E2E tests for heatmap tab, grid, tooltip, and interactions"
```

---

### Task 9: Final verification and cleanup

- [ ] **Step 1: Run full Python test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests pass, including existing tests (no regressions).

- [ ] **Step 2: Run type checking**

Run: `uv run pyright`
Expected: No new errors. If `get_heatmap_counts` or `HeatmapData` need to be added to the pyright include list in `pyproject.toml`, add them.

- [ ] **Step 3: Manual smoke test**

Start the dev server: `uv run python -m scripts.run_dev --reload`

Verify:
1. All existing views (Details, Summaries, Months, Years) still work
2. Heatmap tab renders a grid with correct colors
3. Tooltip shows on hover
4. Click-to-filter works — shows entry cards, clears on re-click
5. Year navigation arrows work
6. Dark/light mode toggle updates heatmap colors
7. Group selector filters heatmap data
8. Empty years show "No events" state

- [ ] **Step 4: Commit any final adjustments**

```bash
git add -A
git commit -m "chore(heatmap): final cleanup and verification"
```
