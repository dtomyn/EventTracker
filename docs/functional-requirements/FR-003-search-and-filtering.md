# FR-003 Search And Filtering

- Category: Functional
- Status: Baseline
- Scope: Timeline filtering, ranked search, result ordering, snippets, and search pagination.
- Primary Sources: `PRODUCT_OVERVIEW.md`, `app/main.py`, `app/services/search.py`, `app/services/embeddings.py`, `app/templates/search.html`, `tests/test_search.py`, `tests/test_smoke.py`, `tests/e2e/test_core_workflows.py`

## Requirement Statements

- FR-003-01 The system shall support timeline filtering on `/` using the `q` query parameter.
- FR-003-02 Timeline filtering shall reuse ranked matching entry ids and then restore matching entries to timeline order before rendering.
- FR-003-03 The system shall provide a separate ranked-search page at `/search`.
- FR-003-04 Ranked search shall use the same default-group and `All groups` scoping rules as the root timeline.
- FR-003-05 Ranked search shall return no ranked result cards when no query is provided.
- FR-003-06 Full-text search shall query `entries.final_text` through the FTS5 index.
- FR-003-07 Ranked search shall highlight snippets with `<mark>` markup in the rendered search results.
- FR-003-08 Ranked search shall paginate additional result cards through `/search/results`.
- FR-003-09 When semantic search is available, ranked search shall combine keyword and semantic matches using reciprocal rank fusion.
- FR-003-10 When semantic search is unavailable or misconfigured, ranked search shall fall back to keyword search without blocking the page.

## Acceptance Notes

- Keyword query construction tokenizes input and quotes each token for FTS use.
- Ranked ordering differs intentionally from timeline filtering order.
- Search surfaces also provide Story Mode launch entry points for the current scope.