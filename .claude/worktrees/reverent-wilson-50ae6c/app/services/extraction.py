from __future__ import annotations

from functools import lru_cache
from html import escape
from importlib.metadata import PackageNotFoundError, version
import io
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import re

import httpx
from bs4 import BeautifulSoup, Tag
from markitdown import MarkItDown, StreamInfo
from youtube_transcript_api import YouTubeTranscriptApi


logger = logging.getLogger(__name__)
_HTML_CONTENT_TYPES = {"application/xhtml+xml", "text/html"}
_MAX_EXTRACTION_PREVIEW_CHARS = 4000
DEFAULT_HTTP_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
DEFAULT_SOURCE_EXTRACTOR_NAME = "markitdown"

try:
    DEFAULT_SOURCE_EXTRACTOR_VERSION = version("markitdown")
except PackageNotFoundError:  # pragma: no cover - only used when dependency metadata is unavailable.
    DEFAULT_SOURCE_EXTRACTOR_VERSION = "unknown"


@dataclass(slots=True)
class ExtractionResult:
    source_url: str
    final_url: str
    title: str | None
    text: str
    markdown: str
    fetched_utc: str
    content_type: str | None
    http_etag: str | None
    http_last_modified: str | None
    content_sha256: str
    extractor_name: str
    extractor_version: str
    markdown_char_count: int


def _normalize_header_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_markdown(value: str) -> str:
    return value.replace("\r\n", "\n").strip()


def _extract_html_title(soup: BeautifulSoup) -> str | None:
    if soup.title and soup.title.string:
        return " ".join(soup.title.string.split())
    return None


def _extract_html_preview_text(soup: BeautifulSoup) -> str:
    chunks = [
        " ".join(tag.get_text(" ", strip=True).split())
        for tag in soup.find_all(("p", "li", "blockquote", "h1", "h2", "h3"))
    ]
    if not any(chunks):
        chunks = [" ".join(part.split()) for part in soup.stripped_strings]
    preview = "\n\n".join(part for part in chunks if part)
    return preview[:_MAX_EXTRACTION_PREVIEW_CHARS]


def _is_html_content(content_type: str | None, source_url: str) -> bool:
    if content_type in _HTML_CONTENT_TYPES:
        return True
    suffix = Path(urlsplit(source_url).path).suffix.casefold()
    return suffix in {".htm", ".html", ".xhtml"}


def _guess_extension(source_url: str, content_type: str | None) -> str | None:
    suffix = Path(urlsplit(source_url).path).suffix.casefold()
    if suffix:
        return suffix
    if content_type == "text/html":
        return ".html"
    if content_type == "application/xhtml+xml":
        return ".xhtml"
    return None


def _build_stream_info(
    *, source_url: str, content_type: str | None, charset: str | None
) -> StreamInfo:
    file_name = Path(urlsplit(source_url).path).name or None
    return StreamInfo(
        mimetype=content_type,
        extension=_guess_extension(source_url, content_type),
        charset=charset,
        filename=file_name,
        url=source_url,
    )


def _clean_html_document(raw_html: str) -> tuple[BeautifulSoup, str]:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["noscript", "script", "style", "template"]):
        tag.decompose()
    for tag_name in ("aside", "footer", "form", "nav"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    title = _extract_html_title(soup)
    content_root = soup.find("article") or soup.find("main") or soup.body
    if content_root is None:
        return soup, str(soup)

    if getattr(content_root, "name", None) == "body":
        body_html = content_root.decode_contents()
    else:
        body_html = str(content_root)
    head_html = f"<head><title>{escape(title)}</title></head>" if title else ""
    return soup, f"<html>{head_html}<body>{body_html}</body></html>"


_YOUTUBE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+",
)
_YOUTUBE_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]+)",
)


def _is_youtube_url(url: str) -> bool:
    return _YOUTUBE_URL_RE.match(url) is not None


def _extract_youtube_video_id(url: str) -> str | None:
    match = _YOUTUBE_VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


def _fetch_youtube_transcript(video_id: str) -> str | None:
    ytt = YouTubeTranscriptApi()
    try:
        transcript_list = ytt.list(video_id)
    except Exception:
        logger.exception("Failed to list YouTube transcripts", extra={"video_id": video_id})
        return None

    languages = ["en"]
    for transcript in transcript_list:
        if transcript.language_code not in languages:
            languages.append(transcript.language_code)

    try:
        fetched = ytt.fetch(video_id, languages=languages)
    except Exception:
        logger.exception("Failed to fetch YouTube transcript", extra={"video_id": video_id})
        return None

    return "\n".join(snippet.text for snippet in fetched.snippets if snippet.text)


@lru_cache(maxsize=1)
def _get_markdown_converter() -> MarkItDown:
    return MarkItDown()


