# ADR-001: Local-first single-process architecture with FastAPI and SQLite

## Status
Accepted

## Date
2026-05-22

## Context
EventTracker targets local-first usage with low operational overhead. Core constraints:

- Run as a single local process with simple setup.
- Keep timeline browsing and entry editing server-rendered for predictable UX.
- Persist all application data in one local database file.
- Support optional AI capabilities without making them mandatory for core workflows.
- Minimize moving parts for development, testing, and maintenance.

## Decision
Use a single-process FastAPI application with server-rendered Jinja templates and SQLite as the primary datastore.

Key implementation shape:

- FastAPI app routes and composition in `app/main.py`.
- SQLite schema/bootstrap and feature setup in `app/db.py`.
- Capability-oriented services under `app/services/`.
- No ORM; use parameterized SQL via `sqlite3`.

## Alternatives Considered

### SPA frontend plus separate API service
- Pros: Rich client interactivity and strict frontend/backend separation.
- Cons: Higher complexity (API contracts, client state orchestration, build tooling), slower iteration for a local-first app.
- Rejected: Added complexity does not match current product and deployment goals.

### Multi-service architecture
- Pros: Independent scaling and deployment boundaries.
- Cons: Significant operational overhead and distributed failure modes.
- Rejected: Premature for current scope and local-first usage model.

### Hosted relational database (for example PostgreSQL)
- Pros: Strong concurrency model and managed infrastructure options.
- Cons: Requires networked deployment and operational setup that conflicts with local-first simplicity.
- Rejected: SQLite better matches single-user local deployment and portability goals.

## Consequences

- Setup remains simple: one process and one database file.
- Server-rendered pages and HTMX-like partial updates stay aligned with current UI architecture.
- Core features work without external services; AI is optional and degrades gracefully when unavailable.
- Large composition root (`app/main.py`) needs ongoing discipline to avoid maintainability drift.
- Future shift to multi-user hosted deployment would require a new ADR and migration plan.
