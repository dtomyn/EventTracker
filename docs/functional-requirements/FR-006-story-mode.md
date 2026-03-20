# FR-006 Story Mode

- Category: Functional
- Status: Baseline
- Scope: Scoped story generation, citation rendering, story snapshot persistence, and saved-story retrieval.
- Primary Sources: `README.md`, `TIMELINE_STORY_MODE_IMPLEMENTATION_PLAN.md`, `app/main.py`, `app/services/story_mode.py`, `app/services/ai_story_mode.py`, `app/templates/story.html`, `tests/test_story_mode.py`, `tests/test_story_routes.py`

## Requirement Statements

- FR-006-01 The system shall provide Story Mode at `GET /story` for the current timeline or search scope.
- FR-006-02 Story Mode shall accept `q`, `group_id`, `year`, `month`, and `format` parameters.
- FR-006-03 Story Mode shall support the formats `executive_summary`, `detailed_chronology`, and `recent_changes`.
- FR-006-04 Story generation shall use chronologically ordered entries, even when the source scope was produced by ranked search.
- FR-006-05 When no entries match the requested scope, Story Mode shall render a non-fatal warning state rather than an HTTP error.
- FR-006-06 The system shall generate stories through `POST /story/generate` and return a server-rendered page containing narrative sections and inline citations.
- FR-006-07 Inline citations shall jump to a citation list within the story page and the citation list shall link back to entry detail pages.
- FR-006-08 The system shall allow users to save generated stories through `POST /story/save`.
- FR-006-09 Saved stories shall preserve the generated narrative, the current scope metadata, the cited entry ids, and the citation order as an immutable snapshot.
- FR-006-10 The system shall provide `GET /story/{id}` to render a previously saved story snapshot.
- FR-006-11 Saved stories shall remain viewable later even if the live scope would now produce different results.

## Acceptance Notes

- Story input is intentionally bounded before prompting the AI provider.
- Saved stories are not editable and are not auto-regenerated in the current implementation.
- Story Mode launch points exist in timeline, search, and drilled year or month flows.