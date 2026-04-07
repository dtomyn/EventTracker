# Non-Functional Requirements Index

- Category: Non-Functional
- Status: Baseline
- Scope: Index of the non-functional requirement documents derived from the implemented EventTracker repository as of 2026-04-06.
- Primary Sources: `README.md`, `pyproject.toml`, `app/db.py`, `app/env.py`, `app/services/*`, `scripts/*`, `tests/*`

## Requirement Statements

- NFR-INDEX-01 The repository shall keep non-functional requirements split into small-scope markdown documents under this folder.
- NFR-INDEX-02 The repository shall require each non-functional requirement document to use the standard template defined in `docs/requirements-template.md`.
- NFR-INDEX-03 The repository shall describe observable operational, quality, security, and maintenance characteristics evidenced by the current implementation in non-functional requirements.

## Acceptance Notes

- `NFR-001-architecture-and-runtime.md`: application shape and delivery model.
- `NFR-002-persistence-and-schema-evolution.md`: database, schema, and indexing constraints.
- `NFR-003-configuration-and-environment.md`: environment loading and runtime configuration.
- `NFR-004-reliability-and-graceful-degradation.md`: fallback and resilience behavior.
- `NFR-005-security-and-trust-boundaries.md`: sanitization and trust-boundary requirements.
- `NFR-006-performance-and-operational-limits.md`: page sizes, limits, caching, and bounded inputs.
- `NFR-007-testing-and-verification.md`: typing, unit, route, and browser-validation expectations.