def _convert_to_markdown(
    raw_bytes: bytes,
    *,
    source_url: str,
    content_type: str | None,
    charset: str | None,
) -> tuple[str, str | None]:
    result = _get_markdown_converter().convert_stream(
        io.BytesIO(raw_bytes),
        stream_info=_build_stream_info(
            source_url=source_url,
            content_type=content_type,
            charset=charset,
        ),
    )
    return _normalize_markdown(result.markdown or result.text_content or ""), result.title


async def extract_url_text(source_url: str) -> ExtractionResult | None:
    if _is_youtube_url(source_url):
        return await _extract_youtube(source_url)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=10.0,
            headers=DEFAULT_HTTP_HEADERS,
        ) as client:
            response = await client.get(source_url)
            response.raise_for_status()
    except Exception:
        logger.exception("URL extraction failed", extra={"source_url": source_url})
        return None

    final_url = str(response.url)
    fetched_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    content_type_header = _normalize_header_value(response.headers.get("content-type"))
    content_type = None
    if content_type_header:
        content_type = content_type_header.split(";", 1)[0].strip().casefold() or None
    charset = response.encoding or None
    http_etag = _normalize_header_value(response.headers.get("etag"))
    http_last_modified = _normalize_header_value(
        response.headers.get("last-modified")
    )

    title: str | None = None
    text = ""
    markdown = ""

    try:
        if _is_html_content(content_type, final_url):
            soup, cleaned_html = _clean_html_document(response.text)
            title = _extract_html_title(soup)
            text = _extract_html_preview_text(soup)
            markdown, markdown_title = _convert_to_markdown(
                cleaned_html.encode("utf-8"),
                source_url=final_url,
                content_type="text/html",
                charset="utf-8",
            )
            title = title or markdown_title
        else:
            markdown, title = _convert_to_markdown(
                response.content,
                source_url=final_url,
                content_type=content_type,
                charset=charset,
            )
            text = markdown[:_MAX_EXTRACTION_PREVIEW_CHARS]
    except Exception:
        logger.exception(
            "Markdown conversion failed",
            extra={"source_url": source_url, "final_url": final_url},
        )
        markdown = text

    if not markdown:
        markdown = text
    if not text:
        text = markdown[:_MAX_EXTRACTION_PREVIEW_CHARS]
    if not markdown:
        logger.warning(
            "URL extraction produced no text", extra={"source_url": source_url}
        )
        return None

    markdown_bytes = markdown.encode("utf-8")
    return ExtractionResult(
        source_url=source_url,
        final_url=final_url,
        title=title,
        text=text,
        markdown=markdown,
        fetched_utc=fetched_utc,
        content_type=content_type,
        http_etag=http_etag,
        http_last_modified=http_last_modified,
        content_sha256=sha256(markdown_bytes).hexdigest(),
        extractor_name=DEFAULT_SOURCE_EXTRACTOR_NAME,
        extractor_version=DEFAULT_SOURCE_EXTRACTOR_VERSION,
        markdown_char_count=len(markdown),
    )


async def _extract_youtube(source_url: str) -> ExtractionResult | None:
    video_id = _extract_youtube_video_id(source_url)
    if not video_id:
        logger.warning("Could not extract video ID", extra={"source_url": source_url})
        return None

    fetched_utc = datetime.now(UTC).replace(microsecond=0).isoformat()

    # Fetch page title and upload date via HTTP
    title: str | None = None
    upload_date: str | None = None
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=10.0,
            headers=DEFAULT_HTTP_HEADERS,
        ) as client:
            response = await client.get(source_url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title = _extract_html_title(soup)
        if title and title.endswith(" - YouTube"):
            title = title[: -len(" - YouTube")]
        date_meta = soup.find("meta", attrs={"itemprop": "datePublished"})
        if isinstance(date_meta, Tag):
            raw_date = date_meta.get("content")
            if raw_date:
                upload_date = str(raw_date)[:10]  # YYYY-MM-DD
    except Exception:
        logger.warning("Failed to fetch YouTube page metadata", extra={"source_url": source_url})

    # Fetch transcript
    transcript = _fetch_youtube_transcript(video_id)
    if not transcript:
        logger.warning(
            "YouTube extraction produced no transcript",
            extra={"source_url": source_url},
        )
        return None

    parts: list[str] = []
    if title:
        parts.append(f"# {title}\n")
    if upload_date:
        parts.append(f"**Published:** {upload_date}\n")
    parts.append(transcript)
    markdown = "\n".join(parts)

    text = markdown[:_MAX_EXTRACTION_PREVIEW_CHARS]
    markdown_bytes = markdown.encode("utf-8")
    return ExtractionResult(
        source_url=source_url,
        final_url=source_url,
        title=title,
        text=text,
        markdown=markdown,
        fetched_utc=fetched_utc,
        content_type="video/youtube",
        http_etag=None,
        http_last_modified=None,
        content_sha256=sha256(markdown_bytes).hexdigest(),
        extractor_name=DEFAULT_SOURCE_EXTRACTOR_NAME,
        extractor_version=DEFAULT_SOURCE_EXTRACTOR_VERSION,
        markdown_char_count=len(markdown),
    )
