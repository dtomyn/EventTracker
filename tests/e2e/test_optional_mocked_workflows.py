from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from datetime import UTC, datetime

import pytest
from playwright.sync_api import Page, expect


def _ensure_group_id(
    db_path: Path,
    group_name: str,
    *,
    web_search_query: str | None = None,
) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT id FROM timeline_groups WHERE name = ?",
            (group_name,),
        ).fetchone()
        if row is None:
            cursor = connection.execute(
                "INSERT INTO timeline_groups(name, web_search_query, is_default) VALUES (?, ?, 0)",
                (group_name, web_search_query),
            )
            connection.commit()
            return int(cursor.lastrowid)

        group_id = int(row[0])
        if web_search_query is not None:
            connection.execute(
                "UPDATE timeline_groups SET web_search_query = ? WHERE id = ?",
                (web_search_query, group_id),
            )
            connection.commit()
        return group_id


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
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
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
            (year, month, day, sort_key, group_id, title, final_text, timestamp, timestamp),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _fill_summary_and_wait_for_html_preview(page: Page, value: str) -> None:
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and "/entries/preview-html" in response.url,
        timeout=5_000,
    ):
        page.get_by_label("Event Summary").fill(value)


def _generated_preview_partial(
    *,
    feedback_class: str,
    feedback_message: str,
    generated_text: str = "",
    suggested_title: str = "",
    suggested_event_year: str = "",
    suggested_event_month: str = "",
    suggested_event_day: str = "",
) -> str:
    title_hint = ""
    if suggested_title:
        title_hint = (
            '<div class="small text-body-secondary mt-2">Suggested title: '
            f"<strong>{suggested_title}</strong></div>"
        )

    date_hint = ""
    if suggested_event_year:
        date_value = suggested_event_year
        if suggested_event_month:
            date_value = f"{date_value}-{suggested_event_month}"
        if suggested_event_day:
            date_value = f"{date_value}-{suggested_event_day}"
        date_hint = (
            '<div class="small text-body-secondary">Suggested date: '
            f"{date_value}</div>"
        )

    return (
        f'<input type="hidden" id="generated_text" name="generated_text" value="{generated_text}">'
        f'<div id="generate-feedback" class="form-text {feedback_class}">{feedback_message}</div>'
        f'<input type="hidden" id="generated_suggested_title" value="{suggested_title}">'
        f'<input type="hidden" id="generated_suggested_event_year" value="{suggested_event_year}">'
        f'<input type="hidden" id="generated_suggested_event_month" value="{suggested_event_month}">'
        f'<input type="hidden" id="generated_suggested_event_day" value="{suggested_event_day}">'
        f"{title_hint}{date_hint}"
    )


def _sse_event(event_name: str, payload: dict[str, object]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


def test_html_preview_sanitizes_unsafe_markup_and_preserves_allowed_tags(
    page: Page,
    ensure_dedicated_group,
) -> None:
    group_id = ensure_dedicated_group()

    page.goto("/entries/new")
    page.get_by_label("Timeline Group").select_option(str(group_id))

    raw_html = (
        "<p><strong>Allowed</strong> <span>inline wrapper</span></p>"
        "<ul><li>Rendered item</li></ul>"
        "<script>alert('xss')</script>"
    )
    _fill_summary_and_wait_for_html_preview(page, raw_html)

    preview = page.locator("#final-text-preview")
    expect(preview.locator(".entry-rich-text")).to_be_visible()
    expect(preview.locator("strong")).to_have_text("Allowed")
    expect(preview.locator("p")).to_contain_text("inline wrapper")
    expect(preview.locator("ul li")).to_have_text("Rendered item")
    expect(preview).not_to_contain_text("alert('xss')")

    preview_html = preview.inner_html()
    assert "<script" not in preview_html
    assert "<span" not in preview_html


def test_html_preview_shows_temporary_unavailable_when_request_fails(
    page: Page,
    ensure_dedicated_group,
) -> None:
    group_id = ensure_dedicated_group()

    page.route(
        "**/entries/preview-html",
        lambda route: route.fulfill(
            status=503,
            content_type="text/plain",
            body="preview unavailable",
        ),
    )

    page.goto("/entries/new")
    page.get_by_label("Timeline Group").select_option(str(group_id))
    _fill_summary_and_wait_for_html_preview(page, "<p>Trigger preview</p>")

    expect(page.locator("#final-text-preview")).to_contain_text(
        "Preview is temporarily unavailable."
    )


def test_ai_generation_applies_mocked_draft_and_metadata(
    page: Page,
    ensure_dedicated_group,
) -> None:
    group_id = ensure_dedicated_group()
    generated_html = "<p>Generated <strong>summary</strong> for the release.</p>"

    page.route(
        "**/entries/generate",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=_generated_preview_partial(
                feedback_class="text-success",
                feedback_message="Summary, title, and date suggestions generated from the current input.",
                generated_text=generated_html,
                suggested_title="Generated release summary",
                suggested_event_year="2026",
                suggested_event_month="3",
                suggested_event_day="18",
            ),
        ),
    )

    page.goto("/entries/new")
    page.get_by_label("Timeline Group").select_option(str(group_id))
    page.get_by_label("Title").fill("Original draft title")
    _fill_summary_and_wait_for_html_preview(page, "<p>Existing summary</p>")

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and "/entries/generate" in response.url,
        timeout=5_000,
    ):
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and "/entries/preview-html" in response.url,
            timeout=5_000,
        ):
            page.get_by_role("button", name="Generate").click()

    expect(page.locator("#generate-feedback")).to_have_text(
        "Summary, title, and date suggestions generated from the current input."
    )
    expect(page.get_by_label("Title")).to_have_value("Generated release summary")
    expect(page.get_by_label("Year")).to_have_value("2026")
    expect(page.get_by_label("Month")).to_have_value("3")
    expect(page.get_by_label("Day")).to_have_value("18")
    expect(page.get_by_label("Event Summary")).to_have_value(generated_html)
    expect(page.locator("#final-text-preview strong")).to_have_text("summary")
    expect(page.locator("#final-text-preview")).to_contain_text(
        "Generated summary for the release."
    )


