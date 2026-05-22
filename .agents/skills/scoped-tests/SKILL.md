---
name: scoped-tests
description: >-
  Use after changing code, before claiming work is done — run the test command
  scoped to what you changed instead of the whole suite, to avoid the timeout
  and context waste of full-suite runs on a small change.
---

# Scoped test runner

Rule: running the full suite when you changed one part of the codebase wastes
context and time. Pick the narrowest command that still covers the change.

1. **Changed one service or area?** Run only that area's tests.
   Examples for EventTracker:
   - Changed `app/services/entries.py` → `uv run pytest tests/test_entries.py`
   - Changed `app/services/search.py` → `uv run pytest tests/test_search.py`
   - Changed `app/services/topics.py` → `uv run pytest tests/test_topics.py`
   - Changed a single route or template → `uv run pytest tests/test_smoke.py`

2. **Changed shared / core code** (`app/db.py`, `app/main.py`, `app/models.py`,
   `app/schemas.py`)? Run the full suite — changes there ripple everywhere.
   ```
   uv run pytest tests/
   ```

3. **Not sure what a change reaches?** Use the `codebase-search` MCP tool
   `find_references` to trace all callers, then scope accordingly.

4. **Changed frontend (templates, CSS, JS)?** Run the TS E2E suite:
   ```
   npm run test:e2e:ts
   ```
   Or scope to a single spec if only one page was touched.

See `CLAUDE.md` for the full command reference.
