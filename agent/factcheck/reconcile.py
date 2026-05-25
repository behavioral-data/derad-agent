"""Stage 4.5 — Evidence Reconciliation.

Text-only path produces `lens_1`, `consolidated_findings`,
`presentation_payload`, and `tone_neutral_justification` in a single
Claude call. When image evidence is supplied, the same call also
performs Lens 2 (image-text) and Lens 3 (cross-modal) reasoning inline
— Claude sees OCR + description + provenance hits per image as
additional grounded context.

The verdict label is NOT emitted here — it is derived from
`consolidated_findings` and `source_quality_table` by the structural
rule in `verdict.py`.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import BaseModel

from .context import PipelineContext
from .llm import call_claude_json, pruned_context
from .schema import (
    Action,
    ConsolidatedFindings,
    Evidence,
    Lens1,
    PresentationPayload,
    SourceQualityEntry,
    Stance,
    UnaddressedProposition,
)


logger = logging.getLogger(__name__)


_RECONCILE_SNIPPET_CAP = 240
_RECONCILE_RATIONALE_CAP = 120


def _compact_evidence(e: Evidence) -> dict:
    """Trim per-evidence snippet to keep the reconcile prompt under control."""
    snippet = (e.snippet or "")[:_RECONCILE_SNIPPET_CAP]
    return {
        "question": e.question,
        "source_url": e.source_url,
        "snippet": snippet,
        "stance": e.stance,
    }


def _compact_quality_entry(s: SourceQualityEntry) -> dict:
    """Trim rationale for the reconcile prompt — reconcile only needs the tier."""
    return {
        "url": s.url,
        "tier": s.tier,
        "tier_source": s.tier_source,
        "rationale": (s.rationale or "")[:_RECONCILE_RATIONALE_CAP],
    }


class ReconciliationOutput(BaseModel):
    lens_1: Lens1
    consolidated_findings: ConsolidatedFindings
    presentation_payload: PresentationPayload
    tone_neutral_justification: str
    evidence_stances: list[Stance]


_SYSTEM_PROMPT = """You are the Evidence Reconciliation stage of a fact-checking bot. You operate in one of FOUR modes determined by the input `action` field: verify, provide_context, challenge_opinion, surface_perspectives. Read the action-specific section below; the shared rules apply to every action.

You receive (a) `central_claim` text, (b) `action` ∈ {verify, provide_context, challenge_opinion, surface_perspectives}, (c) `tweet_context` — metadata about the parent tweet, (d) ordered text evidence snippets each tagged with source URL, (e) the source-quality table classifying every URL by tier, and (f) when the claim is image-bearing, per-image evidence (OCR + description + optional `canonical_image_match` + web-search provenance hits).

═══════════════════════════════════════════════════════════════
TWEET_CONTEXT — used by every action
═══════════════════════════════════════════════════════════════
- **`author_username`** + **`author_description`**: is the handle the actual person being quoted (e.g., @ElonMusk for an Elon Musk quote)? Then the claim is a primary statement. Parody/satire/fan account (bio contains "parody", "satire", "fake", "fanpage", "joke", "not affiliated", etc.)? Then the content is NOT a real statement from the named person — surface that.
- **`author_verified`**: blue-check / business / government. Useful signal but NOT a guarantee of accuracy.
- **`author_created_at`** + **`author_followers_count`**: very new + tiny follower count + extraordinary claim = bot/spam pattern.
- **`posted_at`**: mismatch between tweet's posted_at and the cited event date often means recycled old content.
- **`expanded_urls`**: t.co links resolved here. If the linked article supports/contradicts the claim, treat as evidence.
- **`referenced_tweets`**: quoted / retweeted / replied_to framing.
- **`public_metrics`**: virality is not truth.

