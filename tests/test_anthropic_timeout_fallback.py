"""Regression test: anthropic.APITimeoutError must be caught by the same
degraded-fallback handlers that catch ValueError/TimeoutError at every
Claude call site in the pipeline.

anthropic.APITimeoutError's MRO is:
    APITimeoutError -> APIConnectionError -> APIError -> AnthropicError -> Exception
It is NOT a subclass of TimeoutError (nor ValueError), so a bare
`except (ValueError, TimeoutError)` lets a Claude latency spike escape and
kill the whole mention instead of taking the intended graceful-degrade path.

Covers all five sites: reconcile.py, extract.py, verify.py, sources.py,
multimodal.py.
"""
from __future__ import annotations

import httpx
import anthropic

from agent.factcheck.context import PipelineContext


def _timeout_error() -> anthropic.APITimeoutError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APITimeoutError(request=request)


def test_reconcile_degrades_on_anthropic_timeout(monkeypatch):
    from agent.factcheck import reconcile as reconcile_module

    monkeypatch.setattr(
        reconcile_module,
        "call_claude_json",
        lambda **_: (_ for _ in ()).throw(_timeout_error()),
    )

    out = reconcile_module.reconcile(
        central_claim_text="some claim",
        evidence=[],
        source_quality_table=[],
        ctx=PipelineContext(),
    )

    # Graceful degrade, not a propagated exception: central claim lands in
    # unaddressed_propositions with no fabricated sources.
    assert len(out.consolidated_findings.unaddressed_propositions) == 1
    assert out.consolidated_findings.unaddressed_propositions[0].is_central is True
    assert out.presentation_payload.primary_sources_to_cite == ()


def test_extract_claims_degrades_on_anthropic_timeout(monkeypatch):
    from agent.factcheck import extract as extract_module

    monkeypatch.setattr(
        extract_module,
        "call_claude_json",
        lambda **_: (_ for _ in ()).throw(_timeout_error()),
    )

    out = extract_module.extract_claims("some tweet text", PipelineContext())

    # Degrades to the whole-tweet fallback: one central verifiable claim.
    assert out.action == "verify"
    assert len(out.claims) == 1
    assert out.claims[0].is_central is True
    assert out.claims[0].text == "some tweet text"


def test_verify_decide_next_degrades_on_anthropic_timeout(monkeypatch):
    from agent.factcheck import verify as verify_module

    monkeypatch.setattr(
        verify_module,
        "call_claude_json",
        lambda **_: (_ for _ in ()).throw(_timeout_error()),
    )

    decision = verify_module._decide_next(
        claim_text="some claim",
        action="verify",
        history=[],
        tweet_context=None,
        image_summaries=None,
    )

    # Graceful stop signal (None), not a propagated exception.
    assert decision is None


def test_sources_model_prior_degrades_on_anthropic_timeout(monkeypatch):
    from agent.factcheck import llm as llm_module
    from agent.factcheck import sources as sources_module

    # `_classify_via_model_batch` does `from .llm import call_claude_json`
    # *inside* the function body, so the patch target is the `llm` module
    # attribute, not a name imported into `sources`.
    monkeypatch.setattr(
        llm_module,
        "call_claude_json",
        lambda **_: (_ for _ in ()).throw(_timeout_error()),
    )

    result = sources_module._classify_via_model_batch(["totally-unclassified-domain.example"])

    # Graceful degrade: domain stays unclassified, no exception propagated.
    assert result == {}


def test_multimodal_extract_image_degrades_on_anthropic_timeout(monkeypatch):
    from agent.factcheck import multimodal as multimodal_module
    from agent.factcheck.search import StubSearchBackend

    monkeypatch.setattr(
        multimodal_module,
        "fetch_image_bytes",
        lambda url: (b"\xff\xd8\xff\xe0fake-jpeg-bytes", "image/jpeg"),
    )

    class _RaisingLLM:
        def invoke(self, *_args, **_kwargs):
            raise _timeout_error()

    monkeypatch.setattr(multimodal_module, "get_llm", lambda **_kwargs: _RaisingLLM())

    result = multimodal_module.extract_image(
        "https://example.com/photo.jpg", search_backend=StubSearchBackend()
    )

    # Graceful degrade (None), not a propagated exception.
    assert result is None
