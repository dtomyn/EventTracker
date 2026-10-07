"""E2E tests for the Story Mode page.

Covers: page structure with zero-entry warning, mocked story generation
(success, error codes), the Save snapshot workflow, viewing a saved story,
and scope pill rendering.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Helpers shared across tests
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
        assert cursor.lastrowid is not None
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
) -> int:
    sort_key = (year * 10_000) + (month * 100) + (day or 0)
    ts = _utc_now_iso()
    with sqlite3.connect(db_path) as con:
        cursor = con.execute(
            """
            INSERT INTO entries (
                event_year, event_month, event_day, sort_key, group_id,
                title, source_url, generated_text, final_text, created_utc, updated_utc
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (year, month, day, sort_key, group_id, title, final_text, ts, ts),
        )
        con.commit()
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)


def _group_name(e2e_session, suffix: str) -> str:
    return f"{e2e_session.group_name} Story {suffix}"


def _seed_story(
    db_path: Path,
    *,
    group_id: int,
    title: str,
    narrative_html: str,
    entry_id: int | None = None,
) -> int:
    """Insert a saved story record and optional citation into the DB."""
    ts = _utc_now_iso()
    with sqlite3.connect(db_path) as con:
        cursor = con.execute(
            """
            INSERT INTO timeline_stories (
                scope_type, group_id, query_text, year, month,
                format, title, narrative_html, narrative_text,
                generated_utc, updated_utc, provider_name,
                source_entry_count, truncated_input, error_text
            ) VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?, NULL, ?, ?, ?, ?, 0, NULL)
            """,
            (
                "group",
                group_id,
                "executive_summary",
                title,
                narrative_html,
                ts,
                ts,
                "test-provider",
                1 if entry_id is not None else 0,
            ),
        )
        con.commit()
        assert cursor.lastrowid is not None
        story_id = int(cursor.lastrowid)

        if entry_id is not None:
            con.execute(
                """
                INSERT INTO timeline_story_entries
                    (story_id, entry_id, citation_order, quote_text, note)
                VALUES (?, ?, 1, ?, NULL)
                """,
                (story_id, entry_id, "Seed body for citation."),
            )
            con.commit()

    return story_id


def _seed_story_artifact(
    db_path: Path,
    *,
    story_id: int,
    compiled_html: str,
    compiled_css: str,
) -> None:
    """Insert a saved executive deck artifact for a Story Mode snapshot."""
    ts = _utc_now_iso()
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            INSERT INTO timeline_story_artifacts (
                story_id,
                artifact_kind,
                source_format,
                source_text,
                compiled_html,
                compiled_css,
                metadata_json,
                generated_utc,
                compiled_utc,
                compiler_name,
                compiler_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                story_id,
                "executive_deck",
                "marpit_markdown",
                "---\nmarpit: true\n---\n# Deck",
                compiled_html,
                compiled_css,
                '{"slide_count":1}',
                ts,
                ts,
                "marpit",
                "4.1.2",
            ),
        )
        con.commit()


# ---------------------------------------------------------------------------
# Story page load
# ---------------------------------------------------------------------------


def test_story_page_loads_with_correct_headings_and_form_elements(
    page: Page,
    e2e_session,
) -> None:
    group_id = _ensure_group(
        e2e_session.db_path, _group_name(e2e_session, "page-load")
    )

    page.goto(f"/story?group_id={group_id}")

    expect(page.get_by_role("heading", name="Build a narrative from the current scope")).to_be_visible()
    expect(page.get_by_label("Group")).to_be_visible()
    expect(page.get_by_label("Search query")).to_be_visible()
    expect(page.get_by_label("Year")).to_be_visible()
    expect(page.get_by_label("Month")).to_be_visible()
    expect(page.get_by_label("Format")).to_be_visible()
    expect(page.get_by_role("button", name="Generate story")).to_be_visible()


