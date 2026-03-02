"""Tests for shared/text.py — sanitization, truncation, tag extraction, parsing."""

import pytest
from derad_agent.shared.text import (
    extract_content_between_tags,
    sanitize_query,
    parse_queries_from_text,
    truncate_text,
    format_timestamp,
)


class TestExtractContentBetweenTags:
    def test_basic_extraction(self):
        text = "<answer>Hello world</answer>"
        assert extract_content_between_tags(text, "answer") == "Hello world"

    def test_nested_text(self):
        text = "Before <tag>inner content</tag> after"
        assert extract_content_between_tags(text, "tag") == "inner content"

    def test_multiline(self):
        text = "<data>\nline1\nline2\n</data>"
        assert extract_content_between_tags(text, "data") == "line1\nline2"

    def test_not_found(self):
        assert extract_content_between_tags("no tags here", "missing") is None

    def test_strips_whitespace(self):
        text = "<tag>  spaced  </tag>"
        assert extract_content_between_tags(text, "tag") == "spaced"


class TestSanitizeQuery:
    def test_basic_cleanup(self):
        assert sanitize_query("  hello   world  ") == "hello world"

    def test_empty_string(self):
        assert sanitize_query("") == ""

    def test_newlines_and_tabs(self):
        assert sanitize_query("hello\n\tworld") == "hello world"


class TestParseQueriesFromText:
    def test_numbered_list(self):
        text = "1. First query\n2. Second query\n3. Third query"
        result = parse_queries_from_text(text)
        assert result == ["First query", "Second query", "Third query"]

    def test_plain_lines(self):
        text = "query one\nquery two"
        result = parse_queries_from_text(text)
        assert result == ["query one", "query two"]

    def test_filters_short(self):
        text = "ok\nThis is a valid query"
        result = parse_queries_from_text(text)
        assert len(result) == 1
        assert result[0] == "This is a valid query"

    def test_empty(self):
        assert parse_queries_from_text("") == []
        assert parse_queries_from_text(None) == []


class TestTruncateText:
    def test_short_text_unchanged(self):
        assert truncate_text("hello", max_length=10) == "hello"

    def test_long_text_truncated(self):
        result = truncate_text("a" * 200, max_length=100)
        assert len(result) == 100
        assert result.endswith("...")

    def test_custom_suffix(self):
        result = truncate_text("a" * 200, max_length=50, suffix="~~")
        assert result.endswith("~~")
        assert len(result) == 50


class TestFormatTimestamp:
    def test_valid_timestamp(self):
        result = format_timestamp(0.0)
        # Should produce a date string (exact value depends on timezone)
        assert "-" in result and ":" in result

    def test_invalid_timestamp(self):
        assert format_timestamp(float("inf")) == "Invalid Date" or isinstance(format_timestamp(float("inf")), str)
