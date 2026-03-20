from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3

from playwright.sync_api import Page, expect


def _extract_entry_id(current_url: str) -> int:
    match = re.search(r"/entries/(\d+)/view$", current_url)
    assert match is not None
    return int(match.group(1))


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_group_id(db_path: Path, group_name: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT id FROM timeline_groups WHERE name = ?",
            (group_name,),
        ).fetchone()
        if row is not None:
            return int(row[0])

        cursor = connection.execute(
            "INSERT INTO timeline_groups(name, web_search_query, is_default) VALUES (?, NULL, 0)",
            (group_name,),
        )
        connection.commit()
        lastrowid = cursor.lastrowid
        assert lastrowid is not None
        return int(lastrowid)


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
    sort_key = (year * 10000) + (month * 100) + (day or 0)
    timestamp = _utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO entries (
                event_year,
                event_month,
                event_day,
                sort_key,
                group_id,
                title,
                source_url,
                generated_text,
                final_text,
                created_utc,
                updated_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (
                year,
                month,
                day,
                sort_key,
                group_id,
                title,
                final_text,
                timestamp,
                timestamp,
            ),
        )
        connection.commit()
        lastrowid = cursor.lastrowid
        assert lastrowid is not None
        return int(lastrowid)


def test_create_dedicated_group_and_validate_admin_rules(
    page: Page,
    e2e_session,
    ensure_dedicated_group,
) -> None:
    group_id = ensure_dedicated_group()
    assert group_id > 0

    page.goto("/admin/groups")
    page.get_by_label("New group name").fill(e2e_session.group_name)
    page.get_by_role("button", name="Add Group").click()
    expect(page.get_by_text("A group with that name already exists.")).to_be_visible()

    page.get_by_label("New group name").fill("")
    page.get_by_role("button", name="Add Group").click()
    expect(page.get_by_text("Group name is required.")).to_be_visible()


def test_entry_form_validation_rejects_missing_fields_invalid_urls_and_partial_links(
    page: Page,
) -> None:
    page.goto("/entries/new")
    page.get_by_label("Timeline Group").select_option("")
    page.get_by_label("Year").fill("")
    page.get_by_label("Month").fill("")
    page.get_by_label("Title").fill("")
    page.get_by_label("Source URL").fill("not-a-url")
    page.locator("#link_note_0").fill("Missing URL")
    page.get_by_role("button", name="Add Link").click()
    page.locator("#link_url_1").fill("ftp://example.com/reference")
    page.locator("#link_note_1").fill("Invalid protocol")
    page.get_by_role("button", name="Add Link").click()
    page.locator("#link_url_2").fill("https://example.com/without-note")
    page.get_by_role("button", name="Save Entry").click()

    expect(page).to_have_url(re.compile(r".*/entries/new$"))
    expect(page.locator("#group_id")).to_have_class(re.compile(r".*\bis-invalid\b.*"))
    expect(page.locator("#event_year")).to_have_class(re.compile(r".*\bis-invalid\b.*"))
    expect(page.locator("#event_month")).to_have_class(
        re.compile(r".*\bis-invalid\b.*")
    )
    expect(page.locator("#title")).to_have_class(re.compile(r".*\bis-invalid\b.*"))
    expect(page.locator("#source_url")).to_have_class(re.compile(r".*\bis-invalid\b.*"))
    expect(page.locator("#final_text")).to_have_class(re.compile(r".*\bis-invalid\b.*"))
    expect(page.locator("#link_url_0")).to_have_class(re.compile(r".*\bis-invalid\b.*"))
    expect(page.locator("#link_url_1")).to_have_class(re.compile(r".*\bis-invalid\b.*"))
    expect(page.locator("#link_note_2")).to_have_class(
        re.compile(r".*\bis-invalid\b.*")
    )

    expect(page.get_by_text("This field is required.")).to_have_count(3)
    expect(page.get_by_text("Title is required.")).to_be_visible()
    expect(page.get_by_text("Event summary is required.")).to_be_visible()
    expect(page.locator("#source_url + .invalid-feedback")).to_have_text(
        "Provide a valid http or https URL."
    )
    expect(page.locator("#link_url_0 + .invalid-feedback")).to_have_text(
        "Provide a valid http or https URL."
    )
    expect(page.locator("#link_url_1 + .invalid-feedback")).to_have_text(
        "Provide a valid http or https URL."
    )
    expect(page.locator("#link_note_2 + .invalid-feedback")).to_have_text(
        "Add a brief note for this URL."
    )

    assert page.locator("[data-link-row]").count() == 3
    expect(page.locator("#source_url")).to_have_value("not-a-url")
    expect(page.locator("#link_note_0")).to_have_value("Missing URL")
    expect(page.locator("#link_url_1")).to_have_value("ftp://example.com/reference")
    expect(page.locator("#link_note_1")).to_have_value("Invalid protocol")
    expect(page.locator("#link_url_2")).to_have_value(
        "https://example.com/without-note"
    )


