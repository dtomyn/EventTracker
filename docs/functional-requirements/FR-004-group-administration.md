# FR-004 Group Administration

- Category: Functional
- Status: Baseline
- Scope: Timeline-group creation, editing, default selection, deletion, and validation.
- Primary Sources: `app/main.py`, `app/services/entries.py`, `app/models.py`, `app/templates/admin_groups.html`, `tests/test_entries.py`, `tests/test_smoke.py`, `tests/e2e/test_core_workflows.py`

## Requirement Statements

- FR-004-01 The system shall provide a timeline-group administration page at `/admin/groups`.
- FR-004-02 The system shall list groups with their current entry counts on the administration page.
- FR-004-03 The system shall create a new group only when the normalized name is non-empty and unique case-insensitively.
- FR-004-04 The system shall allow a newly created group to become the default group.
- FR-004-05 The system shall allow existing groups to be renamed and to store or clear an optional `web_search_query`.
- FR-004-06 The system shall enforce that at most one group is marked as default at any time.
- FR-004-07 The system shall not allow deletion of the current default group.
- FR-004-08 The system shall not allow deletion of a group that still contains entries.
- FR-004-09 The system shall return inline validation errors for invalid create, update, and delete operations.
- FR-004-10 The system shall clear cached group web-search results when a group's `web_search_query` changes.
- FR-004-11 The system shall return `404` from group update and delete routes when the targeted group does not exist.

## Acceptance Notes

- Fresh databases are seeded with a default group named `Agentic Coding`.
- Group selection is reused across timeline, search, story, and entry authoring flows.