"""Stage 2 + 3 — claim extraction + check-worthiness gate (design §4.2, §4.3).

A single Claude call decomposes the parent tweet into atomic propositions,
labels each as `verifiable | opinion | mixed`, marks exactly one as
`is_central`, and flags whether each warrants fact-checking effort.

When NO proposition is both verifiable (or mixed) AND check-worthy, the
pipeline short-circuits with `overall_state = "no_checkable_claim"` —
Stages 4-6 are skipped and the renderer emits a tone-appropriate
"nothing to fact-check here" template (no URL required for this path).
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field, model_validator

from .context import PipelineContext
from .llm import call_claude_json, pruned_context
from .schema import ClaimType, OverallState


logger = logging.getLogger(__name__)


class ExtractedClaim(BaseModel):
    text: str = Field(description="A single declarative restatement of the proposition.")
    type: ClaimType
    source_span: str = Field(
        default="",
        description="Verbatim quote from the input tweet that motivates this proposition.",
    )
    is_central: bool
    check_worthy: bool = Field(
        description="True only if this proposition is BOTH verifiable/mixed AND worth fact-checking effort (not trivial, not vague generality, not pure restatement)."
    )
    rationale: str = Field(default="")


class ExtractionOutput(BaseModel):
    claims: list[ExtractedClaim] = Field(
        description="Atomic propositions extracted from the tweet. EXACTLY ONE must have is_central=true.",
    )
    overall_state: OverallState = Field(
        description='"checked" when at least one claim is verifiable/mixed AND check_worthy. "no_checkable_claim" otherwise — bot will skip search and render a "no fact-check needed" reply.'
    )
    reason: str = Field(
        default="",
        description="When overall_state='no_checkable_claim', a short reason (≤ 15 words) the bot can paraphrase. Empty otherwise.",
    )

    @model_validator(mode="after")
    def _exactly_one_central(self) -> "ExtractionOutput":
        if not self.claims:
            raise ValueError("ExtractionOutput must contain at least one claim.")
        central_count = sum(1 for c in self.claims if c.is_central)
        if central_count != 1:
            raise ValueError(
                f"ExtractionOutput must mark exactly one claim is_central=true (got {central_count})."
            )
        return self


_SYSTEM_PROMPT = """You are the claim-extraction stage of a fact-checking pipeline. Your job is to decompose a tweet into atomic, fact-checkable propositions — and to identify when there's nothing to check.

INPUT: the parent tweet text, optionally with image OCR + description, plus context about the tweet (author handle/bio, posted date, expanded URLs).

For each atomic proposition you find:
- `text`: a single declarative restatement, normalized for clarity. Strip hedges ("I think", "reportedly", "apparently") so the proposition itself is testable.
- `type`:
    - "verifiable" — the proposition asserts something that could be confirmed or refuted against the public record (a specific event happened, a person said a specific quote, a photo depicts a specific named entity, a statistic, a date).
    - "opinion" — the proposition is subjective, aesthetic, evaluative, predictive about the future, or a value judgment ("X is smart", "the architecture is impressive", "Y will win", "Z is the best").
    - "mixed" — has a verifiable factual core plus an opinion frame ("Musk's [verifiable: 2023] Neuralink demo was [opinion: groundbreaking]"). Treat the verifiable core as the testable part.
- `source_span`: a short verbatim quote from the tweet (or image OCR) that this proposition is drawn from. Helps audit later.
- `is_central`: TRUE for exactly one proposition — the one the tweet is most centrally making. The headline of the post. Bias toward the most extraordinary/falsifiable claim if there are ties.
- `check_worthy`: TRUE only when BOTH (a) type ∈ {verifiable, mixed} AND (b) the proposition meaningfully impacts what a reader would believe if they took the tweet at face value. FALSE for: pure opinion, vague generalities ("things are bad"), trivial restatements ("this happened"), well-known background facts the bot needn't re-verify ("Paris is in France").
- `rationale`: one short sentence on why you assigned this type/check_worthy.

