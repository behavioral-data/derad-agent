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

from .llm import call_claude_json
from .multimodal import ImageEvidence
from .schema import (
    ConsolidatedFindings,
    Evidence,
    Lens1,
    PresentationPayload,
    SourceQualityEntry,
    Stance,
    UnaddressedProposition,
)


logger = logging.getLogger(__name__)


class ReconciliationOutput(BaseModel):
    lens_1: Lens1
    consolidated_findings: ConsolidatedFindings
    presentation_payload: PresentationPayload
    tone_neutral_justification: str
    evidence_stances: list[Stance]


_SYSTEM_PROMPT = """You are the Evidence Reconciliation stage of a fact-checking pipeline.

You receive (a) the central claim text, (b) `tweet_context` — metadata about the parent tweet (author handle/bio/verified/account-age, posted-at, expanded t.co URLs, referenced-tweet relations, etc.), (c) an ordered list of text evidence snippets each tagged with a source URL, (d) the source-quality table classifying each URL by tier, and (e) when the claim is image-bearing, per-image evidence (OCR'd text, visual description, and Bing-grounded provenance search hits).

Use `tweet_context` actively. In particular:
- **`author_username`** and **`author_description`** (bio): is the handle the actual person being quoted (e.g., @ElonMusk for an Elon Musk quote)? Then the claim is a primary statement and the question is whether it's authentic. Is the handle or bio a parody/satire/fan account (contains "parody", "satire", "fake", "fanpage", "joke", "not affiliated", "unofficial", etc.)? Then the content is not a real statement from the named person — surface that. Is the handle a third-party aggregator or news-style influencer? Then this is a RE-report ABOUT a public figure, and the question is whether what's being reported is true.
- **`author_verified`** / **`author_verified_type`**: blue-check / business / government / none. Useful signal but NOT a guarantee of accuracy — verified accounts can still post false claims.
- **`author_created_at`** + **`author_followers_count`**: very new account + tiny follower count + extraordinary claim = bot/spam pattern. Surface that if relevant.
- **`posted_at`**: when the claim was made. For claims about "recent" or "breaking" events, mismatch between the tweet's posted_at and the actual event date is a giveaway that the tweet is recycling old content. If the evidence places the cited event years before the tweet's posted_at, the tweet is misframing the recency.
- **`expanded_urls`**: any short `t.co` link in the claim resolves to a real URL here, with the page title. If the linked article exists and supports/contradicts the claim, treat the URL like any other evidence URL.
- **`referenced_tweets`**: if `type=quoted`, the parent is quote-tweeting another tweet (we don't have its content, just the relation). If `type=retweeted` or `type=replied_to`, note the framing.
- **`public_metrics`**: virality is not truth. Don't treat high engagement as evidence of accuracy.

Use the source_quality_table for evidence-source classification; tweet_context informs how you interpret the CLAIM itself, not the evidence.

WHEN TEXT EVIDENCE IS EMPTY BUT IMAGE EVIDENCE IS PRESENT — handle these two cases distinctly:
- The image IS the central claim's subject (the post claims "this photo shows X", "this image proves Y", or the central proposition is literally about what's depicted). Image-provenance hits in source_quality_table CAN be cited if they directly speak to the image's identity, source, or context.
- The image is incidental (an illustration / meme / generic visual that accompanies the text claim but doesn't carry it). Image-provenance hits typically address what the image IS, NOT whether the central text claim is true. In that case, do NOT cite image-provenance URLs as evidence for the text claim. Set primary_sources_to_cite=[] and place the central proposition in unaddressed_propositions with reason="evidence retrieved but silent" — the bot will emit a "no credible coverage" reply, not a misleading citation.

Your job is to reconcile all the evidence and emit Lens 1 (text-text) findings. When image evidence is present, also reason about image-text alignment and cross-modal contradictions inline:

1. For each atomic proposition implied by the central claim, decide whether it is verified, refuted, disputed, or unaddressed by the combined evidence. Cite the supporting URLs from the source-quality table. Mark the central proposition with is_central=true.
2. Surface any cross-source contradictions in lens_1.
3. When images are present:
   a. Compare the image's OCR'd text and visual description against the caption / surrounding claim text. If the image shows something the caption says ("photo of a flooded street, caption says flood in city X"), that strengthens the relevant proposition. If the image shows something different ("photo shows a great-grandmother and child, caption says woman gave birth at 101"), that refutes the caption.
   b. Use the image's provenance search hits to surface whether the image has been previously published in a different context. Treat those hits like any other text evidence — cite their URLs from the source-quality table if they're in it.
4. Re-stamp each input text evidence with a stance (supports/refutes/neutral) in input order.
5. Choose ONE headline_finding — the single most important fact the bot should communicate.
6. If the verdict will be Refuted, set counter_fact to the correct version of the claim. Otherwise null.
7. Pick 1–3 primary_sources_to_cite, preferring fact-checker > reputable-news tiers from the source_quality_table. Provide a short display_name (e.g. "Snopes").
8. Pick one short load_bearing_evidence_snippet to optionally quote in the reply.
9. Write a 1–3 sentence tone_neutral_justification anchored to the cited sources.

Hard rules:
- Do not invent sources. Every URL you cite must appear in the input source_quality_table.
- Do not emit a verdict label — that is computed structurally downstream.
- Be conservative: if evidence is thin or only from low-quality/satirical/unknown tiers, mark propositions unaddressed/disputed rather than verified/refuted.
- Do NOT make claims about whether an image is altered, deepfaked, or AI-generated. That question is out of scope (Tier 4 forced NEI per spec). If the only checkable angle is image-authenticity, treat the proposition as unaddressed.
- BUDGET (critical — downstream renderer must fit ≤270 chars across three tones, with a 23-char URL):
    - `headline_finding`: ≤120 characters. ONE punchy sentence.
    - `counter_fact`: ≤120 characters. ONE corrective sentence; null when not refuted.
    - `tone_neutral_justification`: ≤220 characters. 1–2 sentences. Name the load-bearing source(s).
    - `load_bearing_evidence_snippet`: ≤180 characters.
"""


