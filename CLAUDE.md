# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is EventTracker

Local-first timeline application built with FastAPI, SQLite, Jinja2, and Bootstrap. Single-process, server-rendered app where entries are stored in one SQLite file and pages are rendered on the server with lightweight JavaScript for interactivity.

## Commands

### Setup
```bash
uv sync                                    # Install Python dependencies
uv sync --dev                              # Include dev dependencies (playwright, pytest, pyright)
npm install                                # Install TypeScript Playwright dependencies
uv run python -m playwright install chromium
```

### Run dev server
```bash
uv run python -m scripts.run_dev --reload  # Start on http://127.0.0.1:35231/
uv run python -m scripts.init_db           # Initialize/reset database
```

### Python tests
```bash
uv run pytest tests/                       # All unit/integration tests
uv run pytest tests/test_smoke.py          # Smoke tests
uv run pytest tests/test_entries.py        # Single test file
uv run pytest tests/test_entries.py -k "test_name"  # Single test
uv run pytest tests/e2e                    # Python Playwright E2E suite
```

### TypeScript Playwright E2E tests
```bash
npm run test:e2e:ts                        # Run all TS E2E tests
npm run test:e2e:ts -- tstests/e2e/smoke.spec.ts  # Single spec file
npm run test:e2e:ts:headed                 # Visible browser
npm run test:e2e:ts:ui                     # Playwright UI mode
npm run test:e2e:ts:debug                  # Debug mode
npm run serve:e2e:ts                       # Shared server for manual debugging / codegen
npm run codegen:e2e                        # Playwright codegen against shared server
```

### Type checking
```bash
uv run pyright                             # Python type checking (curated file list in pyproject.toml)
```

## Architecture

### Backend structure
- **`app/main.py`** — Composition root: all FastAPI routes, CSRF middleware, Jinja2 setup, template filters. This is a large file (~90KB) containing all route handlers.
- **`app/db.py`** — SQLite schema bootstrapping, FTS5 setup, sqlite-vec setup, connection context manager.
- **`app/models.py`** / **`app/schemas.py`** — Data models and form/payload schemas.
- **`app/services/`** — Service layer split by domain capability:
  - `entries.py` — Entry CRUD, validation, timeline grouping (largest service, ~37KB)
  - `search.py` — FTS5 keyword search + optional semantic search
  - `story_mode.py` / `ai_story_mode.py` — Story Mode scoping and AI narrative generation
  - `ai_generate.py` — AI draft generation (OpenAI or Copilot provider)
  - `embeddings.py` — Optional sqlite-vec embeddings
  - `extraction.py` — URL text extraction (server-side fetch)
  - `group_web_search.py` — Copilot-backed web search sidebar
  - `copilot_runtime.py` / `copilot_sdk.py` — GitHub Copilot integration
  - `topics.py` — Topic clustering and tag graph

### Frontend
- **`app/templates/`** — Server-rendered Jinja2 templates with `partials/` for reusable fragments.
- **`app/static/styles.css`** — Custom CSS on top of Bootstrap 5.3 via CDN.
- Dark mode uses Bootstrap's `data-bs-theme` attribute + `localStorage` persistence.

### Database
- Single SQLite file at `data/EventTracker.db` (configurable via `EVENTTRACKER_DB_PATH`).
- No ORM — raw `sqlite3` with context manager in `app/db.py`.
- No migration framework — schema compatibility checked at startup.
- FTS5 indexes `final_text` only. Embeddings derived from `final_text` only.
- AI and embeddings are optional; the app gracefully degrades without them.

### Testing architecture
- **Python tests** (`tests/`): pytest-based unit/integration tests with mocked external APIs.
- **Python E2E** (`tests/e2e/`): Playwright pytest harness in `conftest.py` — each test gets an isolated temp database copy and a server on a free port. `TESTING=1` env var bypasses CSRF.
- **TypeScript E2E** (`tstests/e2e/`): Playwright with custom harness at `tstests/e2e/helpers/harness.ts` that mirrors the Python isolation model (temp DB copy, free port, teardown). New TS specs must import `test` and `expect` from the harness, not from `@playwright/test` directly. Page Object Models live in `tstests/e2e/poms/`.

### Key patterns
- CSRF protection: HMAC-SHA256 per-session tokens via cookie + form/header.
- Configuration: `.env` file loaded by `python-dotenv` (see `.env.example`). Key vars prefixed with `EVENTTRACKER_`.
- Package management: `uv` (Python), npm (TypeScript E2E only). Not an installable Python package (`[tool.uv] package = false`).
- Pyright type checking covers a curated file list in `pyproject.toml`, not the full tree.
- Entry `sort_key` is derived as `YYYYMMDD`, using `00` when day is missing.
- Scripts in `scripts/` are run as modules: `uv run python -m scripts.<name>`.

## Security Requirements
- NEVER hardcode credentials - always use environment variables
- Use .env.example for templates, never commit .env files
- Sanitize all user inputs before database queries
- Use parameterized queries - never string concatenation for SQL
- Log errors without exposing sensitive data
