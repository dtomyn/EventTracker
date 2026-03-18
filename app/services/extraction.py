from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtractionResult:
    source_url: str
    title: str | None
    text: str


async def extract_url_text(source_url: str) -> ExtractionResult | None:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            response = await client.get(source_url)
            response.raise_for_status()
    except Exception:
        logger.exception("URL extraction failed", extra={"source_url": source_url})
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    paragraphs = [
        " ".join(paragraph.get_text(" ", strip=True).split())
        for paragraph in soup.find_all("p")
    ]
    text = "\n\n".join(part for part in paragraphs if part)
    if not text:
        logger.warning(
            "URL extraction produced no text", extra={"source_url": source_url}
        )
        return None

    title = None
    if soup.title and soup.title.string:
        title = " ".join(soup.title.string.split())

    return ExtractionResult(source_url=source_url, title=title, text=text[:4000])
