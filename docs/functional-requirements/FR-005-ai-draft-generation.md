# FR-005 AI Draft Generation

- Category: Functional
- Status: Baseline
- Scope: AI-assisted single-entry draft generation, source extraction fallback, and sanitized preview rendering.
- Primary Sources: `README.md`, `app/main.py`, `app/services/ai_generate.py`, `app/services/extraction.py`, `app/templates/partials/generated_preview.html`, `tests/test_ai_generate.py`, `tests/test_smoke.py`

## Requirement Statements

- FR-005-01 The system shall expose entry-draft generation at `POST /entries/generate`.
- FR-005-02 The system shall accept `title`, `source_url`, and the current `generated_text` at the generation endpoint.
- FR-005-03 The system shall require at least one of `title` or `source_url` at the generation endpoint.
- FR-005-04 The system shall attempt source extraction before requesting AI generation when `source_url` is present.
- FR-005-05 The system shall fall back to title-only generation when source extraction fails and a title is present.
- FR-005-06 The system shall return a server-rendered error partial instead of saving anything when source extraction fails and no title is present.
- FR-005-07 The system shall return suggested metadata including title and date fields together with generated HTML during draft generation.
- FR-005-08 The system shall never save the generated result automatically during draft generation.
- FR-005-09 The system shall expose `POST /entries/preview-html` to sanitize arbitrary editor HTML into the same preview partial used by the entry form.
- FR-005-10 The system shall return HTTP `400` for draft-generation validation and configuration problems, HTTP `502` for provider failures, and HTTP `500` for unexpected failures.
- FR-005-11 The system shall require AI providers to return structured JSON containing `title`, `draft_html`, and optional date fields, and shall reject empty titles or empty draft payloads.

## Acceptance Notes

- Extracted article text is transient and is not written to the database.
- Generated HTML is later rendered through the same sanitization rules used by saved entry content.