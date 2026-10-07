"""Factories for OpenAI-compatible clients.

All OpenAI SDK clients in the app are built here so gateway-specific auth
options apply consistently to chat, story, topic, and embedding calls.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import cast

import httpx
from openai import AsyncOpenAI, Omit, OpenAI

API_KEY_HEADER_ENV = "OPENAI_API_KEY_HEADER"

# RFC 9110 token characters, which is what an HTTP header field name must be.
_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


class OpenAIClientConfigurationError(ValueError):
    pass


def load_api_key_header() -> str | None:
    """Return the configured header name for sending the API key, if any.

    When unset, the SDK default (``Authorization: Bearer <key>``) is used.
    Callers are expected to have loaded the app environment already.
    """
    header = os.getenv(API_KEY_HEADER_ENV, "").strip()
    if not header:
        return None
    if not _HEADER_NAME_PATTERN.fullmatch(header):
        raise OpenAIClientConfigurationError(
            f"{API_KEY_HEADER_ENV} must be a valid HTTP header name, for example `api-key`."
        )
    if header.casefold() == "authorization":
        raise OpenAIClientConfigurationError(
            f"{API_KEY_HEADER_ENV} must not be `Authorization`; leave it blank to "
            "use the default bearer token."
        )
    return header


def create_openai_client(
    api_key: str,
    base_url: str | None = None,
    api_key_header: str | None = None,
    *,
    http_client: httpx.Client | None = None,
) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        default_headers=_auth_headers(api_key, api_key_header),
        http_client=http_client,
    )


def create_async_openai_client(
    api_key: str,
    base_url: str | None = None,
    api_key_header: str | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url or None,
        default_headers=_auth_headers(api_key, api_key_header),
        http_client=http_client,
    )


def _auth_headers(
    api_key: str, api_key_header: str | None
) -> Mapping[str, str] | None:
    if api_key_header is None:
        return None
    # Send the key only in the custom header so it is not also exposed as a
    # bearer token to gateways that forward Authorization upstream. The SDK
    # drops Omit-valued headers at request time, but its constructor
    # annotation only admits str values.
    headers: dict[str, str | Omit] = {api_key_header: api_key, "Authorization": Omit()}
    return cast(Mapping[str, str], headers)
