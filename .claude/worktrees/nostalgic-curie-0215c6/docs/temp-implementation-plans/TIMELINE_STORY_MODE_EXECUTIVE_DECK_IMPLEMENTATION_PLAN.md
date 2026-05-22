## Timeline Story Mode Executive Deck Implementation Handoff

Build an executive presentation mode for EventTracker Story Mode as a second saved artifact that sits alongside the existing narrative snapshot. The v1 implementation should generate the current narrative output exactly as today, optionally generate an executive deck from the same scoped entries, compile that deck with Marpit into standalone presentation HTML and CSS, persist both the source and compiled deck artifact, and let the user switch between Narrative and Presentation views on the saved story page.

## How To Use This Document In A New Chat

- Tell the new chat to read this file first.
- Ask it to implement one phase at a time, not the whole plan in one pass.
- After each phase, require focused validation and a short handoff summary for the next phase.
- Treat this document as the source of truth for v1 scope and architecture decisions.

## Execution Model

- Use bounded implementation phases instead of a single broad coding pass.
- Preserve existing product patterns: FastAPI server-rendered views, additive SQLite schema changes, fail-open AI behavior, minimal JavaScript, and current timeline/search scope semantics.
- Keep the current Story Mode narrative path stable while adding deck support as a parallel artifact.
- Do not widen scope beyond the v1 boundaries below.

## v1 Scope

- Keep the current Story Mode narrative generation and save flow intact.
- Add an optional executive deck artifact for the same scoped story generation request.
- Persist the deck as a saved artifact linked to the story snapshot.
- Render the saved deck as a standalone presentation document and expose it through the saved story page.
- Let the user switch between Narrative and Presentation modes on saved stories that have both artifacts.
- Use Markdown-native and application-generated visual structures that look executive-ready without introducing client-side diagram engines in v1.
- Keep citations grounded in the same scoped entries used for narrative generation.

## Out of Scope

- Mermaid rendering in the browser
- Chart.js or other client-side chart libraries
- PDF, PPTX, or image export
- Deck editing
- Auto-regeneration when entries change
- Background jobs or streaming generation
- Deck version history beyond the saved story snapshot itself

## Non-Negotiable v1 Decisions

- Use a separate artifact table instead of adding many nullable deck fields to `timeline_stories`.
- Keep `_sanitize_story_html` narrow and unchanged for narrative prose.
- Add a deck-specific render and sanitization path rather than widening the narrative HTML allowlist.
- Use `@marp-team/marpit` for v1 rendering, not `@marp-team/marp-cli`.
- Compile the deck during generate and save flow, store compiled HTML and CSS, and serve the stored compiled artifact later.
- Treat Node plus Marpit as an optional runtime dependency for presentation generation. If unavailable, narrative generation must still succeed.
- Defer Mermaid entirely in v1. Do not include client-side script execution in the presentation document.
- Keep deck source JSON and Markdown deterministic in application code. Do not ask the model for raw HTML.

## Current Repo Constraints That Matter

- `app/db.py` currently defines `timeline_stories` and `timeline_story_entries` for narrative snapshots and citations only.
- `app/models.py` currently has `TimelineStorySnapshot` and `TimelineStoryCitation` but nothing for artifacts or deck generation.
- `app/schemas.py` currently has only story save payloads for narrative snapshots.
- `app/services/ai_story_mode.py` currently supports only structured narrative generation with title, sections, and citations.
- `app/services/story_mode.py` persists and reloads only the narrative snapshot and citation rows.
- `app/main.py` currently renders saved stories and sanitizes story HTML using a very small tag allowlist suitable for prose only.
- `app/templates/story.html` currently has one view path and one save form for narrative output.
- `app/route_helpers.py` mirrors active Story Mode helper logic. Either consolidate it early or keep mirrored behavior in sync.
- `package.json` currently exists only for Playwright and TypeScript tooling. Marpit will be the first runtime Node dependency used by the application itself.

## Target Artifact Design

Each saved story remains the parent snapshot. The executive deck is a child artifact of that story.

Artifact kinds for now:

