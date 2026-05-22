"""Helpers for CSRF token handling in tests."""
from __future__ import annotations

from starlette.testclient import TestClient

from app.main import _generate_csrf_token, _CSRF_COOKIE_NAME


def get_csrf_token(client: TestClient) -> str:
    """Make a GET request to obtain a CSRF session cookie and return the derived token."""
    response = client.get("/")
    session_id = response.cookies.get(_CSRF_COOKIE_NAME)
    if not session_id:
        raise RuntimeError("CSRF cookie not set after GET request")
    return _generate_csrf_token(session_id)


def csrf_data(client: TestClient, data: dict) -> dict:
    """Return *data* with the csrf_token field added."""
    token = get_csrf_token(client)
    return {**data, "csrf_token": token}
