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
anything — the model owns the full reply body, including any pivot
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

import anthropic
from pydantic import BaseModel

from agent.shared.text import URL_RE, X_TWEET_LIMIT, x_weighted_length

from .freeze import RendererView
from .llm import call_claude_json
from .prompt_store import load_prompt
from .render_lint import lint_cross_tone, lint_substance
from .schema import Action, Tone


logger = logging.getLogger(__name__)

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
- state="actionable": communicate the headline_finding plainly. NAME the source(s) by their `display_name` from `primary_sources_to_cite` (e.g. "Snopes", "AP News"). If `counter_fact` is set (verify-refuted), incorporate the corrective. Don't just state the verdict and cite sources — briefly explain the mechanism: what the claim asserts, what the evidence actually shows, and why that settles it.
- state="no_evidence": briefly acknowledge the claim is testable but credible coverage wasn't found.

A separate `/info` short link is appended to your reply automatically; that page carries all source URLs + reasoning. DO NOT include any URL in your reply body.
"""

_PROVIDE_CONTEXT_TEMPLATE = """You are a fact-check bot writing ONE reply tweet to SUPPLY MISSING CONTEXT. The literal claim may be accurate, but the framing leaves out something material.

INPUT: RendererView. Read `presentation_payload.context_note` for the missing piece, `primary_sources_to_cite` for sources backing it (use their `display_name` only — no URLs in your body).

YOUR JOB BY STATE:
- state="actionable": surface the missing context plainly. Name the source by display_name. Don't argue the literal claim is wrong — frame as "what this leaves out is …" or "important context here: …". Explain WHY the missing context changes how a reader should interpret the claim — not just what it is.
- state="no_evidence": acknowledge the framing seems incomplete but credible context coverage wasn't found.

A separate `/info` short link is appended automatically; that page carries the source URLs. DO NOT include any URL in your reply body.
"""

_CHALLENGE_OPINION_TEMPLATE = """You are a fact-check bot writing ONE reply tweet to PUSH BACK on a strongly-stated opinion.

INPUT: RendererView. Read `presentation_payload.counterpoints` for the credible counter-arguments. The `citing_sources` field on each counterpoint identifies the source — use the URL's display name from `primary_sources_to_cite` in your text (no URLs in the body itself).

YOUR JOB BY STATE:
- state="actionable": present the strongest counterpoint from `counterpoints`. NAME the credible critic / outlet / study by name. Be substantive — your job is to put credible push-back in front of the reader. Explain the mechanism — what premise the opinion rests on and what the counter-evidence shows about it — not just who disagrees.
- state="no_evidence": acknowledge the opinion is contested but credible push-back wasn't found in this window.

A separate `/info` short link is appended automatically; that page lists every counterpoint with its source URLs. DO NOT include any URL in your reply body.

Push back on the OPINION, not the person. "Researchers at NEJM argue …" / "Cochrane published a meta-analysis showing …" — focus on the empirical counter.
"""

_SURFACE_PERSPECTIVES_TEMPLATE = """You are a fact-check bot writing ONE reply tweet to SURFACE MULTIPLE PERSPECTIVES on a contested topic.

INPUT: RendererView. `presentation_payload.perspectives` lists 2–3 credible viewpoints, each with `label`, `summary`, and `citing_sources`. The first two are the strongest.

YOUR JOB BY STATE:
- state="actionable": surface ONE alternative perspective that PUSHES BACK against the original claim. Explain the substance of the perspective and the evidence behind it — not just that it exists. Source naming is OPTIONAL — the appended /info link carries every source already, so name them only if space allows; otherwise let the labels and substance do the work.
- state="no_evidence": acknowledge the topic is contested but credible perspectives weren't surfaced.

A separate `/info` short link is appended automatically; that page lists every perspective with its source URLs. DO NOT include any URL in your reply body.
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

