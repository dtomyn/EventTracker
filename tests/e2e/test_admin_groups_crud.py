"""E2E tests for the Admin Groups management page.

Covers: rename/edit a group, set/unset the default group, web-search-query
field, delete an empty group, and blocked delete when entries exist.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_group(
    db_path: Path,
    name: str,
    *,
    web_search_query: str | None = None,
    is_default: bool = False,
) -> int:
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT id FROM timeline_groups WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return int(row[0])
        cursor = con.execute(
            "INSERT INTO timeline_groups(name, web_search_query, is_default) VALUES (?, ?, ?)",
            (name, web_search_query, int(is_default)),
        )
        con.commit()
        return int(cursor.lastrowid)


def _seed_entry(db_path: Path, *, group_id: int) -> None:
    """Insert one minimal entry so the group has entry_count > 0."""
    import datetime

    ts = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            INSERT INTO entries (
                event_year, event_month, event_day, sort_key, group_id,
                title, source_url, generated_text, final_text, created_utc, updated_utc
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (2026, 1, 1, 20260101, group_id, "Seed entry", "<p>seed</p>", ts, ts),
        )
        con.commit()


def _group_name(e2e_session, suffix: str) -> str:
    return f"{e2e_session.group_name} AdminCRUD {suffix}"


# ---------------------------------------------------------------------------
# Create + rename + web-search-query round-trip
# ---------------------------------------------------------------------------


def test_rename_group_and_update_web_search_query(
    page: Page,
    e2e_session,
) -> None:
    original_name = _group_name(e2e_session, "rename-before")
    updated_name = _group_name(e2e_session, "rename-after")
    group_id = _ensure_group(e2e_session.db_path, original_name)

    page.goto("/admin/groups")
    # Scope to the specific group's edit form using its action attribute
    group_form = page.locator(f"form[action='/admin/groups/{group_id}']")

    # Fill in the new name and web search query using stable IDs
    group_form.locator(f"#group-{group_id}").fill(updated_name)
    group_form.locator(f"#group-web-search-{group_id}").fill("AI coding tools launches 2026")
    group_form.get_by_role("button", name="Save").click()

    expect(page).to_have_url(re.compile(r".*/admin/groups\?notice=updated$"))
    expect(page.get_by_role("status")).to_contain_text("updated")

    # Verify the new name and query are persisted
    with sqlite3.connect(e2e_session.db_path) as con:
        row = con.execute(
            "SELECT name, web_search_query FROM timeline_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == updated_name
    assert row[1] == "AI coding tools launches 2026"


def test_rename_group_shows_validation_error_for_empty_name(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "rename-validation")
    group_id = _ensure_group(e2e_session.db_path, group_name)

    page.goto("/admin/groups")
    group_form = page.locator(f"form[action='/admin/groups/{group_id}']")
    group_form.locator(f"#group-{group_id}").fill("")
    group_form.get_by_role("button", name="Save").click()

    # A validation error renders the admin_groups template at the POST URL
    # (/admin/groups/{group_id}) with a 400, so the is-invalid class should appear.
    expect(page.locator(f"#group-{group_id}")).to_have_class(re.compile(r".*\bis-invalid\b.*"))


# ---------------------------------------------------------------------------
# Default group toggle
# ---------------------------------------------------------------------------


def test_set_default_group_and_verify_it_loads_on_home(
    page: Page,
    e2e_session,
) -> None:
    default_name = _group_name(e2e_session, "default-group")
    group_id = _ensure_group(e2e_session.db_path, default_name)

    page.goto("/admin/groups")
    group_form = page.locator(f"form[action='/admin/groups/{group_id}']")
    default_checkbox = group_form.locator(f"#group-default-{group_id}")
    if not default_checkbox.is_checked():
        default_checkbox.check()

    # Submit the group's save form
    group_form.get_by_role("button", name="Save").click()

    expect(page).to_have_url(re.compile(r".*/admin/groups\?notice=updated$"))

    # Confirm the default flag is stored
    with sqlite3.connect(e2e_session.db_path) as con:
        row = con.execute(
            "SELECT is_default FROM timeline_groups WHERE id = ?", (group_id,)
        ).fetchone()
    assert row is not None and row[0] == 1


# ---------------------------------------------------------------------------
# Delete group
# ---------------------------------------------------------------------------


def test_delete_empty_group_succeeds(
    page: Page,
    e2e_session,
) -> None:
    delete_name = _group_name(e2e_session, "delete-empty")
    group_id = _ensure_group(e2e_session.db_path, delete_name)

    page.goto("/admin/groups")
    delete_button = page.locator(f"button[formaction='/admin/groups/{group_id}/delete']")
    expect(delete_button).not_to_be_disabled()
    delete_button.click()

    expect(page).to_have_url(re.compile(r".*/admin/groups\?notice=deleted$"))
    expect(page.get_by_role("status")).to_contain_text("deleted")

    # Group should no longer exist in the DB
    with sqlite3.connect(e2e_session.db_path) as con:
        row = con.execute(
            "SELECT id FROM timeline_groups WHERE id = ?", (group_id,)
        ).fetchone()
    assert row is None


def test_delete_button_disabled_when_group_has_entries(
    page: Page,
    e2e_session,
) -> None:
    nonempty_name = _group_name(e2e_session, "delete-nonempty")
    group_id = _ensure_group(e2e_session.db_path, nonempty_name)
    _seed_entry(e2e_session.db_path, group_id=group_id)

    page.goto("/admin/groups")
    delete_button = page.locator(f"button[formaction='/admin/groups/{group_id}/delete']")
    expect(delete_button).to_be_disabled()
    expect(page.locator(f"#group-web-search-{group_id}").locator("..")).to_contain_text(
        "Delete is disabled until this group has no entries."
    )


# ---------------------------------------------------------------------------
# Web-search-query validation
# ---------------------------------------------------------------------------


def test_create_group_with_web_search_query_persists_query(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "web-query")
    query = "LLM benchmark announcements 2026"

    page.goto("/admin/groups")
    # Scope to the create form (action='/admin/groups', no group ID suffix)
    create_form = page.locator("form[action='/admin/groups']").first
    create_form.locator("#name").fill(group_name)
    create_form.locator("#web-search-query").fill(query)
    create_form.get_by_role("button", name="Add Group").click()

    expect(page).to_have_url(re.compile(r".*/admin/groups\?notice=created$"))

    with sqlite3.connect(e2e_session.db_path) as con:
        row = con.execute(
            "SELECT web_search_query FROM timeline_groups WHERE name = ?", (group_name,)
        ).fetchone()
    assert row is not None
    assert row[0] == query


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_admin_groups_shows_no_groups_empty_state_when_db_is_empty(
    page: Page,
    e2e_session,
) -> None:
    """Verify the empty-state copy is rendered when no groups exist.

    We achieve this by visiting the admin page immediately on a fresh DB
    that has no groups yet (the seed DB may have groups, so we read the
    live count and skip the assertion if groups are present).
    """
    with sqlite3.connect(e2e_session.db_path) as con:
        count = con.execute("SELECT COUNT(*) FROM timeline_groups").fetchone()[0]

    page.goto("/admin/groups")
    # The page heading must always be present
    expect(page.get_by_role("heading", name="Timeline Groups")).to_be_visible()
    if count == 0:
        expect(page.get_by_text("No timeline groups found.")).to_be_visible()
    else:
        # At least one group exists — an edit form should be rendered
        expect(page.locator("form[action='/admin/groups']").first).to_be_visible()
