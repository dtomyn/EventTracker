from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any, cast

from bs4 import BeautifulSoup, Tag
import markdown


ALLOWED_RICH_TEXT_TAGS = {
    "b",
    "blockquote",
    "br",
    "code",
    "em",
    "i",
    "li",
    "ol",
    "p",
    "strong",
    "u",
    "ul",
}
ALLOWED_SOURCE_SNAPSHOT_TAGS = ALLOWED_RICH_TEXT_TAGS | {
    "a",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "pre",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
}
ALLOWED_SEARCH_SNIPPET_TAGS = ALLOWED_RICH_TEXT_TAGS | {"mark"}
ALLOWED_SOURCE_SNAPSHOT_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}


def preview_text(value: str, max_length: int = 280) -> str:
    clean = " ".join(plain_text_from_html(value).split())
    if len(clean) <= max_length:
        return clean
    return clean[: max_length - 1].rstrip() + "\u2026"


def format_plain_text(value: str) -> str:
    escaped = escape(value)
    return escaped.replace("\n", "<br>")


def plain_text_from_html(value: str) -> str:
    if not value:
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def sanitize_rich_text(value: str) -> str:
    return _sanitize_html(value, ALLOWED_RICH_TEXT_TAGS)


def render_source_snapshot_markdown(value: str) -> str:
    """Render stored source Markdown into sanitized HTML for detail and edit views."""
    if not value:
        return ""

    rendered_html = markdown.markdown(
        value,
        extensions=["extra", "nl2br", "sane_lists"],
    )
    rendered_html = _demote_source_snapshot_headings(rendered_html)
    return _sanitize_html(
        rendered_html,
        ALLOWED_SOURCE_SNAPSHOT_TAGS,
        allowed_attributes=ALLOWED_SOURCE_SNAPSHOT_ATTRIBUTES,
    )


def sanitize_search_snippet(value: str) -> str:
    return _sanitize_html(value, ALLOWED_SEARCH_SNIPPET_TAGS)


def _demote_source_snapshot_headings(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    heading_map = {
        "h1": "h3",
        "h2": "h4",
        "h3": "h5",
        "h4": "h6",
        "h5": "h6",
    }
    for tag in soup.find_all(tuple(heading_map)):
        if not isinstance(tag, Tag):
            continue
        tag.name = heading_map[tag.name]
    return str(soup)


def _sanitize_html(
    value: str,
    allowed_tags: set[str],
    *,
    allowed_attributes: Mapping[str, set[str]] | None = None,
) -> str:
    if not value:
        return ""

    soup = BeautifulSoup(value, "html.parser")
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue
        if tag.name in {"script", "style"}:
            tag.decompose()
            continue
        if tag.name not in allowed_tags:
            tag.unwrap()
            continue

        allowed_tag_attributes = (
            allowed_attributes.get(tag.name, set()) if allowed_attributes else set()
        )
        sanitized_attributes: dict[str, str] = {}
        for attribute_name, attribute_value in tag.attrs.items():
            if attribute_name not in allowed_tag_attributes:
                continue

            normalized_value = str(attribute_value).strip()
            if not normalized_value:
                continue

            if tag.name == "a" and attribute_name == "href":
                if not _is_safe_rendered_href(normalized_value):
                    continue
                sanitized_attributes["href"] = normalized_value
                sanitized_attributes["target"] = "_blank"
                sanitized_attributes["rel"] = "noreferrer noopener"
                continue

            sanitized_attributes[attribute_name] = normalized_value

        tag.attrs = cast(Any, sanitized_attributes)

    return str(soup)


def _is_safe_rendered_href(value: str) -> bool:
    if value.startswith("#"):
        return True
    from urllib.parse import urlparse

    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "mailto"} and bool(
        parsed.netloc or parsed.scheme == "mailto"
    )