_NEUTRAL_REGISTER = """# REGISTER:
Write the response in a neutral, detached tone. Declarative sentences, named sources, no rhetorical flourish. Follow these principles, modeled on effective crowd-sourced fact-checking:

1. DIRECT ENGAGEMENT — This is a reply. Open by directly referencing what was stated in the tweet. Name the specific claim. The response should feel like it's talking to this particular post, not delivering a generic briefing.
2. SYNTHESIS — Do not just cite one note. Read all notes together and combine their insights into a unified, holistic response that covers the key factual points.
3. NEUTRAL LANGUAGE — Use plain, measured, non-partisan language. Do not frame the response to favor one political side. Avoid charged words, rhetorical questions, or loaded framing.
4. NON-ARGUMENTATIVE — Do not speculate, editorialize, or express opinions. State what the evidence shows and stop there. If the evidence is mixed, say so plainly.
5. CLARITY — Write in clear, direct sentences that are easy to understand for a general audience.
6. CONTEXT — Prioritize providing useful context that helps readers understand the full picture, not just a narrow rebuttal.

# EXAMPLE:
CLAIM: Vaccines cause autism.

OUTPUT:
The claim that vaccines cause autism isn't supported — large studies covering millions of children find no causal link.\\n\\nThe original study making this connection was retracted, and its author lost his medical license.
"""

_AGREEABLE_REGISTER = """# REGISTER:
Write the reply in an agreeable tone. Acknowledge why a reasonable person might agree with the original claim, then provide the substantive content. Help all parties to the conversation feel understood.

1. RESTATEMENT — Begin by restating the original claim in your own words so they know you understood what they said.
2. VALIDATION — Affirm that it is reasonable to hold the original concern or perspective, without necessarily agreeing with the claim. (e.g., "I can see why this would be troubling" or "A lot of people share this concern.")
3. POLITENESS — Use respectful, non-defensive language throughout. Soften any friction without hiding the evidence.

# EXAMPLE:
CLAIM: "Vaccines cause autism."

OUTPUT:
It sounds like you're worried about a link between vaccines and autism — a concern many parents share.\\n\\nStudies covering millions of children find no such link; the original Wakefield study was retracted.
"""

_SATIRICAL_REGISTER = """# REGISTER:
Write the reply in a satirical tone. Act like a staff writer for a satirical publication like The Onion or a late night TV show like Last Week Tonight with John Oliver.

The action instructions above tell you WHAT to convey (the finding / context / counterpoint / perspective) — they do NOT govern your VOICE. Ignore any "plainly" / "state it directly" / "explain the mechanism" phrasing from them: in this register the satire IS the delivery, not an earnest explanation with a joke bolted on.

# STRICT BOUNDARY:
- NO profanity. NO slurs.
- NO attack on identity, appearance, demographics, gender, race, religion, nationality, accent, age, body, or personal traits.
- TARGET is the CLAIM or the SOURCE's credibility — NEVER the person whose image or name appears.
- No content that could read as harassment of any specific named person.

# REASONING PROCESS (internal — think before writing)

STEP 1: Two worlds.
  World A: what would have to be true if the claim were true?
  World B: what is actually true per the sources?
  Where do they collide most sharply?

STEP 2: Second-order absurdity.
  Name 3 NON-OBVIOUS consequences of taking World A seriously.
  Not "but it's wrong" — that's first-order. Go further:
  who gets inconvenienced, what has to be retroactively rewritten,
  what banal task becomes surreal, what institution has to issue a memo?

STEP 3: Find the angle — resist (c) unless it's genuinely the funniest option:
  (a) Innocent Bystander — someone inconvenienced by the lie
  (b) Retroactive Rewrite — reality scrambling to accommodate the falsehood
  (c) Expert Forced To State The Obvious — institution dragged in
  (d) Mundane Consequence — a tiny, specific, banal effect of the absurd premise
  (e) Something else entirely — a form that fits this particular gap

STEP 4: Write it. Then ask two questions:
  (a) Does this make someone laugh, or just nod? Nodding is not enough.
      Find the specific absurd detail that tips it from ironic to funny.
  (b) Does it feel like a direct reply to THIS tweet, or a standalone article?
      It should feel like a wry remark aimed at what was just said.
  Rewrite until both are yes.

# EXAMPLE:
CLAIM: Vaccines cause autism.

OUTPUT:
You're citing the one retracted paper — the 25 years and millions of children confirming the opposite are fine though.\\n\\nThe researchers said they remain available for further questions and have cleared their schedules through 2040.
"""


