"""Run EventTracker's checks in the current worktree and print a Markdown summary.

Run with the worktree as the working directory, for example:
    uv run --directory <worktree> python <skill>/scripts/run_checks.py --e2e-py copy_link
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

TAIL_LINES = 40
PYTEST_NO_TESTS_COLLECTED = 5


@dataclass
class Check:
    label: str
    command: list[str]
    log_name: str


@dataclass
class Result:
    check: Check
    passed: bool
    summary: str
    log_path: Path
    tail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--e2e-py", metavar="EXPR", help="pytest -k expression for tests/e2e, or 'all'.")
    parser.add_argument("--e2e-ts", metavar="SPEC", action="append", default=[], help="TypeScript spec path; repeatable.")
    parser.add_argument("--skip-unit", action="store_true", help="Skip the unit test suite.")
    parser.add_argument("--skip-pyright", action="store_true", help="Skip pyright.")
    return parser.parse_args()


def build_checks(args: argparse.Namespace) -> list[Check]:
    python = sys.executable
    checks: list[Check] = []
    if not args.skip_unit:
        checks.append(Check("Unit tests", [python, "-m", "pytest", "tests/", "--ignore=tests/e2e", "-q"], "unit.log"))
    if not args.skip_pyright:
        checks.append(Check("Pyright", [python, "-m", "pyright"], "pyright.log"))
    if args.e2e_py:
        selector = [] if args.e2e_py == "all" else ["-k", args.e2e_py]
        label = "E2E (python)" if args.e2e_py == "all" else f"E2E (python, -k {args.e2e_py})"
        checks.append(Check(label, [python, "-m", "pytest", "tests/e2e", "-q", *selector], "e2e-python.log"))
    if args.e2e_ts:
        npm = shutil.which("npm")
        if npm is None:
            raise SystemExit("npm is not on PATH; cannot run TypeScript E2E specs.")
        checks.append(Check(f"E2E (typescript, {', '.join(args.e2e_ts)})", [npm, "run", "test:e2e:ts", "--", *args.e2e_ts], "e2e-typescript.log"))
    return checks


def last_line(output: str) -> str:
    lines = [line.strip(" =") for line in output.splitlines() if line.strip(" =")]
    return lines[-1] if lines else "no output"


def run(check: Check, log_dir: Path) -> Result:
    completed = subprocess.run(check.command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    output = completed.stdout + completed.stderr
    log_path = log_dir / check.log_name
    log_path.write_text(output, encoding="utf-8")
    summary = last_line(output)
    if completed.returncode == PYTEST_NO_TESTS_COLLECTED and "pytest" in check.command:
        summary = f"no tests matched ({summary})"
    tail = "\n".join(output.splitlines()[-TAIL_LINES:])
    return Result(check, completed.returncode == 0, summary, log_path, tail)


def ensure_node_modules() -> None:
    if Path("node_modules").exists():
        return
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm is not on PATH; cannot install TypeScript E2E dependencies.")
    print("node_modules missing; running npm ci ...", file=sys.stderr)
    subprocess.run([npm, "ci", "--no-audit", "--no-fund"], check=True)


def main() -> int:
    args = parse_args()
    if not Path("pyproject.toml").exists() or not Path("app").is_dir():
        raise SystemExit("Run this from an EventTracker worktree (use: uv run --directory <worktree> python ...).")
    if args.e2e_ts:
        ensure_node_modules()

    log_dir = Path(tempfile.mkdtemp(prefix="my-factory-checks-"))
    results = []
    for check in build_checks(args):
        print(f"running {check.label} ...", file=sys.stderr, flush=True)
        results.append(run(check, log_dir))

    print("## Checks")
    for result in results:
        print(f"- {result.check.label}: {'PASS' if result.passed else 'FAIL'} - {result.summary}")
        if not result.passed:
            print(f"  Log: {result.log_path.as_posix()}")
    for result in results:
        if not result.passed:
            print(f"\n### {result.check.label} (last {TAIL_LINES} lines)\n\n```text\n{result.tail}\n```")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
