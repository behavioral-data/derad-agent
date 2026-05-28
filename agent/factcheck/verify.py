"""Stage 4 — iterative verification agent (Papelo-style).

Generates verification questions one at a time, conditioning each on the
evidence already gathered. The configured SearchBackend supplies the
search-and-snippet layer; we don't fetch raw documents in v1 (design
§4.4's Playwright path is a follow-up).

Caps per claim:
  * max_questions = 5
  * max_hits_per_question = 3
  * wall_clock_budget_s = 60

Output: a list of Evidence records spanning all questions. Stage 4.5 then
reconciles across the bundle and assigns per-record stances.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agent.shared.text import canonicalize_url

from .context import PipelineContext
from .llm import call_claude_json, pruned_context
from .schema import Action, Evidence
from .search import SearchBackend, SearchHit


logger = logging.getLogger(__name__)


@dataclass
class _Step:
    question: str
    hits: list[SearchHit] = field(default_factory=list)


class _Decision(BaseModel):
    """Output of the question-or-stop Claude call."""

    action: Literal["continue", "stop"]
    next_question: Optional[str] = Field(
        default=None,
        description="The verification question to ask next. Required when action='continue'.",
    )
    reason: str = Field(
        default="",
        description="Short reason (≤ 1 sentence) for the action — why this question, or why we're stopping.",
    )


_DECISION_SYSTEM = """You are the iterative-verification controller of a fact-checking bot. Your job is to decide whether enough evidence has been gathered for the bot's chosen action, and if not, to issue the SINGLE next web-search question that would close the biggest remaining gap.

You will see:
- `central_claim`: the proposition the bot is acting on.
- `action`: what the bot is trying to do. One of {verify, provide_context, challenge_opinion, surface_perspectives}. The STOP / CONTINUE rules below are action-conditional — read your action's bullet first.
- `tweet_context` (optional): author, date, expanded URLs. Useful for spotting parody accounts, recycled-event tweets.
- `image_summaries` (optional): per-image OCR + brief VLM description, plus `canonical_image_match` when the photo itself is famous.
- `history`: the sequence of (question, retrieved hits) pairs from prior steps. Each hit has url + title + snippet.

