# NFR-004 Reliability And Graceful Degradation

- Category: Non-Functional
- Status: Baseline
- Scope: Fail-open behavior, fallback flows, recoverability, and non-fatal error handling.
- Primary Sources: `README.md`, `app/main.py`, `app/services/entries.py`, `app/services/search.py`, `app/services/extraction.py`, `app/services/embeddings.py`, `app/services/group_web_search.py`, `app/services/ai_generate.py`, `app/services/ai_story_mode.py`, `tests/test_smoke.py`, `tests/test_story_routes.py`

## Requirement Statements

- NFR-004-01 Optional AI, embedding, and sqlite-vec capabilities shall fail open so core manual entry, timeline, and keyword-search workflows remain usable.
- NFR-004-02 Entry create and update operations shall not fail solely because embedding synchronization fails.
- NFR-004-03 Semantic search shall fall back to keyword-only behavior when embeddings are unavailable, misconfigured, or dimensionally incompatible.
- NFR-004-04 Source extraction failures shall fall back to title-only draft generation when possible.
- NFR-004-05 Story Mode shall treat an empty scope as a recoverable user state rather than a server failure.
- NFR-004-06 Group web search shall return configuration or timeout errors as user-facing failures without destabilizing the rest of the page.
- NFR-004-07 Development-server startup shall fail explicitly when the configured port is already in use.
- NFR-004-08 Windows reload sessions shall clean up stale tracked development-server processes before starting a new reload owner.
- NFR-004-09 Saved story snapshots shall remain stable after later entry edits because they store the generated narrative and citation mapping as point-in-time data.

## Acceptance Notes

- Extraction failures are logged and return `None` rather than throwing raw parser or network exceptions through the request path.
- Embedding reindex support is the explicit operational recovery path after model changes.