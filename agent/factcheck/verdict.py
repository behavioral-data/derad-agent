"""Stage 4.5.4 — structural verdict-label emission rule.

Purely structural so the label is auditable from `consolidated_findings` and
`source_quality_table` alone. Section 4.5.4 of agent_design.md.
"""
from __future__ import annotations

from .schema import ConsolidatedFindings, SourceQualityEntry, SourceTier, Verdict


_RELIABLE_TIERS: frozenset[SourceTier] = frozenset({"fact-checker", "reputable-news"})
_RELIABLE_THRESHOLD = 2


def _tier_lookup(table: list[SourceQualityEntry]) -> dict[str, SourceTier]:
    return {entry.url: entry.tier for entry in table}


def _count_reliable(urls: list[str], tier_by_url: dict[str, SourceTier]) -> int:
    return sum(1 for u in urls if tier_by_url.get(u, "unknown") in _RELIABLE_TIERS)


def derive_verdict(
    findings: ConsolidatedFindings,
    source_quality_table: list[SourceQualityEntry],
) -> Verdict:
    """Apply the structural rule. Returns one of the four labels."""
    tier_by_url = _tier_lookup(source_quality_table)

    central_refuted = next((p for p in findings.refuted_propositions if p.is_central), None)
    if central_refuted is not None:
        urls = [s.url for s in central_refuted.refuting_sources]
        if _count_reliable(urls, tier_by_url) >= _RELIABLE_THRESHOLD:
            return "Refuted"

    central_verified = next((p for p in findings.verified_propositions if p.is_central), None)
    if central_verified is not None:
        urls = [s.url for s in central_verified.supporting_sources]
        if _count_reliable(urls, tier_by_url) >= _RELIABLE_THRESHOLD:
            return "Supported"

    central_disputed = next((p for p in findings.disputed_propositions if p.is_central), None)
    if central_disputed is not None:
        for_count = _count_reliable(
            [s.url for s in central_disputed.sources_for], tier_by_url
        )
        against_count = _count_reliable(
            [s.url for s in central_disputed.sources_against], tier_by_url
        )
        # Design §4.5.4: Conflicting requires the source-quality table to fail
        # to resolve the dispute. If one side has ≥2 reliable sources and the
        # other does not, the table has resolved it — promote to Refuted/Supported.
        if for_count >= _RELIABLE_THRESHOLD and against_count >= _RELIABLE_THRESHOLD:
            return "Conflicting"
        if against_count >= _RELIABLE_THRESHOLD and for_count < _RELIABLE_THRESHOLD:
            return "Refuted"
        if for_count >= _RELIABLE_THRESHOLD and against_count < _RELIABLE_THRESHOLD:
            return "Supported"

    return "NotEnoughEvidence"
