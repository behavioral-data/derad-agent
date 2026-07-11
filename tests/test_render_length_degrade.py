"""render() must not fail a whole generation because a reply is marginally over
the *soft* length ceiling. When the only unmet invariant is length, it returns
its shortest attempt (still bounded by the platform limit). URL/refusal
violations, which are not stylistic, must still hard-fail.
"""
from __future__ import annotations

import pytest

from agent.factcheck import render as render_module
from agent.factcheck.render import RenderedReply, render
from agent.factcheck.freeze import RendererView
from agent.factcheck.schema import PresentationPayload


def _view() -> RendererView:
    return RendererView(
        presentation_payload=PresentationPayload(
            headline_finding="The claim is supported by reporting.",
        ),
        tone_neutral_justification="Reputable reporting confirms the claim.",
        action="verify",
        action_outcome="verified_supported",
    )


def test_over_length_only_degrades_to_shortest_attempt(monkeypatch):
    # Clean prose (no URL, not a refusal) but always over the tight cap (320).
    over = ("evidence " * 60).strip()   # ~500 chars

    def _fake(*, prompt, schema, system, reasoning_effort, max_tokens, timeout):
        return RenderedReply(text=over)

    monkeypatch.setattr(render_module, "call_claude_json", _fake)
    out = render(_view(), tone="neutral", length_key="tight")
    assert out == over   # returned despite exceeding the soft cap, not raised


def test_url_violation_still_hard_fails(monkeypatch):
    # A URL in the body is a real invariant, not a stylistic cap — must raise.
    def _fake(*, prompt, schema, system, reasoning_effort, max_tokens, timeout):
        return RenderedReply(text="See https://example.com/x for the details.")

    monkeypatch.setattr(render_module, "call_claude_json", _fake)
    with pytest.raises(ValueError):
        render(_view(), tone="neutral", length_key="short")
