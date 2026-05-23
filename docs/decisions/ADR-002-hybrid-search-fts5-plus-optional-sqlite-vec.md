# ADR-002: Hybrid search with SQLite FTS5 plus optional sqlite-vec semantic recall

## Status
Accepted

## Date
2026-05-22

## Context
EventTracker needs effective retrieval for timeline exploration, ranked search, and retrieval-grounded AI features (Story Mode, Event Chat). Constraints:

- Must work fully offline/local without external search infrastructure.
- Keyword precision is required for exact term matches.
- Semantic recall is useful when wording differs from stored text.
- Search behavior should degrade gracefully when optional capabilities are unavailable.

## Decision
Adopt hybrid search:

- Use SQLite FTS5 over `entries.final_text` as baseline keyword retrieval.
- When embeddings are available, add sqlite-vec semantic retrieval and fuse rankings.
- Keep semantic retrieval optional; do not block core search if embeddings are unavailable.

Implementation anchors:

- Retrieval logic in `app/services/search.py`.
- Embedding management in `app/services/embeddings.py`.
- FTS and vec index setup in `app/db.py`.

## Alternatives Considered

### FTS-only search
- Pros: Simple, deterministic, zero extra model runtime.
- Cons: Misses semantically related results when query terms do not overlap.
- Rejected: Insufficient recall for AI-assisted and exploratory workflows.

### Semantic-only search
- Pros: Better conceptual matching.
- Cons: Less transparent ranking for exact queries; requires embedding pipeline for all useful retrieval.
- Rejected: Weakens deterministic exact-match behavior users expect.

### External search service (for example Elasticsearch or hosted vector DB)
- Pros: Rich search feature set and potentially stronger scaling.
- Cons: Adds infrastructure dependency and complexity; conflicts with local-first deployment.
- Rejected: Operational cost and dependency model do not match project constraints.

## Consequences

- Search remains robust in minimal local environments (FTS baseline).
- Semantic recall improves relevance when embedding support is configured.
- AI retrieval workflows can share one unified search substrate.
- The team must maintain compatibility checks for optional sqlite-vec/embedding dependencies.
- Ranking behavior can vary across environments based on whether semantic recall is enabled, so documentation and tests must continue to cover both modes.
