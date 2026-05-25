"""Stage 2 + 3 — claim extraction + action selection (design §4.2–§4.3).

A single Claude call:
  (a) decomposes the parent tweet into atomic propositions, labels each as
      `verifiable | opinion | mixed`, marks exactly one `is_central`.
  (b) parses the invoker's instruction (the text in the mention itself,
      after the bot handle is stripped) — empty when invoker only tagged.
  (c) selects the bot's action from {verify, provide_context,
      challenge_opinion, surface_perspectives, decline} based on (i) what
      the invoker asked, (ii) what the claim character supports.

If the invoker explicitly asks for one action but the claim doesn't
support it (e.g. asks to fact-check pure opinion), the model pivots
silently to a fitting action and records the original ask as
`pivoted_from` — the renderer surfaces this in a one-clause disclosure.

When `action == "decline"`, Stages 4–5 are skipped via the pipeline's
short-circuit and the renderer emits a tone-aware "nothing actionable"
reply.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from .context import PipelineContext
from .llm import call_claude_json, pruned_context
from .schema import Action, ActionSource, ClaimType, OverallState


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
    suggested_action: Action = Field(
        default="verify",
        description="What the bot should DO with this proposition — verify against evidence, provide missing context, push back as opinion, surface multiple sides, or decline.",
    )
    action_rationale: str = Field(default="")
    rationale: str = Field(default="")


class ExtractionOutput(BaseModel):
    claims: list[ExtractedClaim] = Field(
        description="Atomic propositions extracted from the tweet. EXACTLY ONE must have is_central=true.",
    )
    # Pipeline-wide action — chosen for the WHOLE mention, not per-claim.
    # When the invoker is explicit, parses their ask; when silent, infers
    # from the central proposition's character.
    action: Action = Field(
        default="verify",
        description="The bot's chosen action for this mention. Drives Stage 4 search strategy, Stage 4.5 reconcile prompt, and Stage 7 render template.",
    )
    action_source: ActionSource = Field(
        default="inferred",
        description='"explicit" when the invoker stated the action in their mention; "inferred" when they only tagged and silent; "explicit_but_unactionable" when they asked for an action that doesn\'t fit the claim (model pivoted).',
    )
    pivoted_from: Optional[Action] = Field(
        default=None,
        description="When action_source='explicit_but_unactionable', the action the invoker originally asked for (now to be disclosed to the reader at render time).",
    )
    invoker_instruction_parsed: Optional[str] = Field(
        default=None,
        description="Short paraphrase of what the invoker asked for, when they wrote anything. Empty when invoker only tagged the bot.",
    )
    # Legacy back-compat field — derived from action. Kept transitionally.
    overall_state: OverallState = Field(
        default="checked",
        description='"checked" when action != "decline"; "no_checkable_claim" when action == "decline".',
    )
    reason: str = Field(
        default="",
        description="When action='decline', a short reason (≤ 20 words) the bot can paraphrase. Empty otherwise.",
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

    @model_validator(mode="after")
    def _sync_overall_state(self) -> "ExtractionOutput":
        # action is the canonical source; overall_state is derived so old
        # code paths that read it continue to work during the migration.
        if self.action == "decline":
            object.__setattr__(self, "overall_state", "no_checkable_claim")
        else:
            object.__setattr__(self, "overall_state", "checked")
        return self


_SYSTEM_PROMPT = """You are the claim-extraction-and-action-selection stage of a fact-checking bot. Two jobs in one call:

(A) Decompose the parent tweet into atomic propositions (one of which is central).
(B) Choose ONE action the bot will take for this mention.

INPUT: the parent tweet text, optional image OCR + description + canonical_image_match, parent-tweet context (author handle/bio, posted date, expanded URLs), AND the invoker's instruction (the text the invoker wrote in the mention itself, after the bot handle was stripped). The invoker_instruction may be empty — that means they only tagged and said nothing.

