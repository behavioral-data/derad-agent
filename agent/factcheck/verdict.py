"""Stage 4.5.4 — structural outcome-emission rule (per action).

`derive_action_outcome` is the canonical post-Stage-4.5 function. It maps
the chosen action + the consolidated findings + the source-quality table
to one `ActionOutcome` literal. The renderer template then branches on
(action, outcome) — never on free-form text.

`derive_verdict` is retained as a thin wrapper that returns the legacy
`Verdict` literal for verify outcomes only; downstream code that hasn't
been migrated to ActionOutcome can still call it transitionally.
"""
from __future__ import annotations

from agent.shared.text import canonicalize_url

from .schema import (
    Action,
    ActionOutcome,
    ConsolidatedFindings,
    SourceQualityEntry,
    SourceTier,
    Verdict,
)


_RELIABLE_TIERS: frozenset[SourceTier] = frozenset({"fact-checker", "reputable-news", "primary-source"})
_RELIABLE_THRESHOLD = 2


def _tier_lookup(table: list[SourceQualityEntry]) -> dict[str, SourceTier]:
    return {canonicalize_url(entry.url): entry.tier for entry in table}


def _count_reliable(urls: list[str], tier_by_url: dict[str, SourceTier]) -> int:
    distinct = {canonicalize_url(u) for u in urls}
    return sum(1 for u in distinct if tier_by_url.get(u, "unknown") in _RELIABLE_TIERS)


def _verify_outcome(
    findings: ConsolidatedFindings,
    tier_by_url: dict[str, SourceTier],
) -> ActionOutcome:
    """Verify-mode outcome: derived from verified / refuted / disputed buckets.
    Maps 1:1 onto the legacy Verdict literal."""
    central_refuted = next((p for p in findings.refuted_propositions if p.is_central), None)
    if central_refuted is not None:
        urls = [s.url for s in central_refuted.refuting_sources]
        if _count_reliable(urls, tier_by_url) >= _RELIABLE_THRESHOLD:
            return "verified_refuted"

    central_verified = next((p for p in findings.verified_propositions if p.is_central), None)
    if central_verified is not None:
        urls = [s.url for s in central_verified.supporting_sources]
        if _count_reliable(urls, tier_by_url) >= _RELIABLE_THRESHOLD:
            return "verified_supported"

    central_disputed = next((p for p in findings.disputed_propositions if p.is_central), None)
    if central_disputed is not None:
        for_count = _count_reliable([s.url for s in central_disputed.sources_for], tier_by_url)
        against_count = _count_reliable([s.url for s in central_disputed.sources_against], tier_by_url)
        # Conflicting requires both sides to have ≥2 reliable sources; if
        # one side dominates the table has resolved the dispute.
        if for_count >= _RELIABLE_THRESHOLD and against_count >= _RELIABLE_THRESHOLD:
            return "verified_conflicting"
        if against_count >= _RELIABLE_THRESHOLD and for_count < _RELIABLE_THRESHOLD:
            return "verified_refuted"
        if for_count >= _RELIABLE_THRESHOLD and against_count < _RELIABLE_THRESHOLD:
            return "verified_supported"

    return "verified_nei"


def _context_outcome(
    findings: ConsolidatedFindings,
    tier_by_url: dict[str, SourceTier],
) -> ActionOutcome:
    central = next((c for c in findings.contextual_findings if c.is_central), None)
    if central is None:
        return "context_unavailable"
    urls = [s.url for s in central.citing_sources]
    if _count_reliable(urls, tier_by_url) >= _RELIABLE_THRESHOLD:
        return "context_provided"
    return "context_unavailable"


def _challenge_outcome(
    findings: ConsolidatedFindings,
    tier_by_url: dict[str, SourceTier],
) -> ActionOutcome:
    central = next((p for p in findings.challenged_propositions if p.is_central), None)
    if central is None or not central.counterpoints:
        return "challenge_unavailable"
    # At least one counterpoint backed by ≥1 reliable source.
    for cp in central.counterpoints:
        urls = [s.url for s in cp.citing_sources]
        if _count_reliable(urls, tier_by_url) >= 1:
            return "challenged"
    return "challenge_unavailable"


def _perspectives_outcome(
    findings: ConsolidatedFindings,
    tier_by_url: dict[str, SourceTier],
) -> ActionOutcome:
    # ≥2 distinct perspectives each with ≥1 reliable source.
    backed = 0
    for p in findings.perspectives:
        urls = [s.url for s in p.citing_sources]
        if _count_reliable(urls, tier_by_url) >= 1:
            backed += 1
    if backed >= 2:
        return "perspectives_surfaced"
    return "perspectives_insufficient"


def derive_action_outcome(
    action: Action,
    findings: ConsolidatedFindings,
    source_quality_table: list[SourceQualityEntry],
) -> ActionOutcome:
    """Structural rule that maps (action, findings, source_quality_table)
    to an ActionOutcome. Stage 5 audit re-derives this and forces an
    `_unavailable` / `_nei` outcome when the declared outcome disagrees.
    """
    tier_by_url = _tier_lookup(source_quality_table)
    if action == "verify":
        return _verify_outcome(findings, tier_by_url)
    if action == "provide_context":
        return _context_outcome(findings, tier_by_url)
    if action == "challenge_opinion":
        return _challenge_outcome(findings, tier_by_url)
    if action == "surface_perspectives":
        return _perspectives_outcome(findings, tier_by_url)
    if action == "decline":
        return "declined"
    # Defensive fallback — should be unreachable.
    return "verified_nei"


_OUTCOME_TO_VERDICT: dict[ActionOutcome, Verdict] = {
    "verified_supported": "Supported",
    "verified_refuted": "Refuted",
    "verified_conflicting": "Conflicting",
    "verified_nei": "NotEnoughEvidence",
}


def derive_verdict(
    findings: ConsolidatedFindings,
    source_quality_table: list[SourceQualityEntry],
) -> Verdict:
    """Legacy wrapper. Returns NotEnoughEvidence for non-verify outcomes
    since the old four-label vocabulary has no slot for context_provided
    / challenged / perspectives_surfaced.

    New code should call `derive_action_outcome(action, ...)` directly.
    """
    outcome = _verify_outcome(findings, _tier_lookup(source_quality_table))
    return _OUTCOME_TO_VERDICT[outcome]
