## FR-010 — Quick CLI: Add Single Entry

Purpose
-------
Provide a lightweight command-line entry point to create a single timeline entry quickly, for scripted workflows and developer convenience.

User stories
------------
- As a user, I want to create a single timeline entry from the command line so I can script imports or add items without opening the web UI.
- As a developer, I want a deterministic, testable CLI for adding a single entry so automation and CI workflows can seed test data.

Acceptance criteria
-------------------
- A new script `scripts/add_entry.py` (or equivalent module) is callable via `uv run python -m scripts.add_entry`.
- The command accepts flags for at least: `--title`, `--group-id`, `--year`, `--month`, `--day` (optional), `--final-text`, `--source-url` (optional), `--tags` (comma-separated, optional), and `--links` (JSON or repeated flag, optional).
- Required fields must be validated; missing required args produce a non-zero exit code and a clear error message.
- On success the command prints the created entry id and exits with code 0.
- The command must reuse the app's validation and persistence logic (prefer importing `app.services.entries` or the DB layer) rather than duplicating business rules.
- The command must not require a running web server; it should operate directly against the configured database file (honoring `EVENTTRACKER_DB_PATH` / `.env`).

CLI specification
-----------------
- Invocation example:

```
uv run python -m scripts.add_entry \
  --title "Short note" \
  --group-id 1 \
  --year 2026 --month 3 --day 21 \
  --final-text "This is a one-line entry." \
  --tags "notes,cli" \
  --source-url "https://example.com/article"
```

- Flags
  - `--title` (required): Entry title.
  - `--group-id` (required): timeline group id or `default` to use the seeded default group.
  - `--year` (required): event year.
  - `--month` (required): event month.
  - `--day` (optional): event day.
  - `--final-text` (required): saved rich text (plain text acceptable; sanitization occurs on render).
  - `--source-url` (optional): primary source URL.
  - `--tags` (optional): comma-separated tags.
  - `--links` (optional): JSON array of objects with `url` and `note`, or repeated `--link url|note` flags (implementation detail left to implementer).
  - `--dry-run` (optional): validate and render but do not persist; print the would-be payload and exit 0.

Implementation notes
--------------------
- Prefer to implement the command by importing existing service functions (`app.services.entries`) to create the entry and maintain validation.
- Respect `.env` loading used by `scripts/run_dev.py` so database path and other settings behave consistently.
- Provide unit tests exercising success, validation errors, and `--dry-run` behavior (tests live under `tests/test_cli_add_entry.py`).
- Consider adding a simple wrapper in `pyproject.toml` scripts if desired later.

Security and operational notes
------------------------------
- This CLI operates on the local SQLite DB and therefore requires filesystem access and appropriate permissions.
- Do not attempt remote network calls (AI providers, extraction) during CLI add; any generation or extraction should be optional and off by default.

Open questions / optional enhancements
-----------------------------------
- Should the CLI optionally trigger embedding sync for the new entry? (Recommend: no by default; provide `--sync-embeddings` flag if desired.)
- Preferred `--links` flag format (JSON vs repeated `--link`) — leave choice to implementer; document in the implementation PR.
