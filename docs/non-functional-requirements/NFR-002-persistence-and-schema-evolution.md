# NFR-002 Persistence And Schema Evolution

- Category: Non-Functional
- Status: Baseline
- Scope: SQLite persistence, connection semantics, additive schema management, and derived-index maintenance.
- Primary Sources: `app/db.py`, `app/services/entries.py`, `app/services/embeddings.py`, `app/services/story_mode.py`, `tests/test_db.py`, `tests/test_init_db.py`

## Requirement Statements

- NFR-002-01 The application shall persist primary data in a single SQLite database file.
- NFR-002-02 The application shall default the database path to `data/EventTracker.db` and shall allow override through `EVENTTRACKER_DB_PATH`.
- NFR-002-03 The application shall enable SQLite foreign-key enforcement for database connections.
- NFR-002-04 The application shall commit successful database write operations performed through `connection_context()` and shall roll them back on failure.
- NFR-002-05 The application shall prefer additive schema evolution during database initialization and shall preserve existing user data.
- NFR-002-06 The application shall seed fresh databases with a default timeline group named `Agentic Coding`.
- NFR-002-07 The application shall reassign existing entries lacking a group assignment to a valid default group during initialization.
- NFR-002-08 The application shall maintain full-text indexing over `entries.final_text` through database triggers and rebuild logic.
- NFR-002-09 The application shall create story snapshot tables and indexes through the same initialization path as the core schema.
- NFR-002-10 The application shall create embedding-index tables only when sqlite-vec support and embedding metadata are available.
- NFR-002-11 The application shall fail fast at startup on unsupported entry-schema drift instead of attempting a destructive migration.
- NFR-002-12 The application shall include `id`, case-insensitive unique `name`, optional `web_search_query`, and `is_default` in the persisted `timeline_groups` schema.
- NFR-002-13 The application shall include date parts, `sort_key`, `group_id`, `title`, optional `source_url`, optional `generated_text`, required `final_text`, `created_utc`, and `updated_utc` in the persisted `entries` schema.
- NFR-002-14 The application shall store `url`, `note`, and `created_utc` for each saved entry-link association in the `entry_links` schema.
- NFR-002-15 The application shall persist the active model identifier and vector dimensions in embedding metadata.
- NFR-002-16 The application shall derive stored entry embeddings from `entries.final_text` only.
- NFR-002-17 The application shall treat FTS and embedding tables as derived indexes rather than the primary source of truth for entry content.

## Acceptance Notes

- Initialization creates parent directories for the configured database path as needed.
- Embedding-index mismatches are surfaced with an explicit reindex command path.