@pytest.mark.parametrize(
    ("status_code", "feedback_message"),
    [
        (400, "Title or source URL is required to generate a summary."),
        (502, "Could not generate a summary right now."),
        (500, "Summary generation failed. You can still write manually."),
    ],
)
def test_ai_generation_renders_mocked_error_partials_without_overwriting_form_values(
    page: Page,
    ensure_dedicated_group,
    status_code: int,
    feedback_message: str,
) -> None:
    group_id = ensure_dedicated_group()
    original_title = "Keep my title"
    original_summary = "<p>Keep my manually written summary.</p>"

    page.route(
        "**/entries/generate",
        lambda route: route.fulfill(
            status=status_code,
            content_type="text/html",
            body=_generated_preview_partial(
                feedback_class="text-danger",
                feedback_message=feedback_message,
                generated_text=original_summary,
            ),
        ),
    )

    page.goto("/entries/new")
    page.get_by_label("Timeline Group").select_option(str(group_id))
    page.get_by_label("Title").fill(original_title)
    _fill_summary_and_wait_for_html_preview(page, original_summary)

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and "/entries/generate" in response.url,
        timeout=5_000,
    ):
        page.get_by_role("button", name="Generate").click()

    expect(page.locator("#generate-feedback")).to_have_text(feedback_message)
    expect(page.get_by_label("Title")).to_have_value(original_title)
    expect(page.get_by_label("Event Summary")).to_have_value(original_summary)


def test_group_web_search_panel_is_hidden_without_a_group_query(
    page: Page,
    e2e_session,
) -> None:
    group_id = _ensure_group_id(
        e2e_session.db_path,
        f"{e2e_session.group_name} No Web Query",
    )

    page.goto(f"/?group_id={group_id}")
    expect(page.locator("[data-group-web-search-panel]")).to_have_count(0)


def test_group_web_search_shows_disabled_copy_when_provider_is_not_copilot(
    page: Page,
    e2e_session,
) -> None:
    group_id = _ensure_group_id(
        e2e_session.db_path,
        f"{e2e_session.group_name} Disabled Web Search",
        web_search_query="AI developer tools launches and benchmarks",
    )
    _seed_entry(
        e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=18,
        title="Disabled provider timeline seed",
        final_text="Entry used to initialize the timeline visualization script.",
    )

    page.goto(f"/?group_id={group_id}")
    panel = page.locator("[data-group-web-search-panel]")
    expect(panel).to_be_visible()

    panel.locator("[data-group-web-search-toggle]").dispatch_event("click")
    expect(panel.get_by_text(
        "Available when GitHub Copilot is the active AI provider."
    )).to_have_count(1)
    expect(panel.locator("[data-group-web-search-refresh]")).to_have_count(0)


