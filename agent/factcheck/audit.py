"""Stage 5 — verdict audit. Forces NotEnoughEvidence on any failure.

Mechanical-checks-only audit (URL provenance, central-claim cardinality,
verdict-label/findings consistency). The design also calls for a
CoVe-style Claude pass re-asking whether the verdict is faithful to the
evidence — follow-up work; for now this mechanical contract is the floor.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.shared.text import URL_RE

from .schema import (
    ConsolidatedFindings,
    PresentationPayload,
    SourceQualityEntry,
    Verdict,
)
from .verdict import derive_verdict


@dataclass
class AuditResult:
    passed: bool
    failures: list[str]


def audit(
    *,
    declared_verdict: Verdict,
    findings: ConsolidatedFindings,
    source_quality_table: list[SourceQualityEntry],
    presentation_payload: PresentationPayload,
    tone_neutral_justification: str,
) -> AuditResult:
    """Return AuditResult.passed=False if anything is off. Caller must force NEI on failure."""
    failures: list[str] = []

    expected = derive_verdict(findings, source_quality_table)
    if expected != declared_verdict:
        failures.append(
            f"Verdict label {declared_verdict!r} disagrees with structural rule (expected {expected!r})."
        )

    known_urls = {entry.url for entry in source_quality_table}
    # The renderer is contractually allowed to quote load_bearing_evidence_snippet
    # verbatim, so any URL hiding inside it leaks past audit if we don't scan it.
    for field in (tone_neutral_justification, presentation_payload.load_bearing_evidence_snippet):
        for url in URL_RE.findall(field or ""):
            if url not in known_urls:
                failures.append(f"Text cites URL not in source_quality_table: {url}")

    for src in presentation_payload.primary_sources_to_cite:
        if src.url not in known_urls:
            failures.append(f"primary_sources_to_cite includes URL not in source_quality_table: {src.url}")

    if declared_verdict == "Refuted" and not presentation_payload.counter_fact:
        failures.append("Refuted verdict but presentation_payload.counter_fact is empty.")

    if not presentation_payload.headline_finding.strip():
        failures.append("presentation_payload.headline_finding is empty.")

    central_buckets = {
        "verified": [p for p in findings.verified_propositions if p.is_central],
        "refuted": [p for p in findings.refuted_propositions if p.is_central],
        "disputed": [p for p in findings.disputed_propositions if p.is_central],
        "unaddressed": [p for p in findings.unaddressed_propositions if p.is_central],
    }
    occupied = [name for name, props in central_buckets.items() if props]
    if not occupied:
        failures.append("No central proposition emitted in consolidated_findings.")
    elif len(occupied) > 1:
        failures.append(
            f"Central proposition appears in multiple buckets ({', '.join(occupied)}); "
            "verdict rule would resolve ambiguously."
        )

    return AuditResult(passed=not failures, failures=failures)
