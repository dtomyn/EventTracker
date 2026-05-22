# FR-011 Event Chat

- Category: Functional
- Status: Baseline
- Scope: Retrieval-augmented conversational Q&A over stored events, streaming answers, citation rendering, and provider requirements.
- Primary Sources: `README.md`, `app/main.py`, `app/services/event_chat.py`, `app/templates/chat.html`, `tests/test_event_chat.py`

## Requirement Statements

- FR-011-01 The system shall provide a chat page at `GET /chat` that renders a question input, group scope selector, and answer area.
- FR-011-02 The system shall accept a natural-language question and optional group scope at `POST /chat/query` and return a streaming response using Server-Sent Events.
- FR-011-03 The system shall retrieve relevant entries using the existing search service and construct bounded citation context from the top results.
- FR-011-04 The system shall ground answers only in retrieved entry context and instruct the AI provider not to invent facts, dates, people, or outcomes.
- FR-011-05 The system shall stream incremental answer text as `answer_chunk` SSE events, emit a final `citations` event with structured citation payloads, and emit a terminal `complete` event.
- FR-011-06 The system shall emit an `error` SSE event with a user-facing message when the provider is unavailable or generation fails.
- FR-011-07 The system shall return a graceful empty-result response when no entries match the question instead of forcing a hallucinated answer.
- FR-011-08 The system shall require GitHub Copilot as the active AI provider and shall display a warning when the provider is not configured.
- FR-011-09 The system shall validate questions by trimming whitespace, rejecting empty input, and enforcing a maximum length of 500 characters.
- FR-011-10 The system shall render citation cards under the answer with links back to entry detail pages.
- FR-011-11 The system shall treat each question independently with no multi-turn conversational memory.
- FR-011-12 The system shall reuse the same group scoping model used by the timeline and ranked search pages.

## Acceptance Notes

- Event Chat is stateless per question and does not require database schema changes.
- The chat page is accessible from the shared navigation bar.
- Citation payloads include entry id, title, display date, group name, tags, preview text, and a URL to the entry detail page.
- The inline answer text uses `[Entry N]` citation format referencing entry ids.
- The feature gracefully degrades to a disabled state with a warning when the Copilot provider is not available.