═══════════════════════════════════════════════════════════════
SHARED RULES — every action
═══════════════════════════════════════════════════════════════
- Do not invent sources. Every URL you cite must appear in the input source_quality_table.
- Do not emit verdict_label — derived downstream.
- Be conservative: thin / low-quality evidence ⇒ "unaddressed" / `_unavailable` / `_insufficient` rather than a confident finding.
- Out of scope (Tier 4): claims about whether an image is altered / deepfaked / AI-generated. If the only checkable angle is image-authenticity, treat as unaddressed.
- Re-stamp each input text evidence with a stance (supports / refutes / neutral) in INPUT ORDER (used by every action).
- Lens 1 narrative (text-text reconciliation) is always written; surface cross-source contradictions there.
- For image-bearing claims, fold image-text + cross-modal reasoning INTO the narrative (lens_1.narrative) — Lens 2/3 are not separate outputs at this stage.
- BUDGET (critical — downstream renderer fits ≤256 X-weighted chars):
    - `headline_finding`: ≤120 chars, one punchy sentence.
    - `counter_fact`: ≤120 chars; null unless action=verify AND finding is refuted.
    - `tone_neutral_justification`: ≤220 chars; 1–2 sentences; name load-bearing source(s).
    - `load_bearing_evidence_snippet`: ≤180 chars.
    - `context_note`: ≤220 chars; the missing context the framing hides.
    - Counterpoint.summary: ≤160 chars each; aim for 1–3 counterpoints.
    - Perspective.summary: ≤200 chars each; aim for 2–4 perspectives.

═══════════════════════════════════════════════════════════════
ACTION-SPECIFIC OUTPUT
═══════════════════════════════════════════════════════════════

▌action == "verify"
Populate the existing buckets — verified_propositions / refuted_propositions / disputed_propositions / unaddressed_propositions. Mark the central proposition with `is_central=true`.
- presentation_payload.headline_finding: the one most important fact.
- presentation_payload.counter_fact: set when refuted; otherwise null.
- presentation_payload.primary_sources_to_cite: 1–3 sources, fact-checker > reputable-news.
- presentation_payload.load_bearing_evidence_snippet: a short quote.
- DO NOT populate context_note / counterpoints / perspectives.

▌action == "provide_context"
The literal claim may BE TRUE — the goal is to surface the missing context that changes how a reader should interpret it. STRICT RULE: do NOT populate verified_propositions or refuted_propositions for the central claim. Use contextual_findings instead.
- consolidated_findings.contextual_findings: one entry with is_central=true; `missing_context` is the framing the claim hides.
- presentation_payload.headline_finding: the missing context in one sentence (not "this is true" — the bot will read as missing-context).
- presentation_payload.context_note: ≤220 chars; the missing context.
- presentation_payload.primary_sources_to_cite: 1–3 sources backing the missing context.
- counter_fact: null. counterpoints / perspectives: empty.

▌action == "challenge_opinion"
The central proposition is a strongly-stated opinion. Surface counterpoints from NAMED credible critics (not pundit echo chambers).
- consolidated_findings.challenged_propositions: one entry with is_central=true, containing 1–3 counterpoints.
- Each Counterpoint: summary (≤160 chars), citing_sources (≥1 TierRef from source_quality_table, prefer reputable-news / fact-checker / primary-source), weight ∈ {strong, moderate, weak}.
- presentation_payload.headline_finding: the strongest counterpoint in one sentence.
- presentation_payload.counterpoints: same 1–3 Counterpoint objects.
- presentation_payload.primary_sources_to_cite: 1–3 sources used by the counterpoints (renderer will cite at least one).
- counter_fact: null. context_note / perspectives: empty / null.

▌action == "surface_perspectives"
The topic is genuinely contested. Surface ≥2 distinct credible perspectives, each with ≥1 reputable source.
- consolidated_findings.perspectives: 2–4 Perspective entries, each with label (≤60 chars; e.g. "Economic-cost view"), summary (≤200 chars), citing_sources (≥1 TierRef).
- presentation_payload.headline_finding: a one-sentence framing of the disagreement (NOT a side).
- presentation_payload.perspectives: same Perspective objects.
- presentation_payload.primary_sources_to_cite: 1–3 sources spanning multiple perspectives.
- Mark the central proposition in… see below.
- counter_fact: null. context_note: null. counterpoints: empty.

