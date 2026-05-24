"""Stage 5 — verdict audit. Forces NotEnoughEvidence on any failure.

Thin-slice scope: mechanical checks only. The design also calls for a
CoVe-style Claude pass (re-asking whether the verdict is faithful to the
evidence); that is a follow-up — for now, the mechanical contract is the
audit floor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .schema import (
    ConsolidatedFindings,
    PresentationPayload,
    SourceQualityEntry,
    Verdict,
)
from .verdict import derive_verdict


_URL_RE = re.compile(r"https?://[^\s<>\"')]+")


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
    for url in _URL_RE.findall(tone_neutral_justification):
        if url not in known_urls:
            failures.append(f"Justification cites URL not in source_quality_table: {url}")

    for src in presentation_payload.primary_sources_to_cite:
        if src.url not in known_urls:
            failures.append(f"primary_sources_to_cite includes URL not in source_quality_table: {src.url}")

    if declared_verdict == "Refuted" and not presentation_payload.counter_fact:
        failures.append("Refuted verdict but presentation_payload.counter_fact is empty.")

    if not presentation_payload.headline_finding.strip():
        failures.append("presentation_payload.headline_finding is empty.")

    central_props = (
        [p for p in findings.verified_propositions if p.is_central]
        + [p for p in findings.refuted_propositions if p.is_central]
        + [p for p in findings.disputed_propositions if p.is_central]
        + [p for p in findings.unaddressed_propositions if p.is_central]
    )
    if not central_props:
        failures.append("No central proposition emitted in consolidated_findings.")

    return AuditResult(passed=not failures, failures=failures)
