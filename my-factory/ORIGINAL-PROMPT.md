Build a simple, visual "software factory" in this repo using a Claude Code skill, subagents, and markdown files.
Keep it small: a viewer should understand the whole thing in a few minutes.
Inspect the repo first so the agents know its language, test command, and conventions, and record those facts once in the shared `my-factory-repo-conventions` skill that every agent preloads (see "Shared skills").

## The orchestrator

The factory is one skill, /my-factory, whose master copy lives at `my-factory/skills/my-factory/SKILL.md` (see "Master copies and sync").
Whichever Claude Code session runs it becomes the orchestrator: it runs the loop below, spawns every agent, and is the only writer of my-factory/board.json.
Set `disable-model-invocation: true` in the skill frontmatter so it only runs when the user types the command (it merges into main).

## The loop (edit this to change the factory)

```text
                   ┌──────────────────┐
                   │     feature      │
                   └─────────┬────────┘
                             ▼
                   ┌──────────────────┐
                   │   spec-writer    │
                   │       opus       │
                   └─────────┬────────┘
                             ▼
                   ┌──────────────────┐
                   │     builder      │◄────────────────────┐
                   │       opus       │                     │
                   └─────────┬────────┘                     │
        ┌─────────────┬──────┴──────┬─────────────┐         │
        ▼             ▼             ▼             ▼         │
  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐   │
  │  security │ │     ux    │ │ ui design │ │    code   │   │
  │    opus   │ │   sonnet  │ │   sonnet  │ │   sonnet  │   │
  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘   │
        └─────────────┴──────┬──────┴─────────────┘         │
                             ▼                              │
                   ┌──────────────────┐   any CHANGES:      │
                   │   WAIT FOR ALL   ├─────────────────────┘
                   │   4 reviews in   │   resume builder
                   └─────────┬────────┘   (max 2 rounds)
                             ▼ all PASS
                   ┌──────────────────┐
                   │     approver     │
                   │       opus       │
                   └─┬───────────────┬┘
             APPROVE │               │ ESCALATE
                     ▼               ▼
              ┌─────────────┐ ┌─────────────┐
              │merge to main│ │ needs-human │ ◄── /my-factory approve
              └─────────────┘ └─────────────┘
```

"max 2 rounds" means the builder is resumed at most twice for reviewer findings after round 1, so a job gets up to 3 review rounds.
Expose this as a single constant `MAX_REWORK_ROUNDS = 2` at the top of the skill.
The count restarts after each human `rework`.
If reviews still request changes when the cap is reached, the job goes to needs-human without running the approver.

## Target file system

`my-factory/` holds the master copies of every factory agent and skill.
`.claude/` (Claude Code) and `.agents/` (the cross-tool convention this repo already uses for skills) hold generated copies produced by `my-factory/sync.ps1`.

```text
my-factory/
  README.md                        # one-page map of the factory and how to sync
  ORIGINAL-PROMPT.md               # this prompt
  sync.ps1                         # mirrors agents/ and skills/ into .claude/ and .agents/
  agents/                          # MASTER: one file per agent in the loop, all prefixed "my-factory-"
    my-factory-spec-writer.md
    my-factory-builder.md
    my-factory-security-reviewer.md
    my-factory-ux-reviewer.md
    my-factory-ui-reviewer.md
    my-factory-code-reviewer.md
    my-factory-approver.md
  skills/                          # MASTER: one folder per skill, all prefixed "my-factory"
    my-factory/SKILL.md            # the orchestrator's instructions (user-invoked)
    my-factory-repo-conventions/SKILL.md     # shared: stack, layout, commands, house rules
    my-factory-acceptance-criteria/SKILL.md  # shared: AC contract from spec to build to review
    my-factory-review-protocol/SKILL.md      # shared: how all four reviewers work
    my-factory-run-checks/
      SKILL.md                     # shared: how and when to run checks
      scripts/run_checks.py        # unit tests, pyright, chosen E2E -> Markdown summary
    my-factory-app-preview/
      SKILL.md                     # shared: how to look at the running app safely
      scripts/preview.py           # temp DB copy, free port, screenshots or Playwright flows
  board.json                       # durable state, drives the dashboard (gitignored)
  dashboard.html                   # visual board, polls board.json
  backlog.md                       # queue of features for /my-factory next
  jobs/003-rate-limiting/          # one folder per job (gitignored)
    feature.md                     # the raw request, written by the orchestrator
    spec.md
    build.md
    round-1/                       # one folder per review round,
      review-security.md           # one file per reviewer
      review-ux.md
      review-ui.md
      review-code.md
    rework-1.md                    # human feedback from /my-factory rework, if any
    decision.md
.claude/                           # GENERATED by sync.ps1, committed, never edited by hand
  agents/my-factory-*.md
  skills/my-factory*/
.agents/                           # GENERATED by sync.ps1, committed, never edited by hand
  agents/my-factory-*.md
  skills/my-factory*/
.worktrees/<job-id>/               # git worktree per job (gitignored)
```

