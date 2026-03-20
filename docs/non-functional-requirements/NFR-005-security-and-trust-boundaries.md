# NFR-005 Security And Trust Boundaries

- Category: Non-Functional
- Status: Baseline
- Scope: Content sanitization, URL trust boundaries, server-side fetching constraints, and safe rendering expectations.
- Primary Sources: `PRODUCT_OVERVIEW.md`, `app/main.py`, `app/services/entries.py`, `app/services/extraction.py`, `app/templates/*`, `tests/test_entries.py`, `tests/test_ai_story_mode.py`

## Requirement Statements

- NFR-005-01 Saved and previewed rich-text content shall be sanitized to an allowlist-based HTML subset before rendering.
- NFR-005-02 Search-result snippets shall be sanitized separately and may additionally allow `<mark>` highlighting.
- NFR-005-03 Story HTML shall be restricted to the narrower allowlist used by Story Mode rendering.
- NFR-005-04 User-supplied source URLs and additional-link URLs shall be limited to `http` and `https` schemes.
- NFR-005-05 Server-side extraction shall remove `script`, `style`, and `noscript` elements before building extracted text.
- NFR-005-06 Extracted article text shall be treated as transient working context and shall not be persisted to the database.
- NFR-005-07 The developer extraction endpoint and server-side source fetching behavior shall be treated as suitable only for local or otherwise trusted deployments unless additional controls are introduced.

## Acceptance Notes

- Sanitization is central to entry preview, saved entry rendering, search snippets, and story rendering.
- The current codebase does not introduce a separate remote-content sandbox beyond sanitization and trusted-deployment guidance.