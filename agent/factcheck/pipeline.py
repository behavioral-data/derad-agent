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
from .multimodal import ImageEvidence, extract_image
from .reconcile import reconcile
from .schema import (
    AttachedImage,
    BackendVersion,
    Claim,
    ConsolidatedFindings,
    CrossModalReport,
    Evidence,
    FrozenVerdict,
    ImageProvenance,
    Lens1,
    Lens2,
    Lens3,
    Modality,
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


def _run_multimodal(image_urls: list[str], backend: SearchBackend) -> list[ImageEvidence]:
    """Stage 1.5 — extract per-image evidence. Skips images we can't fetch/process."""
    out: list[ImageEvidence] = []
    for url in image_urls:
        evidence = extract_image(url, search_backend=backend)
        if evidence is not None:
            out.append(evidence)
    return out


def _attached_image_records(images: list[ImageEvidence]) -> list[AttachedImage]:
    """Pack per-image evidence into the frozen schema's AttachedImage rows."""
    records: list[AttachedImage] = []
    for i, img in enumerate(images):
        records.append(
            AttachedImage(
                image_id=f"img{i + 1}",
                image_url=img.image_url,
                ocr_text=img.ocr_text,
                vlm_description=img.description,
                provenance=(),  # Tier 3 hits live in source_quality_table; this field is reserved
                                # for true reverse-image-search matches with earliest-seen dates.
                manipulation_check="out_of_scope_nei",
            )
        )
    return records


def run_pipeline(
    claim_text: str,
    *,
    backend: Optional[SearchBackend] = None,
    target_tweet_id: str = "",
    image_urls: Optional[list[str]] = None,
    freeze_root: Optional[Path] = None,
) -> FrozenVerdict:
    """Run the pipeline end-to-end. Returns the frozen verdict.

    `image_urls`, when present, triggers Stage 1.5: per-image OCR +
    description (Claude VLM) + a Bing-grounded provenance approximation
    search. The results feed Lens 2 / Lens 3 reasoning inside the
    Stage 4.5 reconciliation call.
    """
    backend = backend or build_default_backend()
    invocation_id = str(uuid.uuid4())
    invocation_time = datetime.now(timezone.utc)
    image_urls = image_urls or []
    modality: Modality = "mixed" if image_urls and claim_text.strip() else ("image" if image_urls else "text")

    image_evidence: list[ImageEvidence] = []
    if image_urls:
        image_evidence = _run_multimodal(image_urls, backend)

    hits = backend.search(claim_text, top_k=5)
    text_evidence = _build_evidence(claim_text, hits)

    # Roll image-provenance hits into the source-quality table too — Claude is
    # told to cite from `source_quality_table` only, and image-derived URLs are
    # legitimate evidence sources.
    all_urls = [h.url for h in hits]
    for img in image_evidence:
        all_urls.extend(h.url for h in img.provenance_hits)
    quality_table = build_quality_table(all_urls)

    if not text_evidence and not image_evidence:
        findings, payload, justification = _nei_verdict(claim_text)
        lens_1 = Lens1(narrative="No evidence retrieved.")
        verdict_label = "NotEnoughEvidence"
    else:
        recon = reconcile(
            central_claim_text=claim_text,
            evidence=text_evidence,
            source_quality_table=quality_table,
            image_evidence=image_evidence or None,
        )
        text_evidence = [
            Evidence(
                question=e.question,
                source_url=e.source_url,
                snippet=e.snippet,
                stance=stance,
            )
            for e, stance in zip(text_evidence, recon.evidence_stances)
        ]
        findings = recon.consolidated_findings
        payload = recon.presentation_payload
        justification = recon.tone_neutral_justification
        lens_1 = recon.lens_1
        verdict_label = derive_verdict(findings, quality_table)
    evidence = text_evidence

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

    cross_modal = CrossModalReport(lens_1_text_text=lens_1)
    if image_evidence:
        # Lens 2 + Lens 3 are folded into the same reconcile call; surface a
        # minimal record-keeping marker so the freeze captures that they ran.
        cross_modal = CrossModalReport(
            lens_1_text_text=lens_1,
            lens_2_image_text=Lens2(
                ran=True,
                narrative="Image OCR + description folded into reconcile prompt.",
                image_provenance=ImageProvenance(
                    earliest_seen="unknown",
                    true_caption="unknown",
                    true_context="unknown",
                    provenance_sources=tuple(
                        h.url for img in image_evidence for h in img.provenance_hits
                    ),
                ),
                image_caption_match="undetermined",
            ),
            lens_3_cross_modal=Lens3(
                ran=True,
                narrative="Cross-modal reasoning folded into reconcile prompt.",
            ),
        )

    frozen = FrozenVerdict(
        invocation_id=invocation_id,
        target_tweet_id=target_tweet_id,
        invocation_time=invocation_time,
        modality=modality,
        backend_version=BackendVersion(
            model="claude-via-azure-ai-services",
            vlm_model="claude-via-azure-ai-services" if image_evidence else "",
            search_provider=backend.name,
            pipeline_commit=_git_sha(),
            source_reliability_lists_version={"thin_slice_canned_table": _PIPELINE_VERSION},
        ),
        attached_images=tuple(_attached_image_records(image_evidence)),
        claims=[claim_obj],
        cross_modal_report=cross_modal,
        consolidated_findings=findings,
        source_quality_table=quality_table,
        verdict_label=verdict_label,
        tone_neutral_justification=justification,
        presentation_payload=payload,
    )

    freeze_to_disk(frozen, root=freeze_root)
    return frozen
