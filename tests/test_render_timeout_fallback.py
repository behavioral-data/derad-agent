"""Regression test: a transient Claude timeout on render()'s pass 1 must
fall through to the pass-2 refusal-nudge retry instead of propagating out
of render() and dropping an already-computed reply.

anthropic.APITimeoutError does NOT subclass TimeoutError or ValueError (its
MRO is APITimeoutError -> APIConnectionError -> APIError -> AnthropicError
-> Exception), so a bare `except ValueError` on pass 1 lets it escape.
"""
from __future__ import annotations

import httpx
import pytest
import anthropic

from agent.factcheck import render as render_module
from agent.factcheck.render import RenderedReply, render
from agent.factcheck.freeze import RendererView
from agent.factcheck.schema import PresentationPayload


def _timeout_error() -> anthropic.APITimeoutError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APITimeoutError(request=request)


def _view() -> RendererView:
    return RendererView(
        presentation_payload=PresentationPayload(
            headline_finding="The claim is supported by reporting.",
        ),
        tone_neutral_justification="Reputable reporting confirms the claim.",
        action="verify",
        action_outcome="verified_supported",
    )


def test_pass1_timeout_falls_through_to_pass2_retry(monkeypatch):
    calls: list[str] = []

    def _fake_call_claude_json(*, prompt, schema, system, reasoning_effort, max_tokens, timeout):
        calls.append(prompt)
        if len(calls) == 1:
            # Pass 1: simulate a Claude latency spike.
            raise _timeout_error()
        # Pass 2 (refusal-nudge retry): succeeds.
        return RenderedReply(text="A concise, well-sourced reply.")

    monkeypatch.setattr(render_module, "call_claude_json", _fake_call_claude_json)

    text = render(_view(), tone="neutral")

    assert text == "A concise, well-sourced reply."
    # Exactly two calls: pass-1 (timed out) + pass-2 (refusal nudge, succeeded).
    assert len(calls) == 2


def test_pass1_timeout_without_pass2_recovery_raises_the_pass1_error(monkeypatch):
    """If pass 2 also fails, render() must still surface the pass-1 error
    (not swallow it) — confirms the broadened except doesn't mask failures."""

    def _always_times_out(**_kwargs):
        raise _timeout_error()

    monkeypatch.setattr(render_module, "call_claude_json", _always_times_out)

    with pytest.raises(anthropic.APITimeoutError):
        render(_view(), tone="neutral")
