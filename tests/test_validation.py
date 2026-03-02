"""Tests for shared/validation.py — timestamp validation, agent inputs, search queries."""

import pytest
from pathlib import Path
from derad_agent.shared.validation import (
    validate_timestamp,
    validate_timestamp_millis,
    validate_agent_inputs,
    validate_search_queries,
)


class TestValidateTimestamp:
    def test_float(self):
        assert validate_timestamp(1234.5) == 1234.5

    def test_int(self):
        assert validate_timestamp(1234) == 1234.0

    def test_string_number(self):
        assert validate_timestamp("1234.5") == 1234.5

    def test_none(self):
        assert validate_timestamp(None) is None

    def test_empty_string(self):
        assert validate_timestamp("") is None

    def test_null_string(self):
        assert validate_timestamp("null") is None
        assert validate_timestamp("None") is None

    def test_invalid_string(self):
        assert validate_timestamp("not_a_number") is None

    def test_whitespace_string(self):
        assert validate_timestamp("  1234  ") == 1234.0

    def test_timestamp_millis_conversion(self):
        assert validate_timestamp_millis("1713978050878") == 1713978050.878


class TestValidateAgentInputs:
    def test_valid_statement_input(self, mock_user_dir):
        validate_agent_inputs("What does user think?", mock_user_dir)

    def test_empty_statement_raises(self, mock_user_dir):
        with pytest.raises(ValueError, match="Statement cannot be empty"):
            validate_agent_inputs("", mock_user_dir)

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            validate_agent_inputs("q", tmp_path / "nonexistent")


class TestValidateSearchQueries:
    def test_valid_queries(self):
        result = validate_search_queries(["query one", "query two"])
        assert result == ["query one", "query two"]

    def test_filters_short_queries(self):
        result = validate_search_queries(["ok", "This is valid", "ab"], min_queries=1)
        assert result == ["This is valid"]

    def test_too_few_raises(self):
        with pytest.raises(ValueError, match="At least"):
            validate_search_queries(["ab"], min_queries=1)

    def test_truncates_to_max(self):
        queries = [f"query number {i}" for i in range(10)]
        result = validate_search_queries(queries, max_queries=3)
        assert len(result) == 3

    def test_sanitizes_whitespace(self):
        result = validate_search_queries(["  hello   world  "])
        assert result == ["hello world"]
