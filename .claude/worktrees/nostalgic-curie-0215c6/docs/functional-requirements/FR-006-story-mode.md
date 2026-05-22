# FR-006 Story Mode

- Category: Functional
- Status: Baseline
- Scope: Scoped story generation, citation rendering, optional executive presentation generation, story snapshot persistence, and saved-story retrieval.
- Primary Sources: `README.md`, `TIMELINE_STORY_MODE_IMPLEMENTATION_PLAN.md`, `TIMELINE_STORY_MODE_EXECUTIVE_DECK_IMPLEMENTATION_PLAN.md`, `app/main.py`, `app/services/story_mode.py`, `app/services/ai_story_mode.py`, `app/services/story_deck.py`, `app/templates/story.html`, `app/templates/story_presentation.html`, `tests/test_story_mode.py`, `tests/test_story_deck.py`, `tests/test_story_routes.py`

## Requirement Statements

- FR-006-01 The system shall provide Story Mode at `GET /story` for the current timeline or search scope.
- FR-006-02 The system shall accept `q`, `group_id`, `year`, `month`, and `format` parameters for Story Mode.
- FR-006-03 The system shall support the `executive_summary`, `detailed_chronology`, and `recent_changes` formats in Story Mode.
- FR-006-04 The system shall use chronologically ordered entries for story generation, even when the source scope was produced by ranked search.
- FR-006-05 The system shall render a non-fatal warning state rather than an HTTP error when no entries match the requested Story Mode scope.
- FR-006-06 The system shall generate stories through `POST /story/generate` and return a server-rendered page containing narrative sections and inline citations.
- FR-006-07 The system shall allow users to generate an executive presentation from an already-generated narrative through the Story Mode deck-generation flow.
- FR-006-08 The system shall build the executive presentation from the same scoped entries and grounded citations used for the current narrative result.
- FR-006-09 The system shall return a server-rendered page containing presentation preview controls after successful deck generation, including an embedded preview, a fullscreen preview action, and a standalone HTML download action.
- FR-006-10 The system shall make inline citations jump to a citation list within the story page and shall make that citation list link back to entry detail pages.
- FR-006-11 The system shall allow users to save generated stories through `POST /story/save`.
- FR-006-12 The system shall preserve the generated narrative, the current scope metadata, the cited entry ids, and the citation order as an immutable snapshot when saving stories.
- FR-006-13 The system shall persist an optional `executive_deck` artifact alongside a saved story snapshot when presentation generation succeeded before save.
- FR-006-14 The system shall provide `GET /story/{id}` to render a previously saved story snapshot.
- FR-006-15 The system shall offer `Narrative` and `Presentation` saved-story views when a saved story has a presentation artifact.
- FR-006-16 The system shall render saved Presentation mode inside the saved story page through an embedded viewer that targets `GET /story/{id}/presentation`.
- FR-006-17 The system shall provide `GET /story/{id}/presentation` to render the stored compiled presentation as a standalone slide viewer.
- FR-006-18 The system shall allow users to download a standalone HTML copy of a generated or saved presentation.
- FR-006-19 The system shall keep saved stories viewable later even if the live scope would now produce different results.

## Acceptance Notes

- Story and executive-deck input are intentionally bounded before prompting the AI provider.
- Presentation generation is optional and happens after narrative generation in the current Story Mode UI flow.
- Saved stories and saved presentations are not editable and are not auto-regenerated in the current implementation.
- Saved presentation mode serves stored compiled HTML and CSS rather than recompiling on request.
- Story Mode launch points exist in timeline, search, and drilled year or month flows.