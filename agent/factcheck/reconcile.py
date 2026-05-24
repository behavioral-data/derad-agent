"""Stage 4.5 — Evidence Reconciliation (text-text lens for the thin slice).

Produces `lens_1`, `consolidated_findings`, `presentation_payload`, and
`tone_neutral_justification` in a single Claude call. The verdict label is
NOT emitted here — it is derived from `consolidated_findings` and
`source_quality_table` by the structural rule in `verdict.py`.
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from .llm import call_claude_json
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

You receive (a) the central claim, (b) an ordered list of evidence snippets each tagged with a source URL, and (c) the source-quality table that classifies each URL by tier.

Your job is to perform text-text reconciliation (Lens 1 of the three-lens cascade):

1. For each atomic proposition implied by the central claim, decide whether it is verified, refuted, disputed, or unaddressed by the evidence. Cite the supporting URLs from the source-quality table. Mark the central proposition with is_central=true.
2. Surface any cross-source contradictions in lens_1.
3. Re-stamp each input evidence with a stance (supports/refutes/neutral) in input order.
4. Choose ONE headline_finding — the single most important fact the bot should communicate.
5. If the verdict will be Refuted, set counter_fact to the correct version of the claim. Otherwise null.
6. Pick 1–3 primary_sources_to_cite, preferring fact-checker > reputable-news tiers from the source_quality_table. Provide a short display_name (e.g. "Snopes").
7. Pick one short load_bearing_evidence_snippet to optionally quote in the reply.
8. Write a 1–3 sentence tone_neutral_justification anchored to the cited sources.

Hard rules:
- Do not invent sources. Every URL you cite must appear in the input source_quality_table.
- Do not emit a verdict label — that is computed structurally downstream.
- Be conservative: if evidence is thin or only from low-quality/satirical/unknown tiers, mark propositions unaddressed/disputed rather than verified/refuted.
"""


def reconcile(
    *,
    central_claim_text: str,
    evidence: list[Evidence],
    source_quality_table: list[SourceQualityEntry],
) -> ReconciliationOutput:
    """Run Stage 4.5 text-text reconciliation; returns the structured output."""
    user_prompt = json.dumps(
        {
            "central_claim": central_claim_text,
            "evidence": [e.model_dump() for e in evidence],
            "source_quality_table": [s.model_dump() for s in source_quality_table],
        },
        indent=2,
    )
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
