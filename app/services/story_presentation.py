"""Build cinematic presentation chapters from a saved story narrative.

The saved ``narrative_html`` is already sanitized, but the presentation never
re-emits it as markup. Instead it is parsed into plain-text headings,
paragraphs, and citation references so the template can render everything
through Jinja autoescaping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re

_CITATION_HREF_PATTERN = re.compile(r"^#citation-(\d+)$")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCK_TAGS = {"p", "li", "blockquote"}


@dataclass(slots=True)
class PresentationChapter:
    heading: str
    paragraphs: list[str] = field(default_factory=list)
    citation_orders: list[int] = field(default_factory=list)


class _NarrativeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chapters: list[PresentationChapter] = []
        self._current: PresentationChapter | None = None
        self._heading_buffer: list[str] | None = None
        self._block_buffer: list[str] | None = None
        self._block_is_sources = False
        self._skip_depth = 0

    def _chapter(self) -> PresentationChapter:
        if self._current is None:
            self._current = PresentationChapter(heading="")
            self.chapters.append(self._current)
        return self._current

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        attributes = {name: value or "" for name, value in attrs}
        if tag == "section":
            self._current = None
        elif tag in _HEADING_TAGS:
            self._flush_block()
            self._current = PresentationChapter(heading="")
            self.chapters.append(self._current)
            self._heading_buffer = []
        elif tag in _BLOCK_TAGS:
            self._flush_block()
            self._block_buffer = []
            self._block_is_sources = False
        elif tag == "a":
            match = _CITATION_HREF_PATTERN.match(attributes.get("href", "").strip())
            if match is not None:
                order = int(match.group(1))
                chapter = self._chapter()
                if order not in chapter.citation_orders:
                    chapter.citation_orders.append(order)
                self._block_is_sources = True
        elif tag == "br" and self._block_buffer is not None:
            self._block_buffer.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _HEADING_TAGS and self._heading_buffer is not None:
            chapter = self._chapter()
            chapter.heading = _clean("".join(self._heading_buffer))
            self._heading_buffer = None
        elif tag in _BLOCK_TAGS:
            self._flush_block()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._heading_buffer is not None:
            self._heading_buffer.append(data)
        elif self._block_buffer is not None:
            self._block_buffer.append(data)
        elif data.strip():
            self._chapter().paragraphs.append(_clean(data))

    def _flush_block(self) -> None:
        if self._block_buffer is None:
            return
        text = _clean("".join(self._block_buffer))
        is_sources = self._block_is_sources and _is_sources_line(text)
        self._block_buffer = None
        self._block_is_sources = False
        if text and not is_sources:
            self._chapter().paragraphs.append(text)

    def close(self) -> None:
        super().close()
        self._flush_block()


def _clean(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _is_sources_line(text: str) -> bool:
    remainder = re.sub(r"\[\d+\]", "", text)
    remainder = remainder.replace("Sources", "").replace("Source", "")
    return not remainder.strip(" ,;:")


def build_presentation_chapters(
    narrative_html: str,
    *,
    fallback_heading: str,
    narrative_text: str | None = None,
) -> list[PresentationChapter]:
    """Split a story narrative into presentation chapters.

    Each heading starts a new chapter. Content before the first heading, or a
    narrative without headings, becomes a chapter titled ``fallback_heading``.
    """
    parser = _NarrativeParser()
    parser.feed(narrative_html or "")
    parser.close()

    chapters = [
        chapter
        for chapter in parser.chapters
        if chapter.heading or chapter.paragraphs or chapter.citation_orders
    ]
    for chapter in chapters:
        if not chapter.heading:
            chapter.heading = fallback_heading

    if not chapters and narrative_text and narrative_text.strip():
        paragraphs = [
            _clean(part) for part in narrative_text.split("\n\n") if part.strip()
        ]
        chapters = [PresentationChapter(heading=fallback_heading, paragraphs=paragraphs)]
    return chapters
