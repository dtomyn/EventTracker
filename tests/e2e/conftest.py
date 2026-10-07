from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Iterator

import httpx
import pytest
from playwright.sync_api import Browser, Page, Playwright, expect, sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DB_PATH = REPO_ROOT / "data" / "EventTracker.db"
SERVER_HOST = "127.0.0.1"
# Readiness is event-driven (uvicorn banner), so this is only a ceiling: it costs
# nothing when startup is fast but tolerates a heavily loaded machine, where
# importing the app in many parallel servers can exceed 30s.
SERVER_START_TIMEOUT_SECONDS = 120.0
# Per-test end-of-run screenshots consumed by scripts/generate_test_report.py.
# Run artefacts only: the directory is gitignored.
SCREENSHOTS_DIR = REPO_ROOT / "test-results" / "e2e-screenshots"
TEMP_DIR_CLEANUP_TIMEOUT_SECONDS = 5.0
TEMP_DIR_CLEANUP_RETRY_INTERVAL_SECONDS = 0.2
STALE_TEMP_DIR_AGE_SECONDS = 60 * 60

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
    env["OPENAI_API_KEY_HEADER"] = ""
    env["OPENAI_EMBEDDING_MODEL_ID"] = ""
    env["COPILOT_CHAT_MODEL_ID"] = ""
    env["COPILOT_CLI_PATH"] = ""
    env["COPILOT_CLI_URL"] = ""
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("LOG_LEVEL", "WARNING")
    return env


class _ManagedServer:
    """A uvicorn child process whose output is drained on a background thread.

    The server binds port 0 so the OS assigns a free port atomically; the
    actual port is parsed from uvicorn's startup banner. This removes the race
    between probing for a free port and the server binding it. Draining the
    pipe continuously also stops a chatty server from blocking on a full pipe.
    """

    _READY_PATTERN = re.compile(r"Uvicorn running on http://[\d.]+:(\d+)")

    def __init__(self, db_path: Path, *, ai_provider: str) -> None:
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                SERVER_HOST,
                "--port",
                "0",
                "--log-level",
                "info",
                "--no-access-log",
            ],
            cwd=REPO_ROOT,
            env=_build_server_env(db_path, ai_provider=ai_provider),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._lines: list[str] = []
        self._port: int | None = None
        self._ready = threading.Event()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._lines.append(line)
            if self._port is None:
                match = self._READY_PATTERN.search(line)
                if match:
                    self._port = int(match.group(1))
                    self._ready.set()
        self._ready.set()

    @property
    def output(self) -> str:
        return "".join(self._lines)

    def wait_until_ready(self) -> int:
        """Block until uvicorn reports it is serving, returning the bound port."""
        if not self._ready.wait(SERVER_START_TIMEOUT_SECONDS):
            raise RuntimeError(
                f"EventTracker server did not become ready within {SERVER_START_TIMEOUT_SECONDS:.0f} seconds.\n"
                f"Captured output:\n{self.output}"
            )
        if self._port is None:
            raise RuntimeError(
                "EventTracker server exited before it became ready.\n"
                f"Captured output:\n{self.output}"
            )
        return self._port

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self._reader.join(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()


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
    """Best-effort cleanup for leftover Playwright temp directories from prior runs.

    Only directories older than STALE_TEMP_DIR_AGE_SECONDS are removed: the glob
    also matches the TypeScript harness prefix, and a younger directory may hold
    the live database of a run still in progress.
    """
    temp_root = Path(tempfile.gettempdir())
    cutoff = time.time() - STALE_TEMP_DIR_AGE_SECONDS
    for temp_dir in temp_root.glob("eventtracker-playwright-*"):
        try:
            is_stale = temp_dir.is_dir() and temp_dir.stat().st_mtime < cutoff
        except OSError:
            continue
        if is_stale:
            # Every xdist worker (and any concurrent run) sweeps at session start,
            # so another sweeper may delete the same directory mid-walk.
            try:
                _remove_temp_dir(temp_dir)
            except FileNotFoundError:
                continue


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
    server = _ManagedServer(temp_db_path, ai_provider=ai_provider)
    try:
        port = server.wait_until_ready()
        yield E2ESession(
            base_url=f"http://{SERVER_HOST}:{port}",
            run_id=run_id,
            group_name=f"Playwright E2E {run_id}",
            db_path=temp_db_path,
            ai_provider=ai_provider,
        )
    finally:
        server.stop()
        _remove_temp_dir(temp_dir)


@pytest.fixture(scope="session")
def playwright_instance() -> Iterator[Playwright]:
    _cleanup_stale_temp_dirs()
    with sync_playwright() as playwright:
        yield playwright
    _cleanup_stale_temp_dirs()


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright) -> Iterator[Browser]:
    raw_headless = os.getenv("EVENTTRACKER_PLAYWRIGHT_HEADLESS", "1").strip().lower()
    headless = raw_headless not in {"0", "false", "no"}
    slow_mo = int(os.getenv("EVENTTRACKER_PLAYWRIGHT_SLOW_MO", "0") or "0")
    browser = playwright_instance.chromium.launch(headless=headless, slow_mo=slow_mo)
    try:
        yield browser
    finally:
        browser.close()


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
        # Entrance animations (card reveals, count-ups, view transitions) are not
        # what these tests verify; reduced motion makes the UI settle immediately.
        # Tests that exercise timed behaviour opt back in with page.emulate_media.
        reduced_motion="reduce",
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