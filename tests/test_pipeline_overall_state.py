"""ActionOutcome -> OverallState mapping.

The Stage-6 freeze stamps `overall_state` so downstream analytics can
distinguish runs that produced a usable, evidence-backed result from
runs that fell through to NEI / unavailable / declined. The mapping
itself is small and pure — test it in isolation rather than spinning
the full pipeline.
"""
from __future__ import annotations

import typing

import pytest

from agent.factcheck.pipeline import _overall_state_for
from agent.factcheck.schema import ActionOutcome


_CHECKED = {
    "verified_supported",
    "verified_refuted",
    "verified_conflicting",
    "context_provided",
    "challenged",
    "perspectives_surfaced",
}

_NO_CHECKABLE = {
    "verified_nei",
    "context_unavailable",
    "challenge_unavailable",
    "perspectives_insufficient",
    "declined",
}


@pytest.mark.parametrize("outcome", sorted(_CHECKED))
def test_checked_outcomes_map_to_checked(outcome: ActionOutcome):
    assert _overall_state_for(outcome) == "checked"


@pytest.mark.parametrize("outcome", sorted(_NO_CHECKABLE))
def test_no_usable_result_outcomes_map_to_no_checkable_claim(outcome: ActionOutcome):
    assert _overall_state_for(outcome) == "no_checkable_claim"


def test_mapping_covers_every_declared_action_outcome():
    """Guard against drift: if schema.py adds a new ActionOutcome literal,
    this test fails until the mapping (and the case lists above) are updated."""
    declared = set(typing.get_args(ActionOutcome))
    covered = _CHECKED | _NO_CHECKABLE
    assert declared == covered, f"uncovered outcomes: {declared ^ covered}"
