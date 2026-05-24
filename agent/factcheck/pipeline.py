"""End-to-end orchestrator for the fact-check pipeline (thin slice).

The thin slice runs: hardcoded claim → stub search → reconcile → structural
verdict → mechanical audit → freeze → (caller renders via render.py).

Multimodal (1.5), claim extraction (2), check-worthiness gate (3), and the
iterative verification loop (4) are simplified or skipped — the input string
IS the central claim and each search hit becomes one Evidence record.
"""
from __future__ import annotations

import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .audit import audit
from .freeze import freeze_to_disk
from .reconcile import reconcile
from .schema import (
    BackendVersion,
    Claim,
    ConsolidatedFindings,
    CrossModalReport,
    Evidence,
    FrozenVerdict,
    Lens1,
    PresentationPayload,
    SourceQualityEntry,
    UnaddressedProposition,
)
from .search import SearchBackend, build_default_backend
from .sources import build_quality_table
from .verdict import derive_verdict


_PIPELINE_VERSION = "factcheck-thin-slice-0.1"


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parent,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except OSError:
        return "unknown"


def _build_evidence(query: str, hits) -> list[Evidence]:
    """Stage 4 (thin): one auto-question per claim; each hit becomes one Evidence."""
    return [
        Evidence(
            question=f"Is the claim true? — {query}",
            source_url=hit.url,
            snippet=hit.snippet,
            stance="neutral",
        )
        for hit in hits
    ]


def _nei_verdict(reason: str) -> tuple[ConsolidatedFindings, PresentationPayload, str]:
    findings = ConsolidatedFindings(
        unaddressed_propositions=(
            UnaddressedProposition(
                proposition=reason,
                reason="no evidence retrieved",
                is_central=True,
            ),
        )
    )
    payload = PresentationPayload(
        headline_finding="Not enough reliable evidence to verify this claim.",
        counter_fact=None,
        primary_sources_to_cite=[],
        load_bearing_evidence_snippet="",
    )
    return findings, payload, "Not enough reliable evidence was found to verify or refute this claim."


def run_pipeline(
    claim_text: str,
    *,
    backend: Optional[SearchBackend] = None,
    target_tweet_id: str = "",
    freeze_root: Optional[Path] = None,
) -> FrozenVerdict:
    """Run the thin-slice pipeline end-to-end. Returns the frozen verdict."""
    backend = backend or build_default_backend()
    invocation_id = str(uuid.uuid4())
    invocation_time = datetime.now(timezone.utc)

    hits = backend.search(claim_text, top_k=5)
    evidence = _build_evidence(claim_text, hits)
    quality_table = build_quality_table([h.url for h in hits])

    if not evidence:
        findings, payload, justification = _nei_verdict(claim_text)
        lens_1 = Lens1(narrative="No evidence retrieved.")
        verdict_label = "NotEnoughEvidence"
    else:
        recon = reconcile(
            central_claim_text=claim_text,
            evidence=evidence,
            source_quality_table=quality_table,
        )
        evidence = [
            Evidence(
                question=e.question,
                source_url=e.source_url,
                snippet=e.snippet,
                stance=stance,
            )
            for e, stance in zip(evidence, recon.evidence_stances)
        ]
        findings = recon.consolidated_findings
        payload = recon.presentation_payload
        justification = recon.tone_neutral_justification
        lens_1 = recon.lens_1
        verdict_label = derive_verdict(findings, quality_table)

    audit_result = audit(
        declared_verdict=verdict_label,
        findings=findings,
        source_quality_table=quality_table,
        presentation_payload=payload,
        tone_neutral_justification=justification,
    )
    if not audit_result.passed:
        verdict_label = "NotEnoughEvidence"
        findings, payload, justification = _nei_verdict(
            "Audit failed: " + "; ".join(audit_result.failures)
        )

    claim_obj = Claim(
        claim_id="c1",
        text=claim_text,
        type="verifiable",
        is_central=True,
        evidence=evidence,
    )

    frozen = FrozenVerdict(
        invocation_id=invocation_id,
        target_tweet_id=target_tweet_id,
        invocation_time=invocation_time,
        backend_version=BackendVersion(
            model="claude-via-azure-ai-services",
            search_provider=backend.name,
            pipeline_commit=_git_sha(),
            source_reliability_lists_version={"thin_slice_canned_table": _PIPELINE_VERSION},
        ),
        claims=[claim_obj],
        cross_modal_report=CrossModalReport(lens_1_text_text=lens_1),
        consolidated_findings=findings,
        source_quality_table=quality_table,
        verdict_label=verdict_label,
        tone_neutral_justification=justification,
        presentation_payload=payload,
    )

    freeze_to_disk(frozen, root=freeze_root)
    return frozen
