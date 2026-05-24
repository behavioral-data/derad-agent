"""Unit tests for step_filter_notes_by_relevance."""

import json
import pytest

from agent.runtime.steps.relevance_filter import step_filter_notes_by_relevance


def _note(note_id, summary="some text"):
    return {
        "note_id": note_id,
        "tweet_id": f"t_{note_id}",
        "summary": summary,
        "classification": "MISINFORMED_OR_POTENTIALLY_MISLEADING",
        "current_status": "CURRENTLY_RATED_HELPFUL",
        "created_at_millis": 1000,
        "similarity": 0.8,
    }


def _make_llm_response(keep_ids):
    """Return a fake chain whose .invoke() yields a keep_note_ids response."""

    class _FakeResponse:
        content = json.dumps({"keep_note_ids": keep_ids})

    class _FakeChain:
        def invoke(self, _inputs):
            return _FakeResponse()

    return _FakeChain()


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_filter_returns_subset(monkeypatch):
    notes = [_note("n1"), _note("n2"), _note("n3")]
    chain = _make_llm_response(["n1", "n3"])

    monkeypatch.setattr(
        "agent.runtime.steps.relevance_filter.get_llm",
        lambda **_: object(),  # not used directly; chain is patched below
    )
    monkeypatch.setattr(
        "agent.runtime.steps.relevance_filter.get_relevance_filter_prompt",
        lambda: type("P", (), {"__or__": lambda self, _: chain})(),
    )

    result = step_filter_notes_by_relevance("the claim", notes)
    assert [n["note_id"] for n in result] == ["n1", "n3"]


def test_filter_malformed_json_returns_all_notes(monkeypatch):
    notes = [_note("n1"), _note("n2")]

    class _BadResponse:
        content = "this is not json at all !!!"

    class _BadChain:
        def invoke(self, _inputs):
            return _BadResponse()

    monkeypatch.setattr(
        "agent.runtime.steps.relevance_filter.get_llm",
        lambda **_: object(),
    )
    monkeypatch.setattr(
        "agent.runtime.steps.relevance_filter.get_relevance_filter_prompt",
        lambda: type("P", (), {"__or__": lambda self, _: _BadChain()})(),
    )

    result = step_filter_notes_by_relevance("the claim", notes)
    assert {n["note_id"] for n in result} == {"n1", "n2"}


def test_filter_empty_input_skips_llm_call(monkeypatch):
    called = []

    monkeypatch.setattr(
        "agent.runtime.steps.relevance_filter.get_llm",
        lambda **_: called.append(True) or object(),
    )

    result = step_filter_notes_by_relevance("the claim", [])
    assert result == []
    assert not called


def test_filter_hallucinated_ids_are_dropped(monkeypatch):
    notes = [_note("n1"), _note("n2")]
    # LLM returns one real ID and one invented ID
    chain = _make_llm_response(["n1", "ghost_id_999"])

    monkeypatch.setattr(
        "agent.runtime.steps.relevance_filter.get_llm",
        lambda **_: object(),
    )
    monkeypatch.setattr(
        "agent.runtime.steps.relevance_filter.get_relevance_filter_prompt",
        lambda: type("P", (), {"__or__": lambda self, _: chain})(),
    )

    result = step_filter_notes_by_relevance("the claim", notes)
    assert [n["note_id"] for n in result] == ["n1"]
