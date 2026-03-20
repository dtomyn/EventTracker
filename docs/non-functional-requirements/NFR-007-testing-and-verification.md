# NFR-007 Testing And Verification

- Category: Non-Functional
- Status: Baseline
- Scope: Typing, unit and route tests, browser end-to-end coverage, and current validation constraints.
- Primary Sources: `pyproject.toml`, `tests/*.py`, `tests/e2e/*.py`, `/memories/repo/eventtracker-testing.md`, `/memories/repo/eventtracker-e2e.md`, `/memories/repo/eventtracker-typing.md`

## Requirement Statements

- NFR-007-01 The repository shall maintain automated test coverage for database behavior, entry workflows, search, AI services, group web search, Story Mode, import utilities, and smoke-level application startup.
- NFR-007-02 The repository shall maintain browser end-to-end coverage under `tests/e2e` against an isolated temporary database rather than the live database file.
- NFR-007-03 End-to-end runs shall use an isolated local server instance, a dedicated test group, and disposable test data per run.
- NFR-007-04 The repository shall maintain static type checking through Pyright in basic mode over the explicit include set defined in `pyproject.toml`.
- NFR-007-05 Async service tests shall follow the current repository convention of `unittest.TestCase` plus `asyncio.run(...)` when needed.
- NFR-007-06 Known toolchain constraints shall be documented when they materially affect validation workflows.

## Acceptance Notes

- Current repository guidance notes that Playwright collection on Windows under uv-selected Python 3.14 is unreliable; Python 3.12 is the documented path for that suite.
- Smoke and E2E tests assert user-visible behaviors such as validation states, story workflows, export shape, and timeline drill-down.