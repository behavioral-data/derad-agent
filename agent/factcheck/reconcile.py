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
)


class ReconciliationOutput(BaseModel):
    lens_1: Lens1
    consolidated_findings: ConsolidatedFindings
    presentation_payload: PresentationPayload
    tone_neutral_justification: str
    evidence_stances: list[Stance]


_SYSTEM_PROMPT = """You are the Evidence Reconciliation stage of a fact-checking pipeline.

You receive (a) the central claim text, (b) an ordered list of text evidence snippets each tagged with a source URL, (c) the source-quality table classifying each URL by tier, and (d) when the claim is image-bearing, per-image evidence (OCR'd text inside the image, a neutral visual description, and Bing-grounded search hits about the image's subject).

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
) -> ReconciliationOutput:
    """Run Stage 4.5; returns the structured output.

    `image_evidence` (when non-empty) folds Lens 2 / Lens 3 reasoning into
    the same Claude call. Per-image OCR text, description, and provenance
    search hits are passed alongside the text evidence; Claude is
    instructed to reason about cross-modal alignment inline.
    """
    payload: dict = {
        "central_claim": central_claim_text,
        "evidence": [e.model_dump() for e in evidence],
        "source_quality_table": [s.model_dump() for s in source_quality_table],
    }
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
    output = call_claude_json(
        prompt=user_prompt,
        schema=ReconciliationOutput,
        system=_SYSTEM_PROMPT,
        reasoning_effort="medium",
        max_tokens=4096,
    )
    if len(output.evidence_stances) != len(evidence):
        raise ValueError(
            f"Reconciliation returned {len(output.evidence_stances)} stances for "
            f"{len(evidence)} evidence entries."
        )
    return output
