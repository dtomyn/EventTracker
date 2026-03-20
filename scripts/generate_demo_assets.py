from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - runtime guidance
    raise SystemExit(
        "Pillow is required. Run this script with: uv run --with pillow python .\\scripts\\generate_demo_assets.py"
    ) from exc

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


VIEWPORT_WIDTH = 1200
VIEWPORT_HEIGHT = 800
DEFAULT_BASE_URL = "http://127.0.0.1:35231"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path("docs") / "demo-assets" / "screenshots"
DEFAULT_GIF_NAME = Path("docs") / "demo-assets" / "EventTracker-demo-web-generate.gif"
DEFAULT_NO_SEARCH_GIF_NAME = (
    Path("docs")
    / "demo-assets"
    / "EventTracker-demo-web-generate-no-search-actions.gif"
)
SCREEN_HOLD_MS = 1500
TRANSITION_STEPS = 2
TRANSITION_STEP_MS = 100
CANVAS_SIZE = (900, 600)
CANVAS_BACKGROUND = (249, 250, 251)
SUMMARY_SOURCE_URL = "https://code.visualstudio.com/updates/v1_112"
SEARCH_QUERY = "Copilot"
FULL_FRAME_NAMES = [
    "01-home.png",
    "02-recent-loading.png",
    "03-recent-results.png",
    "04-query-entered.png",
    "05-filter-results.png",
    "06-search-results.png",
    "07-url-entered.png",
    "08-generating.png",
    "09-summary-generated.png",
    "10-dark-mode.png",
    "11-story-mode-screen.png",
    "12-story-generated.png",
]
NO_SEARCH_FRAME_NAMES = [
    "01-home.png",
    "02-recent-loading.png",
    "03-recent-results.png",
    "07-url-entered.png",
    "08-generating.png",
    "09-summary-generated.png",
    "10-dark-mode.png",
    "11-story-mode-screen.png",
    "12-story-generated.png",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture refreshed EventTracker demo screenshots and rebuild the main GIF. "
            "The app must already be running."
        )
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Folder where PNG screenshots will be stored.",
    )
    parser.add_argument(
        "--gif-name",
        default=str(DEFAULT_GIF_NAME),
        help="Path for the rebuilt GIF artifact.",
    )
    parser.add_argument(
        "--also-no-search-actions",
        action="store_true",
        help="Also build a GIF variant that omits the filter and search action frames.",
    )
    parser.add_argument(
        "--no-search-gif-name",
        default=str(DEFAULT_NO_SEARCH_GIF_NAME),
        help="Path for the no-search-actions GIF artifact.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser while capturing assets.",
    )
    return parser.parse_args()


def save_screenshot(page: Page, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(destination), timeout=30_000)
        return
    except PlaywrightTimeoutError:
        pass

    main_region = page.locator("main")
    main_region.wait_for(state="visible", timeout=30_000)
    main_region.screenshot(path=str(destination), timeout=30_000)


