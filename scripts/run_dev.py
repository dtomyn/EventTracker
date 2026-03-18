from __future__ import annotations

import argparse
import atexit
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from dotenv import load_dotenv
import uvicorn


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 35231
RELOAD_PID_FILE = ".eventtracker-dev-reload.pid"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the EventTracker development server."
    )
    parser.add_argument("--host", default="", help="Override the bind host.")
    parser.add_argument("--port", type=int, help="Override the bind port.")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload.")
    return parser.parse_args()


def load_settings(args: argparse.Namespace | None = None) -> tuple[str, int]:
    workspace_root = Path(__file__).resolve().parents[1]
    load_dotenv(workspace_root / ".env", override=False)

    host_override = ""
    raw_port = ""
    if args is not None:
        host_override = args.host.strip() if args.host else ""
        if args.port is not None:
            raw_port = str(args.port)

    host = (
        host_override
        or os.getenv("EVENTTRACKER_HOST", DEFAULT_HOST).strip()
        or DEFAULT_HOST
    )
    raw_port = raw_port or os.getenv("EVENTTRACKER_PORT", str(DEFAULT_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"Invalid EVENTTRACKER_PORT value: {raw_port}") from exc
    return host, port


def _reload_pid_path() -> Path:
    return Path(__file__).resolve().parents[1] / RELOAD_PID_FILE


def _read_reload_pid(pid_path: Path) -> int | None:
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "").strip()
    return bool(output) and "No tasks are running" not in output


def _terminate_process_tree(pid: int) -> None:
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    combined_output = f"{result.stdout}\n{result.stderr}".strip()
    if result.returncode == 0:
        return
    lowered_output = combined_output.casefold()
    if "not found" in lowered_output or "no running instance" in lowered_output:
        return
    raise SystemExit(
        f"Could not stop the previous EventTracker dev server: {combined_output}"
    )


def _clear_reload_pid(pid_path: Path, owner_pid: int) -> None:
    current_pid = _read_reload_pid(pid_path)
    if current_pid != owner_pid:
        return
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        return


def _prepare_reload_session() -> None:
    if sys.platform != "win32":
        return
    pid_path = _reload_pid_path()
    stale_pid = _read_reload_pid(pid_path)
    if stale_pid and stale_pid != os.getpid() and _is_process_running(stale_pid):
        _terminate_process_tree(stale_pid)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_clear_reload_pid, pid_path, os.getpid())


def _ensure_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        last_error: OSError | None = None
        for _ in range(20):
            try:
                sock.bind((host, port))
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)
        raise SystemExit(
            f"Could not start EventTracker on http://{host}:{port}. "
            "The port is already in use. Stop the existing server or use --port."
        ) from last_error


def main() -> None:
    args = parse_args()
    host, port = load_settings(args)
    if args.reload:
        _prepare_reload_session()
    _ensure_port_available(host, port)
    uvicorn.run("app.main:app", host=host, port=port, reload=args.reload)


if __name__ == "__main__":
    main()