_TONE_REGISTERS: dict[Tone, str] = {
    "neutral": _NEUTRAL_REGISTER,
    "agreeable": _AGREEABLE_REGISTER,
    "satirical": _SATIRICAL_REGISTER,
}


# ── Length profiles (tunable: prompt guidance + enforced ceiling in chars) ───
# The ceiling is enforced in _enforce_invariance (capped at X_TWEET_LIMIT); the
# guidance line steers the model toward the target before the ceiling bites.
_LENGTH_PROFILES: dict[str, tuple[str, int]] = {
    "tight":  ("- LENGTH: 1–2 sentences, ~40 words. Make the single most important point, name the source once, stop. No second paragraph, no recap, no throat-clearing.", 320),
    "short":  ("- LENGTH: one short paragraph — 2–4 sentences, ~65 words. Make the key point, name the source, stop. No recap, no restating the verdict, no padding.", 520),
    "medium": ("- LENGTH: 1–2 short paragraphs, ~110 words max. Explain the one key mechanism, name the source, stop. Don't pad, don't repeat the verdict.", 900),
}
_DEFAULT_LENGTH = "short"


# ── Hard constraints ───────────────────────────────────────────────────────

_PIVOT_ASKED_LABEL: dict[Action, str] = {
    "verify": "a fact-check",
    "provide_context": "context",
    "challenge_opinion": "push-back",
    "surface_perspectives": "multiple views",
    "decline": "the bot to weigh in",
}


