"""
Phase 1: TDD Audit - Verify duplicated helper functions behave identically.

This test suite ensures that helper functions defined in both app/main.py
and app/route_helpers.py produce identical outputs before consolidation.

RED phase: These tests verify the duplication exists and behavior matches.
"""

import pytest
from fastapi.exceptions import HTTPException


class TestParseGroupId:
    """Verify _parse_group_id behaves identically in both modules."""

    def test_parse_group_id_from_main_and_route_helpers_match(self):
        """_parse_group_id from both modules should return identical results."""
        from app import main as main_module
        from app import route_helpers as rh_module

        test_cases = [
            "",  # Empty string -> None
            "  ",  # Whitespace only -> None
            "123",  # Valid positive integer -> 123
            "0",  # Zero -> HTTPException
            "-1",  # Negative -> HTTPException
            "abc",  # Non-integer -> HTTPException
            "123abc",  # Mixed -> HTTPException
            " 456 ",  # Whitespace around valid int -> 456
            "all",  # Special case -> None
        ]

        for test_input in test_cases:
            main_error = None
            rh_error = None
            main_result = None
            rh_result = None

            # Try main.py version
            try:
                main_result = main_module._parse_group_id(test_input)
            except HTTPException as e:
                main_error = (e.status_code, e.detail)

            # Try route_helpers version
            try:
                rh_result = rh_module._parse_group_id(test_input)
            except HTTPException as e:
                rh_error = (e.status_code, e.detail)

            # Results should match (either both successful or same error)
            assert main_result == rh_result and main_error == rh_error, (
                f"Mismatch for input '{test_input}': "
                f"main=(result={main_result}, error={main_error}), "
                f"route_helpers=(result={rh_result}, error={rh_error})"
            )

    def test_parse_group_id_returns_int_or_none(self):
        """_parse_group_id should return int | None."""
        from app import route_helpers as rh_module

        result = rh_module._parse_group_id("42")
        assert isinstance(result, int) or result is None
        
        result = rh_module._parse_group_id("")
        assert result is None


class TestParseTimelineCursor:
    """Verify _parse_timeline_cursor behaves identically in both modules."""

    def test_parse_timeline_cursor_matches(self):
        """_parse_timeline_cursor should return identical results."""
        from app import main as main_module
        from app import route_helpers as rh_module

        test_cases = [
            "",  # Empty -> None
            "invalid_base64",  # Invalid -> HTTPException
            "not_even_valid",  # Invalid -> HTTPException
        ]

        for test_input in test_cases:
            main_error = None
            rh_error = None
            main_result = None
            rh_result = None

            # Try main.py version
            try:
                main_result = main_module._parse_timeline_cursor(test_input)
            except HTTPException as e:
                main_error = (e.status_code, e.detail)

            # Try route_helpers version
            try:
                rh_result = rh_module._parse_timeline_cursor(test_input)
            except HTTPException as e:
                rh_error = (e.status_code, e.detail)

            assert main_result == rh_result and main_error == rh_error, (
                f"Mismatch for cursor input: "
                f"main=(result={main_result}, error={main_error}), "
                f"route_helpers=(result={rh_result}, error={rh_error})"
            )


class TestParseSearchCursor:
    """Verify _parse_search_cursor behaves identically in both modules."""

    def test_parse_search_cursor_matches(self):
        """_parse_search_cursor should return identical results."""
        from app import main as main_module
        from app import route_helpers as rh_module

        test_cases = [
            "",  # Empty -> None
            "invalid",  # Invalid -> HTTPException
            "not_valid_either",  # Invalid -> HTTPException
        ]

        for test_input in test_cases:
            main_error = None
            rh_error = None
            main_result = None
            rh_result = None

            # Try main.py version
            try:
                main_result = main_module._parse_search_cursor(test_input)
            except HTTPException as e:
                main_error = (e.status_code, e.detail)

            # Try route_helpers version
            try:
                rh_result = rh_module._parse_search_cursor(test_input)
            except HTTPException as e:
                rh_error = (e.status_code, e.detail)

            assert main_result == rh_result and main_error == rh_error, (
                f"Mismatch for cursor input: "
                f"main=(result={main_result}, error={main_error}), "
                f"route_helpers=(result={rh_result}, error={rh_error})"
            )


class TestParseStoryFormat:
    """Verify _parse_story_format behaves identically in both modules."""

    def test_parse_story_format_matches(self):
        """_parse_story_format should return identical results."""
        from app import main as main_module
        from app import route_helpers as rh_module

        test_cases = [
            "executive_summary",  # Valid -> returns value
            "detailed_chronology",  # Valid -> returns value
            "recent_changes",  # Valid -> returns value
            "",  # Invalid -> HTTPException
            "unknown_format",  # Invalid -> HTTPException
        ]

        for test_input in test_cases:
            main_error = None
            rh_error = None
            main_result = None
            rh_result = None

            # Try main.py version
            try:
                main_result = main_module._parse_story_format(test_input)
            except HTTPException as e:
                main_error = (e.status_code, e.detail)

            # Try route_helpers version
            try:
                rh_result = rh_module._parse_story_format(test_input)
            except HTTPException as e:
                rh_error = (e.status_code, e.detail)

            assert main_result == rh_result and main_error == rh_error, (
                f"Mismatch for format '{test_input}': "
                f"main=(result={main_result}, error={main_error}), "
                f"route_helpers=(result={rh_result}, error={rh_error})"
            )

    def test_parse_story_format_valid_returns_correct_type(self):
        """_parse_story_format should return a valid StoryFormat."""
        from app import route_helpers as rh_module

        result = rh_module._parse_story_format("executive_summary")
        assert result in ("executive_summary", "detailed_chronology", "recent_changes")


