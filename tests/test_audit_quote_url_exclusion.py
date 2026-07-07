"""Regression test: an incidental URL inside a verbatim evidence quote
must not fail the Stage-5 audit, while a fabricated URL in an actual
citation field still must.

`load_bearing_evidence_snippet` and `tone_neutral_justification` carry
human-quote / reasoning narrative, not source citations. A legitimate
verbatim quote can incidentally contain a URL that was never a retrieved
source (e.g. the quoted text itself mentions a link). That must not force
`audit.passed = False` and collapse a valid sourced verdict into a
'no credible coverage' NEI outcome.
"""
from __future__ import annotations

from agent.factcheck.audit import audit
from agent.factcheck.schema import (
    CitableSource,
    ConsolidatedFindings,
    PresentationPayload,
    SourceQualityEntry,
    TierRef,
    VerifiedProposition,
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


def _verified_findings() -> ConsolidatedFindings:
    return ConsolidatedFindings(
        verified_propositions=(
            VerifiedProposition(
                proposition="X is true.",
                supporting_sources=(TierRef(url=_known_url(), tier="reputable-news"),),
                is_central=True,
            ),
        ),
    )


def test_incidental_url_in_evidence_quote_passes_audit():
    """A verbatim quote in load_bearing_evidence_snippet that happens to
    contain a URL never retrieved as a source must not fail the audit."""
    incidental_url = "https://example.com/mentioned-in-the-quote"
    payload = PresentationPayload(
        headline_finding="The claim is supported by reporting.",
        primary_sources_to_cite=(CitableSource(url=_known_url(), display_name="Reuters"),),
        load_bearing_evidence_snippet=(
            f'"...for more, see {incidental_url}," the spokesperson said.'
        ),
    )
    result = audit(
        action="verify",
        declared_outcome="verified_supported",
        findings=_verified_findings(),
        source_quality_table=_quality_table(),
        presentation_payload=payload,
        tone_neutral_justification=(
            f"The spokesperson's quote references {incidental_url}, but that "
            "is not itself a cited source."
        ),
    )
    assert result.passed is True
    assert result.failures == []


def test_fabricated_url_in_citation_field_still_fails_audit():
    """A fabricated URL placed in the ACTUAL citation field
    (primary_sources_to_cite) must still fail the audit — the quote-field
    exclusion must not weaken the no-fabricated-citations guarantee."""
    fabricated_url = "https://hallucinated.example/fake-source"
    payload = PresentationPayload(
        headline_finding="The claim is supported by reporting.",
        primary_sources_to_cite=(
            CitableSource(url=fabricated_url, display_name="Fake Source"),
        ),
        load_bearing_evidence_snippet="A clean quote with no URLs at all.",
    )
    result = audit(
        action="verify",
        declared_outcome="verified_supported",
        findings=_verified_findings(),
        source_quality_table=_quality_table(),
        presentation_payload=payload,
        tone_neutral_justification="Clean justification text.",
    )
    assert result.passed is False
    assert any(fabricated_url in f for f in result.failures)
