"""Stage 4 — iterative verification agent (Papelo-style).

Generates verification questions one at a time, conditioning each on the
evidence already gathered. Foundry+Bing acts as the search-and-snippet
layer; we don't fetch raw documents in v1.

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

from .llm import call_claude_json
from .schema import Evidence
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


_DECISION_SYSTEM = """You are the iterative-verification controller of a fact-checking pipeline. Your job is to decide whether enough evidence has been gathered to verify or refute the central claim, and if not, to issue the SINGLE next web-search question that would close the biggest remaining gap.

You will see:
- `central_claim`: the claim being checked.
- `tweet_context` (optional): author, date, expanded URLs, etc. Useful for spotting parody accounts, recycled-event tweets, and the like.
- `image_summaries` (optional): per-image OCR + brief VLM description. The claim is image-bearing if this is populated.
- `history`: the sequence of (question, retrieved hits) pairs from prior steps in this loop. Each hit has url + title + snippet.

Output ONE JSON object:
- `action`: "continue" or "stop".
- `next_question`: the next question to issue, when action="continue". Must be a CONCRETE web-searchable question, not abstract. Bias toward primary-source / fact-checker / news-coverage angles. Don't repeat a question already in history.
- `reason`: short rationale for the choice.

Heuristics:
- STOP when you have ≥2 fact-checker or reputable-news sources directly supporting OR refuting the central proposition, with consistent stance across them.
- STOP when you've gathered enough to confidently say "not enough evidence" (multiple searches with no relevant coverage).
- CONTINUE when there's a clear next angle: a primary source you haven't searched for, a date / context detail that would settle the question, the originating publisher to check for retractions, the specific person's verified account for direct quotes.
- DO NOT issue a question that's just a re-phrasing of the claim. Issue something the search engine can answer that BEARS on the claim.

Examples of good follow-up questions (after seeing initial generic search):
- "Was the 'tank man' photograph first published by Reuters or AP in 1989?"
- "Did Pfizer's October 2022 EU parliament testimony say transmission was tested?"
- "What was the original publication date and outlet of [image-described event]?"
- "Has @[author handle] been flagged as a parody / impersonation account?"
"""


def _make_user_payload(
    claim_text: str,
    history: list[_Step],
    tweet_context: Optional[dict],
    image_summaries: Optional[list[dict]],
) -> str:
    payload = {
        "central_claim": claim_text,
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
    if tweet_context:
        clean = {k: v for k, v in tweet_context.items() if v not in (None, "", [], {})}
        if clean:
            payload["tweet_context"] = clean
    if image_summaries:
        payload["image_summaries"] = image_summaries
    return json.dumps(payload, indent=2)


class VerifyControllerError(RuntimeError):
    """Raised when the verify-loop controller fails on its first call.

    Distinct from "controller said stop" — that's a legitimate decision
    that produces an NEI verdict downstream. This means we couldn't even
    determine whether to search; the surrounding pipeline should emit
    `pipeline_error` rather than masquerade as NEI.
    """


def _decide_next(
    claim_text: str,
    history: list[_Step],
    tweet_context: Optional[dict],
    image_summaries: Optional[list[dict]],
) -> Optional[_Decision]:
    """Single Claude call: continue with a question, or stop? Returns None on
    LLM failure so the caller can distinguish from a legitimate stop."""
    user_prompt = _make_user_payload(claim_text, history, tweet_context, image_summaries)
    try:
        return call_claude_json(
            prompt=user_prompt,
            schema=_Decision,
            system=_DECISION_SYSTEM,
            reasoning_effort="low",
            max_tokens=1024,
        )
    except Exception:
        logger.exception("Verification-loop controller call failed.")
        return None


def iterative_verify(
    *,
    claim_text: str,
    backend: SearchBackend,
    tweet_context: Optional[dict] = None,
    image_summaries: Optional[list[dict]] = None,
    max_questions: int = 5,
    max_hits_per_question: int = 3,
    wall_clock_budget_s: float = 60.0,
) -> list[Evidence]:
    """Run the Papelo-style loop. Returns Evidence records for Stage 4.5."""
    start = time.monotonic()
    history: list[_Step] = []

    for step_idx in range(max_questions):
        elapsed = time.monotonic() - start
        remaining = wall_clock_budget_s - elapsed
        if remaining <= 0:
            logger.info(
                "iterative_verify: wall-clock budget exhausted after %d questions (elapsed=%.1fs)",
                step_idx, elapsed,
            )
            break

        decision = _decide_next(claim_text, history, tweet_context, image_summaries)
        if decision is None:
            # Controller call failed (LLM outage / parse error / refusal).
            if not history:
                # First call failed → no evidence at all → distinguish from
                # legitimate stop. Surrounding pipeline records pipeline_error
                # via process_mention's outer try/except.
                raise VerifyControllerError(
                    "controller call failed on first iteration; no evidence gathered"
                )
            # Later call failed → we have partial evidence; graceful stop.
            logger.warning(
                "iterative_verify: controller failed on step %d; stopping gracefully with %d records",
                step_idx + 1, sum(len(s.hits) for s in history),
            )
            break

        logger.info(
            "iterative_verify: step %d → action=%s question=%r reason=%r",
            step_idx + 1, decision.action,
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
        "iterative_verify: completed %d questions, %d hits, %.1fs",
        len(history), n_hits, elapsed,
    )

    evidence: list[Evidence] = []
    for step in history:
        for hit in step.hits:
            evidence.append(
                Evidence(
                    question=step.question,
                    source_url=hit.url,
                    snippet=hit.snippet,
                    stance="neutral",
                )
            )
    return evidence
