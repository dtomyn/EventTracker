# NFR-006 Performance And Operational Limits

- Category: Non-Functional
- Status: Baseline
- Scope: Bounded result sizes, caching, timeout behavior, and prompt-input limits used to keep local execution responsive.
- Primary Sources: `app/services/entries.py`, `app/services/search.py`, `app/services/extraction.py`, `app/services/group_web_search.py`, `app/services/ai_story_mode.py`, `tests/test_search.py`, `tests/test_group_web_search.py`

## Requirement Statements

- NFR-006-01 The application shall default timeline-detail pagination to 25 entries per page and shall cap page size at 50.
- NFR-006-02 The application shall default ranked-search pagination to 20 results per page and shall cap page size at 50.
- NFR-006-03 The application shall limit the raw keyword hit set to 50 rows per query for FTS-backed ranked retrieval.
- NFR-006-04 The application shall limit semantic-search vector matches to 25 rows per query.
- NFR-006-05 The application shall use a 10-second network timeout and shall truncate extracted text to 4000 characters during source extraction.
- NFR-006-06 The application shall default group web-search caching to a 300-second in-memory TTL.
- NFR-006-07 The application shall target three to five items and shall not exceed five items in group web-search responses.
- NFR-006-08 The application shall default story-generation input to the most recent 40 chronologically ordered entries when the scope contains more than that limit.
- NFR-006-09 The application shall default each entry summary to a maximum of 280 characters during story prompt preparation.
- NFR-006-10 The application shall limit the preferred-tag vocabulary passed to AI draft generation to 50 tags per group.
- NFR-006-11 The application shall limit group `web_search_query` values to 400 characters.
- NFR-006-12 The application shall default the group web-search backend timeout to 60 seconds, the broadened-search timeout to 45 seconds, and the per-URL reachability check timeout to 5 seconds.
- NFR-006-13 The application shall derive the browser-side group web-search request timeout from the backend timeout plus a 5-second buffer.

## Acceptance Notes

- Cursor-based pagination is used for timeline details and search results.
- These limits bound prompt size, result rendering cost, and in-browser payload size for a local-first deployment model.