"""End-to-end orchestrator for the fact-check pipeline (design v0.5).

Runs all stages from the design spec: Stage 1.5 multimodal extraction →
Stage 2+3 claim extraction & check-worthiness gate → Stage 4 iterative
verification (Papelo-style) → Stage 4.5 reconciliation (three-lens
cascade + source-quality table) → Stage 5 mechanical audit → Stage 6
freeze. The caller invokes Stage 7 rendering separately via render.py
to preserve the invariance boundary.
"""
from __future__ import annotations

import functools
import logging
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .audit import audit, audit_fail_outcome_for
from .context import PipelineContext
from .extract import ExtractionOutput, central_claim, extract_claims
from .freeze import freeze_to_disk
from .multimodal import ImageEvidence, extract_image
from .reconcile import reconcile
from .verify import iterative_verify
from .schema import (
    ActionOutcome,
    ActionSource,
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
    ProvenanceMatch,
    SourceQualityEntry,
    UnaddressedProposition,
    Verdict,
)
from .search import SearchBackend, build_default_backend
from .sources import build_quality_table
from .verdict import derive_action_outcome, derive_verdict


logger = logging.getLogger(__name__)


_PIPELINE_VERSION = "factcheck-0.5"


@functools.lru_cache(maxsize=1)
def _git_sha() -> str:
    """Pipeline commit. Stable for the process lifetime — cache to avoid
    forking a subprocess on every mention."""
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


_CREDIBLE_TIERS = frozenset({"fact-checker", "reputable-news", "primary-source"})


def _has_credible_evidence(quality_table) -> bool:
    """True if at least one URL is in a credible tier; otherwise reconcile
    would land on NEI without learning anything useful."""
    return any(e.tier in _CREDIBLE_TIERS for e in quality_table)


def _resolve_modality(image_urls: list[str], claim_text: str) -> Modality:
    if image_urls and claim_text.strip():
        return "mixed"
    if image_urls:
        return "image"
    return "text"


def _thread_context(tweet_context: Optional[dict]) -> str:
    """Summarize parent-tweet reply/quote/retweet relations into a one-line
    string for the freeze record. Empty when nothing relevant is present."""
    if not tweet_context:
        return ""
    refs = tweet_context.get("referenced_tweets") or []
    parts = [
        f"{r.get('type')}={r.get('id')}"
        for r in refs
        if isinstance(r, dict) and r.get("type") and r.get("id")
    ]
    return "; ".join(parts)


