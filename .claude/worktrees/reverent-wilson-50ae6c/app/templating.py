"""Shared Jinja2 templates instance.

Both ``app.main`` and ``app.route_helpers`` (and future route modules) import
``templates`` from here so that there is exactly one ``Jinja2Templates``
object with all custom filters pre-registered.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.services.entries import (
    format_plain_text,
    render_source_snapshot_markdown,
    sanitize_rich_text,
    sanitize_search_snippet,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

templates.env.filters["plain_text"] = format_plain_text
templates.env.filters["render_entry_html"] = sanitize_rich_text
templates.env.filters["render_search_snippet"] = sanitize_search_snippet
templates.env.filters["render_source_markdown"] = render_source_snapshot_markdown