Job ids are the next free three-digit number plus a short kebab-case slug, for example `004-copy-entry-link`.
Seed backlog.md with two or three small, real features for this repo, written as `- [ ]` checkbox lines.

## Master copies and sync

- Edit agents and skills only under `my-factory/agents/` and `my-factory/skills/`.
- `my-factory/sync.ps1` (PowerShell 7) copies them to `.claude/agents`, `.claude/skills`, `.agents/agents`, and `.agents/skills`.
- Copies are real files, not symlinks, so they work on Windows without developer mode and in every tool that reads those folders.
- Each generated Markdown file gets a marker right after its frontmatter: `<!-- Generated by my-factory/sync.ps1 from <master path>. Edit that file, then re-run the sync. -->`. Other files are copied byte for byte.
- The sync validates the master set and exits 2 if any agent file or skill folder lacks the `my-factory` prefix, a skill has no SKILL.md, a frontmatter `name:` does not match its file or folder name, or an agent preloads a `my-factory*` skill that does not exist.
- The sync owns only `my-factory*` items in the targets: it writes those, prunes the ones no longer in the master set (including emptied folders), and never touches other agents or skills.
- `-Check` reports drift without writing and exits 1 if anything is out of date, so it can run in CI or a pre-commit hook.
- Commit the masters and the generated copies together.

## Shared skills

Review what each agent does and pull every procedure that more than one agent repeats into a shared skill, so each rule lives in one place and the agent files shrink to their role-specific checklist.
Name each one `my-factory-<topic>` and keep it under `my-factory/skills/` so it syncs with everything else.