def test_copilot_group_web_search_handles_empty_result_refresh_result_and_error_states(
    copilot_page: Page,
    copilot_e2e_session,
) -> None:
    group_id = _ensure_group_id(
        copilot_e2e_session.db_path,
        f"{copilot_e2e_session.group_name} Copilot Web Search",
        web_search_query="AI developer tools launches and benchmarks",
    )
    _seed_entry(
        copilot_e2e_session.db_path,
        group_id=group_id,
        year=2026,
        month=3,
        day=18,
        title="Copilot web search timeline seed",
        final_text="Entry used to initialize the timeline visualization script.",
    )

    copilot_page.add_init_script(
        """
        (() => {
            const scenarios = [
                [
                    [50, 'status', { kind: 'status', message: 'Searching the web' }],
                    [100, 'copilot_event', { kind: 'copilot_event', eventType: 'tool.start', message: 'Copilot opened the first source.' }],
                    [150, 'result', { kind: 'result', query: 'AI developer tools launches and benchmarks', items: [], message: 'No related results found right now.' }],
                    [180, 'complete', { kind: 'complete', ok: true }],
                ],
                [
                    [50, 'status', { kind: 'status', message: 'Refreshing recent developments' }],
                    [100, 'copilot_event', { kind: 'copilot_event', phase: 'search', eventType: 'assistant.message.delta', message: 'Merged release notes and benchmark commentary.' }],
                    [150, 'result', { kind: 'result', query: 'AI developer tools launches and benchmarks', items: [{ title: 'Release note roundup', url: 'https://example.com/release-note-roundup', source: 'Example News', article_date: '2026-03-18', snippet: 'Key launch updates and benchmark notes.' }], message: null }],
                    [180, 'complete', { kind: 'complete', ok: true }],
                ],
                [
                    [50, 'status', { kind: 'status', message: 'Refreshing recent developments' }],
                    [100, 'search_error', { kind: 'search_error', message: 'Could not load web results.' }],
                ],
            ];

            window.__mockEventSourceRequests = [];

            class MockEventSource {
                constructor(url) {
                    this.url = url;
                    this.listeners = new Map();
                    this.closed = false;
                    const requestIndex = window.__mockEventSourceRequests.length;
                    window.__mockEventSourceRequests.push(url);
                    const scenario = scenarios[Math.min(requestIndex, scenarios.length - 1)];
                    for (const [delay, eventName, payload] of scenario) {
                        window.setTimeout(() => {
                            if (this.closed) {
                                return;
                            }
                            this._emit(eventName, payload);
                        }, delay);
                    }
                }

                addEventListener(eventName, listener) {
                    const listeners = this.listeners.get(eventName) || [];
                    listeners.push(listener);
                    this.listeners.set(eventName, listeners);
                }

                close() {
                    this.closed = true;
                }

                _emit(eventName, payload) {
                    const listeners = this.listeners.get(eventName) || [];
                    const event = { data: JSON.stringify(payload) };
                    for (const listener of listeners) {
                        listener(event);
                    }
                }
            }

            window.EventSource = MockEventSource;
        })();
        """
    )

    copilot_page.goto(f"/?group_id={group_id}")

    panel = copilot_page.locator("[data-group-web-search-panel]")
    toggle = panel.locator("[data-group-web-search-toggle]")
    empty_state = panel.locator("[data-group-web-search-empty]")
    error_state = panel.locator("[data-group-web-search-error]")
    progress_log = panel.locator("[data-group-web-search-progress-log]")
    results = panel.locator("[data-group-web-search-results]")
    refresh_button = panel.locator("[data-group-web-search-refresh]").first

    toggle.dispatch_event("click")
    copilot_page.wait_for_function("window.__mockEventSourceRequests.length === 1")
    expect(empty_state).to_be_visible()
    expect(progress_log).to_contain_text("Searching the web")
    expect(progress_log).to_contain_text("Tool start")

    refresh_button.dispatch_event("click")
    copilot_page.wait_for_function("window.__mockEventSourceRequests.length === 2")
    expect(results).to_be_visible()
    expect(results.get_by_role("link", name="Release note roundup")).to_be_visible()
    expect(results).to_contain_text("Example News")
    expect(results).to_contain_text("Key launch updates and benchmark notes.")

    refresh_button.dispatch_event("click")
    copilot_page.wait_for_function("window.__mockEventSourceRequests.length === 3")
    expect(error_state).to_be_visible()
    expect(error_state).to_contain_text("Could not load web results.")

    stream_requests = copilot_page.evaluate("window.__mockEventSourceRequests")
    assert any("force_refresh=1" in url for url in stream_requests[1:])