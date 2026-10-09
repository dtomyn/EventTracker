"""Start EventTracker from the current worktree on a temp DB copy, then screenshot pages or run a command.

Run with the worktree as the working directory, for example:
    uv run --directory <worktree> python <skill>/scripts/preview.py shoot --path / --path /search
    uv run --directory <worktree> python <skill>/scripts/preview.py run -- python <flow.py>

The real database is never opened: the main checkout's data/EventTracker.db is copied to a temp
folder first (or an empty database is used with --empty-db). The server always stops on exit.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Iterator
import urllib.error
import urllib.request

HOST = "127.0.0.1"
START_TIMEOUT_SECONDS = 60.0
VIEWPORT_HEIGHTS = {375: 812}
DEFAULT_HEIGHT = 900


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--empty-db", action="store_true", help="Start with an empty database instead of a copy.")
    sub = parser.add_subparsers(dest="mode", required=True)

    shoot = sub.add_parser("shoot", help="Screenshot pages at each width and theme.")
    shoot.add_argument("--path", action="append", required=True, help="Page path such as / or /search; repeatable.")
    shoot.add_argument("--width", action="append", type=int, help="Viewport width; repeatable (default 1280 and 375).")
    shoot.add_argument("--theme", action="append", choices=["light", "dark"], help="Theme; repeatable (default both).")
    shoot.add_argument("--out", type=Path, help="Folder for PNGs (default: a new temp folder).")
    shoot.add_argument("--viewport-only", action="store_true", help="Capture only the viewport, not the full page.")

    run = sub.add_parser("run", help="Run a command with PREVIEW_BASE_URL set, then stop the server.")
    run.add_argument("command", nargs=argparse.REMAINDER, help="Command to run, after --.")
    return parser.parse_args()


def main_checkout_db() -> Path:
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(common).parent / "data" / "EventTracker.db"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def server_env(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["EVENTTRACKER_DB_PATH"] = str(db_path)
    # Keep previews offline and deterministic: no AI provider, no keys.
    env["EVENTTRACKER_AI_PROVIDER"] = "openai"
    for key in ("OPENAI_API_KEY", "OPENAI_CHAT_MODEL_ID", "OPENAI_BASE_URL", "OPENAI_API_KEY_HEADER",
                "OPENAI_EMBEDDING_MODEL_ID", "COPILOT_CHAT_MODEL_ID", "COPILOT_CLI_PATH", "COPILOT_CLI_URL"):
        env[key] = ""
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("LOG_LEVEL", "WARNING")
    return env


def wait_until_ready(base_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit("EventTracker exited before it became ready; run the unit tests to see why.")
        try:
            with urllib.request.urlopen(base_url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.3)
    raise SystemExit(f"EventTracker did not become ready within {START_TIMEOUT_SECONDS:.0f} seconds.")


@contextmanager
def running_app(empty_db: bool) -> Iterator[str]:
    temp_dir = Path(tempfile.mkdtemp(prefix="my-factory-preview-db-"))
    db_path = temp_dir / "EventTracker.db"
    source = main_checkout_db()
    if not empty_db and source.exists():
        shutil.copy2(source, db_path)
    port = free_port()
    base_url = f"http://{HOST}:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(port)],
        env=server_env(db_path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_until_ready(base_url, process)
        print(f"app ready at {base_url} (db: {'empty' if empty_db or not source.exists() else 'copy of data/EventTracker.db'})", file=sys.stderr)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        shutil.rmtree(temp_dir, ignore_errors=True)


def slug(path: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-")
    return cleaned or "home"


def shoot(base_url: str, args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright

    widths = args.width or [1280, 375]
    themes = args.theme or ["light", "dark"]
    out = args.out or Path(tempfile.mkdtemp(prefix="my-factory-preview-shots-"))
    out.mkdir(parents=True, exist_ok=True)
    problems = 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for path in args.path:
            for width in widths:
                for theme in themes:
                    context = browser.new_context(
                        viewport={"width": width, "height": VIEWPORT_HEIGHTS.get(width, DEFAULT_HEIGHT)},
                        color_scheme=theme,
                    )
                    context.add_init_script(f"localStorage.setItem('theme', '{theme}')")
                    page = context.new_page()
                    errors: list[str] = []
                    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                    page.on("pageerror", lambda exc: errors.append(str(exc)))
                    response = page.goto(base_url + path, wait_until="networkidle")
                    status = response.status if response else 0
                    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
                    file = out / f"{slug(path)}-{width}-{theme}.png"
                    page.screenshot(path=str(file), full_page=not args.viewport_only)
                    notes = [f"HTTP {status}"]
                    if overflow:
                        notes.append("horizontal overflow")
                    if errors:
                        notes.append(f"{len(errors)} console error(s): {errors[0][:160]}")
                    if status >= 400 or overflow or errors:
                        problems += 1
                    print(f"- {file.as_posix()} - {path} at {width}px {theme}: {', '.join(notes)}")
                    context.close()
        browser.close()
    print(f"\n{problems} screenshot(s) with problems; open the PNGs with Read to review them.")
    return 0


def normalize_paths(paths: list[str]) -> list[str]:
    normalized = []
    for path in paths:
        # Git Bash rewrites "/search" into "C:/Program Files/Git/search" unless MSYS_NO_PATHCONV=1 is set.
        if re.match(r"^[A-Za-z]:[\\/]", path):
            raise SystemExit(f"--path {path!r} looks like a Windows path. In Git Bash, prefix the command with MSYS_NO_PATHCONV=1.")
        normalized.append("/" + path.lstrip("/"))
    return normalized


def main() -> int:
    args = parse_args()
    if not Path("app/main.py").exists():
        raise SystemExit("Run this from an EventTracker worktree (use: uv run --directory <worktree> python ...).")
    if args.mode == "shoot":
        args.path = normalize_paths(args.path)
    if args.mode == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            raise SystemExit("Give a command after --, for example: run -- python flow.py")
        if command[0] in ("python", "python3"):
            # Use the worktree's interpreter so Playwright and the app's packages are importable.
            command[0] = sys.executable
    with running_app(args.empty_db) as base_url:
        if args.mode == "shoot":
            return shoot(base_url, args)
        env = os.environ.copy()
        env["PREVIEW_BASE_URL"] = base_url
        return subprocess.run(command, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
