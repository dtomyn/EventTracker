# FR-007 Group Web Search

- Category: Functional
- Status: Baseline
- Scope: Copilot-backed per-group web search, disabled states, refresh behavior, and streamed progress events.
- Primary Sources: `README.md`, `app/main.py`, `app/services/group_web_search.py`, `app/templates/partials/story_links.html`, `tests/test_group_web_search.py`

## Requirement Statements

- FR-007-01 The system shall provide group web-search endpoints at `/timeline/group-web-search`, `/timeline/group-web-search/stream`, and `/timeline/group-web-search/refresh`.
- FR-007-02 The system shall require a concrete selected timeline group for group web search and shall not operate for `All groups` scope.
- FR-007-03 The system shall return a disabled response for group web search when the selected group has no stored `web_search_query`.
- FR-007-04 The system shall return a disabled response for group web search when the active AI provider is not `copilot`.
- FR-007-05 The system shall use the selected group's stored query as the only prompt basis for group web search.
- FR-007-06 The system shall return concise structured web results with three to five items, capped at five items, when credible sources are available.
- FR-007-07 The system shall support cache reuse for repeated group web-search requests and forced refresh for explicit reload requests.
- FR-007-08 The system shall support streamed progress and result events for group web search over Server-Sent Events.
- FR-007-09 The system shall exclude web-search result URLs that already exist as saved entry source URLs in the database.
- FR-007-10 The system shall validate the reachability of web-search result URLs and exclude unreachable links from the returned set.
- FR-007-11 The system shall request a broadened follow-up search when the initial pass returns fewer than three usable results after saved-URL and reachability filtering.
- FR-007-12 The system shall return HTTP `504` when the group web-search backend times out.

## Acceptance Notes

- Web-search results are stored in an in-memory cache rather than the database.
- The current UI only surfaces the panel when the selected group can actually use it.
- URL reachability checks use a 5-second timeout per URL.
- Broadened search uses a 45-second timeout, independent of the initial 60-second timeout.