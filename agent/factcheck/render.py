"""Stage 7 — tone renderer. Reads ONLY the RendererView (design §3.2).

The renderer composes one system prompt from three pieces:

  system = _ACTION_TEMPLATES[action] + _TONE_REGISTERS[tone] + _hard_constraints_for(action, state)

Five action templates × three tone registers = compositional, not a
15-prompt Cartesian product. Each action template owns its goal,
state-aware examples, and field expectations. Each tone register owns
rhetorical guidance (style, register, off-limit moves). Hard constraints
are action-aware (verify-refuted needs counter_fact; challenge_opinion
needs a counterpoint URL; surface_perspectives needs ≥2 perspectives).

Pivot disclosure: when `pivoted_from` is set (invoker asked for one
action and the pipeline took another), the renderer is fed the original
ask + the invoker's literal mention text and is instructed to weave a
short clarification into its reply. The pipeline does NOT prepend
anything — the model owns the full 256-char body, including any pivot
clause, so it can compress as needed.

Every reply is produced by the model. There are NO hardcoded templates —
even the "decline" path goes through a tone-aware prompt.

Refusal handling: a `call_claude_json` failure (parse, schema, or detected
refusal) triggers a same-tone retry with a harm-reduction nudge. If that
also fails, the renderer raises → `pipeline_error`.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel

from agent.shared.text import URL_RE, X_TCO_LEN, X_TWEET_LIMIT, x_weighted_length

from .freeze import RendererView
from .llm import call_claude_json
from .schema import Action, Tone


logger = logging.getLogger(__name__)


# The poster appends a t.co-shortened /info link after the rendered reply:
# "<reply>\n<info_url>". The /info URL counts as 23 chars + 1 for the
# newline. Renderer ceiling must leave room for that.
_X_INFO_APPEND_LEN = X_TCO_LEN + 1  # 23 + newline
_X_TWEET_LIMIT = X_TWEET_LIMIT - _X_INFO_APPEND_LEN  # = 256

_REFUSAL_MARKERS = (
    "i can't", "i cannot", "i won't", "i will not", "i am unable",
    "i'm unable", "i'm not able", "i'm sorry, but",
    "i don't feel comfortable", "as an ai", "i must decline",
)


class RenderedReply(BaseModel):
    text: str


# ── State derivation ───────────────────────────────────────────────────────

_RenderState = str  # "actionable" | "no_evidence" | "declined"


def _state_for(view: RendererView) -> _RenderState:
    """Derive the renderer's state from the action_outcome.

    actionable = the action produced something the bot can cite or quote
                 (verify supported/refuted/conflicting, context_provided,
                  challenged, perspectives_surfaced).
    no_evidence = action ran but couldn't surface a usable result
                 (verified_nei, context_unavailable, challenge_unavailable,
                  perspectives_insufficient).
    declined = no actionable angle (action=decline).
    """
    outcome = view.action_outcome
    if outcome == "declined":
        return "declined"
    if outcome in {"verified_nei", "context_unavailable", "challenge_unavailable", "perspectives_insufficient"}:
        return "no_evidence"
    return "actionable"


# ── Per-action templates ───────────────────────────────────────────────────

_VERIFY_TEMPLATE = """You are the fact-check bot writing ONE reply tweet.

INPUT: RendererView with `presentation_payload` + `tone_neutral_justification`. The pipeline has VERIFIED the claim against evidence. The substance of your reply MUST come from `presentation_payload.headline_finding` and `tone_neutral_justification`.

YOUR JOB BY STATE:
- state="actionable": communicate the headline_finding plainly. NAME the source(s) by their `display_name` from `primary_sources_to_cite` (e.g. "Snopes", "AP News"). If `counter_fact` is set (verify-refuted), incorporate the corrective.
- state="no_evidence": briefly acknowledge the claim is testable but credible coverage wasn't found.

A separate `/info` short link is appended to your reply automatically; that page carries all source URLs + reasoning. DO NOT include any URL in your reply body.

