from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

import httpx

from app.services.openai_client import (
    OpenAIClientConfigurationError,
    create_async_openai_client,
    create_openai_client,
    load_api_key_header,
)

_EMBEDDING_RESPONSE = {
    "object": "list",
    "model": "test-model",
    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
    "usage": {"prompt_tokens": 1, "total_tokens": 1},
}


class _HeaderRecorder:
    def __init__(self) -> None:
        self.headers: httpx.Headers | None = None
        self.url: httpx.URL | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.headers = request.headers
        self.url = request.url
        return httpx.Response(200, json=_EMBEDDING_RESPONSE)


class TestLoadApiKeyHeader(unittest.TestCase):
    def test_unset_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(load_api_key_header())

    def test_blank_returns_none(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY_HEADER": "  "}, clear=True):
            self.assertIsNone(load_api_key_header())

    def test_valid_header_is_trimmed(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY_HEADER": " api-key "}, clear=True):
            self.assertEqual(load_api_key_header(), "api-key")

    def test_invalid_header_name_raises(self) -> None:
        for value in ("api key", "api-key:", "api\nkey"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"OPENAI_API_KEY_HEADER": value}, clear=True):
                    with self.assertRaises(OpenAIClientConfigurationError):
                        load_api_key_header()

    def test_authorization_header_is_rejected(self) -> None:
        with patch.dict(
            os.environ, {"OPENAI_API_KEY_HEADER": "authorization"}, clear=True
        ):
            with self.assertRaises(OpenAIClientConfigurationError):
                load_api_key_header()


class TestCreateOpenAIClient(unittest.TestCase):
    def test_default_sends_bearer_token(self) -> None:
        recorder = _HeaderRecorder()
        client = create_openai_client(
            "secret-key",
            "https://gateway.example/openai/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(recorder)),
        )

        client.embeddings.create(model="test-model", input="hello")

        assert recorder.headers is not None
        self.assertEqual(recorder.headers.get("authorization"), "Bearer secret-key")
        self.assertNotIn("api-key", recorder.headers)

    def test_custom_header_replaces_bearer_token(self) -> None:
        recorder = _HeaderRecorder()
        client = create_openai_client(
            "secret-key",
            "https://gateway.example/openai/v1",
            "api-key",
            http_client=httpx.Client(transport=httpx.MockTransport(recorder)),
        )

        client.embeddings.create(model="test-model", input="hello")

        assert recorder.headers is not None
        assert recorder.url is not None
        self.assertEqual(recorder.headers.get("api-key"), "secret-key")
        self.assertNotIn("authorization", recorder.headers)
        self.assertEqual(
            str(recorder.url), "https://gateway.example/openai/v1/embeddings"
        )

    def test_async_custom_header_replaces_bearer_token(self) -> None:
        recorder = _HeaderRecorder()

        async def _run() -> None:
            client = create_async_openai_client(
                "secret-key",
                "https://gateway.example/openai/v1",
                "api-key",
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(recorder)),
            )
            await client.embeddings.create(model="test-model", input="hello")
            await client.close()

        asyncio.run(_run())

        assert recorder.headers is not None
        self.assertEqual(recorder.headers.get("api-key"), "secret-key")
        self.assertNotIn("authorization", recorder.headers)


if __name__ == "__main__":
    unittest.main()
