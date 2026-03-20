Critical:

The documented pytest entry point is currently broken. Running uv run pytest from the repo root fails during collection with ModuleNotFoundError for both app and scripts, because the project is configured as non-package mode in pyproject.toml:1-24 and there is no pytest import-path configuration to put the repository root on sys.path. This is a release-blocking testability issue: contributors and CI cannot rely on the stated test command, and regressions can slip through before review.
Several async tests are incompatible with the current pytest setup. The classes in test_ai_generate.py:119-229 and test_group_web_search.py:300-420 inherit from unittest.IsolatedAsyncioTestCase, but under pytest with the installed plugins they fail with RuntimeError: Runner.run() cannot be called from a running event loop. With PYTHONPATH=. added manually, the suite reaches 72 tests and 9 still fail, all for this reason. This means the project’s AI and group web search behaviors are not actually covered by the normal test run.
Entry validation accepts impossible calendar dates. In entries.py:104-137, event_year, event_month, and event_day are validated independently, but there is no cross-field validation for real calendar dates. Inputs like February 31 or April 31 will be stored as valid entries, which is a correctness and data-integrity bug for a timeline app whose primary function is date-ordered history.
The startup schema migration path can destroy user data automatically. In db.py:231-260, if the entries table columns are not an exact match for either the expected schema or one specific legacy schema, startup logs a warning and drops entries, entry_tags, tags, entry_embeddings, and entries_fts, then recreates the table. For a local-first app, silent destructive migration on startup is too risky. A slightly unexpected but recoverable schema drift should fail fast or migrate explicitly, not discard the user’s timeline.

Improvements:

Validation coverage is strong for required fields and URL/link cases in test_core_workflows.py:93 and core create/edit/search flows in test_smoke.py:74-192, but there is no test coverage for impossible dates, leap-year handling, or migration safety. Adding tests for 2025-02-29, 2026-04-31, and a non-destructive startup migration path would materially improve robustness.
The app still uses FastAPI’s deprecated startup hook in main.py:89-90. It works today, but the test run already emits deprecation warnings. Moving to a lifespan handler would reduce warning noise and future maintenance risk.
Security posture is generally reasonable for a local-first app: stored rich text is sanitized conservatively in entries.py:864-917, and external links are restricted to http/https. The one caveat is that URL extraction still performs server-side fetches against arbitrary user-supplied URLs in extraction.py:16-39. If this app is ever exposed beyond localhost, that becomes an SSRF surface and should be constrained or explicitly documented as local-only behavior.

Nitpicks:

The overall structure is maintainable: route handlers are thin, service boundaries are clear, and the templates consistently render sanitized rich text and snippets. The biggest readability issue is not style but confidence erosion from the broken test entry points; fixing that will improve maintainability more than minor formatting changes.

Summary
The project is in decent shape structurally. The separation between FastAPI routes, entry/search services, and templates is clean, the HTML sanitization approach is conservative, and most non-AI flows are well covered by smoke and E2E tests. The main reasons I would not approve it as-is are operational confidence issues and one real data bug: the default test command is broken, async AI-related tests do not run under the normal pytest invocation, invalid calendar dates are accepted, and one migration path can wipe user data.

Conclusion
Request Changes

The contribution has solid architectural value, especially in how it keeps the app server-rendered and relatively simple, but the project needs the four issues above addressed before I’d consider it safe to approve.