def _hard_constraints_for(
    action: Action, state: _RenderState, pivoted: bool, tone: Tone, length_key: str
) -> str:
    """Action-, tone-, and length-aware hard constraints. Renderer output that
    violates these is rejected and retried with the failure as feedback.

    The earnest "lead with the verdict / explain the mechanism" reinforcements
    are applied for neutral & agreeable only; satirical gets a satire-discipline
    constraint instead, so the comedic voice isn't fighting an explanatory
    skeleton it can't carry.
    """
    length_line = _LENGTH_PROFILES.get(length_key, _LENGTH_PROFILES[_DEFAULT_LENGTH])[0]
    base = [
        "HARD CONSTRAINTS (violations are rejected and retried):",
        "- Communicate the headline_finding faithfully.",
        '- Reproduce every proper noun (people, places, organizations, dates, publications) that appears in tone_neutral_justification or presentation_payload VERBATIM. Do not generalize: keep names like "World News Daily Report", "Buzz Aldrin", "Snopes", and dates like "March 2015" intact.',
        "- ZERO URLs in your reply body. The runtime appends a separate /info short link that carries all source URLs + structured reasoning. Name sources by their display_name (e.g. \"Snopes\", \"AP News\") in your text — never as a link.",
        "- Facts in your reply come ONLY from presentation_payload + tone_neutral_justification. `reply_target` (the post you're replying to) and `invoker_ask` are provided so your phrasing can be responsive — do NOT quote reply_target verbatim, do NOT treat its claims as evidence, and do NOT introduce names / numbers / dates that appear in it but not in presentation_payload or tone_neutral_justification. This holds even when you restate what the claim asserts: characterize the claim only at the level of detail in presentation_payload / tone_neutral_justification. Do NOT import incidental specifics from reply_target (hospitals, cities, named officials, hashtags, dollar figures) into your restatement — repeating a fabricated specific amplifies it.",
        "- No emojis, no hashtags, no @-mentions.",
        length_line,
        '- Output a JSON object with a single "text" field. No preamble, no prose around the JSON.',
    ]
    if pivoted:
        base.append(
            "- The invoker asked for one action and the pipeline took a different one (see `pivoted_from` in the prompt). Weave a brief, natural pivot clarification into your reply — e.g. \"this is actually verifiable, so:\" — within the same char budget. Don't apologize or use stiff disclosure language; just acknowledge the shift and move on."
        )

    if tone == "satirical":
        base.append(
            "- SATIRE DISCIPLINE: the comedic angle IS the delivery. Open on the absurdity or a wry "
            "observation — never a \"Yes/No/Partly\" verdict header or a \"here's the context\" preamble. "
            "Land exactly ONE load-bearing fact and name the source once, woven into the bit — do NOT "
            "append an earnest explanatory paragraph after the joke. If the claim is actually TRUE, or the "
            "evidence is thin/absent, aim the satire at the FRAMING or the source's self-certainty, not at "
            "a fact that gives you nothing to work with. Commit to the voice the whole way through — no "
            "reverting to a straight fact-check for the second half."
        )
        return "\n".join(base)

    # Earnest (neutral / agreeable) action-specific reinforcements
    if action == "verify" and state == "actionable":
        base.append("- Lead with headline_finding (the verdict). When `invoker_ask` poses a yes/no question (e.g. \"is this true?\"), open with a one-word verdict — Yes / No / Partly / Unclear — before the explanation. Then explain: what is the claim asserting, what does the evidence specifically show about that assertion, and what should the reader update? Quoting load_bearing_evidence_snippet inside quotes is encouraged when it makes the argument concrete.")
    if action == "provide_context" and state == "actionable":
        base.append("- Lead with headline_finding. Then explain: what does the claim's framing imply, what does context_note reveal about that implication, and why does the missing context change how a reader should interpret the claim?")
    if action == "challenge_opinion" and state == "actionable":
        base.append("- Lead with headline_finding. Then explain: what premise does the opinion depend on, what does the counter-evidence specifically show about that premise, and what does that imply for the opinion's conclusion? Explicitly NAME the credible critic / outlet whose counterpoint you're citing.")
    if action == "surface_perspectives" and state == "actionable":
        base.append("- Lead with headline_finding. Then surface ONE alternative perspective (the first in the list) that pushes back against the original claim: explain the view, the specific evidence it marshals, and why it challenges the claim. Preserve the label's framing; paraphrase only if the verbatim form is unwieldy.")

    return "\n".join(base)


# ── Composition ────────────────────────────────────────────────────────────

def _system_prompt_for(
    action: Action, tone: Tone, state: _RenderState, pivoted: bool, length_key: str
) -> str:
    template = _ACTION_TEMPLATES.get(action, _VERIFY_TEMPLATE)
    register = _TONE_REGISTERS.get(tone, _NEUTRAL_REGISTER)
    constraints = _hard_constraints_for(action, state, pivoted, tone, length_key)
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
    parts.append("")
    if view.parent_post_text:
        parts.append(
            "reply_target (the post you are replying to — for tone/responsiveness ONLY; "
            "never a source of facts):\n"
            f"{view.parent_post_text!r}\n"
        )
    if view.invoker_instruction_text:
        parts.append(
            "invoker_ask (what the user wrote when tagging the bot; address implicitly if natural):\n"
            f"{view.invoker_instruction_text!r}\n"
        )
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

