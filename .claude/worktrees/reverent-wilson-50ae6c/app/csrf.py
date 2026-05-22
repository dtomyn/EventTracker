from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs

from fastapi import Request
from markupsafe import Markup


def _get_csrf_secret_file() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "csrf_secret.txt"


def _load_or_create_csrf_secret() -> str:
    """Return a stable CSRF secret, persisting it across restarts.

    Priority:
    1. EVENTTRACKER_CSRF_SECRET env var (useful in production / CI)
    2. data/csrf_secret.txt (auto-created on first run; survives dev reloads)
    3. In-memory fallback (used only when the data dir is not writable)
    """
    env_secret = os.environ.get("EVENTTRACKER_CSRF_SECRET", "").strip()
    if env_secret:
        return env_secret
    secret_file = _get_csrf_secret_file()
    try:
        if secret_file.exists():
            stored = secret_file.read_text().strip()
            if len(stored) >= 32:
                return stored
    except OSError:
        pass
    new_secret = secrets.token_hex(32)
    try:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(new_secret)
    except OSError:
        pass
    return new_secret


_CSRF_COOKIE_NAME = "csrf_token"
_CSRF_FORM_FIELD = "csrf_token"
_CSRF_HEADER_NAME = "x-csrf-token"
_CSRF_SECRET = _load_or_create_csrf_secret()
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _generate_csrf_token(session_id: str) -> str:
    """Derive a CSRF token from a per-session random value and a server secret."""
    return hmac.new(
        _CSRF_SECRET.encode(), session_id.encode(), hashlib.sha256
    ).hexdigest()


def _get_or_create_session_id(request: Request) -> tuple[str, bool]:
    """Return (session_id, is_new) from the CSRF cookie, creating one if absent."""
    existing = request.cookies.get(_CSRF_COOKIE_NAME)
    if existing:
        return existing, False
    return secrets.token_hex(16), True


async def csrf_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    session_id, is_new = _get_or_create_session_id(request)
    expected_token = _generate_csrf_token(session_id)

    # Validate token on state-changing requests (skip during automated tests)
    if request.method not in _CSRF_SAFE_METHODS and not os.environ.get("TESTING"):
        # Try form field first, then header (for JS fetch calls)
        submitted_token: str | None = None
        content_type = (request.headers.get("content-type") or "").lower()
        if "application/x-www-form-urlencoded" in content_type:
            body = await request.body()
            parsed_body = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            token_values = parsed_body.get(_CSRF_FORM_FIELD, [])
            submitted_token = token_values[0] if token_values else None
        if not submitted_token:
            submitted_token = request.headers.get(_CSRF_HEADER_NAME)
        if not hmac.compare_digest(submitted_token or "", expected_token):
            from starlette.responses import PlainTextResponse
            return PlainTextResponse("CSRF validation failed", status_code=403)

    # Attach token to request state so templates can access it
    request.state.csrf_token = expected_token

    response = await call_next(request)

    if is_new:
        response.set_cookie(
            _CSRF_COOKIE_NAME,
            session_id,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )

    return response


def csrf_hidden_input(request: Request) -> Markup:
    """Make csrf_token available in all Jinja2 templates."""
    token = getattr(request.state, "csrf_token", "")
    return Markup(
        f'<input type="hidden" name="{_CSRF_FORM_FIELD}" value="{token}">'
    )
