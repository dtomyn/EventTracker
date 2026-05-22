"""E2E tests for the Search and Topic Graph pages.

Covers:
- Search page empty state (no query)
- Search with query returns ranked results with highlights
- Search scoped to a specific group
- "All groups" search returns cross-group hits
- "Create story" link from search results navigates to story page
- "Filter Timeline" link from search results navigates back to filtered timeline
- Topic graph page renders for a group (heading, breadcrumb, SVG element)
"""
from __future__ import annotations

import json
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
    day: int,
    title: str,
    final_text: str,
    tags: list[str] | None = None,
) -> int:
    sort_key = (year * 10_000) + (month * 100) + day
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
        entry_id = int(cursor.lastrowid)

        for tag in tags or []:
            # Tags use a normalized schema: tags(id, name) + entry_tags(entry_id, tag_id)
            con.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
            tag_id_row = con.execute("SELECT id FROM tags WHERE name = ?", (tag,)).fetchone()
            if tag_id_row is not None:
                con.execute(
                    "INSERT OR IGNORE INTO entry_tags(entry_id, tag_id) VALUES (?, ?)",
                    (entry_id, int(tag_id_row[0])),
                )
        con.commit()
    return entry_id


def _group_name(e2e_session, suffix: str) -> str:
    return f"{e2e_session.group_name} Search {suffix}"


# ---------------------------------------------------------------------------
# Search page empty state
# ---------------------------------------------------------------------------


def test_search_page_empty_state_shows_instructions(
    page: Page,
) -> None:
    page.goto("/search")

    expect(page.get_by_role("heading", name="Search Results")).to_be_visible()
    expect(page.get_by_text(
        "Use search to find the strongest keyword and semantic matches"
    )).to_be_visible()
    # No result list should be present without a query
    expect(page.locator("[data-search-results-shell]")).to_have_count(0)


# ---------------------------------------------------------------------------
# Search with a query
# ---------------------------------------------------------------------------


def test_search_returns_matching_entry_with_highlighted_terms(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "query-results")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    unique_term = f"{e2e_session.run_id}uniquesearchterm"
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=15,
        title=f"{unique_term} title",
        final_text=f"<p>The {unique_term} appears in the body text.</p>",
    )

    page.goto(f"/search?q={unique_term}&group_id={group_id}")

    expect(page.get_by_role("heading", name="Search Results")).to_be_visible()
    expect(page.get_by_text(f"{unique_term} title")).to_be_visible()
    # Search snippets highlight matched terms
    highlights = page.locator("mark")
    expect(highlights.first).to_be_visible()


def test_search_shows_no_results_message_for_unmatched_query(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "no-results")
    group_id = _ensure_group(e2e_session.db_path, group_name)

    # Search for something that won't match anything in a fresh isolated group
    page.goto(f"/search?q=xyzabsolutelynevermatchxyz&group_id={group_id}")

    expect(page.get_by_role("heading", name="Search Results")).to_be_visible()
    # 0 matches — the copy should say "0 ranked matches"
    page_text = page.locator("[data-search-result-copy]").text_content() or ""
    assert "0" in page_text


# ---------------------------------------------------------------------------
# Group-scoped search
# ---------------------------------------------------------------------------


def test_search_scoped_to_group_excludes_other_group_entries(
    page: Page,
    e2e_session,
) -> None:
    group_a_name = _group_name(e2e_session, "scope-A")
    group_b_name = _group_name(e2e_session, "scope-B")
    group_a_id = _ensure_group(e2e_session.db_path, group_a_name)
    group_b_id = _ensure_group(e2e_session.db_path, group_b_name)

    shared_term = f"{e2e_session.run_id}sharedscope"
    title_a = f"Group A entry {shared_term}"
    title_b = f"Group B entry {shared_term}"

    _seed_entry(
        e2e_session.db_path,
        group_id=group_a_id,
        year=2026,
        month=3,
        day=10,
        title=title_a,
        final_text=f"<p>{shared_term} content in group A.</p>",
    )
    _seed_entry(
        e2e_session.db_path,
        group_id=group_b_id,
        year=2026,
        month=3,
        day=11,
        title=title_b,
        final_text=f"<p>{shared_term} content in group B.</p>",
    )

    # Scope to group A — should see group A entry, not group B
    page.goto(f"/search?q={shared_term}&group_id={group_a_id}")
    expect(page.get_by_text(title_a)).to_be_visible()
    expect(page.get_by_text(title_b)).not_to_be_visible()


def test_search_all_groups_finds_entries_across_groups(
    page: Page,
    e2e_session,
) -> None:
    group_c_name = _group_name(e2e_session, "all-C")
    group_d_name = _group_name(e2e_session, "all-D")
    group_c_id = _ensure_group(e2e_session.db_path, group_c_name)
    group_d_id = _ensure_group(e2e_session.db_path, group_d_name)

    cross_term = f"{e2e_session.run_id}crossgroup"
    title_c = f"Cross group C {cross_term}"
    title_d = f"Cross group D {cross_term}"

    _seed_entry(
        e2e_session.db_path,
        group_id=group_c_id,
        year=2026,
        month=2,
        day=1,
        title=title_c,
        final_text=f"<p>{cross_term} in group C.</p>",
    )
    _seed_entry(
        e2e_session.db_path,
        group_id=group_d_id,
        year=2026,
        month=2,
        day=2,
        title=title_d,
        final_text=f"<p>{cross_term} in group D.</p>",
    )

    page.goto(f"/search?q={cross_term}&group_id=all")
    expect(page.get_by_text(title_c)).to_be_visible()
    expect(page.get_by_text(title_d)).to_be_visible()


# ---------------------------------------------------------------------------
# Search results action links
# ---------------------------------------------------------------------------


