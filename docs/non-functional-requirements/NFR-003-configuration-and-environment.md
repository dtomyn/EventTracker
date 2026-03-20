# NFR-003 Configuration And Environment

- Category: Non-Functional
- Status: Baseline
- Scope: Environment loading, runtime settings, provider configuration, and operator-facing configuration rules.
- Primary Sources: `app/env.py`, `app/db.py`, `app/services/ai_generate.py`, `app/services/ai_story_mode.py`, `app/services/embeddings.py`, `app/services/group_web_search.py`, `scripts/run_dev.py`, `pyproject.toml`

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

## Acceptance Notes

- Invalid provider values fail with explicit configuration errors.
- Invalid port values fail before server startup.