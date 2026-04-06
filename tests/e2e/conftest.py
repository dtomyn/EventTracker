from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterator

import httpx
import pytest
from playwright.sync_api import Browser, Page, Playwright, expect, sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DB_PATH = REPO_ROOT / "data" / "EventTracker.db"
SERVER_HOST = "127.0.0.1"
SERVER_START_TIMEOUT_SECONDS = 30.0
SCREENSHOTS_DIR = REPO_ROOT / "test-screenshots"
TEMP_DIR_CLEANUP_TIMEOUT_SECONDS = 5.0
TEMP_DIR_CLEANUP_RETRY_INTERVAL_SECONDS = 0.2

# Module-level cache so each external stylesheet URL is fetched only once per
# test session rather than on every request interception.
_CSS_CACHE: dict[str, str] = {}


def _fetch_cdn_css(url: str) -> str:
    """Return the text of a CDN stylesheet, fetching it once and caching the result."""
    if url not in _CSS_CACHE:
        try:
            response = httpx.get(url, timeout=10, follow_redirects=True)
            _CSS_CACHE[url] = response.text if response.status_code == 200 else ""
        except Exception:
            _CSS_CACHE[url] = ""
    return _CSS_CACHE[url]


@dataclass(frozen=True, slots=True)
class E2ESession:
    base_url: str
    run_id: str
    group_name: str
    db_path: Path
    ai_provider: str


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((SERVER_HOST, 0))
        return int(sock.getsockname()[1])


def _copy_seed_database(target_db_path: Path) -> None:
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    if SOURCE_DB_PATH.exists():
        shutil.copy2(SOURCE_DB_PATH, target_db_path)


def _build_server_env(db_path: Path, *, ai_provider: str) -> dict[str, str]:
    env = os.environ.copy()
    env["EVENTTRACKER_DB_PATH"] = str(db_path)
    env["EVENTTRACKER_AI_PROVIDER"] = ai_provider
    env["OPENAI_API_KEY"] = ""
    env["OPENAI_CHAT_MODEL_ID"] = ""
    env["OPENAI_BASE_URL"] = ""
    env["OPENAI_EMBEDDING_MODEL_ID"] = ""
    env["COPILOT_CHAT_MODEL_ID"] = ""
    env["COPILOT_CLI_PATH"] = ""
    env["COPILOT_CLI_URL"] = ""
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("LOG_LEVEL", "WARNING")
    return env


