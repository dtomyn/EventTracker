from __future__ import annotations

import unittest
from unittest.mock import patch

from app.models import Entry, GeneratedExecutiveDeck, GeneratedExecutiveDeckSlide, TimelineStoryScope
from app.services.story_deck import (
    StoryDeckRuntimeUnavailableError,
    build_executive_deck_artifact,
    build_executive_deck_markdown,
    render_story_deck_markdown,
    sanitize_compiled_deck_css,
    sanitize_compiled_deck_html,
)


class TestStoryDeckMarkdown(unittest.TestCase):
    def test_build_executive_deck_markdown_includes_front_matter_visuals_and_sources(
        self,
    ) -> None:
        deck = GeneratedExecutiveDeck(
            title="Launch deck",
            subtitle="Executive readout",
            slides=[
                GeneratedExecutiveDeckSlide(
                    slide_key="launch-title",
                    headline="Launch at a glance",
                    purpose="title",
                    body_points=["Delivery moved into execution."],
                    callouts=["Latest validation is complete."],
                    visuals=["pull_quote"],
                    citations=[1],
                ),
                GeneratedExecutiveDeckSlide(
                    slide_key="launch-summary",
                    headline="Momentum increased",
                    purpose="summary",
                    body_points=[
                        "The team moved from planning into release work.",
                        "Cross-functional alignment improved.",
                    ],
                    callouts=["Execution confidence is higher than last quarter."],
                    visuals=["kpi_strip", "phase_timeline"],
                    citations=[1, 2],
                ),
            ],
        )

        document = build_executive_deck_markdown(deck, [_entry(1), _entry(2)])

        self.assertIn("marpit: true", document.markdown)
        self.assertIn("theme: eventtracker-executive", document.markdown)
        self.assertIn('<div class="et-slide et-slide--title"', document.markdown)
        self.assertIn('<div class="et-kpi-strip">', document.markdown)
        self.assertIn('<ol class="et-phase-timeline">', document.markdown)
        self.assertIn('href="/entries/1/view"', document.markdown)
        self.assertNotIn("size: 16:9", document.markdown)
        self.assertNotIn('<section class="et-slide', document.markdown)
        self.assertEqual(document.citation_orders_by_slide["launch-summary"], [1, 2])
        self.assertEqual(document.visual_kinds, ["pull_quote", "kpi_strip", "phase_timeline"])

    def test_build_executive_deck_artifact_sanitizes_renderer_output(self) -> None:
        deck = GeneratedExecutiveDeck(
            title="Launch deck",
            subtitle=None,
            slides=[
                GeneratedExecutiveDeckSlide(
                    slide_key="launch-title",
                    headline="Launch at a glance",
                    purpose="title",
                    body_points=["Delivery moved into execution."],
                    callouts=["Latest validation is complete."],
                    visuals=["pull_quote"],
                    citations=[1],
                )
            ],
            provider_name="copilot",
            source_entry_count=1,
            truncated_input=False,
        )

        with patch(
            "app.services.story_deck.render_story_deck_markdown",
            return_value=type(
                "RenderedDeckDocumentStub",
                (),
                {
                    "html": '<div class="marpit"><section><script>bad()</script><a href="https://example.com">x</a><h1>Deck</h1></section></div>',
                    "css": "section { color: #123456; }",
                    "compiler_name": "marpit",
                    "compiler_version": "4.1.2",
                },
            )(),
        ):
            artifact = build_executive_deck_artifact(
                deck,
                TimelineStoryScope(scope_type="timeline", group_id=1),
                [_entry(1)],
                generated_utc="2026-03-20T12:00:00+00:00",
            )

        self.assertEqual(artifact.artifact_kind, "executive_deck")
        self.assertNotIn("<script>", artifact.compiled_html)
        self.assertNotIn("https://example.com", artifact.compiled_html)
        self.assertIn('"theme_name":"eventtracker-executive"', artifact.metadata_json)

    def test_build_executive_deck_markdown_deduplicates_text_reused_by_visuals(self) -> None:
        deck = GeneratedExecutiveDeck(
            title="Trajectory deck",
            subtitle="Executive readout",
            slides=[
                GeneratedExecutiveDeckSlide(
                    slide_key="trajectory-slide",
                    headline="Agents move into core infrastructure",
                    purpose="trajectory",
                    body_points=[
                        "Agentic sandboxes become standard compute primitives",
                        "Multi-agent orchestration enters IDEs",
                    ],
                    callouts=[
                        "From hype to ops",
                        "Next: regulated industries",
                    ],
                    visuals=["phase_timeline", "kpi_strip"],
                    citations=[1],
                )
            ],
        )

        document = build_executive_deck_markdown(deck, [_entry(1)])

        self.assertNotIn('<div class="et-point-list">', document.markdown)
        self.assertNotIn('<div class="et-callout-list">', document.markdown)
        self.assertEqual(
            document.markdown.count(
                "Agentic sandboxes become standard compute primitives"
            ),
            1,
        )
        self.assertEqual(
            document.markdown.count("Multi-agent orchestration enters IDEs"),
            1,
        )
        self.assertEqual(document.markdown.count("From hype to ops"), 1)
        self.assertEqual(
            document.markdown.count("Next: regulated industries"),
            1,
        )

    def test_build_executive_deck_markdown_drops_exact_duplicate_model_items(self) -> None:
        deck = GeneratedExecutiveDeck(
            title="Duplicate cleanup deck",
            subtitle=None,
            slides=[
                GeneratedExecutiveDeckSlide(
                    slide_key="cleanup-slide",
                    headline="Cleanup",
                    purpose="highlight",
                    body_points=[
                        "Observability formalizes",
                        "Observability formalizes",
                    ],
                    callouts=[
                        "Observability formalizes",
                        "Security coalitions form",
                        "Security coalitions form",
                    ],
                    visuals=[],
                    citations=[1],
                )
            ],
        )

        document = build_executive_deck_markdown(deck, [_entry(1)])

        self.assertEqual(document.markdown.count("Observability formalizes"), 1)
        self.assertEqual(document.markdown.count("Security coalitions form"), 1)

    def test_build_executive_deck_markdown_title_slide_keeps_single_primary_visual(self) -> None:
        deck = GeneratedExecutiveDeck(
            title="Agentic era deck",
            subtitle="From prototype to production",
            slides=[
                GeneratedExecutiveDeckSlide(
                    slide_key="title-slide",
                    headline="Agentic era deck",
                    purpose="title",
                    body_points=["March-April 2026", "40 signals tracked"],
                    callouts=["March-April 2026.", "40 signals tracked."],
                    visuals=["icon_grid", "stat_card"],
                    citations=[],
                )
            ],
        )

        document = build_executive_deck_markdown(deck, [_entry(1)])

        self.assertNotIn('<div class="et-point-list">', document.markdown)
        self.assertNotIn('<div class="et-callout-list">', document.markdown)
        self.assertNotIn('<div class="et-icon-grid">', document.markdown)
        self.assertIn('<div class="et-stat-grid">', document.markdown)
        self.assertEqual(document.markdown.count("March-April 2026"), 1)
        self.assertEqual(document.markdown.count("40 signals tracked"), 1)

    def test_build_executive_deck_markdown_treats_punctuation_only_variants_as_duplicates(self) -> None:
        deck = GeneratedExecutiveDeck(
            title="Duplicate punctuation deck",
            subtitle=None,
            slides=[
                GeneratedExecutiveDeckSlide(
                    slide_key="punctuation-slide",
                    headline="Cleanup",
                    purpose="highlight",
                    body_points=["40 signals tracked"],
                    callouts=["40 signals tracked."],
                    visuals=[],
                    citations=[1],
                )
            ],
        )

        document = build_executive_deck_markdown(deck, [_entry(1)])

        self.assertEqual(document.markdown.count("40 signals tracked"), 1)