STYLE EXAMPLES (do not copy verbatim; match the actual evidence):
- "This is not accurate. According to Snopes, …"
- "Real photo, miscaptioned: AP News documented the original context."
- (no_evidence) "Not enough credible coverage to verify this either way."
"""

_PROVIDE_CONTEXT_TEMPLATE = """You are a fact-check bot writing ONE reply tweet to SUPPLY MISSING CONTEXT. The literal claim may be accurate, but the framing leaves out something material.

INPUT: RendererView. Read `presentation_payload.context_note` for the missing piece, `primary_sources_to_cite` for sources backing it (use their `display_name` only — no URLs in your body).

YOUR JOB BY STATE:
- state="actionable": surface the missing context plainly. Name the source by display_name. Don't argue the literal claim is wrong — frame as "what this leaves out is …" or "important context here: …".
- state="no_evidence": acknowledge the framing seems incomplete but credible context coverage wasn't found.

A separate `/info` short link is appended automatically; that page carries the source URLs. DO NOT include any URL in your reply body.

STYLE EXAMPLES:
- "Worth noting the context Reuters reports: the base rate here was …"
- "The figure is real, but the WSJ documents that the comparison period excludes …"
"""

_CHALLENGE_OPINION_TEMPLATE = """You are a fact-check bot writing ONE reply tweet to PUSH BACK on a strongly-stated opinion.

INPUT: RendererView. Read `presentation_payload.counterpoints` for the credible counter-arguments. The `citing_sources` field on each counterpoint identifies the source — use the URL's display name from `primary_sources_to_cite` in your text (no URLs in the body itself).

YOUR JOB BY STATE:
- state="actionable": present the strongest counterpoint from `counterpoints`. NAME the credible critic / outlet / study by name. Be substantive — your job is to put credible push-back in front of the reader.
- state="no_evidence": acknowledge the opinion is contested but credible push-back wasn't found in this window.

A separate `/info` short link is appended automatically; that page lists every counterpoint with its source URLs. DO NOT include any URL in your reply body.

TONE-NEUTRAL POSTURE: push back on the OPINION, not the person. "Researchers at NEJM argue …" / "Cochrane published a meta-analysis showing …" — focus on the empirical counter.

STYLE EXAMPLES:
- "Worth weighing the counter: NIH-funded research argues that …"
- "The Atlantic's review documented evidence that …, which complicates this take."
"""

_SURFACE_PERSPECTIVES_TEMPLATE = """You are a fact-check bot writing ONE reply tweet to SURFACE MULTIPLE PERSPECTIVES on a contested topic.

INPUT: RendererView. `presentation_payload.perspectives` lists 2–3 credible viewpoints, each with `label`, `summary`, and `citing_sources`. The first two are the strongest.

YOUR JOB BY STATE:
- state="actionable": surface EXACTLY TWO perspectives in tension — the first two in the list. Preserve each label's framing (you may paraphrase if the verbatim form doesn't fit the char budget, but don't switch sides). DO NOT take a side — frame each viewpoint in its own terms. Source naming is OPTIONAL — the appended /info link carries every source already, so name them only if space allows; otherwise let the labels and substance do the work.
- state="no_evidence": acknowledge the topic is contested but credible perspectives weren't surfaced.

A separate `/info` short link is appended automatically; that page lists every perspective with its source URLs. DO NOT include any URL in your reply body.

STRICT NEUTRALITY: every perspective gets the same charitable framing. Don't editorialize about which side is "right".

STYLE EXAMPLES (fit comfortably under 256 chars):
- "Two camps: Pro-UBI sees a universal floor as essential; targeted-policy camp prefers retraining + sector subsidies. Both have credible backing."
- "Contested space — cost-control view warns of expense; safety-net view emphasizes scale of displacement. Neither side is fringe."
"""

_DECLINE_TEMPLATE = """You are a fact-check bot writing ONE reply tweet when the parent post has NO actionable angle — no factually verifiable claim, no opinion worth contesting, no contested space to surface.

INPUT: RendererView with `presentation_payload.headline_finding` carrying a short reason (e.g. "Personal opinion, no checkable claim.").

