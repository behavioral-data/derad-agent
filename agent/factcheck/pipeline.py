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
from .extract import ExtractionOutput, central_claim, extract_claims
from .freeze import freeze_to_disk
from .multimodal import ImageEvidence, extract_image
from .reconcile import reconcile
from .verify import iterative_verify
from .schema import (
    AttachedImage,
    BackendVersion,
    Claim,
    ClaimType,
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


_log = __import__("logging").getLogger("agent.factcheck.pipeline")


def _short_circuit_no_checkable(
    *,
    invocation_id: str,
    invocation_time,
    target_tweet_id: str,
    modality: Modality,
    backend_name: str,
    image_evidence: list[ImageEvidence],
    extraction: ExtractionOutput,
    freeze_root: Optional[Path],
) -> FrozenVerdict:
    """Stage 2+3 short-circuit. No search, no reconcile — bot replies with
    a hardcoded tone-appropriate 'nothing to fact-check' template at Stage 7."""
    _log.info("run_pipeline[%s]: short-circuit — no_checkable_claim (%s)", invocation_id, extraction.reason)

    reason = extraction.reason or "Personal opinion or aesthetic reaction; no factually verifiable claim identified."
    findings = ConsolidatedFindings(
        unaddressed_propositions=tuple(
            UnaddressedProposition(
                proposition=c.text,
                reason="no evidence retrieved",
                is_central=c.is_central,
            )
            for c in extraction.claims
        )
    )
    payload = PresentationPayload(
        headline_finding=reason,
        counter_fact=None,
        primary_sources_to_cite=(),
        load_bearing_evidence_snippet="",
    )
    claim_objs = tuple(
        Claim(
            claim_id=f"c{i + 1}",
            text=ec.text,
            type=ec.type,
            check_worthy=ec.check_worthy,
            is_central=ec.is_central,
            evidence=(),
        )
        for i, ec in enumerate(extraction.claims)
    )

    frozen = FrozenVerdict(
        invocation_id=invocation_id,
        target_tweet_id=target_tweet_id,
        invocation_time=invocation_time,
        modality=modality,
        backend_version=BackendVersion(
            model="claude-via-azure-ai-services",
            vlm_model="claude-via-azure-ai-services" if image_evidence else "",
            search_provider=backend_name,
            pipeline_commit=_git_sha(),
            source_reliability_lists_version={"thin_slice_canned_table": _PIPELINE_VERSION},
        ),
        attached_images=tuple(_attached_image_records(image_evidence)),
        claims=claim_objs,
        cross_modal_report=CrossModalReport(lens_1_text_text=Lens1(narrative="Skipped: no checkable claim.")),
        consolidated_findings=findings,
        source_quality_table=(),
        verdict_label="NotEnoughEvidence",
        tone_neutral_justification=reason,
        presentation_payload=payload,
        overall_state="no_checkable_claim",
    )
    freeze_to_disk(frozen, root=freeze_root)
    return frozen


def run_pipeline(
    claim_text: str,
    *,
    backend: Optional[SearchBackend] = None,
    target_tweet_id: str = "",
    image_urls: Optional[list[str]] = None,
    tweet_context: Optional[dict] = None,
    freeze_root: Optional[Path] = None,
) -> FrozenVerdict:
    """Run the pipeline end-to-end. Returns the frozen verdict.

    `image_urls`, when present, triggers Stage 1.5: per-image OCR +
    description (Claude VLM) + a Bing-grounded provenance approximation
    search. The results feed Lens 2 / Lens 3 reasoning inside the
    Stage 4.5 reconciliation call.

    `tweet_context` carries fact-checking-relevant metadata pulled from the
    parent tweet (author handle/bio/verified/account-age, posted-at, expanded
    t.co URLs, referenced-tweet relations, language, sensitive flag, public
    metrics). Reconcile uses it to spot parody/aggregator accounts and to
    date-stamp the claim.
    """
    backend = backend or build_default_backend()
    invocation_id = str(uuid.uuid4())
    invocation_time = datetime.now(timezone.utc)
    image_urls = image_urls or []
    modality: Modality = "mixed" if image_urls and claim_text.strip() else ("image" if image_urls else "text")
    author_log = (tweet_context or {}).get("author_username") or "?"
    _log.info(
        "run_pipeline[%s]: starting (modality=%s, claim_chars=%d, images=%d, author=@%s)",
        invocation_id, modality, len(claim_text), len(image_urls), author_log,
    )

    image_evidence: list[ImageEvidence] = []
    if image_urls:
        _log.info("run_pipeline[%s]: Stage 1.5 entering with %d image(s)", invocation_id, len(image_urls))
        image_evidence = _run_multimodal(image_urls, backend)
        _log.info("run_pipeline[%s]: Stage 1.5 complete (%d evidence)", invocation_id, len(image_evidence))

    # ── Stage 2 + 3 — claim extraction & check-worthiness gate ──
    _log.info("run_pipeline[%s]: Stage 2+3 — claim extraction", invocation_id)
    extraction = extract_claims(
        claim_text,
        tweet_context=tweet_context,
        image_evidence=image_evidence or None,
    )
    central = central_claim(extraction)
    _log.info(
        "run_pipeline[%s]: Stage 2+3 → %d propositions, central type=%s check_worthy=%s, overall=%s",
        invocation_id, len(extraction.claims), central.type, central.check_worthy, extraction.overall_state,
    )

    if extraction.overall_state == "no_checkable_claim":
        return _short_circuit_no_checkable(
            invocation_id=invocation_id,
            invocation_time=invocation_time,
            target_tweet_id=target_tweet_id,
            modality=modality,
            backend_name=backend.name,
            image_evidence=image_evidence,
            extraction=extraction,
            freeze_root=freeze_root,
        )

    # Stage 4+ uses the central proposition's text — typically more focused
    # than the raw tweet (hedges stripped, framing normalized).
    central_text = central.text
    _log.info("run_pipeline[%s]: Stage 4 — iterative verification (Papelo-style)", invocation_id)
    image_summaries = (
        [
            {
                "image_url": img.image_url,
                "ocr_text": img.ocr_text,
                "description": img.description,
            }
            for img in image_evidence
        ]
        if image_evidence
        else None
    )
    text_evidence = iterative_verify(
        claim_text=central_text,
        backend=backend,
        tweet_context=tweet_context,
        image_summaries=image_summaries,
    )
    _log.info("run_pipeline[%s]: Stage 4 done (%d evidence records)", invocation_id, len(text_evidence))

    # Roll image-provenance hits into the source-quality table too — Claude is
    # told to cite from `source_quality_table` only, and image-derived URLs are
    # legitimate evidence sources.
    all_urls = [e.source_url for e in text_evidence]
    for img in image_evidence:
        all_urls.extend(h.url for h in img.provenance_hits)
    quality_table = build_quality_table(all_urls)

    if not text_evidence and not image_evidence:
        findings, payload, justification = _nei_verdict(central_text)
        lens_1 = Lens1(narrative="No evidence retrieved.")
        verdict_label = "NotEnoughEvidence"
    else:
        _log.info("run_pipeline[%s]: Stage 4.5 — reconcile", invocation_id)
        recon = reconcile(
            central_claim_text=central_text,
            evidence=text_evidence,
            source_quality_table=quality_table,
            image_evidence=image_evidence or None,
            tweet_context=tweet_context,
        )
        _log.info("run_pipeline[%s]: Stage 4.5 done", invocation_id)
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

    _log.info("run_pipeline[%s]: Stage 5 — audit (declared=%s)", invocation_id, verdict_label)
    audit_result = audit(
        declared_verdict=verdict_label,
        findings=findings,
        source_quality_table=quality_table,
        presentation_payload=payload,
        tone_neutral_justification=justification,
    )
    _log.info(
        "run_pipeline[%s]: Stage 5 audit %s (failures=%d)",
        invocation_id, "passed" if audit_result.passed else "FAILED → NEI",
        len(audit_result.failures),
    )
    if not audit_result.passed:
        verdict_label = "NotEnoughEvidence"
        findings, payload, justification = _nei_verdict(
            "Audit failed: " + "; ".join(audit_result.failures)
        )

    claim_objs: list[Claim] = []
    for i, ec in enumerate(extraction.claims):
        evidence_for_this = evidence if ec.is_central else ()
        claim_objs.append(
            Claim(
                claim_id=f"c{i + 1}",
                text=ec.text,
                type=ec.type,
                check_worthy=ec.check_worthy,
                is_central=ec.is_central,
                evidence=evidence_for_this,
            )
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
        claims=tuple(claim_objs),
        cross_modal_report=cross_modal,
        consolidated_findings=findings,
        source_quality_table=quality_table,
        verdict_label=verdict_label,
        tone_neutral_justification=justification,
        presentation_payload=payload,
    )

    freeze_to_disk(frozen, root=freeze_root)
    return frozen
