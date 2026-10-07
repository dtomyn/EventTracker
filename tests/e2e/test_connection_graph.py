"""E2E tests for the Connection Graph page against the real API.

Covers:
- Nodes render from seeded connections, the side panel shows entry details
- Esc closes the side panel
- The time-lapse scrubber reveals entries in chronological order
"""
from __future__ import annotations

import re
import sqlite3
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

_D3_URL = "https://cdn.jsdelivr.net/npm/d3@7"
_d3_cache: dict[str, str] = {}


def _d3_script() -> str:
    if "body" not in _d3_cache:
        try:
            with urllib.request.urlopen(_D3_URL, timeout=30) as response:
                _d3_cache["body"] = response.read().decode("utf-8")
        except OSError:
            _d3_cache["body"] = ""
    return _d3_cache["body"]


def _allow_d3(page: Page) -> None:
    body = _d3_script()
    if not body:
        pytest.skip("D3 could not be downloaded from the CDN.")

    def fulfill(route: Route) -> None:
        route.fulfill(status=200, content_type="application/javascript", body=body)

    page.route("**/npm/d3@7**", fulfill)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _seed(db_path: Path, group_name: str) -> tuple[int, list[int]]:
    with sqlite3.connect(db_path) as con:
        cursor = con.execute(
            "INSERT INTO timeline_groups(name, web_search_query, is_default) VALUES (?, NULL, 0)",
            (group_name,),
        )
        group_id = int(cursor.lastrowid or 0)
        ids: list[int] = []
        for year, month, day, title in (
            (2026, 1, 5, "Graph first entry"),
            (2026, 2, 10, "Graph second entry"),
            (2026, 3, 20, "Graph third entry"),
        ):
            ts = _now()
            cursor = con.execute(
                """
                INSERT INTO entries (
                    event_year, event_month, event_day, sort_key, group_id,
                    title, source_url, generated_text, final_text, created_utc, updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    year, month, day, year * 10_000 + month * 100 + day, group_id,
                    title, f"<p>{title} summary text.</p>", ts, ts,
                ),
            )
            ids.append(int(cursor.lastrowid or 0))
        for source, target in ((ids[0], ids[1]), (ids[1], ids[2])):
            con.execute(
                "INSERT INTO entry_connections (source_entry_id, target_entry_id, note, created_utc) "
                "VALUES (?, ?, '', ?)",
                (source, target, _now()),
            )
        con.commit()
    return group_id, ids


def test_connection_graph_panel_and_timelapse(page: Page, e2e_session) -> None:
    group_id, ids = _seed(e2e_session.db_path, f"{e2e_session.group_name} Graph")
    _allow_d3(page)

    page.goto(f"/groups/{group_id}/connections/graph")
    shell = page.locator("[data-eg-shell][data-eg-ready='true']")
    expect(shell).to_be_visible(timeout=15_000)
    expect(page.locator("g.eg-node")).to_have_count(3)

    page.locator(f"g.eg-node[data-node-id='{ids[0]}']").click()
    panel = page.locator("[data-eg-panel]")
    expect(panel).to_have_class(re.compile(r"is-open"))
    expect(panel.locator("[data-eg-panel-title]")).to_have_text("Graph first entry")
    expect(panel).to_contain_text("Graph first entry summary text.")
    expect(panel.get_by_role("link", name="Open entry")).to_have_attribute(
        "href", f"/entries/{ids[0]}/view"
    )
    page.keyboard.press("Escape")
    expect(panel).to_have_attribute("aria-hidden", "true")

    scrubber = page.get_by_role("slider", name="Time-lapse date")
    count = page.locator("[data-eg-count]")
    expect(count).to_have_text("3 of 3 entries")
    scrubber.fill("0")
    expect(count).to_have_text("1 of 3 entries")
    expect(page.locator("g.eg-node:not(.is-hidden)")).to_have_count(1)
    scrubber.fill("36")
    expect(count).to_have_text("2 of 3 entries")
    expect(page.locator("path.eg-link.is-hidden")).to_have_count(1)
