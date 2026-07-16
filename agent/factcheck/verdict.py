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


def _count_reliable(
    urls: list[str],
    tier_by_url: dict[str, SourceTier],
    on_point_urls: frozenset[str] = frozenset(),
) -> int:
    """Count reliable-tier URLs. v0.7: an on-point fact-checker or
    primary-source URL counts as 2 — one decisive source suffices (that is
    how community notes actually cite)."""
    on_point_canon = {canonicalize_url(u) for u in on_point_urls}
    distinct = {canonicalize_url(u) for u in urls}
    total = 0
    for u in distinct:
        tier = tier_by_url.get(u, "unknown")
        if tier not in _RELIABLE_TIERS:
            continue
        weight = 2 if (u in on_point_canon and tier in ("fact-checker", "primary-source")) else 1
        total += weight
    return total


def _verify_outcome(
    findings: ConsolidatedFindings,
    tier_by_url: dict[str, SourceTier],
    on_point_urls: frozenset[str] = frozenset(),
) -> ActionOutcome:
    """Verify-mode outcome: derived from verified / refuted / disputed buckets.
    Maps 1:1 onto the legacy Verdict literal."""
    central_refuted = next((p for p in findings.refuted_propositions if p.is_central), None)
    if central_refuted is not None:
        urls = [s.url for s in central_refuted.refuting_sources]
        if _count_reliable(urls, tier_by_url, on_point_urls) >= _RELIABLE_THRESHOLD:
            return "verified_refuted"

    central_verified = next((p for p in findings.verified_propositions if p.is_central), None)
    if central_verified is not None:
        urls = [s.url for s in central_verified.supporting_sources]
        if _count_reliable(urls, tier_by_url, on_point_urls) >= _RELIABLE_THRESHOLD:
            return "verified_supported"

    central_disputed = next((p for p in findings.disputed_propositions if p.is_central), None)
    if central_disputed is not None:
        for_count = _count_reliable([s.url for s in central_disputed.sources_for], tier_by_url, on_point_urls)
        against_count = _count_reliable([s.url for s in central_disputed.sources_against], tier_by_url, on_point_urls)
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
    on_point_urls: frozenset[str] = frozenset(),
) -> ActionOutcome:
    central = next((c for c in findings.contextual_findings if c.is_central), None)
    if central is None:
        return "context_unavailable"
    urls = [s.url for s in central.citing_sources]
    if _count_reliable(urls, tier_by_url, on_point_urls) >= _RELIABLE_THRESHOLD:
        return "context_provided"
    return "context_unavailable"


def _challenge_outcome(
    findings: ConsolidatedFindings,
    tier_by_url: dict[str, SourceTier],
    on_point_urls: frozenset[str] = frozenset(),
) -> ActionOutcome:
    central = next((p for p in findings.challenged_propositions if p.is_central), None)
    if central is None or not central.counterpoints:
        return "challenge_unavailable"
    # At least one counterpoint backed by ≥1 reliable source.
    for cp in central.counterpoints:
        urls = [s.url for s in cp.citing_sources]
        if _count_reliable(urls, tier_by_url, on_point_urls) >= 1:
            return "challenged"
    return "challenge_unavailable"


def _perspectives_outcome(
    findings: ConsolidatedFindings,
    tier_by_url: dict[str, SourceTier],
    on_point_urls: frozenset[str] = frozenset(),
) -> ActionOutcome:
    # ≥2 distinct perspectives each with ≥1 reliable source.
    backed = 0
    for p in findings.perspectives:
        urls = [s.url for s in p.citing_sources]
        if _count_reliable(urls, tier_by_url, on_point_urls) >= 1:
            backed += 1
    if backed >= 2:
        return "perspectives_surfaced"
    return "perspectives_insufficient"


