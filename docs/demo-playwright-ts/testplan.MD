# Playwright End-to-End Test Plan

1. Search from the global navbar
   Goal: Verify the shared search control routes users to ranked results and returns relevant matches.
   Steps:
   - Open `/`.
   - Confirm the global searchbox with the placeholder `Filter timeline in plain English` and the `Search` button are visible.
   - Enter a query that matches visible homepage content, such as `VS Code`.
   - Submit with `Search`.
   - Confirm the app loads `/search` and shows the `Search Results` heading instead of the empty `No search query yet` state.
   - Confirm at least one result contains the query theme and exposes navigation to the underlying entry.
   Assertions:
   - The browser lands on the search experience.
   - Results are rendered for the submitted query.
   - Result navigation is available and usable.

2. Timeline homepage navigation and view switching
   Goal: Verify the homepage shell, primary navigation, and timeline view controls work together.
   Steps:
   - Open `/`.
   - Confirm the page title and `Agentic Coding Timeline` heading are visible.
   - Confirm the navbar exposes `Export`, `Admin`, and `New Entry` links.
   - Confirm the homepage exposes `Story Mode` and `Tag Clusters` links for the selected group.
   - Switch among `Details`, `Summaries`, `Months`, and `Years`.
   - Open one visible timeline bucket such as `March 2026` and then open one entry with `View`.
   Assertions:
   - Each timeline view switch updates the visible timeline state without breaking navigation.
   - Group-specific secondary links remain available from the homepage.
   - Opening an entry from the timeline reaches the entry detail page successfully.

3. Entry lifecycle from create to view to edit
   Goal: Verify a user can create a timeline entry, view it, and update it.
   Steps:
   - Open `/entries/new` from the `New Entry` link.
   - Fill the required entry fields with valid test data and submit the form.
   - Confirm the app redirects to `/entries/{entry_id}/view`.
   - Confirm the new entry title, date, group, and body content are visible on the detail page.
   - Open the matching `Edit` flow for that same entry.
   - Change one meaningful field, save, and return to the detail page.
   Assertions:
   - A successful create redirects to the read-only entry detail page.
   - The saved values persist after the initial create.
   - The edited value is visible after the update and the entry remains navigable from the UI.