class TestStoryDeckRenderer(unittest.TestCase):
    def test_render_story_deck_markdown_requires_node(self) -> None:
        with patch("app.services.story_deck.shutil.which", return_value=None):
            with self.assertRaises(StoryDeckRuntimeUnavailableError):
                render_story_deck_markdown("---\nmarpit: true\n---\n# Deck")

    def test_sanitize_compiled_deck_html_strips_disallowed_tags_and_attrs(self) -> None:
        sanitized = sanitize_compiled_deck_html(
            '<div class="marpit"><section id="one"><p>Hello</p><script>bad()</script><a href="/entries/1/view" onclick="bad()">Entry</a></section></div>'
        )

        self.assertNotIn("<script>", sanitized)
        self.assertNotIn("onclick", sanitized)
        self.assertIn('href="/entries/1/view"', sanitized)

    def test_sanitize_compiled_deck_css_rejects_unsafe_constructs(self) -> None:
        with self.assertRaisesRegex(Exception, "unsupported CSS content"):
            sanitize_compiled_deck_css("@import url('https://example.com/deck.css');")


def _entry(entry_id: int) -> Entry:
    return Entry(
        id=entry_id,
        event_year=2026,
        event_month=3,
        event_day=19,
        sort_key=20260319,
        group_id=1,
        group_name="Default",
        title=f"Entry {entry_id}",
        source_url=None,
        generated_text=None,
        final_text="<p>Deck entry body.</p>",
        created_utc="2026-03-19T12:00:00+00:00",
        updated_utc="2026-03-19T12:00:00+00:00",
        tags=[],
        links=[],
        display_date="3/19/2026",
        preview_text="",
    )