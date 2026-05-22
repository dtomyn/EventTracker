# FR-009 Application Shell And Theme

- Category: Functional
- Status: Baseline
- Scope: Shared navigation, group-aware query actions, export access, and dark-theme behavior.
- Primary Sources: `README.md`, `app/main.py`, `app/templates/base.html`, `app/static/styles.css`, `tests/test_smoke.py`, `tests/e2e/test_core_workflows.py`

## Requirement Statements

- FR-009-01 The system shall provide navigation links for the main timeline, ranked search, group administration, new-entry creation, and JSON export in the shared application shell.
- FR-009-02 The system shall provide a query input with separate `Filter` and `Search` actions in the shared navigation.
- FR-009-03 The system shall preserve a selected `group_id` across timeline, search, and related flows in shared navigation actions when a specific timeline group is selected.
- FR-009-04 The system shall provide a manual dark-theme toggle in the shared shell.
- FR-009-05 The system shall respect the operating system `prefers-color-scheme` setting when no explicit theme preference is stored.
- FR-009-06 The system shall persist the chosen theme preference in browser storage so later page loads reuse the same theme.
- FR-009-07 The system shall use the Bootstrap 5.3 `data-bs-theme` attribute together with CSS custom properties for theme switching.

## Acceptance Notes

- The shell remains server-rendered; theme switching is handled by lightweight page JavaScript.
- Theme styling uses Bootstrap theming hooks together with custom CSS variables.