# Requirements Document Template

Use this template for every requirement document under `docs/functional-requirements` and `docs/non-functional-requirements`.

## Standard Sections

1. `# <ID> <Title>`
2. `- Category:` Functional or Non-Functional
3. `- Status:` Baseline, Proposed, or Revised
4. `- Scope:` Short statement of the bounded topic covered by the document
5. `- Primary Sources:` Code modules, tests, and markdown files reviewed
6. `## Requirement Statements`
7. `## Acceptance Notes`

## Writing Rules

- Keep each document focused on one bounded topic.
- Express normative requirements with `shall` statements.
- Number requirements within the document using the document id prefix.
- Prefer implementation-aligned wording over speculative product language.
- Keep acceptance notes concise and traceable to the current codebase.