═══════════════════════════════════════════════════════════════
PART A — PROPOSITIONS
═══════════════════════════════════════════════════════════════
For each atomic proposition you find:
- `text`: a single declarative restatement, normalized for clarity. Strip hedges ("I think", "reportedly", "apparently") so the proposition is testable.
- `type`:
    - "verifiable" — could be confirmed or refuted against the public record (specific event, named quote, photo identity, statistic, date).
    - "opinion" — subjective, aesthetic, evaluative, predictive, value judgment ("X is smart", "the architecture is impressive", "Y will win").
    - "mixed" — verifiable factual core inside an opinion frame ("Musk's [verifiable: 2023] Neuralink demo was [opinion: groundbreaking]").
- `source_span`: a short verbatim quote from the tweet (or image OCR).
- `is_central`: TRUE for EXACTLY ONE proposition — the headline the tweet is making. Bias toward the most extraordinary / falsifiable claim if there are ties. For a tweet that's purely a contested topic with no single claim, the central proposition IS the topic statement.
- `check_worthy`: TRUE only when type ∈ {verifiable, mixed} AND the proposition meaningfully impacts what a reader would believe. FALSE for pure opinion, vague generalities, well-known background facts the bot needn't re-verify.
- `suggested_action`: what the bot should do for THIS proposition (verify / provide_context / challenge_opinion / surface_perspectives / decline) — pre-aggregation; the top-level `action` will draw on the central proposition's suggestion plus the invoker's instruction.
- `action_rationale`: one short sentence on why this suggested_action fits this proposition.
- `rationale`: one short sentence on why you assigned this type / check_worthy.

CENTRAL-CLAIM defaults: prefer the most extraordinary / falsifiable / image-bearing claim. The central proposition CAN be opinion (then suggested_action is likely challenge_opinion or surface_perspectives).

═══════════════════════════════════════════════════════════════
PART B — ACTION SELECTION
═══════════════════════════════════════════════════════════════
Choose ONE action for the WHOLE mention.

THE FIVE ACTIONS:

  • "verify" — the central proposition is a falsifiable factual claim. The bot will search for primary sources / fact-checkers / reputable news, then say what they show. Use when the literal claim is empirically checkable and worth checking. Examples: "Photo shows Trump at the Capitol on Jan 6" / "GDP grew 2.4% in Q3" / "Senator X said Y in 2023".

  • "provide_context" — the literal claim is TRUE but the framing leaves out something material that changes how a reader should interpret it (omitted denominator, missing time horizon, cherry-picked statistic, decontextualized image). The bot will surface the missing context. Examples: "Crime rose 200% last month" (true but base rate was tiny) / "This photo was taken at the rally" (true but from a different rally entirely).

  • "challenge_opinion" — the central proposition is a strongly-stated opinion or value judgment with falsifiable downstream consequences, on which credible critics have published push-back. The bot will surface those counterpoints. Use for opinions worth contesting; NOT for personal aesthetics. Examples: "Capital punishment deters crime" (empirically contested) / "Vaccinations don't work" (medical consensus pushes back).

  • "surface_perspectives" — the topic is genuinely contested among reasonable people, and there is no single "correct" answer; multiple credible camps exist. The bot will present them. Examples: "Should Congress raise the debt ceiling?" / "Is remote work better than in-office?" / "What's the right immigration policy?". DIFFERENT from challenge_opinion: surface_perspectives means the SPACE itself is contested; challenge_opinion means one specific opinion has identifiable critics.

  • "decline" — nothing actionable. The mention is pure personal-aesthetic reaction, a joke, a non-sequitur, or has no falsifiable proposition AND no opinion worth challenging AND no contested space to surface. The bot says so politely. Use sparingly — prefer one of the other four when there's any actionable angle.