def test_story_page_shows_no_entries_warning_when_scope_is_empty(
    page: Page,
    e2e_session,
) -> None:
    empty_group_id = _ensure_group(
        e2e_session.db_path, _group_name(e2e_session, "empty-scope")
    )

    page.goto(f"/story?group_id={empty_group_id}")

    expect(page.get_by_role("alert")).to_contain_text("No entries match this scope")
    # The placeholder empty state should also be in the output panel
    expect(page.get_by_role("heading", name="No generated story yet")).to_be_visible()


def test_story_page_scope_pills_display_group_and_entry_count(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "scope-pills")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=2,
        day=10,
        title="Scope pill test entry",
        final_text="<p>Scope pill test entry body.</p>",
    )

    page.goto(f"/story?group_id={group_id}")

    story_hero = page.locator(".story-hero")
    expect(story_hero.locator(".story-scope-pill").first).to_be_visible()
    # Entry count should mention 1 entry
    expect(story_hero).to_contain_text("1 scoped entr")
    # Selected group name should appear in a pill
    expect(story_hero).to_contain_text(group_name)


def test_story_page_format_selector_has_three_options(
    page: Page,
    e2e_session,
) -> None:
    group_id = _ensure_group(
        e2e_session.db_path, _group_name(e2e_session, "format-opts")
    )

    page.goto(f"/story?group_id={group_id}")

    format_select = page.get_by_label("Format")
    option_labels = format_select.locator("option").all_text_contents()
    assert "Executive Summary" in option_labels
    assert "Detailed Chronology" in option_labels
    assert "What Changed Recently" in option_labels


# ---------------------------------------------------------------------------
# Mocked story generation round-trip
# ---------------------------------------------------------------------------


def test_story_generate_mocked_success_shows_title_and_narrative(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "gen-success")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=1,
        title="Gen success seed entry",
        final_text="<p>Gen success seed body.</p>",
    )

    generated_title = f"{e2e_session.run_id} Mocked Story Title"
    generated_narrative = "<p>The <strong>arc</strong> of events shows a clear pattern.</p>"

    # Generation streams SSE events; the "result" event carries the full
    # rendered story page. Mock the stream with a simplified page that contains
    # the key selectors we assert on.
    def handle_generate(route):
        result_html = (
            f"<html><body>"
            f'<h2 class="h3 mb-1" id="story-result-title">{generated_title}</h2>'
            f'<div class="story-rich-text" data-story-result>{generated_narrative}</div>'
            f'<div class="alert alert-success" role="alert">Story generated for the current scope.</div>'
            f"</body></html>"
        )
        result_payload = json.dumps({"kind": "result", "html": result_html})
        complete_payload = json.dumps({"kind": "complete", "ok": True})
        body = (
            f"event: result\ndata: {result_payload}\n\n"
            f"event: complete\ndata: {complete_payload}\n\n"
        )
        route.fulfill(status=200, content_type="text/event-stream", body=body)

    page.route("**/story/generate/stream", handle_generate)

    page.goto(f"/story?group_id={group_id}")
    with page.expect_response(
        lambda r: r.request.method == "POST" and "/story/generate/stream" in r.url,
        timeout=8_000,
    ):
        page.get_by_role("button", name="Generate story").click()

    # After the navigation (form POST + server response renders full page),
    # the key content should be visible.
    expect(page.get_by_role("heading", name=generated_title)).to_be_visible()
    expect(page.locator("[data-story-result]")).to_contain_text(
        "arc of events shows a clear pattern."
    )


@pytest.mark.parametrize(
    ("status_code", "expected_alert_text"),
    [
        (400, "Story generation failed"),
        (502, "Story generation failed"),
    ],
)
def test_story_generate_mocked_error_shows_alert(
    page: Page,
    e2e_session,
    status_code: int,
    expected_alert_text: str,
) -> None:
    group_name = _group_name(e2e_session, f"gen-err-{status_code}")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=2,
        title=f"Error seed {status_code}",
        final_text="<p>Error seed body.</p>",
    )

    def handle_error(route):
        route.fulfill(status=status_code, content_type="text/plain", body="upstream failure")

    page.route("**/story/generate/stream", handle_error)

    page.goto(f"/story?group_id={group_id}")
    with page.expect_response(
        lambda r: r.request.method == "POST" and "/story/generate/stream" in r.url,
        timeout=8_000,
    ):
        page.get_by_role("button", name="Generate story").click()

    expect(page.locator("[data-story-trace] .story-trace-line.is-error")).to_contain_text(
        f"{expected_alert_text} (HTTP {status_code})"
    )
    expect(page.get_by_role("button", name="Generate story")).to_be_enabled()