def test_create_edit_filter_search_and_export_entry(
    page: Page,
    e2e_session,
    ensure_dedicated_group,
) -> None:
    group_id = ensure_dedicated_group()
    created_title = f"{e2e_session.run_id} browser draft"
    updated_title = f"{e2e_session.run_id} browser verification"
    source_url = "https://example.com/releases/browser-draft"
    replacement_url = "https://example.com/releases/browser-verification"
    removed_url = "https://example.com/releases/retrospective"
    query_text = "verified search flow"

    page.goto("/entries/new")
    page.get_by_label("Timeline Group").select_option(str(group_id))
    page.get_by_label("Year").fill("2026")
    page.get_by_label("Month").fill("3")
    page.get_by_label("Day").fill("18")
    page.get_by_label("Title").fill(created_title)
    page.get_by_label("Source URL").fill(source_url)
    page.locator("#link_url_0").fill("https://example.com/releases/checklist")
    page.locator("#link_note_0").fill("Release checklist")
    page.get_by_role("button", name="Add Link").click()
    page.locator("#link_url_1").fill(removed_url)
    page.locator("#link_note_1").fill("Retrospective notes")
    page.get_by_label("Event Summary").fill(
        "<p>Playwright captured the first browser workflow for EventTracker.</p>"
    )
    page.get_by_label("Tags").fill("playwright, browser, smoke")
    page.get_by_role("button", name="Save Entry").click()

    expect(page).to_have_url(re.compile(r".*/entries/\d+/view$"))
    entry_id = _extract_entry_id(page.url)
    expect(page.get_by_role("heading", name=created_title)).to_be_visible()
    expect(
        page.get_by_text("Playwright captured the first browser workflow")
    ).to_be_visible()
    expect(page.get_by_text("Release checklist")).to_be_visible()
    expect(page.get_by_text("Retrospective notes")).to_be_visible()
    expect(page.get_by_text("playwright", exact=True)).to_be_visible()

    page.get_by_role("link", name="Edit").click()
    expect(page).to_have_url(re.compile(rf".*/entries/{entry_id}$"))
    page.get_by_label("Day").fill("19")
    page.get_by_label("Title").fill(updated_title)
    page.get_by_label("Source URL").fill(replacement_url)
    page.locator("#link_url_0").fill(replacement_url)
    page.locator("#link_note_0").fill("Verification notes")
    page.get_by_role("button", name="Remove link row").nth(1).click()
    page.get_by_label("Event Summary").fill(
        f"<p>Updated the entry and verified search flow through the UI.</p><p>{query_text}</p>"
    )
    page.get_by_label("Tags").fill("playwright, verification")
    page.get_by_role("button", name="Save Entry").click()

    expect(page).to_have_url(re.compile(rf".*/entries/{entry_id}/view$"))
    expect(page.get_by_role("heading", name=updated_title)).to_be_visible()
    expect(page.get_by_text("March 19, 2026")).to_be_visible()
    expect(page.get_by_text("Verification notes")).to_be_visible()
    expect(page.get_by_text(removed_url)).not_to_be_visible()
    expect(page.get_by_text("Retrospective notes")).not_to_be_visible()

    page.goto(f"/?group_id={group_id}")
    expect(
        page.get_by_role("heading", name=f"{e2e_session.group_name} Timeline")
    ).to_be_visible()
    page.get_by_role("searchbox").fill(query_text)
    page.get_by_role("button", name="Filter").click()
    expect(page).to_have_url(re.compile(rf".*/\?group_id={group_id}&q=.*"))
    expect(page.get_by_role("heading", name="Filtered Timeline")).to_be_visible()
    expect(page.get_by_text(updated_title)).to_be_visible()
    expect(page.get_by_role("heading", name="Search Results")).not_to_be_visible()

    page.get_by_role("searchbox").fill(query_text)
    page.get_by_role("button", name="Search").click()
    expect(page).to_have_url(re.compile(r".*/search\?.*"))
    expect(page.get_by_role("heading", name="Search Results")).to_be_visible()
    expect(page.get_by_text(updated_title)).to_be_visible()
    expect(page.locator("mark").first).to_contain_text("verified")

    with page.expect_download() as download_info:
        page.get_by_role("link", name="Export").click()
    download = download_info.value
    download_path = download.path()
    assert download_path is not None

    payload = json.loads(Path(download_path).read_text(encoding="utf-8"))
    assert payload["count"] >= 1

    matching_entry = next(
        entry for entry in payload["entries"] if entry["title"] == updated_title
    )
    assert matching_entry["group_id"] == group_id
    assert matching_entry["tags"] == ["playwright", "verification"]
    assert matching_entry["source_url"] == replacement_url
    assert matching_entry["links"] == [
        {
            "id": matching_entry["links"][0]["id"],
            "url": replacement_url,
            "note": "Verification notes",
            "created_utc": matching_entry["links"][0]["created_utc"],
        }
    ]


