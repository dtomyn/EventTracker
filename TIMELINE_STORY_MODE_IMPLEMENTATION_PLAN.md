## Timeline Story Mode Implementation Handoff

Build a new Timeline Story Mode for EventTracker as a server-rendered feature that can be launched from a current group, year, month, or search query, generates a narrative arc over ordered entries using the existing AI provider stack, optionally saves generated stories as immutable snapshots, and links every citation back to the underlying entries.

## Execution Model

- Use subagents deliberately for bounded implementation phases instead of one broad coding pass.
- Each subagent should complete one phase end-to-end, including code changes, focused validation, and a short handoff note.
- Preserve existing product patterns: FastAPI server-rendered views, additive SQLite schema changes, fail-open AI behavior, minimal JavaScript, and existing timeline/search scope semantics.
- Do not broaden scope beyond the v1 boundaries below.

## v1 Scope

- Launch Story Mode from current timeline scope, search scope, and year/month drill points.
- Support three formats: executive summary, detailed chronology, and what changed recently.
- Generate on demand and optionally save as a snapshot.
- Saved stories are viewable later but not editable in v1.
- Stories must cite underlying entries and link back to entry detail pages.

## Out of Scope

- Story editing
- Auto-regeneration when entries change
- Streaming generation
- Background job queues
- Export/share outside the app
- Saved-story management dashboards beyond direct story viewing

## Phase Plan With Subagent Handoff Prompts

### 1. Phase 1: Data Model and Persistence Foundation

Goal: Add the schema and in-memory/domain types required for Story Mode snapshots and citations.

Depends on: none

Can run in parallel with: none

Expected files:

- c:\DevSource\EventTracker\app\db.py
- c:\DevSource\EventTracker\app\models.py
- c:\DevSource\EventTracker\app\schemas.py
- possibly new tests for schema/model behavior

Implementation requirements:

- Add additive SQLite schema initialization for story tables and indexes.
- Keep schema evolution non-destructive and aligned with existing init_db patterns.
- Add dataclasses/types for story scope, story snapshot, citation rows, and story format/form state.
- Keep field naming explicit and stable for template use.

Suggested table shape:

- timeline_stories: id, scope_type, group_id nullable, query_text nullable, year nullable, month nullable, format, title, narrative_html, narrative_text nullable, generated_utc, updated_utc, provider_name nullable, source_entry_count, truncated_input flag, error_text nullable
- timeline_story_entries: story_id, entry_id, citation_order, quote_text nullable, note nullable

Subagent prompt:

Workspace: c:\DevSource\EventTracker. Implement Phase 1 for Timeline Story Mode. Edit code directly in the repo. Add additive SQLite schema support in app/db.py for storing saved story snapshots and citation rows. Extend app/models.py and app/schemas.py with Story Mode dataclasses and payload/form types that match existing repo style. Keep the design minimal and focused on v1 snapshot persistence. Add or update targeted tests if appropriate for schema initialization or new model helpers. Validate your changes with focused tests or reasoning if tests are not available. Return a concise summary of files changed, schema decisions, and any follow-up constraints for the next phase.

Status:

- Completed

### 2. Phase 2: Story Retrieval and Persistence Service Layer

Goal: Build a dedicated service for resolving scope, collecting entries, saving stories, and loading saved stories.

Depends on: Phase 1

Can run in parallel with: none

Expected files:

- new c:\DevSource\EventTracker\app\services\story_mode.py
- possible targeted updates in c:\DevSource\EventTracker\app\services\entries.py
- tests for story service behavior

Implementation requirements:

- Reuse existing timeline/search entry retrieval logic rather than duplicating SQL.
- Provide helpers to resolve Story Mode scope from group/query/year/month inputs.
- Always normalize story input entries into chronological ascending order.
- Provide save_story, get_story, list_story_citations, and bounded-input preparation helpers.
- Preserve exact cited entry IDs on save so snapshots remain stable.

Subagent prompt:

Workspace: c:\DevSource\EventTracker. Implement Phase 2 for Timeline Story Mode on top of the Phase 1 schema/types. Create a dedicated app/services/story_mode.py module that resolves Story Mode scope from current group/query/year/month inputs, collects entries using existing timeline/search helpers, orders them chronologically ascending, and persists/reloads saved stories plus citations. Reuse existing service patterns instead of introducing a new architecture. Add focused tests for scope resolution and persistence behavior. Validate with targeted pytest where practical. Return the exact functions added, files changed, and any integration assumptions for route/template work.

### 3. Phase 3: AI Story Generation Layer

Goal: Add a dedicated AI generation module for narrative story output.

Depends on: Phase 2

Can run in parallel with: none

Expected files:

- new c:\DevSource\EventTracker\app\services\ai_story_mode.py
- possible reuse or small refactors in c:\DevSource\EventTracker\app\services\ai_generate.py
- tests alongside existing AI tests

Implementation requirements:

- Mirror the provider abstraction and error taxonomy already used in app/services/ai_generate.py.
- Support the three requested output formats.
- Build prompts from bounded ordered entry summaries, not raw unbounded final_text dumps.
- Require structured response content that can be rendered into sections and citations.
- Fail open with user-facing warning/error states consistent with existing generation UX.

Subagent prompt:

