from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

from app.services.extraction import extract_url_text


def _run(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


class TestExtractUrlText(unittest.TestCase):
    """Tests for extract_url_text() error handling and edge cases."""

    @patch("app.services.extraction.httpx.AsyncClient")
    def test_returns_none_on_timeout(self, mock_client_cls: MagicMock) -> None:
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("timed out")
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = _run(extract_url_text("https://example.com/slow"))
        self.assertIsNone(result)

    @patch("app.services.extraction.httpx.AsyncClient")
    def test_returns_none_on_connection_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("connection refused")
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = _run(extract_url_text("https://example.com/down"))
        self.assertIsNone(result)

    @patch("app.services.extraction.httpx.AsyncClient")
    def test_returns_none_on_404(self, mock_client_cls: MagicMock) -> None:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = _run(extract_url_text("https://example.com/missing"))
        self.assertIsNone(result)

    @patch("app.services.extraction.httpx.AsyncClient")
    def test_returns_none_on_500(self, mock_client_cls: MagicMock) -> None:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = _run(extract_url_text("https://example.com/error"))
        self.assertIsNone(result)

    @patch("app.services.extraction.httpx.AsyncClient")
    def test_returns_none_when_no_paragraphs(self, mock_client_cls: MagicMock) -> None:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "<html><body><div>No paragraphs here</div></body></html>"
        mock_response.content = mock_response.text.encode("utf-8")
        mock_response.url = "https://example.com/no-p-tags"
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.encoding = "utf-8"
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = _run(extract_url_text("https://example.com/no-p-tags"))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("No paragraphs here", result.text)
        self.assertIn("No paragraphs here", result.markdown)

    @patch("app.services.extraction.httpx.AsyncClient")
    def test_extracts_title_and_text(self, mock_client_cls: MagicMock) -> None:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = (
            "<html><head><title>Test Page</title></head>"
            "<body><p>First paragraph.</p><p>Second paragraph.</p></body></html>"
        )
        mock_response.content = mock_response.text.encode("utf-8")
        mock_response.url = "https://example.com/article"
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.encoding = "utf-8"
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = _run(extract_url_text("https://example.com/article"))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.title, "Test Page")
        self.assertIn("First paragraph.", result.text)
        self.assertIn("Second paragraph.", result.text)
        self.assertEqual(result.source_url, "https://example.com/article")
        self.assertEqual(result.final_url, "https://example.com/article")
        self.assertEqual(result.content_type, "text/html")
        self.assertIn("First paragraph.", result.markdown)

    @patch("app.services.extraction.httpx.AsyncClient")
    def test_strips_script_and_style_content(self, mock_client_cls: MagicMock) -> None:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = (
            "<html><body>"
            "<script>var x = 1;</script>"
            "<style>.hidden{display:none}</style>"
            "<p>Clean content.</p>"
            "</body></html>"
        )
        mock_response.content = mock_response.text.encode("utf-8")
        mock_response.url = "https://example.com/scripts"
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.encoding = "utf-8"
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = _run(extract_url_text("https://example.com/scripts"))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertNotIn("var x", result.text)
        self.assertNotIn("hidden", result.text)
        self.assertNotIn("var x", result.markdown)
        self.assertNotIn("hidden", result.markdown)
        self.assertIn("Clean content.", result.text)

    @patch("app.services.extraction.httpx.AsyncClient")
    def test_truncates_text_to_4000_chars(self, mock_client_cls: MagicMock) -> None:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        long_text = "A" * 5000
        mock_response.text = f"<html><body><p>{long_text}</p></body></html>"
        mock_response.content = mock_response.text.encode("utf-8")
        mock_response.url = "https://example.com/long"
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.encoding = "utf-8"
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = _run(extract_url_text("https://example.com/long"))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertLessEqual(len(result.text), 4000)
