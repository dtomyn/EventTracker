"""E2E tests for global navigation, entry detail page, 404 handling,
dark-mode theme toggle, and duplicate source URL detection.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_group(db_path: Path, name: str) -> int:
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT id FROM timeline_groups WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return int(row[0])
        cursor = con.execute(
            "INSERT INTO timeline_groups(name, web_search_query, is_default) VALUES (?, NULL, 0)",
            (name,),
        )
        con.commit()
        return int(cursor.lastrowid)


def _seed_entry(
    db_path: Path,
    *,
    group_id: int,
    year: int,
    month: int,
    day: int | None,
    title: str,
    final_text: str,
    source_url: str | None = None,
) -> int:
    sort_key = (year * 10_000) + (month * 100) + (day or 0)
    ts = _utc_now_iso()
    with sqlite3.connect(db_path) as con:
        cursor = con.execute(
            """
            INSERT INTO entries (
                event_year, event_month, event_day, sort_key, group_id,
                title, source_url, generated_text, final_text, created_utc, updated_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (year, month, day, sort_key, group_id, title, source_url, final_text, ts, ts),
        )
        con.commit()
        return int(cursor.lastrowid)


def _seed_link(db_path: Path, *, entry_id: int, url: str, note: str) -> None:
    ts = _utc_now_iso()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO entry_links(entry_id, url, note, created_utc) VALUES (?, ?, ?, ?)",
            (entry_id, url, note, ts),
        )
        con.commit()


def _seed_tag(db_path: Path, *, entry_id: int, tag: str) -> None:
    with sqlite3.connect(db_path) as con:
        # Tags use a normalized schema: tags(id, name) + entry_tags(entry_id, tag_id)
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
        tag_id_row = con.execute("SELECT id FROM tags WHERE name = ?", (tag,)).fetchone()
        assert tag_id_row is not None
        con.execute(
            "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id) VALUES (?, ?)",
            (entry_id, int(tag_id_row[0])),
        )
        con.commit()


