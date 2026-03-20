# FR-009 Application Shell And Theme

- Category: Functional
- Status: Baseline
- Scope: Shared navigation, group-aware query actions, export access, and dark-theme behavior.
- Primary Sources: `README.md`, `PRODUCT_OVERVIEW.md`, `app/main.py`, `app/templates/base.html`, `app/static/styles.css`, `tests/test_smoke.py`, `tests/e2e/test_core_workflows.py`

## Requirement Statements

- FR-009-01 The shared application shell shall provide navigation links for the main timeline, ranked search, group administration, new-entry creation, and JSON export.
- FR-009-02 The shared navigation shall provide a query input with separate `Filter` and `Search` actions.
- FR-009-03 When a specific timeline group is selected, shared navigation actions shall preserve that `group_id` across timeline, search, and related flows.
- FR-009-04 The application shall provide a manual dark-theme toggle in the shared shell.
- FR-009-05 When no explicit theme preference is stored, the application shall respect the operating system `prefers-color-scheme` setting.
- FR-009-06 The application shall persist the chosen theme preference in browser storage so later page loads reuse the same theme.

## Acceptance Notes

- The shell remains server-rendered; theme switching is handled by lightweight page JavaScript.
- The current implementation uses Bootstrap theming hooks together with custom CSS variables.