HEURISTICS:
- Be CONSERVATIVE on "verifiable". When in doubt between verifiable and opinion, choose opinion. The bot exists to fact-check; spurious fact-checks of opinion content are worse than missed fact-checks.
- Image-bearing tweets: treat "this photo depicts X" / "this photo proves Y" / "the image shows Z" as VERIFIABLE — the image's identity, source, and context are checkable via reverse search and reverse-image grounding.
- Quote attributions: "X said Y" is VERIFIABLE if X is a named person and Y is a specific quote.
- Statistics / dates / outcomes of named events: VERIFIABLE.
- "Breaking", "shocking", "you won't believe": NOT a proposition; ignore the framing word.
- A tweet that's mostly a personal reflection or aesthetic reaction usually contains zero verifiable claims. In that case emit a single proposition summarizing the overall stance with type=opinion and overall_state="no_checkable_claim".

CENTRAL-CLAIM DEFAULTS:
- If the tweet contains exactly one verifiable proposition, mark it central.
- If multiple, prefer the one that drives the rest (the headline) — usually the most extraordinary, the most quoted, or the one the image directly supports.
- The central proposition may be opinion when the tweet has only opinion content; in that case overall_state="no_checkable_claim".

OVERALL_STATE:
- "checked": at least one proposition is verifiable/mixed AND check_worthy → the pipeline runs.
- "no_checkable_claim": no proposition meets that bar → the pipeline short-circuits. Provide a short `reason` (≤15 words) the bot can paraphrase to the user (e.g. "personal opinion, not a factual claim"; "aesthetic reaction with no checkable proposition").

OUTPUT: one JSON object matching the schema. EXACTLY one claim must have is_central=true. Both states must produce a `claims` list (at minimum one entry summarizing what the tweet says).
"""


def extract_claims(claim_text: str, ctx: PipelineContext) -> ExtractionOutput:
    """Run Stage 2 + 3. Returns the structured ExtractionOutput.

    On LLM failure, returns a safe fallback that treats the whole input as
    one verifiable central claim (preserves pre-Stage-2 behaviour).
    """
    payload: dict = {"tweet_text": claim_text}
    cleaned_ctx = pruned_context(ctx.tweet_context)
    if cleaned_ctx:
        payload["tweet_context"] = cleaned_ctx
    if ctx.image_evidence:
        payload["image_evidence"] = [img.to_prompt_summary() for img in ctx.image_evidence]

    user_prompt = json.dumps(payload, indent=2)
    try:
        # ExtractionOutput's @model_validator enforces exactly-one-central;
        # call_claude_json raises ValueError on JSON-parse or schema failure.
        return call_claude_json(
            prompt=user_prompt,
            schema=ExtractionOutput,
            system=_SYSTEM_PROMPT,
            reasoning_effort="low",
            max_tokens=2048,
            timeout=45.0,
        )
    except (ValueError, TimeoutError) as exc:
        # Parse/schema failure or wall-clock timeout — degrade to fallback so
        # the pipeline still produces a reply. Unexpected exceptions propagate.
        logger.warning("extract_claims: degrading to whole-tweet fallback (%s)", exc)
        return _fallback(claim_text)


def _fallback(claim_text: str) -> ExtractionOutput:
    """Pre-Stage-2 behaviour: treat the whole input as one verifiable central
    claim. Used when extraction fails — safer to run the rest of the pipeline
    than to silently drop the mention."""
    return ExtractionOutput(
        claims=[
            ExtractedClaim(
                text=claim_text,
                type="verifiable",
                source_span=claim_text[:160],
                is_central=True,
                check_worthy=True,
                rationale="extraction fallback — extractor failed or returned malformed output",
            )
        ],
        overall_state="checked",
        reason="",
    )


def central_claim(extraction: ExtractionOutput) -> ExtractedClaim:
    """Convenience: return the (one) central claim from an extraction."""
    return next(c for c in extraction.claims if c.is_central)