# ---------------------------------------------------------------------------
# Save and view a saved story
# ---------------------------------------------------------------------------


def test_save_story_and_view_saved_story_page(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "save-story")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    entry_id = _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=5,
        title="Save story seed entry",
        final_text="<p>Save story seed body for citation.</p>",
    )

    story_title = f"{e2e_session.run_id} Saved Story Snapshot"
    narrative_html = "<p>This narrative was generated in a browser test.</p>"

    # Seed the story directly in the DB to avoid complex form submission with CSRF.
    story_id = _seed_story(
        e2e_session.db_path,
        group_id=group_id,
        title=story_title,
        narrative_html=narrative_html,
        entry_id=entry_id,
    )

    # Navigate to the saved story page
    page.goto(f"/story/{story_id}")

    # Saved story page should show the title and narrative
    expect(page.get_by_role("heading", name=story_title)).to_be_visible()
    expect(page.locator("[data-story-result]")).to_contain_text(
        "narrative was generated in a browser test."
    )
    # Should display the "Saved snapshot" badge
    expect(page.get_by_text("Saved snapshot")).to_be_visible()

    # Citations section should be rendered
    expect(page.locator("#story-citations")).to_be_visible()
    expect(page.locator("#citation-1")).to_contain_text("Save story seed entry")

    # Revisiting the same URL should show the same persisted story
    page.goto(f"/story/{story_id}")
    expect(page.get_by_role("heading", name=story_title)).to_be_visible()


