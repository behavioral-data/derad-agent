"""Tests for post_selection/post_assignment.py (participant→posts assignment)."""
from __future__ import annotations

import importlib.util
import pathlib
import random

import pytest

# post_selection/ is not a package; load the module directly from its path.
_MOD = pathlib.Path(__file__).resolve().parent.parent / "post_selection" / "post_assignment.py"
_spec = importlib.util.spec_from_file_location("post_assignment", _MOD)
post_assignment = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(post_assignment)
assign = post_assignment.assign


def test_returns_requested_count():
    out = assign({"a": 0, "b": 0, "c": 0, "d": 0}, 2)
    assert len(out) == 2
    assert set(out) <= {"a", "b", "c", "d"}


def test_favors_lower_assignment_counts():
    # 'c' already assigned 5x; the two zero-count posts must be chosen first.
    out = assign({"a": 0, "b": 0, "c": 5}, 2)
    assert set(out) == {"a", "b"}


def test_partial_fill_spills_into_next_bucket():
    random.seed(0)
    out = assign({"a": 0, "b": 1, "c": 1}, 2)
    assert "a" in out                 # the only count-0 post is always taken
    assert len(out) == 2
    assert out[1] in {"b", "c"}        # second comes from the count-1 bucket


def test_includes_max_count_posts_when_needed():
    # Regression: when every post is at the max count, they must still be
    # assignable (the old range(max_count) dropped these and KeyError'd).
    out = assign({"a": 3, "b": 3, "c": 3}, 2)
    assert len(out) == 2
    assert set(out) <= {"a", "b", "c"}


def test_assign_all_posts():
    out = assign({"a": 0, "b": 2, "c": 1}, 3)
    assert set(out) == {"a", "b", "c"}


def test_raises_when_more_requested_than_available():
    with pytest.raises(ValueError):
        assign({"a": 0, "b": 0}, 3)