def reconcile(
    *,
    central_claim_text: str,
    evidence: list[Evidence],
    source_quality_table: list[SourceQualityEntry],
    image_evidence: Optional[list[ImageEvidence]] = None,
    tweet_context: Optional[dict] = None,
) -> ReconciliationOutput:
    """Run Stage 4.5; returns the structured output.

    `image_evidence` (when non-empty) folds Lens 2 / Lens 3 reasoning into
    the same Claude call. Per-image OCR text, description, and provenance
    search hits are passed alongside the text evidence; Claude is
    instructed to reason about cross-modal alignment inline.

    `tweet_context` carries the parent tweet's surrounding metadata —
    author handle/bio/verified/account-age, posted-at, expanded t.co URLs,
    referenced-tweet relations, language, sensitive flag, public metrics.
    Reconcile uses it to interpret the claim (parody account, third-party
    aggregator, primary statement, etc.) and to date-stamp recency claims.
    """
    payload: dict = {
        "central_claim": central_claim_text,
        "evidence": [e.model_dump() for e in evidence],
        "source_quality_table": [s.model_dump() for s in source_quality_table],
    }
    if tweet_context:
        # Drop None / empty values so the prompt doesn't get noise.
        clean = {k: v for k, v in tweet_context.items() if v not in (None, "", [], {})}
        if clean:
            payload["tweet_context"] = clean
    if image_evidence:
        payload["image_evidence"] = [
            {
                "image_url": img.image_url,
                "ocr_text": img.ocr_text,
                "description": img.description,
                "provenance_search_hint": img.search_hint,
                "provenance_hits": [
                    {"url": h.url, "title": h.title, "snippet": h.snippet}
                    for h in img.provenance_hits
                ],
            }
            for img in image_evidence
        ]

    user_prompt = json.dumps(payload, indent=2)
    try:
        output = call_claude_json(
            prompt=user_prompt,
            schema=ReconciliationOutput,
            system=_SYSTEM_PROMPT,
            reasoning_effort="medium",
            max_tokens=4096,
        )
    except Exception as exc:
        # Refusal or parse failure. Degrade to a "could not reason" output —
        # central claim lands in unaddressed_propositions, no source citations.
        # The renderer's state becomes "no_sources" and produces a tone-aware
        # reply via the model. Pipeline keeps going; mention gets a real reply.
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
        logger.warning(
            "reconcile: returned %d stances for %d evidence entries — repairing.",
            len(output.evidence_stances), len(evidence),
        )
        stances = list(output.evidence_stances)[: len(evidence)]
        while len(stances) < len(evidence):
            stances.append("neutral")
        output.evidence_stances = stances
    return output