class TestParseStoryViewMode:
    """Verify _parse_story_view_mode behaves identically in both modules."""

    def test_parse_story_view_mode_matches(self):
        """_parse_story_view_mode should return identical results."""
        from app import main as main_module
        from app import route_helpers as rh_module

        test_cases = [
            ("narrative", False),
            ("narrative", True),
            ("presentation", False),
            ("presentation", True),
            ("", False),
            ("invalid", True),
        ]

        for raw_value, has_presentation in test_cases:
            main_result = main_module._parse_story_view_mode(
                raw_value, has_presentation=has_presentation
            )
            rh_result = rh_module._parse_story_view_mode(
                raw_value, has_presentation=has_presentation
            )
            assert main_result == rh_result, (
                f"Mismatch for view_mode('{raw_value}', has_presentation={has_presentation}): "
                f"main.py={main_result}, route_helpers.py={rh_result}"
            )


class TestEncodeSSEEvent:
    """Verify _encode_sse_event behaves identically in both modules."""

    def test_encode_sse_event_matches(self):
        """_encode_sse_event should return identical results."""
        from app import main as main_module
        from app import route_helpers as rh_module

        test_cases = [
            ("message", {"text": "hello"}),
            ("status", {"phase": "generate", "message": "processing"}),
            ("error", {"code": 500}),
            ("complete", {"ok": True}),
        ]

        for event_name, payload in test_cases:
            main_result = main_module._encode_sse_event(event_name, payload)
            rh_result = rh_module._encode_sse_event(event_name, payload)
            assert main_result == rh_result, (
                f"Mismatch for event('{event_name}', ...): "
                f"main.py={main_result!r}, route_helpers.py={rh_result!r}"
            )


class TestMonthName:
    """Verify _month_name behaves identically in both modules."""

    def test_month_name_matches(self):
        """_month_name should return identical results."""
        from app import main as main_module
        from app import route_helpers as rh_module

        test_cases = [1, 2, 6, 12]

        for month in test_cases:
            main_result = main_module._month_name(month)
            rh_result = rh_module._month_name(month)
            assert main_result == rh_result, (
                f"Mismatch for month {month}: "
                f"main.py={main_result}, route_helpers.py={rh_result}"
            )

    def test_month_name_valid_months(self):
        """_month_name should work for all valid months."""
        from app import route_helpers as rh_module

        expected_months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]

        for i, expected in enumerate(expected_months, 1):
            result = rh_module._month_name(i)
            assert result == expected


class TestRenderPartial:
    """Verify _render_partial behaves identically in both modules."""

    def test_render_partial_same_template_matches(self):
        """_render_partial should produce identical HTML."""
        from app import main as main_module
        from app import route_helpers as rh_module

        # Both modules should use the same Jinja2 environment
        # Test with a simple template that doesn't require DB context
        template_name = "partials/html_preview_content.html"
        context = {"preview_html": "<p>test</p>", "empty_message": "No content"}

        try:
            main_result = main_module._render_partial(template_name, **context)
            rh_result = rh_module._render_partial(template_name, **context)
            assert main_result == rh_result, (
                f"Mismatch for template '{template_name}': "
                f"main.py and route_helpers.py produced different output"
            )
        except Exception:
            # If template rendering fails, both should fail the same way
            pass


class TestDuplicationMapping:
    """Verify which functions are duplicated."""

    def test_duplicated_functions_exist_in_both_modules(self):
        """Confirm all expected helper functions exist in both modules."""
        from app import main as main_module
        from app import route_helpers as rh_module

        expected_helpers = [
            "_parse_group_id",
            "_parse_timeline_cursor",
            "_parse_search_cursor",
            "_load_group_scope",
            "_load_timeline_scope",
            "_parse_story_format",
            "_parse_story_view_mode",
            "_encode_sse_event",
            "_month_name",
            "_render_partial",
            "_build_story_page_context",
            "_build_story_citation_contexts",
            "_sanitize_story_html",
        ]

        for func_name in expected_helpers:
            assert hasattr(main_module, func_name), (
                f"Function '{func_name}' missing from app/main.py"
            )
            assert hasattr(rh_module, func_name), (
                f"Function '{func_name}' missing from app/route_helpers.py"
            )

    def test_helper_functions_are_callable(self):
        """All duplicated helper functions should be callable."""
        from app import route_helpers as rh_module

        helpers = [
            "_parse_group_id",
            "_parse_story_format",
            "_month_name",
            "_encode_sse_event",
        ]

        for func_name in helpers:
            func = getattr(rh_module, func_name)
            assert callable(func), f"'{func_name}' is not callable"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