- `executive_deck`

Artifact lifecycle in v1:

- User generates a story and optionally requests a deck.
- The app generates the narrative and the deck from the same scoped entries.
- The app compiles the deck to HTML and CSS immediately.
- The generated page carries hidden fields for the compiled deck artifact so the later save action does not need to recompile.
- When the user saves the story, the app writes the narrative snapshot to `timeline_stories`, citations to `timeline_story_entries`, and the deck artifact to `timeline_story_artifacts`.
- When the user revisits a saved story, the page offers a Narrative or Presentation toggle if an `executive_deck` artifact exists.

## Data Model Plan

Add a new table named `timeline_story_artifacts` with these columns:

- `id`: integer primary key
- `story_id`: integer not null, foreign key to `timeline_stories(id)` with delete cascade
- `artifact_kind`: text not null
- `source_format`: text not null
- `source_text`: text not null
- `compiled_html`: text not null default `''`
- `compiled_css`: text not null default `''`
- `metadata_json`: text not null default `'{}'`
- `generated_utc`: text not null
- `compiled_utc`: text nullable
- `compiler_name`: text nullable
- `compiler_version`: text nullable

Indexes and constraints:

- unique index on `story_id, artifact_kind`
- index on `story_id`
- optional index on `artifact_kind, generated_utc desc` if later list views need it

Recommended metadata JSON keys for `executive_deck`:

- `deck_version`
- `theme_name`
- `slide_count`
- `source_entry_count`
- `provider_name`
- `truncated_input`
- `scope_snapshot`
- `citation_orders_by_slide`
- `visual_kinds`
- `generation_warning`

Do not add deck columns to `timeline_stories` in v1.

## Deck Content Contract

The model should output deck structure as JSON only. The app owns the final markdown assembly.

Required deck-level fields:

- `title`
- `subtitle` nullable
- `slides`

Required slide-level fields:

- `slide_key`
- `headline`
- `purpose`
- `body_points`
- `callouts`
- `visuals`
- `citations`

Allowed slide purposes in v1:

- `title`
- `summary`
- `turning_point`
- `trajectory`
- `quote`
- `close`

Allowed visual kinds in v1:

- `kpi_strip`
- `phase_timeline`
- `pull_quote`

Application-owned responsibilities:

- validate JSON shape
- validate citations against allowed entry ids
- normalize slide ordering
- assemble Marpit front matter
- translate visual blocks into markdown-native or application-generated markup
- compile to HTML and CSS
- sanitize stored compiled output

Model-owned responsibilities:

- slide headlines
- concise body points
- executive callout text
- which allowed visual kind to request
- which scoped entries to cite

## File-By-File Work Map

Primary existing files that must change:

- `app/db.py`
- `app/models.py`
- `app/schemas.py`
- `app/services/ai_story_mode.py`
- `app/services/story_mode.py`
- `app/main.py`
- `app/templates/story.html`
- `app/static/styles.css`
- `tests/test_db.py`
- `tests/test_story_mode.py`
- `tests/test_ai_story_mode.py`
- `tests/test_story_routes.py`
- `tests/e2e/test_story_mode.py`

Recommended new files:

- `app/services/story_deck.py`
- `app/templates/story_presentation.html`
- `scripts/render_story_deck.mjs`
- optionally `tests/test_story_deck.py` if deck logic becomes large enough to merit its own test module

Responsibilities by file:

`app/db.py`

- Add additive schema creation for `timeline_story_artifacts`.
- Keep schema evolution safe and non-destructive.
- Update schema tests to assert the new table, columns, and indexes.

`app/models.py`

- Add a `StoryArtifactKind` type alias.
- Add a `TimelineStoryArtifact` dataclass.
- Add `GeneratedExecutiveDeck` and `GeneratedExecutiveDeckSlide` dataclasses.

`app/schemas.py`

- Add `TimelineStoryArtifactSavePayload`.
- Add any form or route payloads needed for saving a generated deck artifact.

`app/services/story_mode.py`

