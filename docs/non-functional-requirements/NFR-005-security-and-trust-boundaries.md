# NFR-005 Security And Trust Boundaries

- Category: Non-Functional
- Status: Baseline
- Scope: CSRF protection, content sanitization, URL trust boundaries, server-side fetching constraints, and safe rendering expectations.
- Primary Sources: `app/main.py`, `app/services/entries.py`, `app/services/extraction.py`, `app/templates/*`, `tests/test_entries.py`, `tests/test_ai_story_mode.py`

## Requirement Statements

### CSRF protection
- NFR-005-00a The application shall protect all state-changing HTTP methods with CSRF token validation.
- NFR-005-00b The application shall derive CSRF tokens from a per-session random cookie value and a server-side secret using HMAC-SHA256.
- NFR-005-00c The application shall accept the CSRF token from a hidden form field (`csrf_token`) or from an HTTP header (`x-csrf-token`).
- NFR-005-00d The application shall set the CSRF session cookie with `HttpOnly` and `SameSite=Strict` attributes, adding `Secure` when served over HTTPS.

### Content sanitization
- NFR-005-01 The application shall sanitize saved and previewed rich-text content to an allowlist-based HTML subset before rendering.
- NFR-005-02 The application shall sanitize search-result snippets separately and may additionally allow `<mark>` highlighting.
- NFR-005-03 The application shall restrict story HTML to the narrower allowlist used by Story Mode rendering.
- NFR-005-04 The application shall limit user-supplied source URLs and additional-link URLs to `http` and `https` schemes.
- NFR-005-05 The application shall remove `script`, `style`, and `noscript` elements during server-side extraction before building extracted text.
- NFR-005-06 The application shall treat extracted article text as transient working context and shall not persist it to the database.
- NFR-005-07 The application shall treat the developer extraction endpoint and server-side source fetching behavior as suitable only for local or otherwise trusted deployments unless additional controls are introduced.
- NFR-005-08 The application shall limit the shared safe rich-text subset to `p`, `b`, `strong`, `i`, `em`, `u`, `ul`, `ol`, `li`, `br`, `blockquote`, and `code`, with `<mark>` additionally permitted only for search snippets.
- NFR-005-09 The application shall limit the story HTML allowlist to `a`, `h2`, `p`, and `section`, permitting only `href`, `title`, and `class` attributes on `<a>` elements and `class` on the remaining elements, and shall restrict link targets to fragment-only (`#...`) and root-relative (`/...`) URLs.

## Acceptance Notes

- Sanitization is central to entry preview, saved entry rendering, search snippets, and story rendering.
- CSRF tokens are available in all Jinja2 templates through a `csrf_hidden_input()` global that renders the hidden form field.
- The repository does not introduce a separate remote-content sandbox beyond sanitization and trusted-deployment guidance.