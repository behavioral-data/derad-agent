"""URL-containment audit must scan every free-text field the renderer
quotes — not just tone_neutral_justification + load_bearing_evidence_snippet.

A hallucinated URL in `counter_fact` or a counterpoint summary that passes
audit will then be rejected downstream by `_enforce_invariance`, burning
all retries and surfacing as a pipeline_error instead of a graceful NEI.
"""
from __future__ import annotations

from agent.factcheck.audit import audit
from agent.factcheck.schema import (
    ConsolidatedFindings,
    Counterpoint,
    PresentationPayload,
    RefutedProposition,
    SourceQualityEntry,
    TierRef,
    ChallengedProposition,
)


def _known_url() -> str:
    return "https://reuters.com/known-article"


def _quality_table() -> list[SourceQualityEntry]:
    return [
        SourceQualityEntry(
            url=_known_url(),
            tier="reputable-news",
            tier_source="model-prior",
            rationale="",
        )
    ]


def test_audit_fails_when_counter_fact_cites_unknown_url():
    rogue = "https://hallucinated.example/fabricated"
    findings = ConsolidatedFindings(
        refuted_propositions=(
            RefutedProposition(
                proposition="X is true.",
                refuting_sources=(TierRef(url=_known_url(), tier="reputable-news"),),
                counter_fact="Actually not X.",
                is_central=True,
            ),
        ),
    )
    payload = PresentationPayload(
        headline_finding="Refuted.",
        counter_fact=f"See evidence at {rogue}",
        load_bearing_evidence_snippet="",
    )
    result = audit(
        action="verify",
        declared_outcome="verified_refuted",
        findings=findings,
        source_quality_table=_quality_table(),
        presentation_payload=payload,
        tone_neutral_justification="",
    )
    assert result.passed is False
    assert any(rogue in f for f in result.failures)


def test_audit_fails_when_counterpoint_summary_cites_unknown_url():
    rogue = "https://hallucinated.example/fabricated"
    findings = ConsolidatedFindings(
        challenged_propositions=(
            ChallengedProposition(
                proposition="Y is the best policy.",
                counterpoints=(
                    Counterpoint(
                        summary="A counter.",
                        citing_sources=(TierRef(url=_known_url(), tier="reputable-news"),),
                    ),
                ),
                is_central=True,
            ),
        ),
    )
    payload = PresentationPayload(
        headline_finding="Pushback.",
        counterpoints=(
            Counterpoint(
                summary=f"Counter argument referencing {rogue}.",
                citing_sources=(TierRef(url=_known_url(), tier="reputable-news"),),
            ),
        ),
    )
    result = audit(
        action="challenge_opinion",
        declared_outcome="challenged",
        findings=findings,
        source_quality_table=_quality_table(),
        presentation_payload=payload,
        tone_neutral_justification="",
    )
    assert result.passed is False
    assert any(rogue in f for f in result.failures)