CENTRAL-PROPOSITION INVARIANT:
The freeze schema requires the central proposition to appear in EXACTLY ONE bucket among: verified/refuted/disputed/unaddressed/contextual/challenged/perspectives. For surface_perspectives, the central proposition is the TOPIC STATEMENT — put it in unaddressed_propositions with reason="evidence retrieved but silent" and is_central=true (the topic itself can't be "verified"; perspectives capture the substance). For provide_context, central goes in contextual_findings. For challenge_opinion, central goes in challenged_propositions. For verify, the existing four buckets.

WHEN TEXT EVIDENCE IS EMPTY:
- If image evidence is present and the image IS the central claim's subject (e.g. canonical_image_match populated with high confidence + claim is about the image), use image-provenance hits as evidence and apply the action-specific output normally.
- If the image is incidental and only image-provenance URLs exist, set primary_sources_to_cite=[] and place the central proposition in unaddressed_propositions with reason="evidence retrieved but silent". Downstream the bot will collapse to the action's `_unavailable` / `_insufficient` outcome.
"""


def reconcile(
    central_claim_text: str,
    *,
    evidence: list[Evidence],
    source_quality_table: list[SourceQualityEntry],
    ctx: PipelineContext,
    action: Action = "verify",
) -> ReconciliationOutput:
    """Run Stage 4.5; returns the structured output.

    `ctx.image_evidence` (when non-empty) folds Lens 2 / Lens 3 reasoning
    into the same Claude call. Per-image OCR text, description, and
    provenance search hits are passed alongside the text evidence; Claude
    is instructed to reason about cross-modal alignment inline.

    `ctx.tweet_context` carries the parent tweet's surrounding metadata —
    author handle/bio/verified/account-age, posted-at, expanded t.co URLs,
    referenced-tweet relations, language, sensitive flag, public metrics.
    Reconcile uses it to interpret the claim (parody account, third-party
    aggregator, primary statement, etc.) and to date-stamp recency claims.
    """
    payload: dict = {
        "central_claim": central_claim_text,
        "action": action,
        "evidence": [_compact_evidence(e) for e in evidence],
        "source_quality_table": [_compact_quality_entry(s) for s in source_quality_table],
    }
    cleaned_ctx = pruned_context(ctx.tweet_context)
    if cleaned_ctx:
        payload["tweet_context"] = cleaned_ctx
    if ctx.image_evidence:
        payload["image_evidence"] = [
            img.to_prompt_with_provenance() for img in ctx.image_evidence
        ]

    user_prompt = json.dumps(payload, indent=2)
    try:
        output = call_claude_json(
            prompt=user_prompt,
            schema=ReconciliationOutput,
            system=_SYSTEM_PROMPT,
            reasoning_effort="medium",
            max_tokens=4096,
            timeout=90.0,
        )
    except (ValueError, TimeoutError) as exc:
        # Refusal/parse failure or stage timeout. Degrade to a "could not
        # reason" output — central claim lands in unaddressed_propositions,
        # no source citations. The renderer's state becomes "no_sources"
        # and produces a tone-aware reply via the model. Pipeline keeps
        # going; mention gets a real reply.
        logger.warning(
            "reconcile: call_claude_json failed (%s) — degrading to no-sources output", exc,
        )
        return ReconciliationOutput(
            lens_1=Lens1(narrative="Reconciliation failed; central claim could not be analyzed."),
            consolidated_findings=ConsolidatedFindings(
                unaddressed_propositions=(
                    UnaddressedProposition(
                        proposition=central_claim_text,
                        reason="evidence retrieved but silent",
                        is_central=True,
                    ),
                ),
            ),
            presentation_payload=PresentationPayload(
                headline_finding="Could not analyze this claim against the available evidence.",
                counter_fact=None,
                primary_sources_to_cite=(),
                load_bearing_evidence_snippet="",
            ),
            tone_neutral_justification="The model declined to reason about this claim or returned an unparseable response; no verdict available.",
            evidence_stances=["neutral"] * len(evidence),
        )
    # Stance count drift can happen when image-only or evidence list is
    # empty — Claude sometimes stamps stances for image-provenance hits.
    # Don't crash the pipeline; truncate or pad to match the text-evidence
    # count so the caller's zip(evidence, stances) is well-defined.
    if len(output.evidence_stances) != len(evidence):
        delta = abs(len(output.evidence_stances) - len(evidence))
        logger.warning(
            "reconcile: returned %d stances for %d evidence entries — repairing (delta=%d).",
            len(output.evidence_stances), len(evidence), delta,
        )
        try:
            from agent.app import metrics as _metrics
            _metrics.reconcile_stance_drift.add(1, {"delta": str(delta)})
        except ImportError:
            pass  # tests / isolated runs without the metrics module
        stances = list(output.evidence_stances)[: len(evidence)]
        while len(stances) < len(evidence):
            stances.append("neutral")
        output.evidence_stances = stances
    return output
