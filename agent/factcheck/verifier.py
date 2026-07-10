# agent/factcheck/verifier.py
"""v0.7 independent verifier — a fresh-context LLM audit of the loop's draft.
Not self-grading: it never shares the loop's conversation. One revision max."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

from .draft import DraftVerdict
from .llm import call_claude_json
from .loop import revise_in_loop, run_loop
from .loop_tools import UNTRUSTED_CLOSE, UNTRUSTED_OPEN, EvidenceRow, ToolRuntime
from .prompt_store import load_prompt
from .schema import VerifierReport

logger = logging.getLogger(__name__)


class VerifierOutput(BaseModel):
    passed: bool
    temporal_leaks: list[str] = Field(default_factory=list)
    derivation_gaps: list[str] = Field(default_factory=list)
    lint_violations: list[str] = Field(default_factory=list)
    injection_flags: list[str] = Field(default_factory=list)
    fabrication_language_ok: bool = True
    required_revisions: str = ""
    downgrade: bool = False


def verify_draft(
    draft: DraftVerdict, rows: list[EvidenceRow], *,
    post_text: str, as_of: Optional[datetime], cutoff: Optional[datetime],
) -> VerifierOutput:
    payload = {
        "post_text": post_text,
        "post_date": as_of.isoformat() if as_of else None,
        "evidence_cutoff": cutoff.isoformat() if cutoff else None,
        "draft": draft.model_dump(),
        "evidence_log": [
            {"idx": r.idx, "url": r.url, "published_date": r.published_at,
             "origin": r.origin, "via_snapshot": r.via_snapshot,
             "snippet": r.snippet,
             "body_excerpt": f"{UNTRUSTED_OPEN}\n{(r.body_markdown or '')[:1200]}\n{UNTRUSTED_CLOSE}" if r.body_markdown else ""}
            for r in rows
        ],
    }
    try:
        return call_claude_json(
            prompt=json.dumps(payload, indent=1, default=str),
            schema=VerifierOutput,
            system=load_prompt("verifier"),
            reasoning_effort="medium",
            max_tokens=4096,
            timeout=90.0,
        )
    except (ValueError, TimeoutError, anthropic.APIConnectionError):
        logger.warning("verifier call failed — failing safe with downgrade", exc_info=True)
        return VerifierOutput(passed=False, downgrade=True, required_revisions="")


def apply_downgrade(draft: DraftVerdict) -> DraftVerdict:
    # Downgrade state is frozen in verifier_report.downgrade — do NOT leak a prose
    # prefix into the justification (it would surface in reply-facing derivations).
    return draft.model_copy(update={
        "confidence": "low",
        "verdict_leaning": "insufficient",
    })


def _to_report(out: VerifierOutput, revision_used: bool) -> VerifierReport:
    return VerifierReport(
        passed=out.passed,
        temporal_leaks=tuple(out.temporal_leaks),
        derivation_gaps=tuple(out.derivation_gaps),
        lint_violations=tuple(out.lint_violations),
        injection_flags=tuple(out.injection_flags),
        fabrication_language_ok=out.fabrication_language_ok,
        required_revisions=out.required_revisions,
        downgrade=out.downgrade,
        revision_used=revision_used,
    )


def run_verified_loop(
    post_text: str, *, client, ctx, as_of, cutoff,
    max_turns: int = 24, wall_clock_s: float = 480.0, model: Optional[str] = None,
):
    draft, runtime, stats, messages = run_loop(
        post_text, client=client, ctx=ctx, as_of=as_of, cutoff=cutoff,
        max_turns=max_turns, wall_clock_s=wall_clock_s, model=model)
    if draft is None:
        return None, runtime, _to_report(
            VerifierOutput(passed=False, downgrade=True,
                           required_revisions="loop never finalized"), False), stats

    out = verify_draft(draft, runtime.rows, post_text=post_text, as_of=as_of, cutoff=cutoff)
    if out.passed:
        return draft, runtime, _to_report(out, False), stats

    revision_used = False
    if out.required_revisions.strip():
        revision_used = True
        revised, _ = revise_in_loop(messages, out.required_revisions,
                                    client=client, runtime=runtime, model=model)
        if revised is not None:
            draft = revised
            out = verify_draft(draft, runtime.rows, post_text=post_text,
                               as_of=as_of, cutoff=cutoff)
            if out.passed:
                return draft, runtime, _to_report(out, True), stats

    # Still failing (or nothing revisable) → downgrade, never loop.
    out.downgrade = True
    return apply_downgrade(draft), runtime, _to_report(out, revision_used), stats
