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
from .loop_tools import UNTRUSTED_CLOSE, UNTRUSTED_OPEN, EvidenceRow
from .prompt_store import load_prompt
from .render_lint import extract_numerals
from .schema import VerifierReport

logger = logging.getLogger(__name__)


class VerifierOutput(BaseModel):
    passed: bool
    temporal_leaks: list[str] = Field(default_factory=list)
    derivation_gaps: list[str] = Field(default_factory=list)
    lint_violations: list[str] = Field(default_factory=list)
    injection_flags: list[str] = Field(default_factory=list)
    scoped_drops: list[str] = Field(default_factory=list)
    fabrication_language_ok: bool = True
    required_revisions: str = ""
    downgrade: bool = False
    cap_demote_to_context: bool = False


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
    # Downgrade is ADVISORY (final-review adjudication): it is frozen in
    # verifier_report.downgrade + confidence="low" for the eval harness and
    # any downstream consumer. It must NOT flip verdict_leaning — that
    # collapsed substantive verdicts to *_nei/_unavailable outcomes while the
    # payload stayed substantive, recreating the v0.6 outcome/payload
    # incoherence this redesign exists to fix. Do NOT leak a prose prefix
    # into the justification either (it would surface in replies).
    return draft.model_copy(update={"confidence": "low"})


_LEAK_HEADLINE = ("This post could not be verified against sources available at "
                  "the time it was posted.")
_LEAK_JUSTIFICATION = ("The available contemporaneous evidence did not support a "
                       "clean verdict, and the strongest supporting material post-dates "
                       "the post. No contemporaneous fact-check is asserted.")


def scrub_temporal_leak(draft: DraftVerdict) -> DraftVerdict:
    """Collapse the reply-facing payload to a temporally-safe hedge.

    Applied ONLY when the verifier confirms a temporal leak that the revision
    round could not fix — the one case where shipping the substantive payload
    would show a study participant a fact that post-dates the post. Unlike the
    advisory downgrade, this DOES neutralize the outcome (verdict_leaning →
    insufficient) and strip the detail carriers, because the alternative is a
    visible anachronism. Free-text headline/justification can't be scrubbed
    surgically, so they are replaced wholesale rather than risk leaving the
    leaking clause in place."""
    return draft.model_copy(update={
        "confidence": "low",
        "verdict_leaning": "insufficient",
        "headline_finding": _LEAK_HEADLINE,
        "justification": _LEAK_JUSTIFICATION,
        "counter_fact": None,
        "context_note": None,
        "load_bearing_evidence_snippet": "",
        "load_bearing_facts": [],
        "counterpoints": [],
        "perspectives": [],
    })


_ENDORSE_DEMOTE_HEADLINE = (
    "This post's literal claim holds up, but its framing rests on a characterization the "
    "available evidence does not establish — treat the framing, not the underlying event, "
    "with caution.")
_ENDORSE_DEMOTE_JUSTIFICATION = (
    "The underlying event checks out, but the post frames it with a characterization the "
    "contemporaneous evidence does not substantiate. The framing is not endorsed; the "
    "literal event is not disputed.")


def demote_endorsement(draft: DraftVerdict) -> DraftVerdict:
    """Enforce the endorsement cap when revision could not re-cast a `supported`
    verdict the verifier judged misleadingly framed.

    An advisory downgrade alone keeps `supported` shipping — so the cap was
    toothless on revision-failure. This re-casts the draft to `provide_context`
    and neutralizes the endorsing prose to a framing hedge. Like the temporal
    scrub, the specific missing framing is not reliably in the evidence log at
    this point, so the free prose is replaced wholesale rather than left
    endorsing. The result ships as a non-endorsement, never as `supported`."""
    return draft.model_copy(update={
        "action": "provide_context",
        "verdict_leaning": "insufficient",
        "confidence": "low",
        "headline_finding": _ENDORSE_DEMOTE_HEADLINE,
        "justification": _ENDORSE_DEMOTE_JUSTIFICATION,
        "counter_fact": None,
        "context_note": _ENDORSE_DEMOTE_HEADLINE,
    })


