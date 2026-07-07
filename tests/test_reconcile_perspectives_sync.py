"""Regression test: consolidated_findings.perspectives must not diverge
from presentation_payload.perspectives after reconcile().

The Stage-5 audit shape check and the Stage-7 renderer read
`presentation_payload.perspectives`, while `derive_action_outcome` (via
`_perspectives_outcome`) reads `consolidated_findings.perspectives`. The LLM
emits both as independent copies of the same data, so it can under-populate
one relative to the other. When that happens the outcome collapses to
`perspectives_insufficient` even though the payload (and therefore the
rendered reply) actually carries ≥2 well-cited perspectives.
"""
from __future__ import annotations

from agent.factcheck.context import PipelineContext
from agent.factcheck.reconcile import ReconciliationOutput
from agent.factcheck.schema import (
    ConsolidatedFindings,
    Lens1,
    Perspective,
    PresentationPayload,
    SourceQualityEntry,
    TierRef,
    UnaddressedProposition,
)
from agent.factcheck.verdict import derive_action_outcome


_URL_A = "https://reuters.com/article-a"
_URL_B = "https://apnews.com/article-b"


def _well_cited_perspectives() -> tuple[Perspective, ...]:
    return (
        Perspective(
            label="Pro view",
            summary="Argues the policy works, citing reputable reporting.",
            citing_sources=(TierRef(url=_URL_A, tier="reputable-news"),),
        ),
        Perspective(
            label="Con view",
            summary="Argues the policy fails, citing reputable reporting.",
            citing_sources=(TierRef(url=_URL_B, tier="reputable-news"),),
        ),
    )


def _quality_table() -> list[SourceQualityEntry]:
    return [
        SourceQualityEntry(url=_URL_A, tier="reputable-news", tier_source="model-prior", rationale=""),
        SourceQualityEntry(url=_URL_B, tier="reputable-news", tier_source="model-prior", rationale=""),
    ]


def _under_populated_output() -> ReconciliationOutput:
    """presentation_payload is fully populated with 2 well-cited
    perspectives; consolidated_findings.perspectives is left empty (the
    LLM under-populated the findings copy while fully populating the
    payload copy)."""
    return ReconciliationOutput(
        lens_1=Lens1(narrative="n/a"),
        consolidated_findings=ConsolidatedFindings(
            unaddressed_propositions=(
                UnaddressedProposition(
                    proposition="the topic statement",
                    reason="evidence retrieved but silent",
                    is_central=True,
                ),
            ),
            perspectives=(),  # under-populated relative to presentation_payload
        ),
        presentation_payload=PresentationPayload(
            headline_finding="The topic is contested.",
            counter_fact=None,
            primary_sources_to_cite=(),
            load_bearing_evidence_snippet="",
            perspectives=_well_cited_perspectives(),
        ),
        tone_neutral_justification="n/a",
        evidence_stances=[],
    )


def test_reconcile_mirrors_payload_perspectives_into_findings(monkeypatch):
    # Fetch the module (rather than using a top-of-file `from ... import
    # reconcile`) and call `reconcile_module.reconcile(...)` below — some
    # other test in the suite (test_reconcile_metrics.py) pops and
    # re-imports `agent.factcheck.reconcile` at runtime, which would leave
    # a module-level import bound to a stale module object whose
    # `call_claude_json` global this patch never touches.
    from agent.factcheck import reconcile as reconcile_module

    monkeypatch.setattr(
        reconcile_module,
        "call_claude_json",
        lambda **_: _under_populated_output(),
    )

    out = reconcile_module.reconcile(
        central_claim_text="some contested topic",
        evidence=[],
        source_quality_table=_quality_table(),
        ctx=PipelineContext(),
        action="surface_perspectives",
    )

    # consolidated_findings must now match presentation_payload — the two
    # copies can no longer diverge.
    assert out.consolidated_findings.perspectives == out.presentation_payload.perspectives
    assert len(out.consolidated_findings.perspectives) == 2


def test_perspectives_outcome_surfaced_despite_under_populated_findings(monkeypatch):
    """End-to-end: a frozen verdict built from an under-populated
    consolidated_findings.perspectives but a fully populated
    presentation_payload.perspectives yields a 'surfaced' outcome, not
    perspectives_insufficient."""
    # Fetch the module (rather than using a top-of-file `from ... import
    # reconcile`) and call `reconcile_module.reconcile(...)` below — some
    # other test in the suite (test_reconcile_metrics.py) pops and
    # re-imports `agent.factcheck.reconcile` at runtime, which would leave
    # a module-level import bound to a stale module object whose
    # `call_claude_json` global this patch never touches.
    from agent.factcheck import reconcile as reconcile_module

    monkeypatch.setattr(
        reconcile_module,
        "call_claude_json",
        lambda **_: _under_populated_output(),
    )

    table = _quality_table()
    out = reconcile_module.reconcile(
        central_claim_text="some contested topic",
        evidence=[],
        source_quality_table=table,
        ctx=PipelineContext(),
        action="surface_perspectives",
    )

    outcome = derive_action_outcome("surface_perspectives", out.consolidated_findings, table)
    assert outcome == "perspectives_surfaced"
