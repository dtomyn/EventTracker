# NFR-002 Persistence And Schema Evolution

- Category: Non-Functional
- Status: Baseline
- Scope: SQLite persistence, connection semantics, additive schema management, and derived-index maintenance.
- Primary Sources: `PRODUCT_OVERVIEW.md`, `app/db.py`, `app/services/entries.py`, `app/services/embeddings.py`, `app/services/story_mode.py`, `tests/test_db.py`, `tests/test_init_db.py`

## Requirement Statements

- NFR-002-01 The application shall persist primary data in a single SQLite database file.
- NFR-002-02 The database path shall default to `data/EventTracker.db` and shall be overrideable through `EVENTTRACKER_DB_PATH`.
- NFR-002-03 Database connections shall enable SQLite foreign-key enforcement.
- NFR-002-04 Database write operations performed through `connection_context()` shall commit on success and roll back on failure.
- NFR-002-05 Database initialization shall prefer additive schema evolution and shall preserve existing user data.
- NFR-002-06 Fresh databases shall be seeded with a default timeline group named `Agentic Coding`.
- NFR-002-07 Existing entries lacking a group assignment shall be reassigned to a valid default group during initialization.
- NFR-002-08 Full-text indexing over `entries.final_text` shall be maintained through database triggers and rebuild logic.
- NFR-002-09 Story snapshot tables and indexes shall be created through the same initialization path as the core schema.
- NFR-002-10 Embedding-index tables shall exist only when sqlite-vec support and embedding metadata are available.
- NFR-002-11 Unsupported entry-schema drift shall fail fast at startup instead of attempting a destructive migration.

## Acceptance Notes

- Initialization creates parent directories for the configured database path as needed.
- Embedding-index mismatches are surfaced with an explicit reindex command path.