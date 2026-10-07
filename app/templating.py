"""Shared Jinja2 templates instance.

Both ``app.main`` and ``app.route_helpers`` (and future route modules) import
``templates`` from here so that there is exactly one ``Jinja2Templates``
object with all custom filters pre-registered.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from pathlib import Path
import sqlite3

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from jinja2.runtime import Context

from app.db import connection_context
from app.services.entries import (
    build_tag_tone_map,
    format_plain_text,
    render_source_snapshot_markdown,
    sanitize_rich_text,
    sanitize_search_snippet,
    tag_tone,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

templates.env.filters["plain_text"] = format_plain_text
templates.env.filters["render_entry_html"] = sanitize_rich_text
templates.env.filters["render_search_snippet"] = sanitize_search_snippet
templates.env.filters["render_source_markdown"] = render_source_snapshot_markdown


def _load_tag_tone_map() -> Mapping[str, int] | None:
    try:
        with connection_context() as connection:
            return build_tag_tone_map(connection)
    except sqlite3.Error:
        logger.warning("Could not load tag tone map; using hashed tag tones.")
        return None


def get_request_tag_tone_map(request: object) -> Mapping[str, int] | None:
    """Return the tag tone map, computed at most once per request."""
    state = getattr(request, "state", None)
    if state is None:
        return _load_tag_tone_map()
    cached = getattr(state, "tag_tone_map", None)
    if cached is None:
        cached = _load_tag_tone_map()
        state.tag_tone_map = cached
    return cached


@pass_context
def _tag_tone_filter(context: Context, tag: str) -> int:
    request = context.get("request")
    tone_map = get_request_tag_tone_map(request) if request is not None else None
    return tag_tone(str(tag), tone_map)


templates.env.filters["tag_tone"] = _tag_tone_filter