def test_timeline_views_and_drill_down_cover_details_summaries_months_and_years(
    page: Page,
    e2e_session,
) -> None:
    group_name = f"{e2e_session.group_name} Timeline Drilldown"
    group_id = _ensure_group_id(e2e_session.db_path, group_name)

    march_primary_title = f"{e2e_session.run_id} timeline march highlight"
    march_secondary_title = f"{e2e_session.run_id} timeline march follow-up"
    april_title = f"{e2e_session.run_id} timeline april checkpoint"
    prior_year_title = f"{e2e_session.run_id} timeline archive"

    march_primary_id = _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=18,
        title=march_primary_title,
        final_text="Primary March event used for timeline drill-down assertions.",
    )
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=7,
        title=march_secondary_title,
        final_text="Second March event to keep the summary month grouped.",
    )
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=4,
        day=2,
        title=april_title,
        final_text="April event used to prove the month buckets split within a year.",
    )
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2025,
        month=1,
        day=5,
        title=prior_year_title,
        final_text="Prior year event used to prove the year bucket drill-down.",
    )

    page.goto(f"/?group_id={group_id}")

    current_view = page.locator("[data-current-view-label]")
    current_context = page.locator("[data-current-view-context]")
    detail_panel = page.locator("#timeline-details-view")
    summary_panel = page.locator("#visualization-content")
    history = page.locator("[data-view-history]")
    playback_controls = page.locator("[data-playback-panel]")
    playback_status = page.locator("[data-playback-status]")
    play_button = page.locator("[data-playback-action='play']")
    pause_button = page.locator("[data-playback-action='pause']")
    restart_button = page.locator("[data-playback-action='restart']")
    details_button = page.locator("[data-zoom-target='details']")
    summaries_button = page.locator("[data-zoom-target='events']")
    months_button = page.locator("[data-zoom-target='months']")
    years_button = page.locator("[data-zoom-target='years']")

    expect(page.get_by_role("heading", name=f"{group_name} Timeline")).to_be_visible()
    expect(current_view).to_have_text("Details")
    expect(current_context).to_have_text("4 loaded entries")
    expect(detail_panel.get_by_text(march_primary_title)).to_be_visible()
    expect(detail_panel.get_by_text(march_secondary_title)).to_be_visible()
    expect(detail_panel.get_by_text(april_title)).to_be_visible()
    expect(detail_panel.get_by_text(prior_year_title)).to_be_visible()
    expect(detail_panel.get_by_role("link", name="View").first).to_be_visible()
    expect(detail_panel.get_by_role("link", name="Edit").first).to_be_visible()
    expect(playback_controls).to_have_attribute("hidden", "")

    summaries_button.click()
    expect(current_view).to_have_text("Summaries")
    expect(current_context).to_have_text("All summaries")
    expect(summary_panel.locator(".visualization-group")).to_have_count(3)
    expect(summary_panel.get_by_text("March 2026")).to_be_visible()
    expect(summary_panel.get_by_text(march_primary_title)).to_be_visible()
    expect(summary_panel.get_by_text(march_secondary_title)).to_be_visible()
    expect(playback_controls).not_to_have_attribute("hidden", "")
    expect(playback_status).to_have_attribute("hidden", "")
    expect(play_button).to_have_text("")
    expect(play_button).to_have_attribute("aria-label", "Play summaries replay")
    expect(pause_button).to_be_disabled()

    play_button.click()
    expect(play_button).to_be_disabled()
    expect(pause_button).to_be_enabled()
    expect(playback_status).to_have_text(
        "Playing January 2025 oldest first.", timeout=5000
    )

    pause_button.click()
    expect(play_button).to_be_enabled()
    expect(pause_button).to_be_disabled()
    expect(play_button).to_have_text("")
    expect(play_button).to_have_attribute("aria-label", "Resume summaries replay")
    expect(playback_status).to_contain_text("Paused during")

    restart_button.click()
    expect(play_button).to_be_disabled()
    expect(pause_button).to_be_enabled()
    expect(playback_status).to_have_text(
        "Playing January 2025 oldest first.", timeout=5000
    )

    months_button.click()
    expect(current_view).to_have_text("Months")
    expect(current_context).to_have_text("All months")
    expect(playback_controls).to_have_attribute("hidden", "")
    expect(
        summary_panel.locator(
            ".visualization-summary-card", has_text="March 2026"
        ).locator(".visualization-summary-count")
    ).to_have_text("2")
    expect(
        summary_panel.locator(
            ".visualization-summary-card", has_text="April 2026"
        ).locator(".visualization-summary-count")
    ).to_have_text("1")
    expect(
        summary_panel.locator(
            ".visualization-summary-card", has_text="January 2025"
        ).locator(".visualization-summary-count")
    ).to_have_text("1")

    years_button.click()
    expect(current_view).to_have_text("Years")
    expect(current_context).to_have_text("All years")
    expect(
        summary_panel.locator(".visualization-summary-card", has_text="2026").locator(
            ".visualization-summary-count"
        )
    ).to_have_text("3")
    expect(
        summary_panel.locator(".visualization-summary-card", has_text="2025").locator(
            ".visualization-summary-count"
        )
    ).to_have_text("1")

    summary_panel.get_by_role("button", name="Open 2026").click()
    expect(current_view).to_have_text("Months")
    expect(current_context).to_have_text("Months in 2026")
    expect(history).to_be_visible()
    expect(history).to_contain_text("2026")
    expect(
        summary_panel.locator(
            ".visualization-summary-card", has_text="March 2026"
        ).locator(".visualization-summary-count")
    ).to_have_text("2")
    expect(
        summary_panel.locator(
            ".visualization-summary-card", has_text="April 2026"
        ).locator(".visualization-summary-count")
    ).to_have_text("1")
    expect(summary_panel.get_by_text("January 2025")).not_to_be_visible()

    summary_panel.get_by_role("button", name="Open March 2026").click()
    expect(current_view).to_have_text("Summaries")
    expect(current_context).to_have_text("Summaries in March 2026")
    expect(history).to_contain_text("March 2026")
    expect(summary_panel.locator(".visualization-group")).to_have_count(1)
    expect(summary_panel.get_by_text(march_primary_title)).to_be_visible()
    expect(summary_panel.get_by_text(march_secondary_title)).to_be_visible()
    expect(summary_panel.get_by_text(april_title)).not_to_be_visible()
    expect(summary_panel.get_by_text(prior_year_title)).not_to_be_visible()

    history.get_by_role("button", name="Back").click()
    expect(current_view).to_have_text("Months")
    expect(current_context).to_have_text("Months in 2026")

    history.get_by_role("button", name="Back").click()
    expect(current_view).to_have_text("Years")
    expect(current_context).to_have_text("All years")

    details_button.click()
    expect(current_view).to_have_text("Details")
    expect(current_context).to_have_text("4 loaded entries")
    expect(history).to_be_hidden()

    detail_panel.locator("[data-entry-card]", has_text=march_primary_title).get_by_role(
        "link", name="View"
    ).click()
    expect(page).to_have_url(re.compile(rf".*/entries/{march_primary_id}/view$"))
    expect(page.get_by_role("heading", name=march_primary_title)).to_be_visible()

    page.go_back()
    expect(page).to_have_url(re.compile(rf".*/\?group_id={group_id}$"))
    expect(current_view).to_have_text("Details")

    detail_panel.locator("[data-entry-card]", has_text=march_primary_title).get_by_role(
        "link", name="Edit"
    ).click()
    expect(page).to_have_url(re.compile(rf".*/entries/{march_primary_id}$"))
    expect(page.get_by_role("heading", name="Edit Entry")).to_be_visible()
    expect(page.get_by_label("Title")).to_have_value(march_primary_title)
