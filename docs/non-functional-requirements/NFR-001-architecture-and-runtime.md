# NFR-001 Architecture And Runtime

- Category: Non-Functional
- Status: Baseline
- Scope: Application shape, runtime model, rendering approach, and local execution constraints.
- Primary Sources: `README.md`, `PRODUCT_OVERVIEW.md`, `pyproject.toml`, `app/main.py`, `scripts/run_dev.py`

## Requirement Statements

- NFR-001-01 EventTracker shall run as a single-process Python web application.
- NFR-001-02 The HTTP service shall be implemented with FastAPI and served through Uvicorn.
- NFR-001-03 HTML pages shall be rendered on the server with Jinja2 templates.
- NFR-001-04 Browser behavior shall remain lightweight and page-oriented rather than adopting a single-page-application architecture.
- NFR-001-05 Static assets shall be served from `app/static`.
- NFR-001-06 The default local development bind target shall be `127.0.0.1:35231` unless overridden by CLI or environment settings.
- NFR-001-07 Application startup shall initialize required database structures before serving requests.

## Acceptance Notes

- `scripts/run_dev.py` adds Windows-specific reload-process cleanup and explicit port-availability checks.
- The repository targets Python `>=3.12` and is structured for local execution with `uv`.