def test_saved_story_with_presentation_toggle_shows_iframe(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "presentation-toggle")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    entry_id = _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=5,
        title="Presentation seed entry",
        final_text="<p>Presentation seed body for citation.</p>",
    )

    story_title = f"{e2e_session.run_id} Saved Story With Presentation"
    story_id = _seed_story(
        e2e_session.db_path,
        group_id=group_id,
        title=story_title,
        narrative_html="<p>This narrative also has a saved presentation.</p>",
        entry_id=entry_id,
    )
    _seed_story_artifact(
        e2e_session.db_path,
        story_id=story_id,
        compiled_html='<div class="marpit"><section><h1>Saved deck</h1></section></div>',
        compiled_css="section { color: #123456; }",
    )

    page.goto(f"/story/{story_id}")

    expect(page.get_by_role("link", name="Narrative", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Presentation", exact=True)).to_be_visible()

    page.get_by_role("link", name="Presentation", exact=True).click()

    presentation_frame = page.locator(".story-presentation-frame")
    expect(presentation_frame).to_be_visible()
    expect(presentation_frame).to_have_attribute(
        "src", f"/story/{story_id}/deck"
    )


def test_saved_story_presentation_navigation_buttons_change_slides(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "presentation-navigation")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    entry_id = _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=5,
        title="Presentation navigation seed entry",
        final_text="<p>Presentation navigation seed body for citation.</p>",
    )

    story_title = f"{e2e_session.run_id} Saved Story Presentation Navigation"
    story_id = _seed_story(
        e2e_session.db_path,
        group_id=group_id,
        title=story_title,
        narrative_html="<p>This narrative also has a multi-slide presentation.</p>",
        entry_id=entry_id,
    )
    _seed_story_artifact(
        e2e_session.db_path,
        story_id=story_id,
        compiled_html=(
            '<div class="marpit">'
            '<section><h1>Deck slide one</h1><p>First slide body.</p></section>'
            '<section><h1>Deck slide two</h1><p>Second slide body.</p></section>'
            '</div>'
        ),
        compiled_css="section { color: #123456; }",
    )

    page.goto(f"/story/{story_id}/deck")

    counter = page.locator("[data-deck-counter]")
    expect(counter).to_have_text("1 / 2")
    expect(page.locator(".deck-slide-scaler section.is-active")).to_contain_text(
        "Deck slide one"
    )

    page.get_by_role("button", name="Next slide").click()
    expect(counter).to_have_text("2 / 2")
    expect(page.locator(".deck-slide-scaler section.is-active")).to_contain_text(
        "Deck slide two"
    )

    page.get_by_role("button", name="Previous slide").click()
    expect(counter).to_have_text("1 / 2")
    expect(page.locator(".deck-slide-scaler section.is-active")).to_contain_text(
        "Deck slide one"
    )


def test_saved_story_presentation_download_keeps_navigation_controls(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "presentation-download-navigation")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    entry_id = _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=5,
        title="Presentation download seed entry",
        final_text="<p>Presentation download seed body for citation.</p>",
    )

    story_title = f"{e2e_session.run_id} Downloaded Story Presentation"
    story_id = _seed_story(
        e2e_session.db_path,
        group_id=group_id,
        title=story_title,
        narrative_html="<p>This narrative also has a downloadable multi-slide presentation.</p>",
        entry_id=entry_id,
    )
    _seed_story_artifact(
        e2e_session.db_path,
        story_id=story_id,
        compiled_html=(
            '<div class="marpit">'
            '<section><h1>Download slide one</h1><p>First download slide.</p></section>'
            '<section><h1>Download slide two</h1><p>Second download slide.</p></section>'
            '</div>'
        ),
        compiled_css="section { color: #123456; }",
    )

    page.goto(f"/story/{story_id}?view=presentation")

    with page.expect_download() as download_info:
        page.get_by_role("button", name="Download HTML").click()
    download = download_info.value
    download_path = download.path()
    assert download_path is not None

    saved_download_path = Path(download_path).with_name(
        download.suggested_filename or "presentation-download.html"
    )
    download.save_as(str(saved_download_path))
    downloaded_html = saved_download_path.read_text(encoding="utf-8")
    assert ".et-slide--thank_you .et-pull-quote" in downloaded_html

    page.goto(saved_download_path.as_uri())

    counter = page.locator("#ct")
    expect(counter).to_have_text("1 / 2")
    expect(page.locator(".dk-sl section.is-active")).to_contain_text(
        "Download slide one"
    )

    page.get_by_role("button", name="Next").click()
    expect(counter).to_have_text("2 / 2")
    expect(page.locator(".dk-sl section.is-active")).to_contain_text(
        "Download slide two"
    )

    page.get_by_role("button", name="Previous").click()
    expect(counter).to_have_text("1 / 2")
    expect(page.locator(".dk-sl section.is-active")).to_contain_text(
        "Download slide one"
    )


# ---------------------------------------------------------------------------
# Year / month scoping on the story page
# ---------------------------------------------------------------------------


def test_story_page_year_month_scope_filters_count_correctly(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "year-month-scope")
    group_id = _ensure_group(e2e_session.db_path, group_name)

    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2025,
        month=11,
        day=10,
        title="Nov 2025 entry",
        final_text="<p>November event.</p>",
    )
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=1,
        day=15,
        title="Jan 2026 entry",
        final_text="<p>January event.</p>",
    )

    # Scope to November 2025 — should see 1 entry
    page.goto(f"/story?group_id={group_id}&year=2025&month=11")
    expect(page.locator(".story-hero")).to_contain_text("1 scoped entr")

    # Scope to 2026 full year — should see 1 entry
    page.goto(f"/story?group_id={group_id}&year=2026")
    expect(page.locator(".story-hero")).to_contain_text("1 scoped entr")

    # No date filter — should see 2 entries
    page.goto(f"/story?group_id={group_id}")
    expect(page.locator(".story-hero")).to_contain_text("2 scoped entr")


