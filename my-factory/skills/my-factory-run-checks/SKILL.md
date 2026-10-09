---
name: my-factory-run-checks
description: Run EventTracker's unit tests, pyright, and selected E2E tests in a job worktree and get a ready-to-paste Checks summary. Preloaded into the builder, code reviewer, and approver.
user-invocable: false
---

# Run checks

One command runs the same checks the same way for the builder, the code reviewer, and the approver, so their results are comparable.

## Usage

Run it with `uv run --directory` pointing at the job worktree, so it uses that worktree's code and environment.
Quote the script path; on Windows it may contain backslashes.

```bash
uv run --directory <worktree> python "${CLAUDE_SKILL_DIR}/scripts/run_checks.py"
```

Options:
- `--e2e-py <pytest -k expression>` - also run matching Python E2E tests (`tests/e2e`). Use `--e2e-py all` for the whole suite.
- `--e2e-ts <spec path>` - also run a TypeScript Playwright spec; repeat for more. Runs `npm ci` first if `node_modules` is missing.
- `--skip-unit`, `--skip-pyright` - only when you are re-running a single E2E check you just fixed.

The first run in a fresh worktree is slower because `uv` builds its environment.
The unit suite alone takes about 90 seconds; give the command a timeout of at least 10 minutes when E2E is included.

## When to include E2E
- The diff touches `app/templates/`, `app/static/`, inline JavaScript, or a route that renders HTML: run the E2E tests for those pages.
- build.md names E2E tests as evidence: run exactly those.
- Pure service or database changes: unit tests and pyright are enough.

## Output
The script prints a Markdown block you can paste into build.md, a review, or decision.md:

```markdown
## Checks
- Unit tests: PASS - 489 passed in 84.36s
- Pyright: PASS - 0 errors, 0 warnings, 0 informations
- E2E (python, -k copy_link): FAIL - 1 failed, 3 passed in 21.10s
  Log: C:/Users/.../my-factory-checks-xxxx/e2e-python.log
```

For each failing check it also prints the last lines of output and the full log path.
Exit code 0 means every check that ran passed; 1 means at least one failed.
Report the numbers exactly as printed; never round a failure into a pass.
