# NFR-003 Configuration And Environment

- Category: Non-Functional
- Status: Baseline
- Scope: Environment loading, runtime settings, provider configuration, and operator-facing configuration rules.
- Primary Sources: `app/env.py`, `app/db.py`, `app/services/ai_generate.py`, `app/services/ai_story_mode.py`, `app/services/embeddings.py`, `app/services/group_web_search.py`, `scripts/run_dev.py`, `pyproject.toml`

## Requirement Statements

- NFR-003-01 The application shall load environment variables from the repository `.env` file through `app/env.py`.
- NFR-003-02 Application modules shall not rely on `scripts/run_dev.py` to load `.env` before configuration-backed code executes.
- NFR-003-03 Repository-level CLI scripts may load `.env` directly when preparing standalone command execution.
- NFR-003-04 The AI provider selection setting shall be `EVENTTRACKER_AI_PROVIDER` and shall support `openai` and `copilot`.
- NFR-003-05 The default AI provider shall be `openai`.
- NFR-003-06 OpenAI draft and story generation shall require `OPENAI_API_KEY` and `OPENAI_CHAT_MODEL_ID`.
- NFR-003-07 Semantic embeddings shall require `OPENAI_API_KEY` and `OPENAI_EMBEDDING_MODEL_ID`.
- NFR-003-08 OpenAI-based integrations shall support an optional `OPENAI_BASE_URL` for compatible endpoints.
- NFR-003-09 Copilot-backed integrations shall default `COPILOT_CHAT_MODEL_ID` to `gpt-5` and may accept optional CLI path or URL overrides.
- NFR-003-10 Logging shall default to `INFO` unless `LOG_LEVEL` is set explicitly.
- NFR-003-11 Development-server host and port shall be configurable through CLI arguments or `EVENTTRACKER_HOST` and `EVENTTRACKER_PORT`.

## Acceptance Notes

- Invalid provider values fail with explicit configuration errors.
- Invalid port values fail before server startup.