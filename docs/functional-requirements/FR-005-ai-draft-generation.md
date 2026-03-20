# FR-005 AI Draft Generation

- Category: Functional
- Status: Baseline
- Scope: AI-assisted single-entry draft generation, source extraction fallback, and sanitized preview rendering.
- Primary Sources: `README.md`, `app/main.py`, `app/services/ai_generate.py`, `app/services/extraction.py`, `app/templates/partials/generated_preview.html`, `tests/test_ai_generate.py`, `tests/test_smoke.py`

## Requirement Statements

- FR-005-01 The system shall expose entry-draft generation at `POST /entries/generate`.
- FR-005-02 The generation endpoint shall accept `title`, `source_url`, and the current `generated_text`.
- FR-005-03 The generation endpoint shall require at least one of `title` or `source_url`.
- FR-005-04 When `source_url` is present, the system shall attempt source extraction before requesting AI generation.
- FR-005-05 When source extraction fails and a title is present, the system shall fall back to title-only generation.
- FR-005-06 When source extraction fails and no title is present, the system shall return a server-rendered error partial instead of saving anything.
- FR-005-07 Draft generation shall return suggested metadata including title and date fields together with generated HTML.
- FR-005-08 Draft generation shall never save the generated result automatically.
- FR-005-09 The system shall expose `POST /entries/preview-html` to sanitize arbitrary editor HTML into the same preview partial used by the entry form.
- FR-005-10 Draft-generation validation and configuration problems shall return HTTP `400`, provider failures shall return HTTP `502`, and unexpected failures shall return HTTP `500`.

## Acceptance Notes

- Extracted article text is transient and is not written to the database.
- Providers are required to return structured JSON containing `title`, `draft_html`, and optional date parts.
- Generated HTML is later rendered through the same sanitization rules used by saved entry content.