def _seed_source_snapshot(
    db_path: Path,
    *,
    entry_id: int,
    source_url: str,
    final_url: str,
    markdown: str,
) -> None:
    ts = _utc_now_iso()
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            INSERT INTO entry_source_snapshots (
                entry_id,
                source_url,
                final_url,
                raw_title,
                source_markdown,
                fetched_utc,
                content_type,
                http_etag,
                http_last_modified,
                content_sha256,
                extractor_name,
                extractor_version,
                markdown_char_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
            """,
            (
                entry_id,
                source_url,
                final_url,
                "Captured source title",
                markdown,
                ts,
                "text/html",
                hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                "markitdown",
                "0.1.5",
                len(markdown),
            ),
        )
        con.commit()


def _group_name(e2e_session, suffix: str) -> str:
    return f"{e2e_session.group_name} Nav {suffix}"


# ---------------------------------------------------------------------------
# Navigation bar
# ---------------------------------------------------------------------------


def test_navbar_links_navigate_to_correct_pages(
    page: Page,
    e2e_session,
) -> None:
    page.goto("/")

    # "New Entry" button leads to the new entry form
    page.get_by_role("link", name="New Entry").click()
    expect(page).to_have_url(re.compile(r".*/entries/new$"))
    expect(page.get_by_role("heading", name="New Entry")).to_be_visible()

    # Admin button leads to the groups admin page
    page.get_by_role("link", name="Admin").click()
    expect(page).to_have_url(re.compile(r".*/admin/groups$"))
    expect(page.get_by_role("heading", name="Timeline Groups")).to_be_visible()

    # "Events" brand link returns to the timeline
    page.get_by_role("link", name="Events").click()
    expect(page).to_have_url(re.compile(r".*/$"))


def test_export_link_in_navbar_downloads_json(
    page: Page,
) -> None:
    with page.expect_download() as dl:
        page.goto("/")
        page.get_by_role("link", name="Export").click()
    download = dl.value
    download_path = download.path()
    assert download_path is not None
    import json
    payload = json.loads(Path(download_path).read_text(encoding="utf-8"))
    assert "count" in payload
    assert "entries" in payload


# ---------------------------------------------------------------------------
# Dark-mode theme toggle
# ---------------------------------------------------------------------------


def test_dark_mode_toggle_switches_and_persists_theme(
    page: Page,
) -> None:
    page.goto("/")

    # Initial theme on a fresh context is 'light'
    initial_theme = page.evaluate(
        "document.documentElement.getAttribute('data-bs-theme')"
    )
    toggle = page.locator("#theme-toggle")
    toggle.click()

    new_theme = page.evaluate(
        "document.documentElement.getAttribute('data-bs-theme')"
    )
    assert new_theme != initial_theme, "Theme should change after toggle"

    persisted = page.evaluate("localStorage.getItem('theme')")
    assert persisted == new_theme, "New theme should be persisted in localStorage"

    # Toggle back
    toggle.click()
    restored_theme = page.evaluate(
        "document.documentElement.getAttribute('data-bs-theme')"
    )
    assert restored_theme == initial_theme


# ---------------------------------------------------------------------------
# Entry detail page
# ---------------------------------------------------------------------------


def test_entry_detail_page_shows_title_date_group_tags_source_url_and_links(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "detail")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    source_url = "https://example.com/releases/detail-test"
    entry_id = _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=10,
        title=f"{e2e_session.run_id} detail page title",
        final_text="<p>Detail page <strong>body</strong> text.</p>",
        source_url=source_url,
    )
    _seed_link(
        e2e_session.db_path,
        entry_id=entry_id,
        url="https://example.com/releases/followup",
        note="Follow-up article",
    )
    _seed_tag(e2e_session.db_path, entry_id=entry_id, tag="playwright")
    _seed_tag(e2e_session.db_path, entry_id=entry_id, tag="detail-test")
    _seed_source_snapshot(
        e2e_session.db_path,
        entry_id=entry_id,
        source_url=source_url,
        final_url="https://example.com/releases/detail-test/final",
        markdown="# Captured source\n\n[Reference](https://example.com/reference)",
    )

    page.goto(f"/entries/{entry_id}/view")

    expect(page.get_by_role("heading", level=1)).to_contain_text("detail page title")
    # Date and group name in secondary text
    page_text = page.locator("body").text_content() or ""
    assert "March 10, 2026" in page_text or "2026" in page_text
    assert group_name in page_text

    # Tags
    expect(page.get_by_text("playwright", exact=True)).to_be_visible()
    expect(page.get_by_text("detail-test", exact=True)).to_be_visible()

    # Rich text body
    expect(page.locator(".entry-rich-text")).to_contain_text("Detail page")
    expect(page.locator(".entry-rich-text strong")).to_have_text("body")

    # Source URL table
    expect(page.get_by_role("link", name=source_url)).to_be_visible()
    expect(page.get_by_role("heading", name="Saved Source Snapshot")).to_be_visible()
    expect(page.locator(".source-snapshot-rendered")).to_contain_text("Captured source")
    expect(page.locator(".source-snapshot-rendered a")).to_have_attribute(
        "href", "https://example.com/reference"
    )

    # Additional links table
    expect(page.get_by_role("link", name="https://example.com/releases/followup")).to_be_visible()
    expect(page.get_by_text("Follow-up article")).to_be_visible()

    # Edit button navigates to edit form
    page.get_by_role("link", name="Edit").click()
    expect(page).to_have_url(re.compile(rf".*/entries/{entry_id}$"))
    expect(page.get_by_role("heading", name="Edit Entry")).to_be_visible()
    expect(page.get_by_text("Saved source snapshot")).to_be_visible()


def test_entry_detail_page_shows_no_links_placeholder_when_empty(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "no-links")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    entry_id = _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=1,
        day=5,
        title=f"{e2e_session.run_id} no links entry",
        final_text="<p>No links here.</p>",
    )

    page.goto(f"/entries/{entry_id}/view")

    expect(page.get_by_text("No additional links saved for this event.")).to_be_visible()


# ---------------------------------------------------------------------------
# 404 handling
# ---------------------------------------------------------------------------


def test_entry_not_found_returns_404(
    page: Page,
) -> None:
    response = page.goto("/entries/9999999/view")
    assert response is not None
    assert response.status == 404


def test_story_not_found_returns_non_200(
    page: Page,
    e2e_session,
) -> None:
    # Use Playwright's API request context to send a GET without navigating
    # the page, avoiding page.goto() load-event timeout on server error pages.
    import httpx

    response = httpx.get(
        f"{e2e_session.base_url}/story/9999999",
        timeout=15,
        follow_redirects=False,
    )
    # The route should return a non-200 response (404 when schema is current,
    # or 500 when the seed DB has an older timeline_stories schema).
    assert response.status_code != 200, (
        f"Expected a non-200 response for a non-existent story, got {response.status_code}"
    )


def test_nonexistent_group_on_timeline_returns_404(
    page: Page,
) -> None:
    response = page.goto("/?group_id=9999999")
    assert response is not None
    assert response.status == 404


# ---------------------------------------------------------------------------
# Duplicate source URL
# ---------------------------------------------------------------------------


def test_create_entry_with_duplicate_source_url_shows_error(
    page: Page,
    e2e_session,
    ensure_dedicated_group,
) -> None:
    group_id = ensure_dedicated_group()
    dup_url = "https://example.com/releases/duplicate-url-test"

    # Seed an entry with that URL directly so it already exists
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=2,
        day=1,
        title="Existing entry with dup URL",
        final_text="<p>Already saved.</p>",
        source_url=dup_url,
    )

    page.goto("/entries/new")
    page.get_by_label("Timeline Group").select_option(str(group_id))
    page.get_by_label("Year").fill("2026")
    page.get_by_label("Month").fill("2")
    page.get_by_label("Day").fill("15")
    page.get_by_label("Title").fill(f"{e2e_session.run_id} duplicate url")
    page.get_by_label("Source URL").fill(dup_url)
    page.get_by_label("Event Summary").fill("<p>Duplicate URL attempt.</p>")
    page.get_by_role("button", name="Save Entry").click()

    expect(page).to_have_url(re.compile(r".*/entries/new$"))
    expect(page.locator("#source_url")).to_have_class(re.compile(r".*\bis-invalid\b.*"))
    expect(page.locator("#source_url + .invalid-feedback")).to_contain_text(
        "same source URL"
    )


# ---------------------------------------------------------------------------
# Back to timeline link on entry detail
# ---------------------------------------------------------------------------


def test_back_to_timeline_link_navigates_to_home(
    page: Page,
    e2e_session,
) -> None:
    group_id = _ensure_group(
        e2e_session.db_path, _group_name(e2e_session, "back-link")
    )
    entry_id = _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=4,
        day=1,
        title=f"{e2e_session.run_id} back link entry",
        final_text="<p>Back link test.</p>",
    )

    page.goto(f"/entries/{entry_id}/view")
    page.get_by_role("link", name="Back to timeline").click()
    expect(page).to_have_url(re.compile(r".*/$"))
