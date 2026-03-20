# FR-007 Group Web Search

- Category: Functional
- Status: Baseline
- Scope: Copilot-backed per-group web search, disabled states, refresh behavior, and streamed progress events.
- Primary Sources: `README.md`, `app/main.py`, `app/services/group_web_search.py`, `app/templates/partials/story_links.html`, `tests/test_group_web_search.py`

## Requirement Statements

- FR-007-01 The system shall provide group web-search endpoints at `/timeline/group-web-search`, `/timeline/group-web-search/stream`, and `/timeline/group-web-search/refresh`.
- FR-007-02 Group web search shall require a concrete selected timeline group and shall not operate for `All groups` scope.
- FR-007-03 Group web search shall return a disabled response when the selected group has no stored `web_search_query`.
- FR-007-04 Group web search shall return a disabled response when the active AI provider is not `copilot`.
- FR-007-05 Group web search shall use the selected group's stored query as the only search prompt basis.
- FR-007-06 Group web search shall return concise structured web results with three to five items when credible sources are available, capped at five items.
- FR-007-07 Group web search shall support cache reuse for repeated requests and forced refresh for explicit reload requests.
- FR-007-08 Group web search shall support streamed progress and result events over Server-Sent Events.

## Acceptance Notes

- Web-search results are stored in an in-memory cache rather than the database.
- The current UI only surfaces the panel when the selected group can actually use it.