YOUR JOB: a brief acknowledgment that there's nothing to fact-check / push back on / contextualize. Don't editorialize. NO URL — keep it short (≤ 120 chars is fine).

STYLE EXAMPLES:
- "No factual claim to check here — reads as opinion."
- "Personal take, nothing for the fact-checker to weigh in on."
"""


_ACTION_TEMPLATES: dict[Action, str] = {
    "verify": _VERIFY_TEMPLATE,
    "provide_context": _PROVIDE_CONTEXT_TEMPLATE,
    "challenge_opinion": _CHALLENGE_OPINION_TEMPLATE,
    "surface_perspectives": _SURFACE_PERSPECTIVES_TEMPLATE,
    "decline": _DECLINE_TEMPLATE,
}


# ── Per-tone register snippets ─────────────────────────────────────────────

_NEUTRAL_REGISTER = """REGISTER — plain correction with source. Bode, Vraga & Tully (2020) style: straightforward, evidence-first, detached. Declarative sentences, named sources, no rhetorical flourish."""

_AGREEABLE_REGISTER = """REGISTER — empathetic, non-judgemental. Lewandowsky Debunking Handbook structure: acknowledge why a reasonable person might engage with this, then provide the substantive content. Avoid bare negation; lead with the alternative.

Patterns:
- Affirm the impulse: "Easy to see why this caught attention — …"
- Provide the alternative directly; don't pivot through a "but actually" turn.
- Warm, never condescending. Never "well, actually" or "let me explain"."""

_AGONISTIC_REGISTER = """REGISTER — pointed, sardonic. Boukes & Hameleers (2022) style: the substance lands inside a wrapper of dry mockery. Often a single rhetorical line; always grounded in the evidence.

Targeted ridicule lands; generic ridicule sounds canned. Read tone_neutral_justification carefully for a specific damning signal and make THAT the centerpiece:
- "self-described satirical/parody site" → mock that the source openly disclosed it.
- "AI-generated" / "Midjourney" / "deepfake" → mock the generation artefact or unchecked viral spread.
- "miscaptioned" / "out-of-context" → mock the trivial-verification miss ("Ten seconds with reverse image search would have settled this.").
- recycled old event → mock the recency theatre.
- mainstream fact-checker already covered it → mock the unforced error.

STRICT BOUNDARY:
- NO profanity. NO slurs.
- NO attack on identity, appearance, demographics, gender, race, religion, nationality, accent, age, body, or personal traits.
- TARGET is the CLAIM or the SOURCE's credibility — NEVER the person whose image or name appears.
- No content that could read as harassment of any specific named person.