_CINEMATIC_NARRATIVE_HTML = (
    '<section class="story-section mb-4"><h2 class="h5 mb-2">Opening act</h2>'
    "<p>First chapter body.</p>"
    '<p class="small text-body-secondary mb-0">Sources '
    '<a href="#citation-1" class="story-inline-citation">[1]</a></p></section>'
    '<section class="story-section mb-4"><h2 class="h5 mb-2">Second act</h2>'
    "<p>Second chapter body with &lt;script&gt;alert(1)&lt;/script&gt; text.</p></section>"
)


def _seed_cinematic_story(e2e_session, suffix: str) -> tuple[int, str]:
    group_id = _ensure_group(e2e_session.db_path, _group_name(e2e_session, suffix))
    entry_id = _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=4,
        day=12,
        title="Cinematic cited entry",
        final_text="<p>Cinematic cited entry body.</p>",
    )
    story_title = f"{e2e_session.run_id} Cinematic Story {suffix}"
    story_id = _seed_story(
        e2e_session.db_path,
        group_id=group_id,
        title=story_title,
        narrative_html=_CINEMATIC_NARRATIVE_HTML,
        entry_id=entry_id,
    )
    return story_id, story_title


def test_saved_story_present_button_opens_cinematic_presentation(
    page: Page,
    e2e_session,
) -> None:
    story_id, story_title = _seed_cinematic_story(e2e_session, "present-button")

    page.goto(f"/story/{story_id}")
    present_link = page.locator("[data-story-present-link]")
    expect(present_link).to_be_visible()
    expect(present_link).to_have_attribute("href", f"/story/{story_id}/presentation")
    present_link.click()

    expect(page).to_have_url(re.compile(rf"/story/{story_id}/presentation#title$"))
    active = page.locator("[data-cine-slide].is-active")
    expect(active.locator(".cine-title")).to_have_text(story_title)
    expect(page.locator("[data-cine-counter]")).to_have_text("1 / 4")


def test_cinematic_presentation_keyboard_navigation_and_hash(
    page: Page,
    e2e_session,
) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on(
        "console",
        lambda message: errors.append(message.text)
        if message.type == "error"
        and not message.text.startswith("Failed to load resource")
        else None,
    )
    story_id, _ = _seed_cinematic_story(e2e_session, "keyboard")

    page.goto(f"/story/{story_id}/presentation")
    counter = page.locator("[data-cine-counter]")
    active = page.locator("[data-cine-slide].is-active")
    expect(counter).to_have_text("1 / 4")

    page.keyboard.press("ArrowRight")
    expect(counter).to_have_text("2 / 4")
    expect(page).to_have_url(re.compile(r"#chapter-1$"))
    expect(active.locator(".cine-heading")).to_have_text("Opening act")
    cited_link = active.locator(".cine-cite-card")
    expect(cited_link).to_contain_text("Cinematic cited entry")
    expect(cited_link).to_have_attribute("href", re.compile(r"^/entries/\d+/view$"))

    page.keyboard.press("PageDown")
    expect(counter).to_have_text("3 / 4")
    expect(active.locator(".cine-heading")).to_have_text("Second act")
    # AI text is rendered as escaped text, never as markup.
    expect(active.locator(".cine-body")).to_contain_text("<script>alert(1)</script>")

    page.keyboard.press("Space")
    expect(counter).to_have_text("4 / 4")
    expect(page).to_have_url(re.compile(r"#end$"))

    page.keyboard.press("ArrowLeft")
    expect(counter).to_have_text("3 / 4")
    page.keyboard.press("Home")
    expect(counter).to_have_text("1 / 4")
    page.keyboard.press("End")
    expect(counter).to_have_text("4 / 4")
    page.keyboard.press("PageUp")
    expect(counter).to_have_text("3 / 4")
    expect(page).to_have_url(re.compile(r"#chapter-2$"))

    page.reload()
    expect(counter).to_have_text("3 / 4")
    expect(active.locator(".cine-heading")).to_have_text("Second act")

    page.locator("[data-cine-dot='1']").click()
    expect(counter).to_have_text("2 / 4")

    page.keyboard.press("Escape")
    expect(page).to_have_url(re.compile(rf"/story/{story_id}$"))
    assert errors == []