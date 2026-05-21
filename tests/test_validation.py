"""Tests for shared/validation.py."""

import pytest
from derad_agent.shared.validation import (
    validate_agent_inputs,
    validate_search_queries,
)


class TestValidateAgentInputs:
    def test_valid(self, mock_user_dir):
        validate_agent_inputs("What does user think?", mock_user_dir)

    def test_empty_statement_raises(self, mock_user_dir):
        with pytest.raises(ValueError, match="Statement cannot be empty"):
            validate_agent_inputs("", mock_user_dir)

    def test_whitespace_only_statement_raises(self, mock_user_dir):
        with pytest.raises(ValueError, match="Statement cannot be empty"):
            validate_agent_inputs("   ", mock_user_dir)

class TestValidateSearchQueries:
    def test_valid(self):
        assert validate_search_queries(["query one", "query two"]) == ["query one", "query two"]

    def test_filters_too_short(self):
        result = validate_search_queries(["ok", "This is valid", "ab"], min_queries=1)
        assert result == ["This is valid"]

    def test_too_few_raises(self):
        with pytest.raises(ValueError, match="At least"):
            validate_search_queries(["ab"], min_queries=1)

    def test_truncates_to_max(self):
        queries = [f"query number {i}" for i in range(10)]
        assert len(validate_search_queries(queries, max_queries=3)) == 3

    def test_sanitizes_whitespace(self):
        assert validate_search_queries(["  hello   world  "]) == ["hello world"]
