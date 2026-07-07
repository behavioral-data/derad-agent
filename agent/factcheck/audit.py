"""Stage 5 — outcome audit. Forces an `_unavailable` / `_nei` outcome on
any structural mismatch. Mechanical checks only — CoVe-style Claude
audit is a planned follow-up.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.shared.text import URL_RE, canonicalize_url

from .schema import (
    Action,
    ActionOutcome,
    ConsolidatedFindings,
    PresentationPayload,
    SourceQualityEntry,
    TierRef,
)
from .verdict import derive_action_outcome


@dataclass
class AuditResult:
    passed: bool
    failures: list[str]


# Fallback outcomes per action when audit fails — keep the action category
# intact so the renderer still picks the right template.
_AUDIT_FAIL_OUTCOME: dict[Action, ActionOutcome] = {
    "verify": "verified_nei",
    "provide_context": "context_unavailable",
    "challenge_opinion": "challenge_unavailable",
    "surface_perspectives": "perspectives_insufficient",
    "decline": "declined",
}


def audit_fail_outcome_for(action: Action) -> ActionOutcome:
    """Public helper for callers that need the audit-fail outcome for an
    action (e.g. pipeline.run_pipeline when re-routing after a failed
    audit)."""
    return _AUDIT_FAIL_OUTCOME.get(action, "verified_nei")


def _collect_action_urls(
    payload: PresentationPayload,
) -> list[str]:
    """Every URL the renderer might surface in the reply body or quote.
    The audit checks that all of these appear in source_quality_table.
    """
    urls: list[str] = [s.url for s in payload.primary_sources_to_cite]
    for cp in payload.counterpoints:
        urls.extend(s.url for s in cp.citing_sources)
    for p in payload.perspectives:
        urls.extend(s.url for s in p.citing_sources)
    return [canonicalize_url(u) for u in urls]


def _central_bucket_check(
    action: Action, findings: ConsolidatedFindings, failures: list[str]
) -> None:
    """Central proposition must live in exactly one bucket. The bucket
    must match the action: verify uses the four legacy buckets;
    provide_context uses contextual_findings; challenge_opinion uses
    challenged_propositions; surface_perspectives uses unaddressed
    (the topic statement; perspectives are children)."""
    bucket_centrals: dict[str, int] = {
        "verified": sum(1 for p in findings.verified_propositions if p.is_central),
        "refuted": sum(1 for p in findings.refuted_propositions if p.is_central),
        "disputed": sum(1 for p in findings.disputed_propositions if p.is_central),
        "unaddressed": sum(1 for p in findings.unaddressed_propositions if p.is_central),
        "contextual": sum(1 for c in findings.contextual_findings if c.is_central),
        "challenged": sum(1 for p in findings.challenged_propositions if p.is_central),
    }
    occupied = [name for name, n in bucket_centrals.items() if n > 0]
    if not occupied:
        failures.append("No central proposition emitted in consolidated_findings.")
        return
    if len(occupied) > 1:
        failures.append(
            f"Central proposition appears in multiple buckets ({', '.join(occupied)}); "
            "outcome rule would resolve ambiguously."
        )
        return
    # Bucket × action match
    bucket = occupied[0]
    if action == "verify" and bucket not in {"verified", "refuted", "disputed", "unaddressed"}:
        failures.append(
            f"action=verify but central is in {bucket!r} bucket (expected verified/refuted/disputed/unaddressed)."
        )
    if action == "provide_context" and bucket not in {"contextual", "unaddressed"}:
        failures.append(
            f"action=provide_context but central is in {bucket!r} bucket (expected contextual_findings or unaddressed)."
        )
    if action == "challenge_opinion" and bucket not in {"challenged", "unaddressed"}:
        failures.append(
            f"action=challenge_opinion but central is in {bucket!r} bucket (expected challenged_propositions or unaddressed)."
        )
    if action == "surface_perspectives" and bucket != "unaddressed":
        failures.append(
            f"action=surface_perspectives but central is in {bucket!r} bucket (expected unaddressed)."
        )


def audit(
    *,
    action: Action,
    declared_outcome: ActionOutcome,
    findings: ConsolidatedFindings,
    source_quality_table: list[SourceQualityEntry],
    presentation_payload: PresentationPayload,
    tone_neutral_justification: str,
) -> AuditResult:
    """Return AuditResult.passed=False if anything is off. Caller swaps in
    the audit-fail outcome on failure."""
    failures: list[str] = []

    # Note: in the current flow `declared_outcome` is itself computed by
    # `derive_action_outcome` upstream, so re-deriving here would be a
    # tautology. The real value of this audit is the shape + URL-containment
    # checks below — which catch reconcile drift (model emits a URL not in
    # the source_quality_table, or violates the central-bucket invariant)
    # that a pure structural-rule recompute would miss.

    # 2. Every URL the renderer can surface must be in source_quality_table.
    known_urls = {canonicalize_url(entry.url) for entry in source_quality_table}
    # Free-form text fields where the LLM writes findings/citations in
    # prose — any URL fabricated into these must still resolve to
    # source_quality_table. Deliberately EXCLUDES tone_neutral_justification
    # and load_bearing_evidence_snippet: those carry a human-quote /
    # reasoning narrative that can legitimately reproduce a verbatim
    # evidence quote containing an incidental URL that was never itself a
    # retrieved source (e.g. a URL mentioned inside the quoted text). That
    # is not a citation and must not fail the audit. The structured
    # citation fields (primary_sources_to_cite, counterpoints/perspectives
    # citing_sources) are still fully enforced below via
    # `_collect_action_urls`, so fabricated citations are still caught.
    free_text_fields: list[str] = [
        presentation_payload.headline_finding,
        presentation_payload.counter_fact,
        presentation_payload.context_note,
    ]
    for cp in presentation_payload.counterpoints:
        free_text_fields.append(cp.summary)
    for p in presentation_payload.perspectives:
        free_text_fields.append(p.summary)
        free_text_fields.append(p.label)
    for field in free_text_fields:
        for url in URL_RE.findall(field or ""):
            if canonicalize_url(url) not in known_urls:
                failures.append(f"Text cites URL not in source_quality_table: {url}")

    # Structured URLs across the action-specific payload fields.
    for url in _collect_action_urls(presentation_payload):
        if url not in known_urls:
            failures.append(f"Payload includes URL not in source_quality_table: {url}")

    # 3. Verify-specific: refuted ⇒ counter_fact required.
    if action == "verify" and declared_outcome == "verified_refuted" and not presentation_payload.counter_fact:
        failures.append("verified_refuted but presentation_payload.counter_fact is empty.")

    # 4. Headline finding must be non-empty for any non-decline action.
    if action != "decline" and not presentation_payload.headline_finding.strip():
        failures.append("presentation_payload.headline_finding is empty.")

    # 5. Action-specific shape checks.
    if action == "provide_context" and declared_outcome == "context_provided":
        if not presentation_payload.context_note:
            failures.append("context_provided but presentation_payload.context_note is empty.")
    if action == "challenge_opinion" and declared_outcome == "challenged":
        if not presentation_payload.counterpoints:
            failures.append("challenged but presentation_payload.counterpoints is empty.")
    if action == "surface_perspectives" and declared_outcome == "perspectives_surfaced":
        if len(presentation_payload.perspectives) < 2:
            failures.append(
                f"perspectives_surfaced but only {len(presentation_payload.perspectives)} perspective(s) "
                "(need ≥ 2)."
            )

    # 6. Central-proposition cardinality + bucket × action match.
    _central_bucket_check(action, findings, failures)

    return AuditResult(passed=not failures, failures=failures)