Workspace: c:\DevSource\EventTracker. Implement Phase 3 for Timeline Story Mode. Create an AI story generation service that follows the existing patterns in app/services/ai_generate.py, including provider selection, configuration handling, normalization, and error mapping. Support executive summary, detailed chronology, and what changed recently. Use bounded chronological entry context and structured output with citations. Add focused service tests, preferably matching repo conventions in tests/test_ai_generate.py. Validate what you can locally and return a concise summary of interfaces, prompt constraints, and parser behavior.

### 4. Phase 4: FastAPI Routes and Page Integration

Goal: Expose Story Mode through server-rendered endpoints aligned with current timeline/search behavior.

Depends on: Phase 2 and Phase 3

Can run in parallel with: none

Expected files:

- c:\DevSource\EventTracker\app\main.py
- possibly helper context types already defined there
- route tests

Implementation requirements:

- Add GET /story for current scope rendering.
- Add POST /story/generate to create a story result for the selected format and scope.
- Add POST /story/save to persist a generated story snapshot.
- Add GET /story/{id} to render a saved story.
- Reuse _load_group_scope, _load_timeline_scope, and current feedback/error patterns wherever possible.
- Empty scope should be a warning state, not a server error.

Subagent prompt:

Workspace: c:\DevSource\EventTracker. Implement Phase 4 for Timeline Story Mode. Wire the new story services into FastAPI routes in app/main.py, reusing existing scope helpers and server-rendered response patterns. Add GET /story, POST /story/generate, POST /story/save, and GET /story/{id}. Keep behavior aligned with current group/search/timeline semantics and make empty-scope handling user-friendly. Add focused route tests. Validate with targeted tests or type checks if practical. Return the routes added, request parameters supported, and any template context contracts needed next.

### 5. Phase 5: Templates and Launch Affordances

Goal: Add the Story Mode UI and connect it from timeline/search surfaces.

Depends on: Phase 4

Can run in parallel with: route polish or test expansion only after core page contract is stable

Expected files:

- new c:\DevSource\EventTracker\app\templates\story.html
- new story partials under c:\DevSource\EventTracker\app\templates\partials\
- c:\DevSource\EventTracker\app\templates\timeline.html
- c:\DevSource\EventTracker\app\templates\search.html
- c:\DevSource\EventTracker\app\templates\partials\timeline_bucket_cards.html
- possibly CSS in c:\DevSource\EventTracker\app\static\styles.css

Implementation requirements:

- Add a one-click launch affordance for the current timeline scope.
- Add search-driven story launch.
- Add year/month launch points where they fit current UI patterns.
- Render readable sections for phases, turning points, repeated themes, and recent changes.
- Render citations as links back to entry detail pages.
- Keep design aligned with the current app, not a SPA rewrite.

Subagent prompt:

Workspace: c:\DevSource\EventTracker. Implement Phase 5 for Timeline Story Mode. Add a dedicated story page template and any needed partials, then integrate Story Mode launch affordances into timeline.html, search.html, and timeline bucket cards where appropriate. Keep the UI server-rendered and aligned with the existing EventTracker design. Ensure the page exposes scope context, format selection, generate/save actions, clear narrative sections, and citation links back to entries. Update styles minimally if needed. Validate manually through template reasoning and any available route tests. Return files changed and a short explanation of the user flow.

### 6. Phase 6: End-to-End Validation and Regression Coverage

Goal: Prove the feature works and does not break typing or core flows.

Depends on: Phases 1 through 5

Can run in parallel with: none

Expected files:

- tests/test_story_mode.py or similarly named targeted tests
- tests/e2e/test_core_workflows.py and or tests/e2e/test_optional_mocked_workflows.py

Implementation requirements:

- Add focused tests for scope resolution, prompt truncation behavior, save/load snapshots, route responses, and citation rendering.
- Add one happy-path e2e or mocked workflow covering launch, generate, save, and reload.
- Run the existing type check task and targeted pytest coverage for changed files.

Subagent prompt:

Workspace: c:\DevSource\EventTracker. Implement Phase 6 for Timeline Story Mode. Add the remaining focused regression tests and one demo-style workflow that exercises Story Mode from launch through save and reload. Run targeted pytest coverage and the repo type check if feasible. Do not fix unrelated failures outside your changed surface unless they directly block Story Mode. Return the validation commands run, pass/fail results, and any residual risks.

## Recommended Subagent Order

1. Phase 1 subagent
2. Phase 2 subagent
3. Phase 3 subagent
4. Phase 4 subagent
5. Phase 5 subagent
6. Phase 6 subagent

## Execution Guardrails For All Subagents

- Keep changes minimal and consistent with existing code style.
- Reuse existing helpers and patterns before adding new abstractions.
- Preserve the app's fail-open approach for optional AI features.
- Keep all schema changes additive.
- Prefer chronological story ordering even when search results were ranked.
- Do not introduce a background worker or SPA architecture.
- Validate changed surfaces before handing off to the next subagent.

## Definition of Done

- A user can launch Story Mode from timeline/search/year/month contexts.
- The app can generate one of three narrative formats over the scoped entries.
- The generated output reads like a narrative arc with sections and citations.
- The user can save the generated story and revisit it later.
- Saved stories link back to the exact underlying entries used as citations.
- Focused tests and type checks cover the new behavior.