- Agents preload their skills through a YAML `skills:` list in their frontmatter, so the skill content is in context from the start and no `Skill` tool is needed.
- Shared skills set `user-invocable: false`: hidden from the slash menu but still preloadable. (A skill with `disable-model-invocation: true`, like the orchestrator, cannot be preloaded.)
- Deterministic work goes into a bundled Python script (standard library plus the project's own dev dependencies), referenced as `"${CLAUDE_SKILL_DIR}/scripts/<file>.py"` in quotes because the path may contain backslashes on Windows.
- Scripts run with `uv run --directory <worktree> python ...` so they use the job worktree's code and environment, and refuse to run anywhere else.

| Skill | Purpose | Preloaded by |
|-------|---------|--------------|
| `my-factory-repo-conventions` | Single source of truth for the repo facts from the inspection: stack, where code goes, commands, house rules, Windows/Git Bash notes | all seven agents |
| `my-factory-acceptance-criteria` | The AC contract: `AC-<n>: <observable behaviour>. Verified by: <how>.` in spec.md, an evidence table with one row per AC in build.md, and how reviewers and the approver check the evidence | spec writer, builder, code reviewer, approver |
| `my-factory-review-protocol` | Shared reviewer procedure: inputs, diff commands, later rounds (mark each earlier finding fixed, not fixed, or disputed), blocker/major/minor grading, verdict rule, file format | security, UX, UI, and code reviewers |
| `my-factory-run-checks` | `run_checks.py` runs unit tests, pyright, and optional Python (`--e2e-py <-k expr or all>`) and TypeScript (`--e2e-ts <spec>`) E2E, writes full logs to a temp folder, prints a pasteable `## Checks` block plus failure tails, exits 1 on any failure, and reports "no tests matched" when a selector finds nothing | builder, code reviewer, approver |
| `my-factory-app-preview` | `preview.py` copies the main checkout's `data/EventTracker.db` to a temp folder (or `--empty-db`), starts uvicorn from the worktree on a free port with AI providers blanked, then either `shoot`s pages at widths 1280 and 375 in light and dark (flagging HTTP errors, horizontal overflow, and console errors) or `run`s a command with `PREVIEW_BASE_URL` set; it always stops the server and deletes the copy | builder, UX reviewer, UI reviewer |

Details the scripts must handle:
- In `preview.py run`, a leading `python` is replaced with the worktree's interpreter so Playwright and the app's packages are importable.
- Git Bash rewrites `/search` into `C:/Program Files/Git/search`; the skill tells agents to prefix commands with `MSYS_NO_PATHCONV=1`, and `preview.py` rejects Windows-looking paths with that hint.
- Theme screenshots set both the browser colour scheme and `localStorage["theme"]`, which is how the app persists dark mode.

The orchestrator's agent table lists each agent's preloaded skills.
The agent files keep only their role: inputs, role-specific checks, and their output file's extra section.

## Example files

`my-factory/agents/<name>.md` (Claude Code subagent syntax; synced to `.claude/agents/` and `.agents/agents/`):

```markdown
---
name: my-factory-example-agent
description: One line on when the orchestrator should use this agent.
tools: Read, Grep, Glob, Bash, Write
model: sonnet
skills:
  - my-factory-repo-conventions
  - my-factory-review-protocol
---
What this agent reads, what it does, and which file it writes.
```

Every agent name and file name starts with "my-factory-" so they are namespaced alongside the /my-factory skill.
The builder also gets Edit.
Models follow the diagram: spec-writer, builder, security, and approver on opus; ux, ui design, and code on sonnet.

`my-factory/board.json`:

```json
{ "updated": "2026-10-08T14:03:00Z",
  "jobs": [ { "id": "003-rate-limiting", "title": "Rate limiting",
  "branch": "my-factory/003-rate-limiting",
  "stage": "review", "round": 2,
  "reviews": { "security": "CHANGES", "ux": "PASS", "ui": "pending", "code": "PASS" },
  "history": [ { "security": "CHANGES", "ux": "PASS", "ui": "CHANGES", "code": "PASS" } ],
  "reworks": 0, "builder": "builder-003-rate-limiting",
  "decision": null, "note": "",
  "created": "2026-10-08T13:40:00Z", "updated": "2026-10-08T14:03:00Z" } ] }
```

- `stage` is one of `spec`, `build`, `review`, `approve`, `needs-human`, `merged`.
- `reviews` is the current round; when a new round starts, the orchestrator appends it to `history` and resets every chip to `pending`.
- `reworks` counts human rework notes; `note` explains why a job is in needs-human.
- The orchestrator rewrites the whole file after every change and creates it as `{ "jobs": [] }` if missing.

## Rules
- Agents get context from files, not the conversation, and write only their own file.
- The orchestrator passes forward-slash absolute paths in every agent prompt (Git Bash on Windows strips backslashes).
- Each review starts with VERDICT: PASS or CHANGES; decision.md starts with APPROVE or ESCALATE.
- Reviewers use CHANGES only for blocker or major findings; minor findings alone are a PASS.
- From round 2 on, each reviewer checks its own previous review was addressed and looks for regressions, without raising new nitpicks on unchanged code.
- The builder works and commits on a feature branch (my-factory/<job-id>) inside its own git worktree (.worktrees/<job-id>), so the main checkout never switches branches.
- The builder tests against the spec's acceptance criteria, test-first, and records evidence per criterion in build.md.
- The builder commits without co-author trailers, never pushes, and never edits CHANGELOG.md or generated files.
- WAIT FOR ALL: a review round is done only when every reviewer's file exists in the round folder. Never resume the builder mid-round.
- Spawn the four reviewers in a single message so they run in parallel.
- Spawn the builder with a name (builder-<job-id>) and resume that same builder with SendMessage for each new round, pointing it at the round folder.
- If the named builder no longer exists (for example in a new session), spawn a fresh builder with the same name and point it at spec.md, build.md, and the latest round folder.
- If an agent does not write its file or its first line is not a valid verdict, re-spawn it once, then escalate to needs-human.
- UX and UI reviewers run the app from the worktree against a temp copy of data/EventTracker.db (never the original) and take Playwright screenshots; the UI reviewer covers 1280px and 375px widths in light and dark theme.
- The approver escalates on any failed check, schema or user-data changes, dependency changes, auth/CSRF/secrets changes, or open questions resolved by guessing.

## Merge
- First merge main into the job branch inside the worktree and re-run the unit tests; on conflict or failure, go to needs-human.
- Then merge with `--no-ff` in whichever worktree has main checked out (or a temporary one), remove the job worktree, and delete the branch.
- Merging is local only; never push.

## Commands
/my-factory <feature>               run a new job through the loop
/my-factory next                    run the first unchecked item in backlog.md and tick it with its job id
/my-factory approve <job-id>        merge a needs-human job into main
/my-factory rework <job-id> <note>  send a needs-human job back to the builder with feedback (written to rework-<n>.md), then review again

## Dashboard
One self-contained HTML file, served from the repo root with `uv run python -m http.server 8765 --bind 127.0.0.1 --directory my-factory` (bind to localhost; `python3` is often missing on Windows).
Also add it to .claude/launch.json as `my-factory-dashboard`.
Jobs are cards moving across the loop's stages as columns: Spec, Build, Review, Approve, Needs human, Merged.
Each review round shows as a row of reviewer chips that fill in as verdicts land.
needs-human jobs stand out and show the approve and rework commands.
Click a card to read its markdown files, requesting only files the board says exist.
Render markdown with a small escape-first renderer so agent-written HTML is never executed.
Support light and dark mode via prefers-color-scheme, stack the columns below 960px, and keep the last good board when polling fails.
Clean and minimal.

## Housekeeping
- Gitignore my-factory/board.json, my-factory/jobs/, and .worktrees/.
- Never use the em dash; use a plain dash.
- Verify the dashboard in a browser with temporary sample data, then delete the sample data.
- Verify the sync end to end: `-Check` reports drift, a normal run writes the copies, a second `-Check` is clean, a removed master item is pruned from both targets, and a badly named master item fails validation.
- Add a short `my-factory/README.md` that maps the folder, lists the shared skills, and shows the sync and dashboard commands.
- Verify both scripts for real: run `run_checks.py` with an E2E selector, run `preview.py shoot` and `preview.py run`, and fix anything they reveal in the app (the first run found the timeline's action buttons overflowing at 375px).