Output ONE JSON object:
- `action` (field name, not the bot's action): "continue" or "stop".
- `next_question`: the next question to issue, when action="continue". Must be a CONCRETE web-searchable question. Don't repeat a question already in history.
- `reason`: short rationale.

STOP / CONTINUE — read the bullet for YOUR action:

  • verify
    STOP: ≥2 fact-checker or reputable-news sources directly support OR refute the central proposition, with consistent stance.
    CONTINUE: primary source not yet searched (originating publisher, official press release, the specific person's verified account); date / context detail that would settle ambiguity.

  • provide_context
    STOP: ≥2 reputable-tier sources have surfaced the missing piece (the omitted denominator, the longer time horizon, the decontextualizing fact). The literal claim doesn't need to be re-verified; the goal is to find what the framing leaves OUT.
    CONTINUE: the missing context isn't yet documented; search for background, the longer history, comparable cases, the source the claim is implicitly cherry-picking from.

  • challenge_opinion
    STOP: ≥2 reputable-tier or fact-checker sources where NAMED credible critics push back on the opinion's empirical premises. Pundit-on-pundit slap fights from low-quality tier do NOT count.
    CONTINUE: search for credentialed critics, empirical studies that bear against the opinion, expert organizations / journals / opinion essays in reputable venues. Be specific — search for the named expert + topic, not just the topic.

  • surface_perspectives
    STOP: each of ≥2 distinct viewpoints has ≥1 credible source backing it. The goal is BREADTH — at least one source per distinct camp, not depth in one camp.
    CONTINUE: identify a distinct viewpoint you haven't found a credible source for yet; search "{topic} {opposing-camp-label}" or "{topic} {named-organization-on-that-side}".

GENERAL: STOP also fires when wall-clock or question cap is hit (handled by the runner). DO NOT issue a question that's just a re-phrasing of the claim. Issue something the search engine can answer that BEARS on the claim.

Good follow-up examples:
- "Was the 'tank man' photograph first published by Reuters or AP in 1989?"  (verify)
- "What's the historical base rate for {statistic} so the {N}% jump can be contextualized?"  (provide_context)
- "What have public-health researchers said about claims that {opinion}?"  (challenge_opinion)
- "What does the {labor-economist / business-lobby} side argue about {topic}?"  (surface_perspectives)
"""


_SEED_QUERIES_BY_ACTION: dict[Action, list[str]] = {
    "verify": [],                          # seed = "fact check: {claim}" (handled below)
    "provide_context": [
        "{claim} background context",
        "{claim} full story",
    ],
    "challenge_opinion": [
        "critique of: {claim}",
        "evidence against: {claim}",
    ],
    "surface_perspectives": [
        "{claim} debate",
        "{claim} different perspectives",
    ],
    "decline": [],                         # never gets here — pipeline short-circuits
}


def _seed_queries(claim_text: str, action: Action) -> list[str]:
    """Build the action-conditional seed queries that run before the
    controller loop. verify uses one; the other actions use two (breadth
    over depth)."""
    if action == "verify":
        return [f"fact check: {claim_text}"]
    return [tmpl.format(claim=claim_text) for tmpl in _SEED_QUERIES_BY_ACTION.get(action, [])]


_DEFAULT_MAX_QUESTIONS: dict[Action, int] = {
    "verify": 5,
    "provide_context": 6,         # 2 seeds + 4 controller steps
    "challenge_opinion": 6,
    "surface_perspectives": 6,
    "decline": 0,
}


def _make_user_payload(
    claim_text: str,
    action: Action,
    history: list[_Step],
    tweet_context: Optional[dict],
    image_summaries: Optional[list[dict]],
) -> str:
    payload = {
        "central_claim": claim_text,
        "action": action,
        "history": [
            {
                "question": step.question,
                "hits": [
                    {"url": h.url, "title": h.title, "snippet": h.snippet}
                    for h in step.hits
                ],
            }
            for step in history
        ],
    }
    cleaned_ctx = pruned_context(tweet_context)
    if cleaned_ctx:
        payload["tweet_context"] = cleaned_ctx
    if image_summaries:
        payload["image_summaries"] = image_summaries
    return json.dumps(payload, indent=2)


def _decide_next(
    claim_text: str,
    action: Action,
    history: list[_Step],
    tweet_context: Optional[dict],
    image_summaries: Optional[list[dict]],
) -> Optional[_Decision]:
    """Single Claude call: continue with a question, or stop? Returns None on
    LLM failure so the caller can distinguish from a legitimate stop."""
    user_prompt = _make_user_payload(claim_text, action, history, tweet_context, image_summaries)
    try:
        return call_claude_json(
            prompt=user_prompt,
            schema=_Decision,
            system=_DECISION_SYSTEM,
            reasoning_effort="low",
            max_tokens=1024,
            timeout=30.0,
        )
    except (ValueError, TimeoutError):
        # Parse/schema failure or wall-clock timeout. Caller distinguishes
        # first-iteration failure (raise VerifyControllerError) from later
        # ones (graceful stop with partial evidence).
        logger.warning("Verification-loop controller call failed.", exc_info=True)
        return None


def iterative_verify(
    claim_text: str,
    ctx: PipelineContext,
    *,
    backend: SearchBackend,
    action: Action = "verify",
    max_questions: Optional[int] = None,
    max_hits_per_question: int = 3,
    wall_clock_budget_s: float = 60.0,
) -> list[Evidence]:
    """Run the Papelo-style loop. Returns Evidence records for Stage 4.5.

    The seed phase (no Claude controller call) issues 1 or 2 action-
    conditional seed queries — `verify` uses one ("fact check: …"); the
    other actions use two for breadth. The controller then drives the
    loop until the action-specific stop condition or a cap is hit.
    """
    if max_questions is None:
        max_questions = _DEFAULT_MAX_QUESTIONS.get(action, 5)
    start = time.monotonic()
    history: list[_Step] = []

    seed_queries = _seed_queries(claim_text, action)
    for i, seed in enumerate(seed_queries, 1):
        hits = backend.search(seed, top_k=max_hits_per_question)
        history.append(_Step(question=seed, hits=list(hits)))
        logger.info(
            "iterative_verify[%s]: step %d (seed) → query=%r hits=%d",
            action, i, seed[:120], len(hits),
        )

    n_seed = len(history)
    for step_idx in range(n_seed, max_questions):
        elapsed = time.monotonic() - start
        remaining = wall_clock_budget_s - elapsed
        if remaining <= 0:
            logger.info(
                "iterative_verify[%s]: wall-clock budget exhausted after %d questions (elapsed=%.1fs)",
                action, step_idx, elapsed,
            )
            break

        decision = _decide_next(claim_text, action, history, ctx.tweet_context, ctx.image_summaries)
        if decision is None:
            # Controller call failed (LLM outage / parse error / refusal).
            # We have at least one step of evidence from the seed search,
            # so this is always the partial-evidence path now — never the
            # zero-evidence path that raises VerifyControllerError.
            logger.warning(
                "iterative_verify[%s]: controller failed on step %d; stopping gracefully with %d records",
                action, step_idx + 1, sum(len(s.hits) for s in history),
            )
            break

        logger.info(
            "iterative_verify[%s]: step %d → decision=%s question=%r reason=%r",
            action, step_idx + 1, decision.action,
            (decision.next_question or "")[:120],
            (decision.reason or "")[:120],
        )
        if decision.action == "stop" or not decision.next_question:
            break

        hits = backend.search(decision.next_question, top_k=max_hits_per_question)
        history.append(_Step(question=decision.next_question, hits=list(hits)))

    elapsed = time.monotonic() - start
    n_hits = sum(len(s.hits) for s in history)
    logger.info(
        "iterative_verify[%s]: completed %d questions, %d hits, %.1fs",
        action, len(history), n_hits, elapsed,
    )

    evidence: list[Evidence] = []
    seen: set[str] = set()
    for step in history:
        for hit in step.hits:
            key = canonicalize_url(hit.url)
            if key in seen:
                continue
            seen.add(key)
            evidence.append(
                Evidence(
                    question=step.question,
                    source_url=hit.url,
                    snippet=hit.snippet,
                    stance="neutral",
                    body_markdown=hit.body_markdown,
                )
            )
    return evidence
