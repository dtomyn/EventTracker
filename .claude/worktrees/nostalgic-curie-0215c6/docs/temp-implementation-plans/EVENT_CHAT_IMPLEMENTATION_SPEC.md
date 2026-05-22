# Event Chat Implementation Spec

## 1. Title
Event Chat: Conversational Q&A About Timeline Events

## 2. Overview
A lightweight chat interface allowing users to ask open-ended questions about their stored events. The feature retrieves relevant entries via keyword and semantic search, constructs a grounded context, and uses GitHub Copilot SDK as the initial conversational backend. Responses stream via Server-Sent Events. Chat history is stateless per question, so the MVP does not require database schema changes.

## 3. Goals
- Provide conversational access to event context without requiring search syntax.
- Ground answers in actual stored entries and render citations back to event detail pages.
- Scope questions to a selected timeline group or all groups.
- Stream responses incrementally for a responsive UX.
- Preserve provider abstraction so an OpenAI fallback can be added later.
- Reuse the existing search, embeddings, and Copilot runtime patterns already present in the repo.

## 4. Non-Goals
- Persistent chat history or saved conversations.
- Multi-turn conversational memory beyond the current question.
- Custom Copilot tools or function calling in the MVP.
- Real-time collaboration, sharing, or analytics dashboards.

## 5. User Experience
- Add a new `Chat` page at `/chat`.
- Render a simple server-rendered layout with a question input, group selector, and answer area.
- Let the user ask a natural-language question such as `What were the most important security-related events last quarter?`
- Stream the answer into the page as it is generated.
- Render a citations list under the answer with links back to matching entries.
- Keep each question independent. Asking another question starts a new grounded response rather than continuing hidden conversation state.

## 6. Architecture

### Retrieval Layer
- Reuse `search_entries()` from `app/services/search.py` as the baseline retrieval path.
- If embeddings are enabled, include semantic recall through `search_semantic_matches()` from `app/services/embeddings.py`.
- Filter by selected group when one is chosen.
- Retrieve the top 8 to 12 entries and format them into a bounded context window.

### Answer Generation
- Add a new service module at `app/services/event_chat.py`.
- Build a system prompt that tells the model to answer only from retrieved entries, stay factual, and cite entry ids.
- Reuse the provider selection pattern already present in `app/services/ai_generate.py` and `app/services/ai_story_mode.py`.
- Use the existing Copilot client wrapper in `app/services/copilot_runtime.py` for session lifecycle and streaming.

### Streaming
- Add an SSE route that mirrors the existing streaming pattern used by `timeline_group_web_search_stream()` in `app/main.py`.
- Stream partial answer chunks, then emit a final citations payload and completion event.

## 7. Retrieval Strategy
1. Accept `question` and optional `group_id`.
2. Run `search_entries(connection, question, group_id=...)`.
3. If semantic search is available, merge semantic matches with keyword matches and deduplicate by entry id.
4. Assemble context entries using:
   - entry id
   - title
   - display date
   - group name
   - tags
   - bounded preview of `final_text`
5. Cap the total context size so the model receives focused evidence instead of large raw entry dumps.

The MVP should stay retrieval-grounded. It should not let Copilot inspect the database directly.

## 8. Route and Template Plan

### Route: `GET /chat`
- Render a dedicated chat page.
- Provide the current timeline group list and selected scope in the template context.
- Reuse the same group concepts already used by timeline and search pages.

### Route: `POST /chat/query`
- Accept the question and selected group.
- Validate input and resolve scope.
- Retrieve relevant entries.
- Start answer generation and return a `StreamingResponse` with `text/event-stream`.

### SSE Events
- `answer_chunk`: incremental answer text
- `citations`: final structured citations payload
- `complete`: terminal success event
- `error`: user-facing failure message

### Template Plan
- Add `app/templates/chat.html` for the page shell.
- Add a small amount of JavaScript to submit the form, consume SSE events, append streamed text, and render citations.
- Keep the page server-rendered and stylistically aligned with the existing Jinja and Bootstrap patterns.

## 9. File Changes

### New Files
- `app/services/event_chat.py`
  - Retrieval orchestration
  - prompt assembly
  - provider invocation
  - SSE-friendly event emission helpers
- `app/templates/chat.html`
  - question form
  - scope selector
  - streamed answer container
  - citations list

### Modified Files
- `app/main.py`
  - add `GET /chat`
  - add `POST /chat/query`
  - reuse existing SSE encoding helpers and current route patterns
- `app/templates/base.html`
  - optional navigation link to the chat page
- `app/services/search.py`
  - optional helper extraction if a chat-specific retrieval wrapper makes the integration cleaner

### Deferred Files
- No new database tables in `app/db.py` for the MVP.
- No custom tool module in the Copilot stack for the MVP.

## 10. Error Handling and Safety

### Validation
- Trim whitespace from the question.
- Reject empty questions.
- Bound question length to a reasonable maximum.
- Validate `group_id` against existing timeline groups.

### Provider Failures
- Map Copilot configuration and runtime failures to user-friendly SSE `error` events.
- Log detailed exceptions server-side.
- Return a clear failure state when the Copilot provider is unavailable.

### Retrieval Safety
- If no entries match, return a graceful empty-result response instead of forcing a hallucinated answer.
- Instruct the model not to invent events, dates, people, or conclusions that do not appear in the provided context.
- Keep prompts grounded in sanitized stored content and bounded previews.

## 11. Validation Plan

### Unit Tests
- Add `tests/test_event_chat.py`.
- Cover:
  - successful retrieval and prompt assembly
  - empty retrieval results
  - provider failure mapping
  - citation hydration from retrieved entries

### Route Tests
- Verify `GET /chat` renders.
- Verify `POST /chat/query` validates inputs and streams expected event types.
- Verify group scoping only includes entries from the selected group.

### E2E Follow-Up
- Add a Playwright flow after the core feature is working:
  - open chat page
  - ask a question
  - verify streamed answer appears
  - verify citation links navigate to entry detail pages

## 12. Open Follow-Ups
- Add persistent chat history only if repeated use shows clear value.
- Add custom Copilot tools later if retrieval-only prompting proves too limiting.
- Consider source snapshot excerpts as additional retrieval context if `final_text` alone is insufficient.
- Consider an OpenAI streaming fallback if Copilot availability becomes a local setup constraint.