def _start_server(db_path: Path, port: int, *, ai_provider: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            SERVER_HOST,
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env=_build_server_env(db_path, ai_provider=ai_provider),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for_server(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = ""
            if process.stdout is not None:
                output = process.stdout.read()
            raise RuntimeError(
                "EventTracker server exited before it became ready.\n"
                f"Captured output:\n{output}"
            )
        try:
            response = httpx.get(base_url, timeout=1.5)
            if response.status_code == 200:
                return
        except Exception as exc:  # pragma: no cover - timing dependent.
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(
        f"EventTracker server did not become ready within {SERVER_START_TIMEOUT_SECONDS:.0f} seconds."
    ) from last_error


def _stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    if process.stdout is not None:
        process.stdout.close()


def _is_retryable_windows_cleanup_error(error: OSError) -> bool:
    """Return True when Windows reports a transient file-lock cleanup failure."""
    return getattr(error, "winerror", None) == 32


def _remove_temp_dir(temp_dir: Path) -> bool:
    """Delete a Playwright temp directory, retrying briefly for Windows file locks."""
    deadline = time.monotonic() + TEMP_DIR_CLEANUP_TIMEOUT_SECONDS
    while temp_dir.exists():
        try:
            shutil.rmtree(temp_dir)
            return True
        except OSError as exc:
            if not _is_retryable_windows_cleanup_error(exc):
                raise
            if time.monotonic() >= deadline:
                return False
            time.sleep(TEMP_DIR_CLEANUP_RETRY_INTERVAL_SECONDS)
    return True


def _cleanup_stale_temp_dirs() -> None:
    """Best-effort cleanup for leftover Playwright temp directories from prior runs."""
    temp_root = Path(tempfile.gettempdir())
    for temp_dir in temp_root.glob("eventtracker-playwright-*"):
        if temp_dir.is_dir():
            _remove_temp_dir(temp_dir)


def _lookup_group_id(db_path: Path, group_name: str) -> int | None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT id FROM timeline_groups WHERE name = ?",
            (group_name,),
        ).fetchone()
    return int(row[0]) if row else None


@pytest.fixture
def e2e_session() -> Iterator[E2ESession]:
    yield from _create_e2e_session(ai_provider="openai")


@pytest.fixture
def copilot_e2e_session() -> Iterator[E2ESession]:
    yield from _create_e2e_session(ai_provider="copilot")


def _create_e2e_session(*, ai_provider: str) -> Iterator[E2ESession]:
    run_id = time.strftime("%Y%m%d%H%M%S")
    temp_dir = Path(tempfile.mkdtemp(prefix="eventtracker-playwright-"))
    temp_db_path = temp_dir / "EventTracker-playwright.db"
    _copy_seed_database(temp_db_path)
    port = _find_free_port()
    base_url = f"http://{SERVER_HOST}:{port}"
    process = _start_server(temp_db_path, port, ai_provider=ai_provider)
    try:
        _wait_for_server(base_url, process)
        yield E2ESession(
            base_url=base_url,
            run_id=run_id,
            group_name=f"Playwright E2E {run_id}",
            db_path=temp_db_path,
            ai_provider=ai_provider,
        )
    finally:
        _stop_server(process)
        _remove_temp_dir(temp_dir)


@pytest.fixture(scope="session")
def playwright_instance() -> Iterator[Playwright]:
    _cleanup_stale_temp_dirs()
    with sync_playwright() as playwright:
        yield playwright
    _cleanup_stale_temp_dirs()


@pytest.fixture
def browser(playwright_instance: Playwright) -> Iterator[Browser]:
    raw_headless = os.getenv("EVENTTRACKER_PLAYWRIGHT_HEADLESS", "1").strip().lower()
    headless = raw_headless not in {"0", "false", "no"}
    slow_mo = int(os.getenv("EVENTTRACKER_PLAYWRIGHT_SLOW_MO", "0") or "0")
    browser = playwright_instance.chromium.launch(headless=headless, slow_mo=slow_mo)
    try:
        yield browser
    finally:
        browser.close()


SCREENSHOTS_DIR = REPO_ROOT / "test-screenshots"


@pytest.fixture
def page(request: pytest.FixtureRequest, browser: Browser, e2e_session: E2ESession) -> Iterator[Page]:
    yield from _create_page(browser, e2e_session, test_name=request.node.nodeid)


@pytest.fixture
def copilot_page(request: pytest.FixtureRequest, browser: Browser, copilot_e2e_session: E2ESession) -> Iterator[Page]:
    yield from _create_page(browser, copilot_e2e_session, test_name=request.node.nodeid)


def _create_page(browser: Browser, session: E2ESession, *, test_name: str = "") -> Iterator[Page]:
    context = browser.new_context(
        base_url=session.base_url,
        accept_downloads=True,
        viewport={"width": 1440, "height": 1100},
    )
    context.route(
        "**://cdn.jsdelivr.net/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/css",
            body=_fetch_cdn_css(route.request.url),
        )
        if route.request.resource_type == "stylesheet"
        else route.abort(),
    )
    page = context.new_page()
    page.set_default_timeout(10_000)
    try:
        yield page
    finally:
        if test_name:
            try:
                SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
                safe_name = re.sub(r"[^\w\-]", "_", test_name)[:120]
                page.screenshot(path=str(SCREENSHOTS_DIR / f"{safe_name}.png"), full_page=True)
            except Exception:
                pass
        context.close()


@pytest.fixture
def ensure_dedicated_group(
    page: Page, e2e_session: E2ESession
) -> Callable[[], int]:
    def _ensure() -> int:
        group_id = _lookup_group_id(e2e_session.db_path, e2e_session.group_name)
        if group_id is not None:
            return group_id

        page.goto("/admin/groups")
        page.get_by_label("New group name").fill(e2e_session.group_name)
        page.get_by_role("button", name="Add Group").click()
        expect(page).to_have_url(re.compile(r".*/admin/groups\?notice=created$"))

        group_id = _lookup_group_id(e2e_session.db_path, e2e_session.group_name)
        assert group_id is not None
        return group_id

    return _ensure