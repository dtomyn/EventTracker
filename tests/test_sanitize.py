from __future__ import annotations

import unittest

from app.services.entries import sanitize_rich_text, sanitize_search_snippet


class TestSanitizeRichText(unittest.TestCase):
    """XSS attack vector tests for sanitize_rich_text()."""

    # -- Dangerous content is stripped --

    def test_script_tag_removed(self) -> None:
        result = sanitize_rich_text("<p>Hello</p><script>alert('xss')</script>")
        self.assertNotIn("<script", result)
        self.assertNotIn("alert", result)
        self.assertIn("<p>Hello</p>", result)

    def test_style_tag_removed(self) -> None:
        result = sanitize_rich_text("<style>body{display:none}</style><p>Hi</p>")
        self.assertNotIn("<style", result)
        self.assertIn("<p>Hi</p>", result)

    def test_event_handler_onerror_removed(self) -> None:
        result = sanitize_rich_text('<img onerror="alert(1)" src=x>')
        self.assertNotIn("onerror", result)
        self.assertNotIn("alert", result)

    def test_event_handler_onclick_removed(self) -> None:
        result = sanitize_rich_text('<p onclick="alert(1)">Click</p>')
        self.assertNotIn("onclick", result)
        self.assertIn("<p>", result)

    def test_event_handler_onload_removed(self) -> None:
        result = sanitize_rich_text('<body onload="alert(1)">Hi</body>')
        self.assertNotIn("onload", result)

    def test_event_handler_onmouseover_removed(self) -> None:
        result = sanitize_rich_text('<div onmouseover="alert(1)">Hover</div>')
        self.assertNotIn("onmouseover", result)

    def test_javascript_protocol_in_href_removed(self) -> None:
        result = sanitize_rich_text('<a href="javascript:alert(1)">click</a>')
        self.assertNotIn("javascript:", result)

    def test_data_uri_in_img_removed(self) -> None:
        result = sanitize_rich_text(
            '<img src="data:text/html,<script>alert(1)</script>">'
        )
        self.assertNotIn("<script", result)

    def test_nested_script_attempt(self) -> None:
        result = sanitize_rich_text("<scr<script>ipt>alert(1)</script>")
        self.assertNotIn("<script", result)
        # The inner <script> is decomposed; remaining text is safe (not executable)
        self.assertNotIn("<script>", result)

    def test_css_expression_removed(self) -> None:
        result = sanitize_rich_text(
            '<div style="background:url(javascript:alert(1))">text</div>'
        )
        self.assertNotIn("javascript:", result)
        self.assertNotIn("style", result)

    def test_disallowed_tags_unwrapped(self) -> None:
        result = sanitize_rich_text("<div><span>inner</span></div>")
        self.assertNotIn("<div", result)
        self.assertNotIn("<span", result)
        self.assertIn("inner", result)

    def test_img_tag_removed(self) -> None:
        result = sanitize_rich_text('<img src="http://evil.com/track.png">')
        self.assertNotIn("<img", result)

    def test_all_attributes_stripped_from_allowed_tags(self) -> None:
        result = sanitize_rich_text('<p class="x" id="y" style="color:red">Hi</p>')
        self.assertNotIn("class=", result)
        self.assertNotIn("id=", result)
        self.assertNotIn("style=", result)
        self.assertIn("<p>Hi</p>", result)

    # -- Legitimate content is preserved --

    def test_bold_preserved(self) -> None:
        self.assertIn("<b>bold</b>", sanitize_rich_text("<b>bold</b>"))
        self.assertIn("<strong>bold</strong>", sanitize_rich_text("<strong>bold</strong>"))

    def test_italic_preserved(self) -> None:
        self.assertIn("<i>italic</i>", sanitize_rich_text("<i>italic</i>"))
        self.assertIn("<em>emphasis</em>", sanitize_rich_text("<em>emphasis</em>"))

    def test_lists_preserved(self) -> None:
        html = "<ul><li>item 1</li><li>item 2</li></ul>"
        result = sanitize_rich_text(html)
        self.assertIn("<ul>", result)
        self.assertIn("<li>", result)

    def test_ordered_list_preserved(self) -> None:
        html = "<ol><li>first</li><li>second</li></ol>"
        result = sanitize_rich_text(html)
        self.assertIn("<ol>", result)
        self.assertIn("<li>", result)

    def test_paragraph_preserved(self) -> None:
        self.assertIn("<p>text</p>", sanitize_rich_text("<p>text</p>"))

    def test_code_tag_preserved(self) -> None:
        self.assertIn("<code>x = 1</code>", sanitize_rich_text("<code>x = 1</code>"))

    def test_br_preserved(self) -> None:
        result = sanitize_rich_text("line1<br>line2")
        self.assertIn("line1", result)
        self.assertIn("line2", result)

    def test_blockquote_preserved(self) -> None:
        result = sanitize_rich_text("<blockquote>quoted</blockquote>")
        self.assertIn("<blockquote>", result)

    def test_underline_preserved(self) -> None:
        self.assertIn("<u>underlined</u>", sanitize_rich_text("<u>underlined</u>"))

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(sanitize_rich_text(""), "")

    def test_none_like_empty_returns_empty(self) -> None:
        self.assertEqual(sanitize_rich_text(""), "")


class TestSanitizeSearchSnippet(unittest.TestCase):
    """Tests for sanitize_search_snippet() — must preserve <mark> tags."""

    def test_mark_tag_preserved(self) -> None:
        result = sanitize_search_snippet("Found <mark>keyword</mark> in text")
        self.assertIn("<mark>keyword</mark>", result)

    def test_script_removed_but_mark_preserved(self) -> None:
        result = sanitize_search_snippet(
            "<mark>match</mark><script>alert(1)</script>"
        )
        self.assertIn("<mark>match</mark>", result)
        self.assertNotIn("<script", result)
        self.assertNotIn("alert", result)

    def test_event_handler_removed_from_mark(self) -> None:
        result = sanitize_search_snippet('<mark onclick="alert(1)">match</mark>')
        self.assertNotIn("onclick", result)
        self.assertIn("<mark>match</mark>", result)

    def test_disallowed_tags_unwrapped(self) -> None:
        result = sanitize_search_snippet("<div><mark>hit</mark></div>")
        self.assertNotIn("<div", result)
        self.assertIn("<mark>hit</mark>", result)

    def test_allowed_rich_text_tags_also_preserved(self) -> None:
        result = sanitize_search_snippet("<p><mark>found</mark> in <b>bold</b></p>")
        self.assertIn("<mark>", result)
        self.assertIn("<b>bold</b>", result)
        self.assertIn("<p>", result)