def _enforce_invariance(
    text: str, view: RendererView, state: _RenderState, max_chars: int = X_TWEET_LIMIT
) -> None:
    """Invariance check.

    All URLs are forbidden in the body — sources live on the /info page,
    which is reached via the short link the runtime appends after the
    rendered text. The body talks about sources by their display_name only.

    Always: non-empty, not a refusal, body ≤ `max_chars` X-weighted chars
    (the length-profile ceiling, itself never above X_TWEET_LIMIT). Pivot
    disclosure (when applicable) is part of the body — the model owns the
    whole envelope, no mechanical prefix.
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

    if x_weighted_length(text) > max_chars:
        raise ValueError(
            f"Rendered reply body is {x_weighted_length(text)} X-weighted chars (body limit {max_chars})."
        )


# ── Public entry point ─────────────────────────────────────────────────────

def render(
    view: RendererView, tone: Tone, *,
    max_invariance_retries: int = 3, length_key: str = _DEFAULT_LENGTH,
) -> str:
    """Compose system = action_template + tone_register + hard_constraints,
    call Claude, enforce invariance with retries, fall back to refusal nudge
    on call_claude_json failure, raise on second failure.

    `length_key` selects a length profile (prompt guidance + enforced char
    ceiling). Satirical renders run with a reasoning budget so the register's
    multi-step comedic reasoning can actually execute before it commits."""
    if tone not in _TONE_REGISTERS:
        raise ValueError(f"Unknown tone {tone!r}")
    if view.action not in _ACTION_TEMPLATES:
        raise ValueError(f"Unknown action {view.action!r}")

    max_chars = min(_LENGTH_PROFILES.get(length_key, _LENGTH_PROFILES[_DEFAULT_LENGTH])[1], X_TWEET_LIMIT)
    effort = "medium" if tone == "satirical" else None
    render_timeout = 60.0 if effort else 30.0
    state = _state_for(view)
    pivoted = bool(view.pivoted_from and view.pivoted_from != view.action)
    system_prompt = _system_prompt_for(view.action, tone, state, pivoted, length_key)
    base_prompt = _build_prompt(view, state)
    last_error: Exception | None = None

    # Pass 1 — normal prompt, invariance-feedback retries
    last_text: Optional[str] = None
    best_over: Optional[str] = None   # shortest attempt that failed ONLY the length cap
    for attempt in range(max_invariance_retries + 1):
        prompt = base_prompt
        if last_error is not None and isinstance(last_error, ValueError):
            err_msg = str(last_error)
            extra = ""
            if last_text is not None and ("body limit" in err_msg or "X-weighted chars" in err_msg):
                excess = x_weighted_length(last_text) - max_chars
                extra = (
                    f" Your last attempt was {x_weighted_length(last_text)} chars; the cap is "
                    f"{max_chars}. You must cut at least {excess + 40} chars. Trim redundant "
                    f"sentences and repetition — keep the substance.\n\nYour previous attempt was:\n{last_text!r}\n"
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
                reasoning_effort=effort,
                max_tokens=8192,
                timeout=render_timeout,
            )
        except (ValueError, anthropic.APIConnectionError) as exc:
            # anthropic.APIConnectionError (parent of APITimeoutError, which
            # does NOT subclass TimeoutError/ValueError) must be caught here
            # too — otherwise a transient Claude timeout on pass 1 escapes
            # render() entirely and skips the pass-2 refusal-nudge retry,
            # dropping an already-computed reply.
            logger.warning(
                "render[%s/%s]: pass-1 call_claude_json failed (%s) — escalating to refusal nudge",
                view.action, tone, exc,
            )
            last_error = exc
            break
        text = reply.text.strip()
        try:
            _enforce_invariance(text, view, state, max_chars)
            return text
        except ValueError as exc:
            last_error = exc
            last_text = text
            if "body limit" in str(exc) and (
                best_over is None or x_weighted_length(text) < x_weighted_length(best_over)
            ):
                best_over = text
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
            reasoning_effort=effort,
            max_tokens=8192,
            timeout=render_timeout,
        )
    except Exception as exc:
        logger.warning("render[%s/%s]: refusal-nudge call failed (%s)", view.action, tone, exc)
    else:
        text = reply.text.strip()
        try:
            _enforce_invariance(text, view, state, max_chars)
            logger.info("render[%s/%s]: succeeded after refusal nudge", view.action, tone)
            return text
        except ValueError as exc:
            last_error = exc
            if "body limit" in str(exc) and (
                best_over is None or x_weighted_length(text) < x_weighted_length(best_over)
            ):
                best_over = text

    # Graceful length degrade: if the ONLY unmet constraint was the soft length
    # ceiling, return the shortest attempt rather than failing the whole render
    # over a stylistic cap. URLs/refusals still hard-fail; still bounded by the
    # platform limit.
    if best_over is not None and x_weighted_length(best_over) <= X_TWEET_LIMIT:
        logger.warning(
            "render[%s/%s]: could not meet length cap %d after retries; returning "
            "shortest valid attempt (%d chars)",
            view.action, tone, max_chars, x_weighted_length(best_over),
        )
        return best_over
    if last_error is not None:
        raise last_error
    raise RuntimeError("render produced no output and no error")


# ── v0.7 neutral-first rendering + register transformation ─────────────────
#
# WHY this exists: tone variants used to be three INDEPENDENT generations from
# the payload, and they diverged — the satirical variant dropped load-bearing
# facts, and variants asserted conflicting numbers. Neutral-first fixes the
# fact set once (the neutral render is the source of truth) and derives the
# other registers by TRANSFORMING that text, holding substance constant while
# changing only voice. The Task-10 lints (R-4 substance, R-5 cross-tone) are
# the gate: a variant that can't pass after retries falls back to the neutral
# text rather than shipping divergent substance.

def _transform_register(neutral_text: str, tone: Tone, view: RendererView,
                        feedback: str = "") -> str:
    """One register-transformation call: neutral text in, same-substance
    re-voiced text out. Enforces the standard invariance checks.

    Substance is held fixed by construction — the prompt feeds the neutral
    reply (the source of truth) plus the frozen fact list and instructs the
    model to change only voice. `feedback`, when set, is the prior attempt's
    lint/invariance violations, appended so the retry can self-correct."""
    system = load_prompt("render_transform") + "\n\n" + _TONE_REGISTERS[tone]
    max_chars = min(_LENGTH_PROFILES[_DEFAULT_LENGTH][1], X_TWEET_LIMIT)
    prompt = (
        f"NEUTRAL REPLY (source of truth):\n{neutral_text}\n\n"
        f"TARGET REGISTER: {tone}\n"
        f"FACT LIST (must survive):\n"
        + "\n".join(f"- {f}" for f in view.presentation_payload.load_bearing_facts)
        + (f"\n\nPREVIOUS ATTEMPT FAILED THESE CHECKS:\n{feedback}" if feedback else "")
    )
    reply = call_claude_json(
        prompt=prompt, schema=RenderedReply, system=system,
        reasoning_effort="medium" if tone == "satirical" else None,
        max_tokens=4096, timeout=60.0,
    )
    text = reply.text.strip()
    _enforce_invariance(text, view, _state_for(view), max_chars)
    return text


def render_all_tones(
    view: RendererView, *, length_key: Optional[str] = None, max_lint_retries: int = 2,
) -> dict:
    """v0.7 neutral-first rendering. Neutral is rendered as before; satirical
    and agreeable are register TRANSFORMATIONS of the neutral text, gated by
    lint R-4 (substance) and R-5 (cross-tone facts). A variant that cannot
    pass falls back to the neutral text.

    Returns a dict with keys neutral/satirical/agreeable. A lint-failing
    variant is NEVER shipped: after `max_lint_retries` failed attempts (each
    retried with the violations as feedback), that key falls back to the
    neutral text so all three registers agree on substance."""
    neutral = render(view, "neutral", **({"length_key": length_key} if length_key else {}))
    payload, just = view.presentation_payload, view.tone_neutral_justification
    out = {"neutral": neutral}
    for tone in ("satirical", "agreeable"):
        text, feedback = None, ""
        for _ in range(max_lint_retries + 1):
            try:
                candidate = _transform_register(neutral, tone, view, feedback)
            except Exception as exc:                      # transform/invariance failure
                logger.warning("render_all_tones[%s]: transform failed (%s)", tone, exc)
                feedback = str(exc)
                continue
            violations = lint_substance(candidate, payload, just)
            violations += [v for v in
                           lint_cross_tone({tone: candidate}, payload.load_bearing_facts)]
            if not violations:
                text = candidate
                break
            feedback = "; ".join(violations)
            logger.info("render_all_tones[%s]: lint retry (%s)", tone, feedback[:200])
        out[tone] = text if text is not None else neutral
    return out