def _nei_verdict(
    reason: str, *, reason_label: str = "no evidence retrieved"
) -> tuple[ConsolidatedFindings, PresentationPayload, str]:
    findings = ConsolidatedFindings(
        unaddressed_propositions=(
            UnaddressedProposition(
                proposition=reason,
                reason=reason_label,
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


_MULTIMODAL_MAX_WORKERS = 4


def _run_multimodal(image_urls: list[str], backend: SearchBackend) -> list[ImageEvidence]:
    """Stage 1.5 — extract per-image evidence concurrently. Skips images we
    can't fetch/process. Each per-image pipeline does fetch + VLM call +
    provenance search (all IO-bound), so threading wins on multi-image tweets.
    """
    if not image_urls:
        return []
    if len(image_urls) == 1:
        evidence = extract_image(image_urls[0], search_backend=backend)
        return [evidence] if evidence is not None else []
    with ThreadPoolExecutor(max_workers=min(_MULTIMODAL_MAX_WORKERS, len(image_urls))) as pool:
        results = list(pool.map(lambda u: extract_image(u, search_backend=backend), image_urls))
    return [r for r in results if r is not None]


def _attached_image_records(images: list[ImageEvidence]) -> list[AttachedImage]:
    """Pack per-image evidence into the frozen schema's AttachedImage rows.

    `canonical_image_match` is forwarded from Stage 1.5's VLM (when the
    photograph itself is a known artifact — Tank Man, Pope Balenciaga, etc).
    `provenance` is populated from the Tier 3 web-search hits — title becomes
    `match_caption`. `earliest_seen` stays unknown until Track-B image-vector
    search lands; design §4.1.5.1 marks that as a v2 stretch.
    """
    records: list[AttachedImage] = []
    for i, img in enumerate(images):
        provenance = tuple(
            ProvenanceMatch(match_url=h.url, match_caption=h.title or "")
            for h in img.provenance_hits
        )
        records.append(
            AttachedImage(
                image_id=f"img{i + 1}",
                image_url=img.image_url,
                ocr_text=img.ocr_text,
                vlm_description=img.description,
                canonical_image_match=img.canonical_image_match,
                provenance=provenance,
                manipulation_check="out_of_scope_nei",
            )
        )
    return records


def _short_circuit_decline(
    *,
    invocation_id: str,
    invocation_time,
    target_tweet_id: str,
    modality: Modality,
    backend_name: str,
    image_evidence: list[ImageEvidence],
    extraction: ExtractionOutput,
    thread_context_str: str,
    invoker_instruction_text: str,
    freeze_root: Optional[Path],
) -> FrozenVerdict:
    """Action == decline path. No search, no reconcile — the renderer emits
    a tone-aware 'nothing actionable here' reply directly from the
    extraction's reason field."""
    logger.info("run_pipeline[%s]: short-circuit — action=decline (%s)", invocation_id, extraction.reason)

    reason = extraction.reason or "No factually verifiable claim and no clear angle to push back or contextualize."
    findings = ConsolidatedFindings(unaddressed_propositions=())
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
        thread_context=thread_context_str,
        modality=modality,
        backend_version=BackendVersion(
            model="claude-via-azure-ai-services",
            vlm_model="claude-via-azure-ai-services" if image_evidence else "",
            search_provider=backend_name,
            pipeline_commit=_git_sha(),
            source_reliability_lists_version={"curated_lists": _PIPELINE_VERSION, "model_prior": "claude-sonnet"},
        ),
        attached_images=tuple(_attached_image_records(image_evidence)),
        claims=claim_objs,
        cross_modal_report=CrossModalReport(lens_1_text_text=Lens1(narrative="Skipped: action=decline.")),
        consolidated_findings=findings,
        source_quality_table=(),
        action=extraction.action,
        action_source=extraction.action_source,
        pivoted_from=extraction.pivoted_from,
        invoker_instruction_text=invoker_instruction_text,
        action_outcome="declined",
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
    invoker_instruction: str = "",
    freeze_root: Optional[Path] = None,
) -> FrozenVerdict:
    """Run the pipeline end-to-end. Returns the frozen verdict.

    `image_urls`, when present, triggers Stage 1.5: per-image OCR +
    description (Claude VLM) + a web-search provenance approximation
    over the configured SearchBackend. The results feed Lens 2 / Lens 3
    reasoning inside the Stage 4.5 reconciliation call.

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
    modality = _resolve_modality(image_urls, claim_text)
    thread_context_str = _thread_context(tweet_context)
    author_log = (tweet_context or {}).get("author_username") or "?"
    logger.info(
        "run_pipeline[%s]: starting (modality=%s, claim_chars=%d, images=%d, author=@%s)",
        invocation_id, modality, len(claim_text), len(image_urls), author_log,
    )

    image_evidence: list[ImageEvidence] = []
    if image_urls:
        logger.info("run_pipeline[%s]: Stage 1.5 entering with %d image(s)", invocation_id, len(image_urls))
        image_evidence = _run_multimodal(image_urls, backend)
        logger.info("run_pipeline[%s]: Stage 1.5 complete (%d evidence)", invocation_id, len(image_evidence))

    ctx = PipelineContext(
        tweet_context=tweet_context,
        image_evidence=image_evidence,
        invoker_instruction=invoker_instruction or "",
    )

    # ── Stage 2 + 3 — claim extraction + action selection ──
    logger.info("run_pipeline[%s]: Stage 2+3 — claim extraction + action selection (invoker=%r)",
                invocation_id, (invoker_instruction or "")[:60])
    extraction = extract_claims(claim_text, ctx)
    central = central_claim(extraction)
    logger.info(
        "run_pipeline[%s]: Stage 2+3 → %d propositions, central type=%s, action=%s (source=%s, pivoted_from=%s)",
        invocation_id, len(extraction.claims), central.type,
        extraction.action, extraction.action_source, extraction.pivoted_from,
    )

    if extraction.action == "decline":
        return _short_circuit_decline(
            invocation_id=invocation_id,
            invocation_time=invocation_time,
            target_tweet_id=target_tweet_id,
            modality=modality,
            backend_name=backend.name,
            image_evidence=image_evidence,
            extraction=extraction,
            thread_context_str=thread_context_str,
            invoker_instruction_text=invoker_instruction or "",
            freeze_root=freeze_root,
        )

    # Stage 4+ uses the central proposition's text — typically more focused
    # than the raw tweet (hedges stripped, framing normalized).
    central_text = central.text
    logger.info("run_pipeline[%s]: Stage 4 — iterative verification (Papelo-style)", invocation_id)
    text_evidence = iterative_verify(central_text, ctx, backend=backend, action=extraction.action)
    logger.info("run_pipeline[%s]: Stage 4 done (%d evidence records)", invocation_id, len(text_evidence))

    # Roll image-provenance hits into the source-quality table too — Claude is
    # told to cite from `source_quality_table` only, and image-derived URLs are
    # legitimate evidence sources.
    all_urls = [e.source_url for e in text_evidence]
    for img in image_evidence:
        all_urls.extend(h.url for h in img.provenance_hits)
    quality_table = build_quality_table(all_urls)

    evidence = text_evidence  # default; the reconcile branch re-stamps with stances

    if not text_evidence and not image_evidence:
        findings, payload, justification = _nei_verdict(central_text)
        lens_1 = Lens1(narrative="No evidence retrieved.")
        verdict_label = "NotEnoughEvidence"
    elif not text_evidence and not _has_credible_evidence(quality_table):
        # Image-only mention whose provenance hits are all in
        # low-quality/satirical/unknown/aggregator tiers — reconcile would
        # land on NEI anyway. Skip the expensive reasoning_effort=medium
        # call (~5-10s + one Claude call saved).
        logger.info(
            "run_pipeline[%s]: Stage 4.5 — skipping reconcile (image-only, no credible-tier provenance)",
            invocation_id,
        )
        findings, payload, justification = _nei_verdict(
            central_text, reason_label="evidence retrieved but silent",
        )
        lens_1 = Lens1(narrative="Image provenance retrieved but no credible-tier sources.")
        verdict_label = "NotEnoughEvidence"
    else:
        logger.info("run_pipeline[%s]: Stage 4.5 — reconcile", invocation_id)
        recon = reconcile(
            central_text,
            evidence=text_evidence,
            source_quality_table=quality_table,
            ctx=ctx,
            action=extraction.action,
        )
        logger.info("run_pipeline[%s]: Stage 4.5 done", invocation_id)
        stamped_evidence = [
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
        evidence = stamped_evidence

    # action_outcome is the canonical post-Stage-4.5 label. verdict_label
    # is retained transitionally so legacy readers still get something.
    action_outcome = derive_action_outcome(extraction.action, findings, quality_table)
    verdict_label = derive_verdict(findings, quality_table)

    logger.info(
        "run_pipeline[%s]: Stage 5 — audit (action=%s, declared_outcome=%s)",
        invocation_id, extraction.action, action_outcome,
    )
    audit_result = audit(
        action=extraction.action,
        declared_outcome=action_outcome,
        findings=findings,
        source_quality_table=quality_table,
        presentation_payload=payload,
        tone_neutral_justification=justification,
    )
    logger.info(
        "run_pipeline[%s]: Stage 5 audit %s (failures=%d)",
        invocation_id, "passed" if audit_result.passed else "FAILED → audit-fail outcome",
        len(audit_result.failures),
    )
    if not audit_result.passed:
        action_outcome = audit_fail_outcome_for(extraction.action)
        verdict_label = "NotEnoughEvidence"
        findings, payload, justification = _nei_verdict(
            "Audit failed: " + "; ".join(audit_result.failures),
            reason_label="evidence retrieved but silent",
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
        thread_context=thread_context_str,
        modality=modality,
        backend_version=BackendVersion(
            model="claude-via-azure-ai-services",
            vlm_model="claude-via-azure-ai-services" if image_evidence else "",
            search_provider=backend.name,
            pipeline_commit=_git_sha(),
            source_reliability_lists_version={"curated_lists": _PIPELINE_VERSION, "model_prior": "claude-sonnet"},
        ),
        attached_images=tuple(_attached_image_records(image_evidence)),
        claims=tuple(claim_objs),
        cross_modal_report=cross_modal,
        consolidated_findings=findings,
        source_quality_table=quality_table,
        action=extraction.action,
        action_source=extraction.action_source,
        pivoted_from=extraction.pivoted_from,
        invoker_instruction_text=invoker_instruction or "",
        action_outcome=action_outcome,
        verdict_label=verdict_label,
        tone_neutral_justification=justification,
        presentation_payload=payload,
    )

    freeze_to_disk(frozen, root=freeze_root)
    return frozen
