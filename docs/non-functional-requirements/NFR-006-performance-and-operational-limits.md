# NFR-006 Performance And Operational Limits

- Category: Non-Functional
- Status: Baseline
- Scope: Bounded result sizes, caching, timeout behavior, and prompt-input limits used to keep local execution responsive.
- Primary Sources: `app/services/entries.py`, `app/services/search.py`, `app/services/extraction.py`, `app/services/group_web_search.py`, `app/services/ai_story_mode.py`, `tests/test_search.py`, `tests/test_group_web_search.py`

## Requirement Statements

- NFR-006-01 Timeline-detail pagination shall default to 25 entries per page and cap page size at 50.
- NFR-006-02 Ranked-search pagination shall default to 20 results per page and cap page size at 50.
- NFR-006-03 FTS-backed ranked retrieval shall limit the raw keyword hit set to 50 rows per query.
- NFR-006-04 Semantic-search retrieval shall limit vector matches to 25 rows per query.
- NFR-006-05 Source extraction shall use a 10-second network timeout and truncate extracted text to 4000 characters.
- NFR-006-06 Group web-search caching shall default to a 300-second in-memory TTL.
- NFR-006-07 Group web-search responses shall target three to five items and shall not exceed five items.
- NFR-006-08 Story-generation input shall default to the most recent 40 chronologically ordered entries when the scope contains more than that limit.
- NFR-006-09 Story prompt preparation shall default each entry summary to a maximum of 280 characters.

## Acceptance Notes

- Cursor-based pagination is used for timeline details and search results.
- These limits bound prompt size, result rendering cost, and in-browser payload size for a local-first deployment model.