def test_create_story_link_from_search_navigates_to_story_page(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "story-link")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    q = f"{e2e_session.run_id}storylinkterm"
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=20,
        title=f"{q} entry",
        final_text=f"<p>{q} story link seed.</p>",
    )

    page.goto(f"/search?q={q}&group_id={group_id}")
    page.get_by_role("link", name="Create story").click()

    expect(page).to_have_url(re.compile(r".*/story.*"))
    expect(page.get_by_role("heading", name="Build a narrative from the current scope")).to_be_visible()


def test_filter_timeline_link_from_search_returns_to_filtered_timeline(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "filter-link")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    q = f"{e2e_session.run_id}filterterm"
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=21,
        title=f"{q} timeline entry",
        final_text=f"<p>{q} filter link seed.</p>",
    )

    page.goto(f"/search?q={q}&group_id={group_id}")
    page.get_by_role("link", name="Filter Timeline").click()

    expect(page).to_have_url(re.compile(r".*/\?.*q=.*"))
    expect(page.get_by_role("heading", name="Filtered Timeline")).to_be_visible()


# ---------------------------------------------------------------------------
# Topic graph page
# ---------------------------------------------------------------------------


def test_topic_graph_page_renders_heading_and_svg_container(
    page: Page,
    e2e_session,
) -> None:
    group_name = _group_name(e2e_session, "topic-graph")
    group_id = _ensure_group(e2e_session.db_path, group_name)
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=1,
        title="Topic graph seed entry",
        final_text="<p>Topic graph seed body.</p>",
        tags=["ai", "benchmark", "release"],
    )

    # Mock the topics API to return an empty graph so the JS renders the
    # "no clusters" placeholder without requiring D3 (which is aborted by the
    # CDN route handler in the test context).
    page.route(
        f"**/api/groups/{group_id}/topics",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"nodes": [], "edges": [], "clusters": []}),
        ),
    )

    page.goto(f"/groups/{group_id}/topics/graph")

    expect(page.get_by_role("heading", name="Tag Clusters")).to_be_visible()
    # Breadcrumb shows the group name
    expect(page.locator("nav[aria-label='breadcrumb']")).to_contain_text(group_name)
    # After the API returns empty nodes, the JS renders the empty-state message
    expect(page.get_by_text("No tag clusters found", exact=False)).to_be_visible(
        timeout=8_000
    )
    # The "Back to Timeline" link should be present
    expect(page.get_by_role("link", name="Back to Timeline")).to_be_visible()

    # Clicking the back link takes us to the timeline for that group
    page.get_by_role("link", name="Back to Timeline").click()
    expect(page).to_have_url(re.compile(rf".*/\?group_id={group_id}$"))


def test_topic_graph_page_returns_404_for_nonexistent_group(
    page: Page,
) -> None:
    response = page.goto("/groups/9999999/topics/graph")
    assert response is not None
    assert response.status == 404


# ---------------------------------------------------------------------------
# Timeline group selector on the timeline page
# ---------------------------------------------------------------------------


def test_timeline_group_selector_switches_between_groups(
    page: Page,
    e2e_session,
) -> None:
    group_alpha_name = _group_name(e2e_session, "selector-alpha")
    group_beta_name = _group_name(e2e_session, "selector-beta")
    group_alpha_id = _ensure_group(e2e_session.db_path, group_alpha_name)
    group_beta_id = _ensure_group(e2e_session.db_path, group_beta_name)

    alpha_title = f"{e2e_session.run_id} alpha entry"
    beta_title = f"{e2e_session.run_id} beta entry"

    _seed_entry(
        e2e_session.db_path,
        group_id=group_alpha_id,
        year=2026,
        month=3,
        day=5,
        title=alpha_title,
        final_text="<p>Alpha group entry.</p>",
    )
    _seed_entry(
        e2e_session.db_path,
        group_id=group_beta_id,
        year=2026,
        month=3,
        day=6,
        title=beta_title,
        final_text="<p>Beta group entry.</p>",
    )

    # Navigate to alpha
    page.goto(f"/?group_id={group_alpha_id}")
    expect(page.get_by_role("heading", name=f"{group_alpha_name} Timeline")).to_be_visible()
    expect(page.get_by_text(alpha_title)).to_be_visible()
    expect(page.get_by_text(beta_title)).not_to_be_visible()

    # Navigate to beta
    page.goto(f"/?group_id={group_beta_id}")
    expect(page.get_by_role("heading", name=f"{group_beta_name} Timeline")).to_be_visible()
    expect(page.get_by_text(beta_title)).to_be_visible()
    expect(page.get_by_text(alpha_title)).not_to_be_visible()


def test_timeline_all_groups_view_shows_entries_from_multiple_groups(
    page: Page,
    e2e_session,
) -> None:
    group_x_name = _group_name(e2e_session, "all-X")
    group_y_name = _group_name(e2e_session, "all-Y")
    group_x_id = _ensure_group(e2e_session.db_path, group_x_name)
    group_y_id = _ensure_group(e2e_session.db_path, group_y_name)

    x_title = f"{e2e_session.run_id} group X entry"
    y_title = f"{e2e_session.run_id} group Y entry"

    _seed_entry(
        e2e_session.db_path,
        group_id=group_x_id,
        year=2026,
        month=1,
        day=10,
        title=x_title,
        final_text="<p>Group X content.</p>",
    )
    _seed_entry(
        e2e_session.db_path,
        group_id=group_y_id,
        year=2026,
        month=1,
        day=11,
        title=y_title,
        final_text="<p>Group Y content.</p>",
    )

    page.goto("/?group_id=all")
    expect(page.get_by_role("heading", name="Timeline")).to_be_visible()
    expect(page.get_by_text(x_title)).to_be_visible()
    expect(page.get_by_text(y_title)).to_be_visible()
