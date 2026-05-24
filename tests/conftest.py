"""Shared fixtures for agent tests."""

import pytest


@pytest.fixture
def mock_user_dir(tmp_path):
    """A temporary directory pretending to be an index directory."""
    user_dir = tmp_path / "test_user"
    user_dir.mkdir()
    return user_dir
