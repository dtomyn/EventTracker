# FR-008 Data Portability And Tools

- Category: Functional
- Status: Baseline
- Scope: Export, import, database initialization, embedding reindex support, and source-extraction debugging utilities.
- Primary Sources: `README.md`, `app/main.py`, `scripts/import_entries.py`, `scripts/init_db.py`, `app/services/extraction.py`, `tests/test_import_entries.py`, `tests/test_init_db.py`

## Requirement Statements

- FR-008-01 The system shall provide JSON export of all entries at `GET /entries/export`.
- FR-008-02 The system shall include a top-level `count` and `entries` array in JSON export responses.
- FR-008-03 The system shall include saved tags, additional links, and display-oriented metadata needed to reconstruct the saved record in each exported entry.
- FR-008-04 The system shall use a timestamped filename prefixed with `EventTracker-export-` for exported files.
- FR-008-05 The repository shall provide `scripts/import_entries.py` to import legacy HTML list content and prior JSON exports.
- FR-008-06 The system shall skip exact duplicate entries during import by default and shall support an `--allow-duplicates` override.
- FR-008-07 The system shall validate exported tag and link structure during JSON import before writing any imported row.
- FR-008-08 The repository shall provide `scripts/init_db.py` to initialize required database structures.
- FR-008-09 The repository shall support `--reindex-embeddings` in `scripts/init_db.py` to rebuild sqlite-vec embeddings when embedding configuration is valid.
- FR-008-10 The system shall provide `GET /dev/extract` as a developer utility for inspecting extraction results for a source URL.
- FR-008-11 The system shall omit display-only derived fields that are not part of the persisted entry payload contract from JSON export.

## Acceptance Notes

- HTML import derives date and title from heading text and preserves entry body HTML.
- Import writes entries without attempting per-entry embedding synchronization.
- Imported entries target the seeded default group.
- Reindex support is intended as the operational recovery path for embedding-model changes.