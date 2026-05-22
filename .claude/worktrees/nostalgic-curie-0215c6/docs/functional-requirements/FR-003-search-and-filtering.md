# FR-003 Search And Filtering

- Category: Functional
- Status: Baseline
- Scope: Timeline filtering, ranked search, result ordering, snippets, and search pagination.
- Primary Sources: `README.md`, `app/main.py`, `app/services/search.py`, `app/services/embeddings.py`, `app/templates/search.html`, `tests/test_search.py`, `tests/test_smoke.py`, `tests/e2e/test_core_workflows.py`

## Requirement Statements

- FR-003-01 The system shall support timeline filtering on `/` using the `q` query parameter.
- FR-003-02 The system shall reuse ranked matching entry ids for timeline filtering and then restore matching entries to timeline order before rendering.
- FR-003-03 The system shall provide a separate ranked-search page at `/search`.
- FR-003-04 The system shall apply the same default-group and `All groups` scoping rules to ranked search as it applies to the root timeline.
- FR-003-05 The system shall return no ranked result cards when no query is provided.
- FR-003-06 The system shall query `entries.final_text` through the FTS5 index for full-text search.
- FR-003-07 The system shall highlight ranked-search snippets with `<mark>` markup in rendered search results.
- FR-003-08 The system shall paginate additional ranked result cards through `/search/results`.
- FR-003-09 The system shall combine keyword and semantic matches in ranked search using reciprocal rank fusion when semantic search is available.
- FR-003-10 The system shall fall back to keyword search for ranked search without blocking the page when semantic search is unavailable or misconfigured.
- FR-003-11 The system shall use `bm25(entries_fts)` for ranked keyword-search ordering.
- FR-003-12 The system shall sanitize search-result snippet HTML before rendering and may preserve `<mark>` highlighting.
- FR-003-13 The system shall run semantic search only when sqlite-vec support and embedding configuration are both available.

## Acceptance Notes

- Keyword query construction tokenizes input and quotes each token for FTS use.
- Ranked ordering differs intentionally from timeline filtering order.
- Search surfaces also provide Story Mode launch entry points for the current scope.