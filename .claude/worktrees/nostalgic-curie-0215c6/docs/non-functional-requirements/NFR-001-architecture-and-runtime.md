# NFR-001 Architecture And Runtime

- Category: Non-Functional
- Status: Baseline
- Scope: Application shape, runtime model, rendering approach, and local execution constraints.
- Primary Sources: `README.md`, `pyproject.toml`, `app/main.py`, `scripts/run_dev.py`

## Requirement Statements

- NFR-001-01 EventTracker shall run as a single-process Python web application.
- NFR-001-02 The application shall implement the HTTP service with FastAPI and shall serve it through Uvicorn.
- NFR-001-03 The application shall render HTML pages on the server with Jinja2 templates.
- NFR-001-04 The application shall keep browser behavior lightweight and page-oriented rather than adopting a single-page-application architecture.
- NFR-001-05 The application shall serve static assets from `app/static`.
- NFR-001-06 The application shall default the local development bind target to `127.0.0.1:35231` unless CLI or environment settings override it.
- NFR-001-07 The application shall initialize required database structures during startup before serving requests.

## Acceptance Notes

- `scripts/run_dev.py` adds Windows-specific reload-process cleanup and explicit port-availability checks.
- The repository targets Python `>=3.12` and is structured for local execution with `uv`.