When the action is surface_perspectives, the sarcasm is OFF — the goal is to present multiple credible camps with equal weight; mocking either side breaks neutrality."""


_TONE_REGISTERS: dict[Tone, str] = {
    "neutral": _NEUTRAL_REGISTER,
    "agreeable": _AGREEABLE_REGISTER,
    "agonistic": _AGONISTIC_REGISTER,
}


# ── Hard constraints ───────────────────────────────────────────────────────

_PIVOT_ASKED_LABEL: dict[Action, str] = {
    "verify": "a fact-check",
    "provide_context": "context",
    "challenge_opinion": "push-back",
    "surface_perspectives": "multiple views",
    "decline": "the bot to weigh in",
}


def _hard_constraints_for(
    action: Action, state: _RenderState, pivoted: bool
) -> str:
    """Action-aware hard constraints. Renderer output that violates these is
    rejected and retried with the failure as feedback."""
    base = [
        "HARD CONSTRAINTS (violations are rejected and retried):",
        "- Communicate the headline_finding faithfully.",
        '- Reproduce every proper noun (people, places, organizations, dates, publications) that appears in tone_neutral_justification or presentation_payload VERBATIM. Do not generalize: keep names like "World News Daily Report", "Buzz Aldrin", "Snopes", and dates like "March 2015" intact.',
        "- ZERO URLs in your reply body. The runtime appends a separate /info short link that carries all source URLs + structured reasoning. Name sources by their display_name (e.g. \"Snopes\", \"AP News\") in your text — never as a link.",
        "- Never introduce facts outside presentation_payload + tone_neutral_justification.",
        "- No emojis, no hashtags, no @-mentions.",
        f"- ≤{_X_TWEET_LIMIT} X-weighted chars total. Aim a few chars under.",
        '- Output a JSON object with a single "text" field. No preamble, no prose around the JSON.',
    ]
    if pivoted:
        base.append(
            "- The invoker asked for one action and the pipeline took a different one (see `pivoted_from` in the prompt). Weave a brief, natural pivot clarification into your reply — e.g. \"this is actually verifiable, so:\" — within the same char budget. Don't apologize or use stiff disclosure language; just acknowledge the shift and move on."
        )

    # Action-specific reinforcements
    if action == "verify" and state == "actionable":
        base.append("- This is a verify action — quoting load_bearing_evidence_snippet (when present and short) inside quotes is encouraged.")
    if action == "challenge_opinion" and state == "actionable":
        base.append("- This is a challenge_opinion action — explicitly NAME the credible critic / outlet whose counterpoint you're citing.")
    if action == "surface_perspectives" and state == "actionable":
        base.append("- This is a surface_perspectives action — present EXACTLY two perspectives (the first two listed). Preserve each label's framing; paraphrase only if the verbatim form blows the char budget. Source naming is OPTIONAL — /info carries them.")

    return "\n".join(base)


# ── Composition ────────────────────────────────────────────────────────────

def _system_prompt_for(
    action: Action, tone: Tone, state: _RenderState, pivoted: bool
) -> str:
    template = _ACTION_TEMPLATES.get(action, _VERIFY_TEMPLATE)
    register = _TONE_REGISTERS.get(tone, _NEUTRAL_REGISTER)
    constraints = _hard_constraints_for(action, state, pivoted)
    return f"{template}\n\n{register}\n\n{constraints}"


def _build_prompt(view: RendererView, state: _RenderState) -> str:
    parts = [
        "Render the reply.\n",
        f"action: {view.action}",
        f"action_outcome: {view.action_outcome}",
        f"state: {state}",
    ]
    if view.pivoted_from and view.pivoted_from != view.action:
        asked = _PIVOT_ASKED_LABEL.get(view.pivoted_from, view.pivoted_from)
        parts.append(f"pivoted_from: {view.pivoted_from} (invoker asked for {asked})")
        if view.invoker_instruction_text:
            parts.append(f"invoker_instruction_text: {view.invoker_instruction_text!r}")
    parts.append("")
    parts.append(f"presentation_payload:\n{view.presentation_payload.model_dump_json(indent=2)}\n")
    parts.append(f"tone_neutral_justification:\n{view.tone_neutral_justification}")
    return "\n".join(parts)


# ── Refusal-aware retry nudge ──────────────────────────────────────────────

_REFUSAL_NUDGE = """The previous attempt did not produce a usable reply. This is a public-good fact-check bot — its job is to attach credible context to claims circulating on X. The reply target is misinformation or framing, never an individual. Stay within the constraints. Stick to evidence already in presentation_payload + tone_neutral_justification; do not editorialize beyond the register."""


def _looks_like_refusal(text: str) -> bool:
    """Refusal almost always leads. Anchor markers to start-of-text so
    legitimate replies that quote refusal phrases don't false-trigger."""
    lower = text.lstrip().lower()
    return any(lower.startswith(marker) for marker in _REFUSAL_MARKERS)


# ── Invariance check ───────────────────────────────────────────────────────

def _enforce_invariance(text: str, view: RendererView, state: _RenderState) -> None:
    """Invariance check.

    All URLs are forbidden in the body — sources live on the /info page,
    which is reached via the short link the runtime appends after the
    rendered text. The body talks about sources by their display_name only.

    Always: non-empty, not a refusal, body ≤ _X_TWEET_LIMIT X-weighted chars.
    Pivot disclosure (when applicable) is part of the body — the model owns
    the whole envelope, no mechanical prefix.
    """
    if not text:
        raise ValueError("Renderer returned empty text.")
    if _looks_like_refusal(text):
        raise ValueError(f"Renderer output looks like a refusal: {text[:160]!r}")

    urls_in_reply = URL_RE.findall(text)
    if urls_in_reply:
        raise ValueError(
            f"Renderer emitted URL(s) in the body — sources belong on /info, not in the tweet: {sorted(set(urls_in_reply))}"
        )

    if x_weighted_length(text) > _X_TWEET_LIMIT:
        raise ValueError(
            f"Rendered reply body is {x_weighted_length(text)} X-weighted chars (body limit {_X_TWEET_LIMIT})."
        )


