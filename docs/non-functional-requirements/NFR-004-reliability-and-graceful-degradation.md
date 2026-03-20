# NFR-004 Reliability And Graceful Degradation

- Category: Non-Functional
- Status: Baseline
- Scope: Fail-open behavior, fallback flows, recoverability, and non-fatal error handling.
- Primary Sources: `README.md`, `app/main.py`, `app/services/entries.py`, `app/services/search.py`, `app/services/extraction.py`, `app/services/embeddings.py`, `app/services/group_web_search.py`, `app/services/ai_generate.py`, `app/services/ai_story_mode.py`, `tests/test_smoke.py`, `tests/test_story_routes.py`

## Requirement Statements

- NFR-004-01 The application shall fail open for optional AI, embedding, and sqlite-vec capabilities so core manual entry, timeline, and keyword-search workflows remain usable.
- NFR-004-02 The application shall not fail entry create and update operations solely because embedding synchronization fails.
- NFR-004-03 The application shall fall back to keyword-only behavior for semantic search when embeddings are unavailable, misconfigured, or dimensionally incompatible.
- NFR-004-04 The application shall fall back to title-only draft generation when source extraction fails and fallback is possible.
- NFR-004-05 The application shall treat an empty Story Mode scope as a recoverable user state rather than a server failure.
- NFR-004-06 The application shall return configuration or timeout errors from group web search as user-facing failures without destabilizing the rest of the page.
- NFR-004-07 The application shall fail development-server startup explicitly when the configured port is already in use.
- NFR-004-08 The application shall clean up stale tracked development-server processes before starting a new Windows reload owner.
- NFR-004-09 The application shall keep saved story snapshots stable after later entry edits by storing the generated narrative and citation mapping as point-in-time data.

## Acceptance Notes

- Extraction failures are logged and return `None` rather than throwing raw parser or network exceptions through the request path.
- Embedding reindex support is the explicit operational recovery path after model changes.