# Functional Requirements Index

- Category: Functional
- Status: Baseline
- Scope: Index of the functional requirement documents derived from the implemented EventTracker repository as of 2026-04-06.
- Primary Sources: `README.md`, `app/main.py`, `app/services/*`, `scripts/*`, `tests/*`

## Requirement Statements

- FR-INDEX-01 The repository shall keep functional requirements split into small-scope markdown documents under this folder.
- FR-INDEX-02 The repository shall require each functional requirement document to use the standard template defined in `docs/requirements-template.md`.
- FR-INDEX-03 The repository shall describe current implemented behavior rather than aspirational roadmap items in functional requirements.

## Acceptance Notes

- `FR-001-entry-lifecycle.md`: entry create, edit, validation, and detail behavior.
- `FR-002-timeline-browsing.md`: root timeline, view switching, drill-down, and pagination behavior.
- `FR-003-search-and-filtering.md`: timeline filtering and ranked search behavior.
- `FR-004-group-administration.md`: timeline group management rules.
- `FR-005-ai-draft-generation.md`: AI-assisted entry draft generation and HTML preview behavior.
- `FR-006-story-mode.md`: scoped narrative generation and snapshot behavior.
- `FR-007-group-web-search.md`: Copilot-backed group web search behavior.
- `FR-008-data-portability-and-tools.md`: export, import, initialization, and developer utility behavior.
- `FR-009-application-shell-and-theme.md`: shared navigation and theme behavior.
- `FR-010-topic-clustering.md`: semantic topic clustering and D3.js mind map behavior.