- Add `save_story_artifact`.
- Add `get_story_artifact`.
- Add `list_story_artifacts` only if the template context needs more than one artifact lookup.
- Keep existing story save and citation logic intact.

`app/services/ai_story_mode.py`

- Add a second system prompt for deck generation.
- Add deck response parsing and validation.
- Reuse current provider selection and error taxonomy.
- Reuse bounded ordered entry preparation.

`app/services/story_deck.py`

- Build deterministic Marpit markdown from structured deck JSON.
- Define the v1 theme and markdown assembly rules.
- Invoke the Node renderer script.
- Sanitize compiled deck HTML and CSS before persistence.
- Return a persistence-ready artifact payload.

`scripts/render_story_deck.mjs`

- Read markdown from stdin or a temp file path argument.
- Use `@marp-team/marpit` to render html and css.
- Return structured JSON to stdout so Python can parse success and failure cleanly.

`app/main.py`

- Extend generation flow to optionally create a deck artifact.
- Keep narrative generation success independent from deck generation success.
- Pass generated deck artifact fields through the save form hidden inputs.
- Save the artifact alongside the story snapshot.
- Add a saved presentation route such as `GET /story/{story_id}/presentation`.
- Add deck-specific template context fields like `has_presentation` and `presentation_url`.

`app/templates/story.html`

- Add an `include_deck` checkbox to the generate form.
- Add hidden fields for generated deck artifact state in the save form.
- Add a Narrative and Presentation toggle for saved stories that have a deck artifact.
- Render the presentation via iframe pointing to the standalone presentation route.

`app/templates/story_presentation.html`

- Wrap stored compiled HTML and CSS in a standalone document.
- Keep the page self-contained and script-free in v1.
- Ensure it is readable in an iframe and as a direct URL.

`app/static/styles.css`

- Add minimal styling for the story view toggle and presentation frame.
- Do not restyle the whole story page.

`package.json`

- Add `@marp-team/marpit` under `dependencies`.
- Optionally add a small debug script for manual deck rendering if useful.

## Phase Plan With Subagent Handoff Prompts

### 1. Phase 1: Artifact Schema and Persistence Foundation

Goal: Add deck artifact persistence without changing the current narrative snapshot behavior.

Depends on: none

Can run in parallel with: none

Expected files:

- `c:\DevSource\EventTracker\app\db.py`
- `c:\DevSource\EventTracker\app\models.py`
- `c:\DevSource\EventTracker\app\schemas.py`
- `c:\DevSource\EventTracker\app\services\story_mode.py`
- focused schema and persistence tests

Implementation requirements:

- Add `timeline_story_artifacts` as an additive schema change.
- Add dataclasses and payload types for story artifacts and executive deck generation results.
- Add artifact save and load helpers without disturbing current story save behavior.
- Keep the design minimal and stable for template and route use later.

Subagent prompt:

Workspace: `c:\DevSource\EventTracker`. Read `docs/temp-implementation-plans/TIMELINE_STORY_MODE_EXECUTIVE_DECK_IMPLEMENTATION_PLAN.md` and implement Phase 1 only. Edit code directly in the repo. Add additive SQLite schema support in `app/db.py` for `timeline_story_artifacts`, extend `app/models.py` and `app/schemas.py` with artifact and deck dataclasses and payloads, and extend `app/services/story_mode.py` with minimal artifact persistence helpers. Keep current narrative Story Mode behavior unchanged. Add or update focused tests for schema initialization and artifact persistence. Validate with targeted tests if practical. Return a concise summary of files changed, schema decisions, and follow-up constraints for Phase 2.

### 2. Phase 2: AI Executive Deck Generation Contract

Goal: Add a structured AI generation path for executive decks using the existing provider stack.

Depends on: Phase 1

Can run in parallel with: none

Expected files:

- `c:\DevSource\EventTracker\app\services\ai_story_mode.py`
- focused AI service tests

Implementation requirements:

- Mirror current provider selection, configuration handling, and error taxonomy.
- Reuse bounded chronological entry preparation.
- Add a deck-specific system prompt and parser that require JSON-only output.
- Validate that every cited entry id exists in the provided scoped entries.
- Keep Mermaid out of the response contract in v1.

