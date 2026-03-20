# FR-001 Entry Lifecycle

- Category: Functional
- Status: Baseline
- Scope: Creation, editing, validation, persistence, and read-only presentation of timeline entries.
- Primary Sources: `app/main.py`, `app/services/entries.py`, `app/models.py`, `app/schemas.py`, `tests/test_entries.py`, `tests/test_smoke.py`, `tests/e2e/test_core_workflows.py`

## Requirement Statements

- FR-001-01 The system shall let users create and edit entries with year, month, optional day, timeline group, title, optional source URL, optional generated draft HTML, required final rich text, comma-separated tags, and optional additional links.
- FR-001-02 The system shall require `title`, `final_text`, and `group_id` on both create and update.
- FR-001-03 The system shall validate `event_year` within `1900` through `2100`, `event_month` within `1` through `12`, and `event_day` within `1` through `31` when present.
- FR-001-04 The system shall reject impossible calendar dates such as February 30 when `event_year`, `event_month`, and `event_day` are all provided.
- FR-001-05 The system shall accept `source_url` and additional link URLs only when they are valid `http` or `https` URLs.
- FR-001-06 The system shall require both `url` and `note` for any additional link row that contains either value.
- FR-001-07 The system shall normalize tags from comma-separated input, collapse extra whitespace, remove empties, and deduplicate case-insensitively.
- FR-001-08 The system shall prevent duplicate `source_url` values within the same timeline group while allowing the same URL in different groups.
- FR-001-09 The system shall replace tag associations and additional-link associations on every successful update.
- FR-001-10 The system shall redirect successful create and update requests to `/entries/{id}/view` with a `303` response.
- FR-001-11 The system shall return `404` for edit and view requests targeting a non-existent entry.
- FR-001-12 The read-only entry page shall display the saved content, date, group name, tags, source URL, and additional links.

## Acceptance Notes

- `sort_key` is derived from the saved date and is recalculated on update.
- Entry saves attempt embedding synchronization on a best-effort basis but do not require it for success.
- Inline validation errors are rendered back into the server-rendered form state.