def derive_action_outcome(
    action: Action,
    findings: ConsolidatedFindings,
    source_quality_table: list[SourceQualityEntry],
    *,
    on_point_urls: frozenset[str] = frozenset(),
) -> ActionOutcome:
    """Structural rule that maps (action, findings, source_quality_table)
    to an ActionOutcome. Stage 5 audit re-derives this and forces an
    `_unavailable` / `_nei` outcome when the declared outcome disagrees.

    A URL in `on_point_urls` whose tier is fact-checker or primary-source
    counts as 2 toward the reliable-source threshold (v0.7 weighted
    sufficiency — one decisive on-point source suffices).
    """
    tier_by_url = _tier_lookup(source_quality_table)
    if action == "verify":
        return _verify_outcome(findings, tier_by_url, on_point_urls)
    if action == "provide_context":
        return _context_outcome(findings, tier_by_url, on_point_urls)
    if action == "challenge_opinion":
        return _challenge_outcome(findings, tier_by_url, on_point_urls)
    if action == "surface_perspectives":
        return _perspectives_outcome(findings, tier_by_url, on_point_urls)
    if action == "decline":
        return "declined"
    # Defensive fallback — should be unreachable.
    return "verified_nei"


# Outcomes that mean "the pipeline produced no usable result."
_NO_RESULT_OUTCOMES: frozenset[ActionOutcome] = frozenset({
    "verified_nei", "context_unavailable", "challenge_unavailable",
    "perspectives_insufficient",
})

_DECISIVE_VERIFY: dict[str, ActionOutcome] = {
    "refuted": "verified_refuted",
    "supported": "verified_supported",
    "conflicting": "verified_conflicting",
}

_SUBSTANTIVE_BY_ACTION: dict[Action, ActionOutcome] = {
    "provide_context": "context_provided",
    "challenge_opinion": "challenged",
    "surface_perspectives": "perspectives_surfaced",
}


def reconcile_outcome_with_finding(
    outcome: ActionOutcome,
    action: Action,
    verdict_leaning: str,
    *,
    verifier_passed: bool,
    verifier_advisory_downgrade: bool = False,
) -> ActionOutcome:
    """Promote a mechanical 'no-result' outcome to the finding it actually states,
    when the INDEPENDENT verifier approved the draft — or, for non-verify actions,
    when it merely applied an ADVISORY downgrade (confidence lowered, payload NOT
    scrubbed).

    The source-tier count in `derive_action_outcome` can return `verified_nei`
    etc. even when the model committed a decisive verdict and the verifier
    confirmed it is derivable and warranted — producing a label that
    contradicts the reply (findings say "FALSE", the outcome code says
    "not enough evidence"). Once the verifier is the sourcing guard, that
    mechanical recount should not silently null a verifier-approved verdict;
    the source count feeds `confidence`, not the label.

    `verify` verdicts already survive an advisory downgrade: `apply_downgrade`
    only lowers confidence without flipping `verdict_leaning`, so
    `derive_action_outcome` still returns the decisive verdict when sourced, and
    this promotion covers the passed-but-weakly-sourced case. The non-verify
    actions (`provide_context` / `challenge_opinion` / `surface_perspectives`)
    lacked that coherence: an advisory downgrade set `verifier_passed=False`,
    which collapsed a substantive finding to `*_unavailable`. So a substantive
    non-verify finding is also promoted under an advisory downgrade — i.e. a
    downgrade the verifier did NOT escalate to a payload scrub. A scrubbed
    payload (a confirmed central temporal leak) is NOT an advisory downgrade and
    keeps the conservative no-result label."""
    if outcome not in _NO_RESULT_OUTCOMES:
        return outcome
    if action == "verify":
        if not verifier_passed:
            return outcome
        return _DECISIVE_VERIFY.get(verdict_leaning, outcome)  # 'insufficient' stays no-result
    if verifier_passed or verifier_advisory_downgrade:
        return _SUBSTANTIVE_BY_ACTION.get(action, outcome)
    return outcome


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
