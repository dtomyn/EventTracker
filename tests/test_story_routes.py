from __future__ import annotations

from html import unescape
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db import connection_context, init_db
from app.main import app
from app.models import GeneratedExecutiveDeck, GeneratedExecutiveDeckSlide
from app.schemas import TimelineStoryArtifactSavePayload
from app.services.ai_story_mode import (
    GeneratedStoryCitation,
    GeneratedStorySection,
    GeneratedTimelineStory,
    StoryGenerationError,
)
from app.services.entries import EntryPayload, create_timeline_group, save_entry
from app.services.story_mode import get_story, get_story_artifact


class TestStoryRoutes(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("EVENTTRACKER_DB_PATH")
        self.previous_testing = os.environ.get("TESTING")
        os.environ["EVENTTRACKER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "EventTracker-test.db"
        )
        os.environ["TESTING"] = "1"
        init_db()

    def tearDown(self) -> None:
        if self.previous_db_path is None:
            os.environ.pop("EVENTTRACKER_DB_PATH", None)
        else:
            os.environ["EVENTTRACKER_DB_PATH"] = self.previous_db_path
        if self.previous_testing is None:
            os.environ.pop("TESTING", None)
        else:
            os.environ["TESTING"] = self.previous_testing
        self.temp_dir.cleanup()

    def test_story_page_renders_empty_scope_warning_without_failing(self) -> None:
        with TestClient(app) as client:
            response = client.get("/story")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Story Mode", response.text)
        self.assertIn("No entries match this scope yet", response.text)
        self.assertIn('action="/story/generate"', response.text)
        self.assertIn("Narrative guide", response.text)

    def test_generate_story_uses_current_search_scope_and_renders_result(self) -> None:
        with connection_context() as connection:
            other_group = create_timeline_group(connection, "Other Group")
            earliest_id = self._create_entry(
                connection,
                year=2024,
                month=2,
                day=5,
                title="Alpha milestone",
                final_text="<p>First milestone shipped.</p>",
            )
            middle_id = self._create_entry(
                connection,
                year=2024,
                month=10,
                day=9,
                title="Bridge milestone",
                final_text="<p>Bridge milestone kept momentum moving.</p>",
            )
            latest_id = self._create_entry(
                connection,
                year=2025,
                month=7,
                day=18,
                title="Beta milestone",
                final_text="<p>Second milestone validated.</p>",
            )
            self._create_entry(
                connection,
                year=2026,
                month=3,
                day=1,
                group_id=other_group.id,
                title="External milestone",
                final_text="<p>Milestone outside the selected group.</p>",
            )

        mocked_generation = AsyncMock(
            return_value=GeneratedTimelineStory(
                format="detailed_chronology",
                title="Milestone narrative",
                sections=[
                    GeneratedStorySection(
                        heading="Momentum built steadily",
                        body="The milestones progressed from initial delivery into validation.",
                        citation_orders=[1, 2],
                    )
                ],
                citations=[
                    GeneratedStoryCitation(
                        citation_order=1,
                        entry_id=earliest_id,
                        quote_text="First milestone shipped.",
                        note="Initial turning point",
                    ),
                    GeneratedStoryCitation(
                        citation_order=2,
                        entry_id=latest_id,
                        quote_text="Second milestone validated.",
                        note="Follow-up validation",
                    ),
                ],
                provider_name="copilot",
                source_entry_count=2,
                truncated_input=True,
            )
        )

        with patch("app.main.generate_timeline_story", mocked_generation):
            with TestClient(app) as client:
                response = client.post(
                    "/story/generate",
                    data={
                        "q": " milestone ",
                        "group_id": "1",
                        "format": "detailed_chronology",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Milestone narrative", response.text)
        self.assertIn("Momentum built steadily", response.text)
        self.assertIn('action="/story/save"', response.text)
        self.assertIn(f'href="/entries/{earliest_id}/view"', response.text)
        self.assertIn(f'href="/entries/{latest_id}/view"', response.text)
        self.assertIn(
            "This story used detailed context for the most recent 2 entries out of 3 in scope",
            response.text,
        )
        self.assertIn(
            "1 older entry was summarized at a higher level to preserve earlier context.",
            response.text,
        )
        self.assertIn(
            "Narrow the scope if you need earlier events included with the same level of detail and citation support.",
            response.text,
        )
        self.assertIn("data-story-trace", response.text)
        self.assertIn("Agent trace", response.text)
        self.assertRegex(
            response.text,
            r'<a[^>]+href="#citation-1"[^>]*>\[1\]</a>',
        )
        self.assertRegex(
            response.text,
            r'<a[^>]+href="#citation-2"[^>]*>\[2\]</a>',
        )

        await_args = mocked_generation.await_args
        self.assertIsNotNone(await_args)
        assert await_args is not None
        scope = await_args.args[0]
        story_format = await_args.args[1]
        entries = await_args.args[2]
        self.assertEqual(scope.scope_type, "search")
        self.assertEqual(scope.group_id, 1)
        self.assertEqual(scope.query_text, "milestone")
        self.assertEqual(story_format, "detailed_chronology")
        self.assertEqual([entry.id for entry in entries], [earliest_id, middle_id, latest_id])

    def test_generate_story_keeps_empty_scope_non_fatal(self) -> None:
        mocked_generation = AsyncMock()

        with patch("app.main.generate_timeline_story", mocked_generation):
            with TestClient(app) as client:
                response = client.post(
                    "/story/generate",
                    data={
                        "q": "missing",
                        "group_id": "all",
                        "format": "executive_summary",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("No entries match this scope yet", response.text)
        self.assertEqual(mocked_generation.await_count, 0)

    def test_generate_deck_after_narrative_embeds_artifact(
        self,
    ) -> None:
        with connection_context() as connection:
            entry_id = self._create_entry(
                connection,
                year=2026,
                month=3,
                day=19,
                title="Deck citation entry",
                final_text="<p>Deck citation body.</p>",
            )

        mocked_deck_generation = AsyncMock(
            return_value=GeneratedExecutiveDeck(
                title="Deck-backed narrative",
                subtitle="Executive readout",
                slides=[
                    GeneratedExecutiveDeckSlide(
                        slide_key="deck-title",
                        headline="Deck-backed narrative",
                        purpose="title",
                        body_points=["Deck body point"],
                        callouts=["Deck callout"],
                        visuals=["pull_quote"],
                        citations=[entry_id],
                    )
                ],
                provider_name="copilot",
                source_entry_count=1,
                truncated_input=False,
            )
        )
        mocked_artifact_builder = patch(
            "app.main.build_executive_deck_artifact",
            return_value=TimelineStoryArtifactSavePayload(
                artifact_kind="executive_deck",
                source_format="marpit_markdown",
                source_text="---\nmarpit: true\n---\n# Deck",
                compiled_html='<div class="marpit"><section><h1>Deck</h1></section></div>',
                compiled_css="section { color: #123456; }",
                metadata_json='{"slide_count":1}',
                generated_utc="2026-03-19T12:00:00+00:00",
                compiled_utc="2026-03-19T12:00:02+00:00",
                compiler_name="marpit",
                compiler_version="4.1.2",
            ),
        )

        citations_json = json.dumps(
            [
                {
                    "entry_id": entry_id,
                    "citation_order": 1,
                    "quote_text": "Deck citation body.",
                    "note": "Narrative evidence",
                }
            ]
        )

        with (
            patch("app.main.generate_executive_deck", mocked_deck_generation),
            mocked_artifact_builder,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/story/generate-deck",
                    data={
                        "group_id": "1",
                        "format": "executive_summary",
                        "title": "Deck-backed narrative",
                        "narrative_html": "<section><h2>Current state</h2><p>Narrative body.</p></section>",
                        "narrative_text": "Current state\n\nNarrative body.",
                        "generated_utc": "2026-03-19T12:00:00+00:00",
                        "provider_name": "copilot",
                        "source_entry_count": "1",
                        "truncated_input": "false",
                        "error_text": "",
                        "citations_json": citations_json,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Deck-backed narrative", response.text)
        self.assertIn("Presentation Preview", response.text)
        self.assertIn("Open fullscreen", response.text)
        self.assertIn("Download HTML", response.text)
        self.assertIn('name="presentation_artifact_json"', response.text)
        self.assertEqual(mocked_deck_generation.await_count, 1)

    def test_generate_deck_failure_keeps_narrative_usable(
        self,
    ) -> None:
        with connection_context() as connection:
            entry_id = self._create_entry(
                connection,
                year=2026,
                month=3,
                day=19,
                title="Narrative survives deck failure",
                final_text="<p>Deck failure citation body.</p>",
            )

        citations_json = json.dumps(
            [
                {
                    "entry_id": entry_id,
                    "citation_order": 1,
                    "quote_text": "Deck failure citation body.",
                    "note": None,
                }
            ]
        )

        with patch(
            "app.main.generate_executive_deck",
            AsyncMock(side_effect=StoryGenerationError("Deck provider unavailable.")),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/story/generate-deck",
                    data={
                        "group_id": "1",
                        "format": "executive_summary",
                        "title": "Narrative survives deck failure",
                        "narrative_html": "<section><h2>Current state</h2><p>The narrative is still rendered.</p></section>",
                        "narrative_text": "Current state\n\nThe narrative is still rendered.",
                        "generated_utc": "2026-03-19T12:00:00+00:00",
                        "provider_name": "copilot",
                        "source_entry_count": "1",
                        "truncated_input": "false",
                        "error_text": "",
                        "citations_json": citations_json,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Narrative survives deck failure", response.text)
        self.assertIn("Deck provider unavailable.", response.text)
        self.assertNotIn('name="presentation_artifact_json"', response.text)

    def test_save_story_redirects_and_saved_story_page_renders_snapshot(self) -> None:
        with connection_context() as connection:
            entry_id = self._create_entry(
                connection,
                year=2026,
                month=3,
                day=19,
                title="Saved citation entry",
                final_text="<p>Saved story citation body.</p>",
            )

        citations_json = json.dumps(
            [
                {
                    "entry_id": entry_id,
                    "citation_order": 1,
                    "quote_text": "Saved story citation body.",
                    "note": "Snapshot citation",
                }
            ]
        )

        with TestClient(app) as client:
            save_response = client.post(
                "/story/save",
                data={
                    "group_id": "1",
                    "format": "executive_summary",
                    "title": "Saved scope story",
                    "narrative_html": "<section><h2>Current State</h2><p>Snapshot body.</p></section>",
                    "narrative_text": "Current State\n\nSnapshot body.",
                    "generated_utc": "2026-03-19T12:00:00+00:00",
                    "provider_name": "copilot",
                    "source_entry_count": "1",
                    "truncated_input": "false",
                    "error_text": "",
                    "citations_json": citations_json,
                },
                follow_redirects=False,
            )

            self.assertEqual(save_response.status_code, 303)
            location = save_response.headers["location"]
            self.assertRegex(location, r"^/story/\d+$")

            saved_response = client.get(location)

        self.assertEqual(saved_response.status_code, 200)
        self.assertIn("Saved scope story", saved_response.text)
        self.assertIn("Saved", saved_response.text)
        self.assertIn(f'href="/entries/{entry_id}/view"', saved_response.text)
        self.assertIn("Snapshot citation", saved_response.text)

        story_id = int(location.rsplit("/", 1)[1])
        with connection_context() as connection:
            story = get_story(connection, story_id)

        self.assertIsNotNone(story)
        assert story is not None
        self.assertEqual(story.title, "Saved scope story")
        self.assertEqual(story.format, "executive_summary")
        self.assertEqual(
            [citation.entry_id for citation in story.citations], [entry_id]
        )

    def test_save_story_with_presentation_artifact_persists_toggle_and_route(self) -> None:
        with connection_context() as connection:
            entry_id = self._create_entry(
                connection,
                year=2026,
                month=3,
                day=19,
                title="Presentation citation entry",
                final_text="<p>Presentation citation body.</p>",
            )

        citations_json = json.dumps(
            [
                {
                    "entry_id": entry_id,
                    "citation_order": 1,
                    "quote_text": "Presentation citation body.",
                    "note": "Snapshot citation",
                }
            ]
        )
        presentation_artifact_json = json.dumps(
            {
                "artifact_kind": "executive_deck",
                "source_format": "marpit_markdown",
                "source_text": "---\nmarpit: true\n---\n# Deck",
                "compiled_html": '<div class="marpit"><section><h1>Deck</h1></section></div>',
                "compiled_css": "section { color: #123456; }",
                "metadata_json": '{"slide_count":1}',
                "generated_utc": "2026-03-19T12:00:00+00:00",
                "compiled_utc": "2026-03-19T12:00:02+00:00",
                "compiler_name": "marpit",
                "compiler_version": "4.1.2",
            }
        )

        with TestClient(app) as client:
            save_response = client.post(
                "/story/save",
                data={
                    "group_id": "1",
                    "format": "executive_summary",
                    "title": "Saved scope story",
                    "narrative_html": "<section><h2>Current State</h2><p>Snapshot body.</p></section>",
                    "narrative_text": "Current State\n\nSnapshot body.",
                    "generated_utc": "2026-03-19T12:00:00+00:00",
                    "provider_name": "copilot",
                    "source_entry_count": "1",
                    "truncated_input": "false",
                    "error_text": "",
                    "citations_json": citations_json,
                    "presentation_artifact_json": presentation_artifact_json,
                },
                follow_redirects=False,
            )

            self.assertEqual(save_response.status_code, 303)
            location = save_response.headers["location"]
            story_id = int(location.rsplit("/", 1)[1])

            saved_response = client.get(location)
            presentation_view_response = client.get(f"{location}?view=presentation")
            presentation_response = client.get(f"/story/{story_id}/presentation")

        self.assertEqual(saved_response.status_code, 200)
        self.assertIn("Deck ready", saved_response.text)
        self.assertIn(f'href="/story/{story_id}?view=presentation"', saved_response.text)

        self.assertEqual(presentation_view_response.status_code, 200)
        self.assertIn(f'src="/story/{story_id}/presentation"', presentation_view_response.text)

        self.assertEqual(presentation_response.status_code, 200)
        self.assertIn('<div class="marpit">', presentation_response.text)
        self.assertIn("section { color: #123456; }", presentation_response.text)
        self.assertIn(".et-slide--thank_you .et-pull-quote", presentation_response.text)

        with connection_context() as connection:
            artifact = get_story_artifact(connection, story_id, "executive_deck")

        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual(artifact.compiler_name, "marpit")

    def test_story_route_workflow_covers_launch_generate_save_and_reload(self) -> None:
        with connection_context() as connection:
            earliest_id = self._create_entry(
                connection,
                year=2024,
                month=2,
                day=5,
                title="Alpha milestone",
                final_text="<p>First milestone shipped.</p>",
            )
            latest_id = self._create_entry(
                connection,
                year=2025,
                month=7,
                day=18,
                title="Beta milestone",
                final_text="<p>Second milestone validated.</p>",
            )

        mocked_generation = AsyncMock(
            return_value=GeneratedTimelineStory(
                format="detailed_chronology",
                title="Milestone narrative",
                sections=[
                    GeneratedStorySection(
                        heading="Momentum built steadily",
                        body="The milestones progressed from initial delivery into validation.",
                        citation_orders=[1, 2],
                    )
                ],
                citations=[
                    GeneratedStoryCitation(
                        citation_order=1,
                        entry_id=earliest_id,
                        quote_text="First milestone shipped.",
                        note="Initial turning point",
                    ),
                    GeneratedStoryCitation(
                        citation_order=2,
                        entry_id=latest_id,
                        quote_text="Second milestone validated.",
                        note="Follow-up validation",
                    ),
                ],
                provider_name="copilot",
                source_entry_count=2,
                truncated_input=False,
            )
        )

        with patch("app.main.generate_timeline_story", mocked_generation):
            with TestClient(app) as client:
                launch_response = client.get(
                    "/story",
                    params={"q": "milestone", "group_id": "1"},
                )
                self.assertEqual(launch_response.status_code, 200)
                self.assertIn(
                    "Build a narrative from the current scope", launch_response.text
                )
                self.assertIn("Search: milestone", launch_response.text)

                generate_response = client.post(
                    "/story/generate",
                    data={
                        "q": "milestone",
                        "group_id": "1",
                        "format": "detailed_chronology",
                    },
                )

                self.assertEqual(generate_response.status_code, 200)
                self.assertIn(
                    "Story generated for the current scope.", generate_response.text
                )
                self.assertIn('action="/story/save"', generate_response.text)
                self.assertIn("Milestone narrative", generate_response.text)
                self.assertIn("Momentum built steadily", generate_response.text)
                self.assertIn(
                    f'href="/entries/{earliest_id}/view"', generate_response.text
                )
                self.assertIn(
                    f'href="/entries/{latest_id}/view"', generate_response.text
                )
                self.assertRegex(
                    generate_response.text,
                    r'<a[^>]+href="#citation-1"[^>]*>\[1\]</a>',
                )
                self.assertRegex(
                    generate_response.text,
                    r'<a[^>]+href="#citation-2"[^>]*>\[2\]</a>',
                )

                save_payload = self._extract_story_save_payload(generate_response.text)
                save_response = client.post(
                    "/story/save",
                    data=save_payload,
                    follow_redirects=False,
                )

                self.assertEqual(save_response.status_code, 303)
                location = save_response.headers["location"]
                self.assertRegex(location, r"^/story/\d+$")

                saved_response = client.get(location)
                reload_response = client.get(location)

        self.assertEqual(saved_response.status_code, 200)
        self.assertIn("Saved snapshot", saved_response.text)
        self.assertIn("Milestone narrative", saved_response.text)
        self.assertIn("Momentum built steadily", saved_response.text)
        self.assertIn("Initial turning point", saved_response.text)
        self.assertIn("Follow-up validation", saved_response.text)
        self.assertIn(f'href="/entries/{earliest_id}/view"', saved_response.text)
        self.assertIn(f'href="/entries/{latest_id}/view"', saved_response.text)

        self.assertEqual(reload_response.status_code, 200)
        self.assertIn("Saved snapshot", reload_response.text)
        self.assertIn("Milestone narrative", reload_response.text)

        story_id = int(location.rsplit("/", 1)[1])
        with connection_context() as connection:
            story = get_story(connection, story_id)

        self.assertIsNotNone(story)
        assert story is not None
        self.assertEqual(story.title, "Milestone narrative")
        self.assertEqual(story.format, "detailed_chronology")
        self.assertEqual(story.query_text, "milestone")
        self.assertEqual(story.group_id, 1)
        self.assertEqual(
            [citation.entry_id for citation in story.citations],
            [earliest_id, latest_id],
        )

    def test_timeline_and_search_surfaces_link_into_story_mode(self) -> None:
        with connection_context() as connection:
            self._create_entry(
                connection,
                year=2024,
                month=2,
                day=5,
                title="Alpha milestone",
                final_text="<p>First milestone shipped.</p>",
            )

        with TestClient(app) as client:
            timeline_response = client.get(
                "/",
                params={"q": "milestone", "group_id": "1"},
            )
            search_response = client.get(
                "/search",
                params={"q": "milestone", "group_id": "1"},
            )
            years_response = client.get(
                "/timeline/years",
                params={"q": "milestone", "group_id": "1"},
            )
            months_response = client.get(
                "/timeline/months",
                params={"q": "milestone", "group_id": "1", "year": "2024"},
            )

        self.assertEqual(timeline_response.status_code, 200)
        self.assertIn('href="/story?group_id=1&q=milestone"', timeline_response.text)

        self.assertEqual(search_response.status_code, 200)
        self.assertIn('href="/story?group_id=1&q=milestone"', search_response.text)

        self.assertEqual(years_response.status_code, 200)
        self.assertIn(
            "/story?group_id=1&q=milestone&year=2024",
            years_response.json()["items_html"],
        )

        self.assertEqual(months_response.status_code, 200)
        self.assertIn(
            "/story?group_id=1&q=milestone&year=2024&month=2",
            months_response.json()["items_html"],
        )

    def _create_entry(
        self,
        connection,
        *,
        year: int,
        month: int,
        day: int | None,
        title: str,
        final_text: str,
        group_id: int = 1,
    ) -> int:
        return save_entry(
            connection,
            EntryPayload(
                event_year=year,
                event_month=month,
                event_day=day,
                group_id=group_id,
                title=title,
                source_url=None,
                generated_text=None,
                final_text=final_text,
                tags=[],
                links=[],
            ),
        )

    def _extract_story_save_payload(self, html: str) -> dict[str, str]:
        form_match = re.search(
            r'<form method="post" action="/story/save".*?</form>',
            html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(form_match)
        assert form_match is not None

        payload: dict[str, str] = {}
        for field_name in (
            "q",
            "group_id",
            "year",
            "month",
            "format",
            "title",
            "narrative_html",
            "narrative_text",
            "generated_utc",
            "provider_name",
            "source_entry_count",
            "truncated_input",
            "error_text",
            "citations_json",
        ):
            input_match = re.search(
                rf'<input type="hidden" name="{field_name}" value="([^"]*)">',
                form_match.group(0),
            )
            if input_match is not None:
                payload[field_name] = unescape(input_match.group(1))
                continue

            textarea_match = re.search(
                rf'<textarea name="{field_name}" hidden>(.*?)</textarea>',
                form_match.group(0),
                flags=re.DOTALL,
            )
            self.assertIsNotNone(
                textarea_match,
                msg=f"Missing hidden field: {field_name}",
            )
            assert textarea_match is not None
            payload[field_name] = unescape(textarea_match.group(1))
        return payload