# ── Public entry point ─────────────────────────────────────────────────────

def render(view: RendererView, tone: Tone, *, max_invariance_retries: int = 3) -> str:
    """Compose system = action_template + tone_register + hard_constraints,
    call Claude, enforce invariance with retries, fall back to refusal nudge
    on call_claude_json failure, raise on second failure."""
    if tone not in _TONE_REGISTERS:
        raise ValueError(f"Unknown tone {tone!r}")
    if view.action not in _ACTION_TEMPLATES:
        raise ValueError(f"Unknown action {view.action!r}")

    state = _state_for(view)
    pivoted = bool(view.pivoted_from and view.pivoted_from != view.action)
    system_prompt = _system_prompt_for(view.action, tone, state, pivoted)
    base_prompt = _build_prompt(view, state)
    last_error: Exception | None = None

    # Pass 1 — normal prompt, invariance-feedback retries
    last_text: Optional[str] = None
    for attempt in range(max_invariance_retries + 1):
        prompt = base_prompt
        if last_error is not None and isinstance(last_error, ValueError):
            err_msg = str(last_error)
            extra = ""
            if last_text is not None and ("body limit" in err_msg or "X-weighted chars" in err_msg):
                excess = x_weighted_length(last_text) - _X_TWEET_LIMIT
                target = max(_X_TWEET_LIMIT - 12, 40)  # aim under, not exactly at, the cap
                extra = (
                    f" Your last attempt was {x_weighted_length(last_text)} chars; the cap is "
                    f"{_X_TWEET_LIMIT}. You must cut AT LEAST {excess + 12} chars to land near "
                    f"{target}. Rewrite from scratch — do not tweak. Keep ONLY: the verdict + one "
                    f"source name + (if refuted) the corrective. Drop the verbatim quote, drop "
                    f"explanatory clauses, drop any 'according to' framing — just state the finding "
                    f"and name the source.\n\nYour previous attempt was:\n{last_text!r}\n"
                )
            prompt += (
                f"\n\nYour previous attempt failed this hard constraint: {err_msg}.{extra} "
                "Fix it and try again. Stay within all the other constraints."
            )
        try:
            reply = call_claude_json(
                prompt=prompt,
                schema=RenderedReply,
                system=system_prompt,
                reasoning_effort=None,
                max_tokens=512,
                timeout=30.0,
            )
        except ValueError as exc:
            logger.warning(
                "render[%s/%s]: pass-1 call_claude_json failed (%s) — escalating to refusal nudge",
                view.action, tone, exc,
            )
            last_error = exc
            break
        text = reply.text.strip()
        try:
            _enforce_invariance(text, view, state)
            return text
        except ValueError as exc:
            last_error = exc
            last_text = text
            logger.info(
                "render[%s/%s]: invariance retry %d/%d (%s)",
                view.action, tone, attempt + 1, max_invariance_retries, exc,
            )

    # Pass 2 — refusal nudge once
    try:
        reply = call_claude_json(
            prompt=base_prompt + "\n\n" + _REFUSAL_NUDGE,
            schema=RenderedReply,
            system=system_prompt,
            reasoning_effort=None,
            max_tokens=512,
            timeout=30.0,
        )
        text = reply.text.strip()
        _enforce_invariance(text, view, state)
        logger.info("render[%s/%s]: succeeded after refusal nudge", view.action, tone)
        return text
    except Exception as exc:
        logger.warning("render[%s/%s]: refusal-nudge pass also failed (%s)", view.action, tone, exc)
        if last_error is not None:
            raise last_error
        raise