INVOKER INSTRUCTION HANDLING:

  • If `invoker_instruction` is empty or just "fact check" / "what is this" / "@eddiexbot":
      → action_source = "inferred"
      → choose the action that fits the central proposition's character.

  • If `invoker_instruction` is present and the requested action FITS:
      → action_source = "explicit"
      → action = the invoker's requested action.
      → Set `invoker_instruction_parsed` to a short paraphrase ("invoker asked for a fact-check", "invoker asked for context").

  • If `invoker_instruction` is present BUT the requested action DOESN'T fit the claim (e.g. asks to "fact check this" on pure opinion, or "push back" on a verifiable factual claim that's correctly stated):
      → action_source = "explicit_but_unactionable"
      → action = the action that DOES fit the claim (pivot silently to the fitting action).
      → `pivoted_from` = the action the invoker originally asked for.
      → `invoker_instruction_parsed` = short paraphrase of the original ask.
      → The renderer will prepend a one-clause disclosure ("This reads as opinion rather than a checkable claim, so here's a push-back instead — ...") so the reader isn't surprised by the pivot.

INVOKER-INSTRUCTION PARSING CHEAT-SHEET:
  - "fact check" / "verify" / "check" / "is this true" → verify
  - "context" / "what's the context" / "what's missing" / "background" → provide_context
  - "push back" / "challenge" / "rebut" / "counter" / "this is nonsense" → challenge_opinion
  - "different views" / "what do both sides say" / "perspectives" / "what's the debate" → surface_perspectives
  - Anything not matching any of these: try to map to the closest action; if no obvious fit, action_source = "inferred" and pick from the claim.

═══════════════════════════════════════════════════════════════
HEURISTICS — what to be conservative about
═══════════════════════════════════════════════════════════════
- Be CONSERVATIVE on "verifiable". When in doubt between verifiable and opinion, choose opinion.
- Be CONSERVATIVE on "challenge_opinion". The opinion must be one that credible critics have actually published push-back on — not "I personally disagree." If you can't easily picture a named credible critic, downgrade to decline.
- Be CONSERVATIVE on "decline". Use it only when there's truly nothing actionable; prefer one of the other four actions if there's ANY hook.
- Image-bearing tweets: treat "this photo depicts X" / "this image proves Y" as VERIFIABLE — image identity is checkable. A `canonical_image_match` with confidence=high means the photo is famous; combine that with the tweet's framing to choose between verify (the photo is what the tweet says) and provide_context (the photo is real but the tweet's caption recontextualizes it falsely).
- "Breaking", "shocking", "you won't believe": NOT propositions; ignore.
- Quote attributions: "X said Y" is VERIFIABLE if X is named and Y is specific.

OUTPUT: one JSON object matching the schema. EXACTLY ONE claim must have is_central=true. When action == "decline", set `reason` to a short paraphrase (≤ 20 words) the bot can echo to the user. Both `action` and `action_source` are required.
"""


def extract_claims(claim_text: str, ctx: PipelineContext) -> ExtractionOutput:
    """Run Stage 2 + 3. Returns the structured ExtractionOutput.

    On LLM failure, returns a safe fallback that treats the whole input as
    one verifiable central claim with action="verify".
    """
    payload: dict = {"tweet_text": claim_text}
    # invoker_instruction may be empty — that's the "only tagged" signal.
    payload["invoker_instruction"] = ctx.invoker_instruction or ""
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
    """Safe fallback when extraction fails: treat whole input as one
    verifiable central claim with action=verify. Preserves the safest
    behavior — run the verify pipeline rather than silently drop."""
    return ExtractionOutput(
        claims=[
            ExtractedClaim(
                text=claim_text,
                type="verifiable",
                source_span=claim_text[:160],
                is_central=True,
                check_worthy=True,
                suggested_action="verify",
                action_rationale="extraction fallback — assume verifiable claim",
                rationale="extraction fallback — extractor failed or returned malformed output",
            )
        ],
        action="verify",
        action_source="inferred",
        pivoted_from=None,
        invoker_instruction_parsed=None,
        overall_state="checked",
        reason="",
    )


def central_claim(extraction: ExtractionOutput) -> ExtractedClaim:
    """Convenience: return the (one) central claim from an extraction."""
    return next(c for c in extraction.claims if c.is_central)