def wait_for_recent_results(page: Page, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    results = page.locator("[data-group-web-search-results] a")
    loading = page.locator("[data-group-web-search-loading]")
    while time.monotonic() < deadline:
        if results.count() > 0 and not loading.is_visible():
            return
        page.wait_for_timeout(1_000)
    raise TimeoutError("Timed out waiting for Recent Developments results.")


def wait_for_generated_summary(page: Page, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    summary = page.get_by_label("Event Summary")
    feedback = page.locator("#generate-feedback")
    while time.monotonic() < deadline:
        summary_value = summary.input_value().strip()
        feedback_text = feedback.inner_text().strip()
        if summary_value and "Generating summary" not in feedback_text:
            return
        page.wait_for_timeout(1_000)
    raise TimeoutError("Timed out waiting for generated summary output.")


def wait_for_story_result(page: Page, timeout_seconds: int = 180) -> None:
    deadline = time.monotonic() + timeout_seconds
    title = page.locator("#story-result-title")
    while time.monotonic() < deadline:
        if title.count() > 0:
            return
        page.wait_for_timeout(1_000)
    raise TimeoutError("Timed out waiting for Story Mode output.")


def normalize_frame(path: Path) -> Image.Image:
    with Image.open(path) as image:
        working = image.convert("RGB")
        scale = max(
            CANVAS_SIZE[0] / working.width,
            CANVAS_SIZE[1] / working.height,
        )
        resized = working.resize(
            (
                max(1, round(working.width * scale)),
                max(1, round(working.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        left = max(0, (resized.width - CANVAS_SIZE[0]) // 2)
        top = max(0, (resized.height - CANVAS_SIZE[1]) // 2)
        right = left + CANVAS_SIZE[0]
        bottom = top + CANVAS_SIZE[1]
        return resized.crop((left, top, right, bottom))


def build_gif(frame_paths: list[Path], destination: Path) -> None:
    base_frames = [normalize_frame(path) for path in frame_paths]
    assembled_frames: list[Image.Image] = []
    durations: list[int] = []

    for index, frame in enumerate(base_frames):
        assembled_frames.append(frame)
        durations.append(SCREEN_HOLD_MS)
        if index == len(base_frames) - 1:
            continue

        next_frame = base_frames[index + 1]
        for step in range(1, TRANSITION_STEPS + 1):
            alpha = step / (TRANSITION_STEPS + 1)
            assembled_frames.append(Image.blend(frame, next_frame, alpha))
            durations.append(TRANSITION_STEP_MS)

    palette_frames = [
        frame.quantize(colors=64, method=Image.Quantize.MEDIANCUT)
        for frame in assembled_frames
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    palette_frames[0].save(
        destination,
        save_all=True,
        append_images=palette_frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )


def resolve_frame_paths(output_dir: Path, frame_names: list[str]) -> list[Path]:
    return [output_dir / frame_name for frame_name in frame_names]


def resolve_repo_path(path_value: str) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def capture_assets(page: Page, base_url: str, output_dir: Path) -> list[Path]:
    page.set_viewport_size({"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})

    output_paths = resolve_frame_paths(output_dir, FULL_FRAME_NAMES)

    page.goto(f"{base_url}/")
    page.wait_for_load_state("networkidle")
    save_screenshot(page, output_paths[0])

    page.get_by_role("button", name="Recent Developments").click()
    page.wait_for_timeout(300)
    refresh_button = page.get_by_role("button", name="Refresh").first
    if refresh_button.is_enabled():
        refresh_button.click()
        page.wait_for_timeout(250)
    save_screenshot(page, output_paths[1])

    wait_for_recent_results(page)
    save_screenshot(page, output_paths[2])

    searchbox = page.get_by_role("searchbox", name="Filter timeline in plain English")
    searchbox.fill(SEARCH_QUERY)
    save_screenshot(page, output_paths[3])

    page.get_by_role("button", name="Filter").click()
    page.wait_for_load_state("networkidle")
    save_screenshot(page, output_paths[4])

    searchbox = page.get_by_role("searchbox", name="Filter timeline in plain English")
    searchbox.fill(SEARCH_QUERY)
    page.get_by_role("button", name="Search").click()
    page.wait_for_load_state("networkidle")
    save_screenshot(page, output_paths[5])

    page.get_by_role("link", name="New Entry").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Source URL").fill(SUMMARY_SOURCE_URL)
    save_screenshot(page, output_paths[6])

    page.get_by_role("button", name="Generate").click()
    page.wait_for_timeout(600)
    save_screenshot(page, output_paths[7])

    wait_for_generated_summary(page)
    save_screenshot(page, output_paths[8])

    page.goto(f"{base_url}/")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Toggle dark mode").click()
    page.wait_for_timeout(600)
    save_screenshot(page, output_paths[9])

    page.get_by_role("link", name="Story Mode").click()
    page.wait_for_load_state("networkidle")
    save_screenshot(page, output_paths[10])

    page.locator("[data-story-generate-button]").click(no_wait_after=True)
    wait_for_story_result(page)
    page.wait_for_timeout(500)
    save_screenshot(page, output_paths[11])

    return output_paths


def remove_existing_pngs(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for existing_file in output_dir.glob("*.png"):
        existing_file.unlink()


def main() -> int:
    args = parse_args()
    output_dir = resolve_repo_path(args.output_dir)
    gif_path = resolve_repo_path(args.gif_name)
    no_search_gif_path = resolve_repo_path(args.no_search_gif_name)

    remove_existing_pngs(output_dir)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context()
        page = context.new_page()
        try:
            frame_paths = capture_assets(page, args.base_url.rstrip("/"), output_dir)
        finally:
            context.close()
            browser.close()

    build_gif(frame_paths, gif_path)
    if args.also_no_search_actions:
        build_gif(
            resolve_frame_paths(output_dir, NO_SEARCH_FRAME_NAMES), no_search_gif_path
        )

    print(f"Wrote {gif_path}")
    if args.also_no_search_actions:
        print(f"Wrote {no_search_gif_path}")
    print(f"Saved {len(frame_paths)} screenshots to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