def apply_scoped_drops(draft: DraftVerdict, drops: list[str],
                       rows: list[EvidenceRow]) -> DraftVerdict:
    """Remove verifier-flagged PERIPHERAL items from the reply-facing draft
    without touching the verdict. Each drop string is either an exact fact
    string (matched against load_bearing_facts / peripheral_facts) or a source
    URL (matched against primary_sources and the row backing an evidence_ref).
    Central verdict fields (verdict_leaning, headline_finding, counter_fact,
    verdict_derivation) are never modified here."""
    if not drops:
        return draft
    dropset = set(drops)
    by_idx = {r.idx: r for r in rows}
    lb = [f for f in draft.load_bearing_facts if f not in dropset]
    pf = [f for f in draft.peripheral_facts if f not in dropset]
    ps = [s for s in draft.primary_sources if s.url not in dropset]
    er = [e for e in draft.evidence_refs
          if by_idx.get(e.row) is None or by_idx[e.row].url not in dropset]
    # Exact-match/URL-drift debugging aid: surface any drop that removed
    # nothing (not a load_bearing/peripheral fact, not a primary-source or
    # referenced-row URL). Return behavior is unchanged.
    matched = ({f for f in draft.load_bearing_facts if f in dropset}
               | {f for f in draft.peripheral_facts if f in dropset}
               | {s.url for s in draft.primary_sources if s.url in dropset}
               | {by_idx[e.row].url for e in draft.evidence_refs
                  if by_idx.get(e.row) is not None and by_idx[e.row].url in dropset})
    unmatched = dropset - matched
    if unmatched:
        logger.debug("apply_scoped_drops: %d drop string(s) matched nothing "
                     "(no field/url removed): %s", len(unmatched), sorted(unmatched))
    return draft.model_copy(update={
        "load_bearing_facts": lb, "peripheral_facts": pf,
        "primary_sources": ps, "evidence_refs": er,
    })


def _warn_prose_residual(drops: list[str], draft: DraftVerdict) -> None:
    """VISIBILITY-ONLY defense-in-depth for the prose-residual gap.

    `scoped_drops` never edits reply prose. If a dropped string's numeral(s)
    still appear in the concatenated reply-facing prose (`headline_finding` +
    `counter_fact` + `context_note` + `justification`), the verifier mis-routed
    a prose-embedded defect through `scoped_drops` instead of
    `required_revisions`. Log it so the leak is auditable — do NOT scrub here:
    auto-scrubbing would re-introduce the NEI collapse this feature exists to
    fix."""
    if not drops:
        return
    prose = " ".join(p for p in (draft.headline_finding, draft.counter_fact,
                                 draft.context_note, draft.justification) if p)
    prose_numerals = extract_numerals(prose)
    for drop in drops:
        leaked = extract_numerals(drop) & prose_numerals
        if leaked:
            logger.warning(
                "run_verified_loop: scoped_drop %r left numeral(s) %s in reply prose "
                "— a prose-embedded defect must be a required_revision, not a drop",
                drop, sorted(leaked))


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
        scoped_drops=tuple(out.scoped_drops),
        cap_demote_to_context=out.cap_demote_to_context,
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
    # Peripheral defects are auto-applied and never block the verdict.
    draft = apply_scoped_drops(draft, out.scoped_drops, runtime.rows)
    if out.passed:
        _warn_prose_residual(out.scoped_drops, draft)
        return draft, runtime, _to_report(out, False), stats

    # Central defect path: one revision round, then re-verify.
    revision_used = False
    if out.required_revisions.strip():
        revision_used = True
        revised, _ = revise_in_loop(messages, out.required_revisions,
                                    client=client, runtime=runtime, model=model)
        if revised is None:
            # Mechanical failure (revision finalize never validated / turns
            # exhausted) — retry the SAME demanded revision once with an
            # explicit resubmit instruction. This is not a second verifier
            # round; the verifier's demand is unchanged.
            logger.warning("run_verified_loop: revision produced no draft — retrying once")
            revised, _ = revise_in_loop(
                messages,
                out.required_revisions
                + "\n\nIMPORTANT: resubmit the COMPLETE corrected draft via the "
                  "finalize tool — every required field must be present.",
                client=client, runtime=runtime, model=model)
        if revised is not None:
            draft = revised
            out = verify_draft(draft, runtime.rows, post_text=post_text,
                               as_of=as_of, cutoff=cutoff)
            draft = apply_scoped_drops(draft, out.scoped_drops, runtime.rows)
            if out.passed:
                _warn_prose_residual(out.scoped_drops, draft)
                return draft, runtime, _to_report(out, True), stats

    # Still failing on a CENTRAL defect → downgrade, never loop.
    out.downgrade = True
    if out.temporal_leaks:
        # temporal_leaks now means a CENTRAL post-cutoff fact the revision
        # could not re-source — the one case that warrants neutralizing the
        # reply-facing payload (a peripheral post-cutoff corroborator would
        # have been a scoped_drop instead).
        logger.warning("run_verified_loop: unfixed CENTRAL temporal leak — scrubbing payload: %s",
                       "; ".join(out.temporal_leaks)[:300])
        return scrub_temporal_leak(draft), runtime, _to_report(out, revision_used), stats
    if out.cap_demote_to_context and draft.action == "verify" and draft.verdict_leaning == "supported":
        # Endorsement-cap enforcement: the verifier judged this `supported`
        # verdict misleadingly framed and revision could not re-cast it. Advisory
        # downgrade would keep `supported` shipping, so demote to provide_context
        # rather than endorse a misframed post.
        logger.warning("run_verified_loop: endorsement cap unresolved by revision — "
                       "demoting supported -> provide_context")
        return demote_endorsement(draft), runtime, _to_report(out, revision_used), stats
    return apply_downgrade(draft), runtime, _to_report(out, revision_used), stats