Subagent prompt:

Workspace: `c:\DevSource\EventTracker`. Read `docs/temp-implementation-plans/TIMELINE_STORY_MODE_EXECUTIVE_DECK_IMPLEMENTATION_PLAN.md` and implement Phase 2 only. Extend `app/services/ai_story_mode.py` with an executive deck generation path that follows the same provider and error patterns as the existing narrative generation logic. Require structured JSON output, bounded chronological entry context, and validated citations. Add focused tests alongside the existing AI story tests. Validate what you can locally and return the new interfaces, prompt constraints, and parser behavior for Phase 3.

### 3. Phase 3: Deck Assembly and Marpit Renderer Integration

Goal: Turn structured deck JSON into a saved compiled artifact.

Depends on: Phase 1 and Phase 2

Can run in parallel with: none

Expected files:

- new `c:\DevSource\EventTracker\app\services\story_deck.py`
- new `c:\DevSource\EventTracker\scripts\render_story_deck.mjs`
- `c:\DevSource\EventTracker\package.json`
- focused deck assembly and renderer tests

Implementation requirements:

- Build deterministic Marpit markdown from the validated deck model.
- Use markdown-native and application-generated visual structures only.
- Add a small Node renderer wrapper around `@marp-team/marpit`.
- Return compiled html, css, and metadata to Python.
- Sanitize compiled output before persistence.
- If Node or Marpit is unavailable, return a controlled failure that lets the narrative path keep working.

Subagent prompt:

Workspace: `c:\DevSource\EventTracker`. Read `docs/temp-implementation-plans/TIMELINE_STORY_MODE_EXECUTIVE_DECK_IMPLEMENTATION_PLAN.md` and implement Phase 3 only. Create a dedicated `app/services/story_deck.py` module that builds Marpit markdown from the structured executive deck model, invokes a new `scripts/render_story_deck.mjs` renderer that uses `@marp-team/marpit`, sanitizes the compiled output, and returns a persistence-ready artifact payload. Update `package.json` for the runtime dependency. Add focused tests for markdown assembly, renderer failure handling, and sanitization. Validate locally if practical and return the exact interfaces and runtime assumptions for Phase 4.

### 4. Phase 4: Route Integration and Story Save Flow

Goal: Wire optional deck generation into the existing Story Mode generate and save flow.

Depends on: Phase 1 through Phase 3

Can run in parallel with: none

Expected files:

- `c:\DevSource\EventTracker\app\main.py`
- possibly `c:\DevSource\EventTracker\app\route_helpers.py` if mirrored helpers remain active
- focused route tests

Implementation requirements:

- Extend `POST /story/generate` with an `include_deck` form field.
- Generate the deck artifact after narrative generation when requested.
- If deck generation or compilation fails, keep the page usable and show a warning while still rendering the narrative.
- Extend `POST /story/save` to persist the artifact if generated deck fields are present.
- Add `GET /story/{story_id}/presentation` to render the stored deck artifact.
- Do not compile on request in v1. Use the stored compiled artifact.

Subagent prompt:

Workspace: `c:\DevSource\EventTracker`. Read `docs/temp-implementation-plans/TIMELINE_STORY_MODE_EXECUTIVE_DECK_IMPLEMENTATION_PLAN.md` and implement Phase 4 only. Wire the new executive deck services into `app/main.py` and any mirrored story helper path still in use. Extend `POST /story/generate` with optional deck generation, preserve fail-open behavior when deck generation fails, extend `POST /story/save` to persist the deck artifact, and add `GET /story/{story_id}/presentation` to serve the stored compiled deck. Add focused route tests. Validate with targeted tests if practical and return the new request parameters, template context contracts, and any follow-up needs for Phase 5.

### 5. Phase 5: Story Page UI and Presentation View

Goal: Surface Presentation mode cleanly in the existing Story Mode UI.

Depends on: Phase 4

Can run in parallel with: CSS polish only after template contract is stable

Expected files:

