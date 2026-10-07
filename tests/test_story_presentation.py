from __future__ import annotations

import unittest

from app.services.story_presentation import build_presentation_chapters


class BuildPresentationChaptersTests(unittest.TestCase):
    def test_splits_sections_into_chapters_with_citations(self) -> None:
        narrative_html = (
            '<section class="story-section mb-4"><h2 class="h5 mb-2">First &amp; foremost</h2>'
            "<p>Opening paragraph.</p><p>Second paragraph.</p>"
            '<p class="small text-body-secondary mb-0">Sources '
            '<a href="#citation-1" class="story-inline-citation">[1]</a> '
            '<a href="#citation-3" class="story-inline-citation">[3]</a></p></section>'
            '<section class="story-section mb-4"><h2 class="h5 mb-2">Later</h2>'
            "<p>Closing thoughts.</p></section>"
        )

        chapters = build_presentation_chapters(narrative_html, fallback_heading="Story")

        self.assertEqual([chapter.heading for chapter in chapters], ["First & foremost", "Later"])
        self.assertEqual(chapters[0].paragraphs, ["Opening paragraph.", "Second paragraph."])
        self.assertEqual(chapters[0].citation_orders, [1, 3])
        self.assertEqual(chapters[1].paragraphs, ["Closing thoughts."])
        self.assertEqual(chapters[1].citation_orders, [])

    def test_inline_citation_inside_paragraph_keeps_text(self) -> None:
        chapters = build_presentation_chapters(
            '<h2>Heading</h2><p>Growth accelerated <a href="#citation-2">[2]</a> quickly.</p>',
            fallback_heading="Story",
        )

        self.assertEqual(chapters[0].paragraphs, ["Growth accelerated [2] quickly."])
        self.assertEqual(chapters[0].citation_orders, [2])

    def test_narrative_without_headings_uses_fallback_heading(self) -> None:
        chapters = build_presentation_chapters(
            "<p>Only a paragraph.</p>", fallback_heading="My story"
        )

        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].heading, "My story")
        self.assertEqual(chapters[0].paragraphs, ["Only a paragraph."])

    def test_markup_is_reduced_to_plain_text(self) -> None:
        chapters = build_presentation_chapters(
            "<h2>Safe <em>title</em></h2><p>&lt;script&gt;x&lt;/script&gt;</p>"
            "<script>alert(1)</script><style>p{}</style>",
            fallback_heading="Story",
        )

        self.assertEqual(chapters[0].heading, "Safe title")
        self.assertEqual(chapters[0].paragraphs, ["<script>x</script>"])

    def test_empty_html_falls_back_to_narrative_text(self) -> None:
        chapters = build_presentation_chapters(
            "", fallback_heading="Story", narrative_text="One.\n\nTwo."
        )

        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].paragraphs, ["One.", "Two."])

    def test_empty_inputs_return_no_chapters(self) -> None:
        self.assertEqual(build_presentation_chapters("", fallback_heading="Story"), [])


if __name__ == "__main__":
    unittest.main()
