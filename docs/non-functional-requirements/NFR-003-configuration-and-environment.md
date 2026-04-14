# NFR-003 Configuration And Environment

- Category: Non-Functional
- Status: Baseline
- Scope: Environment loading, runtime settings, provider configuration, optional presentation-compilation runtime requirements, and operator-facing configuration rules.
- Primary Sources: `app/env.py`, `app/db.py`, `app/services/ai_generate.py`, `app/services/ai_story_mode.py`, `app/services/story_deck.py`, `app/services/embeddings.py`, `app/services/group_web_search.py`, `scripts/run_dev.py`, `scripts/render_story_deck.mjs`, `pyproject.toml`, `package.json`

## Requirement Statements

- NFR-003-01 The application shall load environment variables from the repository `.env` file through `app/env.py`.
- NFR-003-02 The application shall not rely on `scripts/run_dev.py` to load `.env` before configuration-backed code executes.
- NFR-003-03 The repository shall load `.env` with override semantics in `scripts/run_dev.py` and `scripts/import_entries.py` when preparing standalone command execution.
- NFR-003-04 The application shall use `EVENTTRACKER_AI_PROVIDER` as the AI provider selection setting and shall support `openai` and `copilot`.
- NFR-003-05 The application shall default the AI provider to `openai`.
- NFR-003-06 The application shall require `OPENAI_API_KEY` and `OPENAI_CHAT_MODEL_ID` for OpenAI draft and story generation.
- NFR-003-07 The application shall require `OPENAI_API_KEY` and `OPENAI_EMBEDDING_MODEL_ID` for semantic embeddings.
- NFR-003-08 The application shall support an optional `OPENAI_BASE_URL` for compatible OpenAI-based endpoints.
- NFR-003-09 The application shall default `COPILOT_CHAT_MODEL_ID` to `gpt-5` for Copilot-backed integrations and may accept optional CLI path or URL overrides.
- NFR-003-10 The application shall default logging to `INFO` unless `LOG_LEVEL` is set explicitly.
- NFR-003-11 The application shall allow configuration of development-server host and port through CLI arguments or `EVENTTRACKER_HOST` and `EVENTTRACKER_PORT`.
- NFR-003-12 The application shall accept `EVENTTRACKER_CSRF_SECRET` as an optional environment variable to provide a stable CSRF signing secret across restarts; when unset, the application shall persist an auto-generated secret to `data/csrf_secret.txt`.
- NFR-003-13 The application shall accept `EVENTTRACKER_GROUP_WEB_SEARCH_TIMEOUT_SECONDS`, `EVENTTRACKER_GROUP_WEB_SEARCH_BROADENED_TIMEOUT_SECONDS`, and `EVENTTRACKER_GROUP_WEB_SEARCH_REQUEST_TIMEOUT_MS` as optional environment variables to tune group web-search timeouts.
- NFR-003-14 The application shall skip CSRF validation when the `TESTING` environment variable is set, to support automated test harnesses.
- NFR-003-15 The application shall treat Node.js plus the installed `@marp-team/marpit` dependency as an optional local runtime requirement for executive presentation compilation rather than as a prerequisite for core narrative Story Mode.

## Acceptance Notes

- Invalid provider values fail with explicit configuration errors.
- Invalid port values fail before server startup.
- `npm install` is the repository step that satisfies the Marpit runtime dependency once Node.js is available locally.
- The CSRF secret auto-generation falls back to an in-memory value when the data directory is not writable.