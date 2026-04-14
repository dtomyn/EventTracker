# NFR-007 Testing And Verification

- Category: Non-Functional
- Status: Baseline
- Scope: Typing, unit and route tests, browser end-to-end coverage, and current validation constraints.
- Primary Sources: `pyproject.toml`, `tests/*.py`, `tests/e2e/*.py`, `/memories/repo/eventtracker-testing.md`, `/memories/repo/eventtracker-e2e.md`, `/memories/repo/eventtracker-typing.md`

## Requirement Statements

- NFR-007-01 The repository shall maintain automated test coverage for database behavior, entry workflows, search, AI services, group web search, Story Mode narrative and presentation workflows, import utilities, and smoke-level application startup.
- NFR-007-02 The repository shall maintain Python browser end-to-end coverage under `tests/e2e` against an isolated temporary database rather than the live database file.
- NFR-007-02a The repository shall maintain TypeScript Playwright end-to-end coverage under `tstests/e2e` using the same isolation model (temporary database copy, free port, automatic teardown).
- NFR-007-02b TypeScript E2E specs shall import `test` and `expect` from the project harness (`tstests/e2e/helpers/harness.ts`) rather than from `@playwright/test` directly, and shall use Page Object Models under `tstests/e2e/poms/` for reusable page interactions.
- NFR-007-03 The repository shall use an isolated local server instance, a dedicated test group, and disposable test data per run for end-to-end runs.
- NFR-007-04 The repository shall maintain static type checking through Pyright in basic mode over the explicit include set defined in `pyproject.toml`.
- NFR-007-05 The repository shall use the `unittest.TestCase` plus `asyncio.run(...)` convention for async service tests when needed.
- NFR-007-06 The repository shall document known toolchain constraints when they materially affect validation workflows.
- NFR-007-07 The `TESTING=1` environment variable shall bypass CSRF validation in both Python and TypeScript E2E harnesses to allow automated state-changing requests without token management.

## Acceptance Notes

- Current repository guidance notes that Playwright collection on Windows under uv-selected Python 3.14 is unreliable; Python 3.12 is the documented path for that suite.
- Smoke and E2E tests assert user-visible behaviors such as validation states, story and presentation workflows, export shape, and timeline drill-down.