- `c:\DevSource\EventTracker\app\templates\story.html`
- new `c:\DevSource\EventTracker\app\templates\story_presentation.html`
- `c:\DevSource\EventTracker\app\static\styles.css`

Implementation requirements:

- Add an `include_deck` affordance to the generate form.
- Add a Narrative and Presentation toggle only when a saved story has a deck artifact.
- Render presentation view via iframe to the standalone presentation route.
- Keep the page accessible, server-rendered, and aligned with the existing design language.
- Do not add a client-side framework.

Subagent prompt:

Workspace: `c:\DevSource\EventTracker`. Read `docs/temp-implementation-plans/TIMELINE_STORY_MODE_EXECUTIVE_DECK_IMPLEMENTATION_PLAN.md` and implement Phase 5 only. Update the Story Mode templates and styles so a user can request deck generation, save a story that includes a deck artifact, and switch between Narrative and Presentation modes on the saved story page. Add a standalone `story_presentation.html` wrapper for the stored compiled deck artifact. Keep JavaScript minimal and accessible. Validate by reasoning and any focused route or UI tests already available. Return files changed and a short explanation of the user flow.

### 6. Phase 6: Focused Regression and End-To-End Validation

Goal: Prove deck support works without regressing existing Story Mode behavior.

Depends on: Phases 1 through 5

Can run in parallel with: none

Expected files:

- updated targeted unit and route tests
- `tests/e2e/test_story_mode.py`
- optionally one new TypeScript e2e if the existing Python suite becomes awkward

Implementation requirements:

- Cover schema initialization, artifact save and load, AI parser validation, generate route behavior, save route behavior, presentation route behavior, and UI toggle behavior.
- Add failure cases for missing Node, missing artifact, malformed AI output, and sanitization.
- Run focused pytest coverage and the existing type check task if practical.
- Do not fix unrelated failures unless they directly block this feature.

Subagent prompt:

Workspace: `c:\DevSource\EventTracker`. Read `docs/temp-implementation-plans/TIMELINE_STORY_MODE_EXECUTIVE_DECK_IMPLEMENTATION_PLAN.md` and implement Phase 6 only. Add the remaining focused regression tests and one happy-path workflow that exercises Story Mode with a generated executive deck from generation through save and reload. Run targeted pytest coverage and the repo type check if feasible. Do not fix unrelated failures outside the changed surface unless they directly block this feature. Return the validation commands run, pass or fail results, and any residual risks.

## Recommended Implementation Order

1. Phase 1
2. Phase 2
3. Phase 3
4. Phase 4
5. Phase 5
6. Phase 6

## Validation Checklist For Every Phase

- Keep schema changes additive.
- Keep current Story Mode narrative behavior intact.
- Reuse existing helpers and route patterns before adding new abstractions.
- Preserve fail-open behavior for optional AI features.
- Keep chronological ordering for deck generation input just as for narrative generation.
- Do not add Mermaid, Chart.js, or export features in v1.
- If `app/route_helpers.py` still mirrors live Story Mode logic, either update it in the same phase or explicitly consolidate it before proceeding.

## Definition Of Done

- A user can generate the current Story Mode narrative exactly as before.
- A user can optionally request an executive deck during story generation.
- The app can compile and persist the deck as a child artifact of the saved story snapshot.
- A saved story with a deck artifact exposes both Narrative and Presentation modes.
- Presentation mode renders from stored compiled deck HTML and CSS, not from on-request compilation.
- Narrative generation still succeeds even when deck generation fails.
- Focused tests cover schema, parser, persistence, routes, and UI behavior for the new artifact.

## Suggested New Chat Kickoff Prompt

Workspace: `c:\DevSource\EventTracker`. Read `docs/temp-implementation-plans/TIMELINE_STORY_MODE_EXECUTIVE_DECK_IMPLEMENTATION_PLAN.md` first, then implement Phase 1 only. Do the code changes directly in the repo, run focused validation, and return a concise handoff note for Phase 2. Keep all current Story Mode narrative behavior intact while adding the artifact persistence foundation for executive decks.