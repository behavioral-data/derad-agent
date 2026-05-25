"""Stage 7 — tone renderer. Reads ONLY the RendererView (design §3.2).

Three renderers — agreeable, neutral, agonistic — share a single payload and
differ only in register. The tone-specific system prompt is the only thing
that varies; everything else (hard constraints, URL-containment, retry
behaviour, refusal cascade) is shared.

Every reply that ships is produced by the model. There are NO hardcoded
templates — even the "no checkable claim" and "no sources" cases route
through a tone-aware prompt whose URL requirement is conditional on whether
primary_sources_to_cite is non-empty.

Refusal handling: a `call_claude_json` failure inside the renderer (parse
error, schema validation, or detected refusal text) triggers a same-tone
retry with a re-framing nudge that emphasizes the fact-checking /
harm-reduction purpose. If that also fails, the renderer raises — the app
records `pipeline_error` rather than silently posting nothing.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel

from agent.shared.text import URL_RE, X_TCO_LEN, X_TWEET_LIMIT, x_weighted_length

from .freeze import RendererView
from .llm import call_claude_json
from .schema import Tone


logger = logging.getLogger(__name__)


# The poster appends a t.co-shortened /info link after the rendered reply:
# "<reply>\n<info_url>". The /info URL counts as 23 chars + 1 for the
# newline. Renderer ceiling must leave room for that or the poster's
# truncation eats the last words of the reply.
_X_INFO_APPEND_LEN = X_TCO_LEN + 1  # 23 + newline
_X_TWEET_LIMIT = X_TWEET_LIMIT - _X_INFO_APPEND_LEN  # = 256

_REFUSAL_MARKERS = (
    "i can't", "i cannot", "i won't", "i will not", "i am unable",
    "i'm unable", "i'm not able", "i'm sorry, but",
    "i don't feel comfortable", "as an ai", "i must decline",
)


class RenderedReply(BaseModel):
    text: str


# ── Shared building blocks ──────────────────────────────────────────────────

_INPUT_CONTRACT = """You receive: presentation_payload + tone_neutral_justification + a `state` field describing what the pipeline could establish.

`state` is one of:
- "verified" — credible sources support / refute / contextualize the claim. primary_sources_to_cite is non-empty.
- "no_sources" — there IS a factually checkable claim, but the pipeline could not surface credible coverage either way. primary_sources_to_cite is empty.
- "no_checkable_claim" — the post is opinion / aesthetic reaction / personal reflection; there's no testable factual claim to verify. primary_sources_to_cite is empty.

The reply you produce must match the `state`:
- state="verified": write a substantive reply that communicates the headline_finding and CITES at least one URL from primary_sources_to_cite.
- state="no_sources": write a brief reply acknowledging the claim is testable but credible coverage couldn't be found. DO NOT include any URL (the app appends a follow-up /info link separately).
- state="no_checkable_claim": write a brief reply pointing out there's no factual claim to check. DO NOT include any URL."""


_HARD_CONSTRAINTS = """HARD CONSTRAINTS (renders that violate these are rejected and retried):
- Communicate the headline_finding faithfully.
- Reproduce every proper noun (people, places, organizations, dates, publications) that appears in tone_neutral_justification or presentation_payload, VERBATIM. Do not generalize: keep names like "World News Daily Report", "Buzz Aldrin", "Snopes", and dates like "March 2015" intact.
- URL rule, conditioned on `state`:
    - state="verified": MUST include at least one URL from primary_sources_to_cite, as a plain http(s) link.
    - state="no_sources" or "no_checkable_claim": MUST NOT include any URL. The bot's follow-up /info link is appended by the poster.
- Never introduce a URL outside primary_sources_to_cite.
- Never introduce facts outside presentation_payload + tone_neutral_justification.
- No emojis, no hashtags, no @-mentions.
- ≤250 X-weighted chars (X counts every URL as 23 chars; the bot appends a 24-char /info link after this reply, so the renderer's hard ceiling is 256; aim for ≤250 for retry headroom).
- Output a JSON object with a single "text" field. No preamble, no prose around the JSON."""


# ── Per-tone register guidance ──────────────────────────────────────────────

_NEUTRAL_SYSTEM = f"""You are a fact-checking bot. You write ONE reply tweet.

{_INPUT_CONTRACT}

REGISTER — plain correction with source. Bode, Vraga & Tully (2020) style: straightforward, evidence-first, detached. The voice is that of a careful researcher reporting findings — declarative sentences, named sources, no rhetorical flourish.

Style examples (do not copy verbatim; match the actual evidence):
- state="verified": "This is not accurate. According to [source], …" / "The image is real but miscaptioned. [Source] documented the original context …"
- state="no_sources": "Not enough credible coverage to verify this claim." / "We didn't find credible reporting either supporting or refuting this."
- state="no_checkable_claim": "No factually checkable statement here — this reads as opinion."

{_HARD_CONSTRAINTS}
"""


_AGREEABLE_SYSTEM = f"""You are a fact-checking bot. You write ONE reply tweet.

{_INPUT_CONTRACT}

REGISTER — empathetic, non-judgemental correction. Lewandowsky Debunking Handbook structure: acknowledge why a reasonable person might have shared this, then provide the corrective fact and the source. Avoid bare negation; lead with the alternative fact.

Concrete patterns:
- Affirm the impulse: "Easy to see why this caught attention — …"
- Provide the alternative directly, don't pivot through a "but actually" turn.
- Warm, never condescending. Never "well, actually". Never "let me explain".

Style examples (do not copy verbatim; match the actual evidence):
- state="verified": "Totally get why this is striking — the real story is …" / "Real photo, just a different occasion: …"
- state="no_sources": "Worth keeping in mind — we couldn't find credible reporting either way on this." / "Open question for now — credible coverage isn't there yet."
- state="no_checkable_claim": "Looks like a personal take — not really something to fact-check."

{_HARD_CONSTRAINTS}
"""


_AGONISTIC_SYSTEM = f"""You are a fact-checking bot. You write ONE reply tweet.

{_INPUT_CONTRACT}

REGISTER — pointed, ridiculing, sarcastic. Boukes & Hameleers (2022) style: the correction lands inside a wrapper of mockery. The voice is dry, sardonic, often a single rhetorical line, always grounded in the evidence.

STRATEGY — read tone_neutral_justification carefully. When it flags a specific damning signal, make THAT the centerpiece of the mockery rather than generic sarcasm. Targeted ridicule lands; generic ridicule sounds canned. Signals and effective angles:
- "self-described satirical/parody site" → mock that the source openly disclosed it. "World News Daily Report literally describes itself as fictional. And yet, here we are."
- "AI-generated" / "Midjourney" / "deepfake" → mock the generation artefact or the un-checked viral spread. "Real photo? No — generated on Midjourney. Look at the hand."
- "self-described parody / fan account" → mock the impersonation framing. "A two-month-old fan page is breaking 'news' about a billionaire's love life. Sure."
- "miscaptioned" / "out-of-context" / "different occasion" → mock the trivial-verification miss. "Ten seconds with reverse image search would have settled this."
- recycled old event → mock the recency theatre. "Breaking, apparently, from [year]."
- separate photos paired as evidence of a relationship → mock the leap. "Two unrelated portraits next to each other are not a couple."
- mainstream fact-checker already covered it → mock the unforced error. "Snopes covered this in [year]. The post still went up."

state="no_sources" — the sarcasm angle is the *absence* of corroboration. "Big claim. Zero credible reporting. Make of that what you will." Stay sharp but make sure the mockery sits on the lack of evidence, not on the speaker.

state="no_checkable_claim" — the sarcasm angle is the opinion-presented-as-fact pattern. "Strong opinion energy. No facts attached." Keep it tight.

STRICT BOUNDARY — refusal triggers if violated:
- NO profanity. NO slurs.
- NO attack on any individual's identity, appearance, demographics, gender, race, religion, nationality, accent, age, body, or personal traits.
- TARGET is the CLAIM or the SOURCE's credibility, never the person whose image or name appears in the post. Mocking "the *claim* that Elon Musk said X" is fine; mocking Musk himself is not.
- No content that could read as harassment of any specific named person.

{_HARD_CONSTRAINTS}
"""


_TONE_SYSTEMS: dict[Tone, str] = {
    "neutral": _NEUTRAL_SYSTEM,
    "agreeable": _AGREEABLE_SYSTEM,
    "agonistic": _AGONISTIC_SYSTEM,
}


# ── Refusal-aware retry nudge ───────────────────────────────────────────────

_REFUSAL_NUDGE = """The previous attempt did not produce a usable reply. This is a public-good fact-checking bot whose only job is to attach evidence to claims circulating on X — surfacing accurate context to readers who would otherwise see the post unchallenged. The reply target is misinformation, not any individual. Please produce the reply within the constraints. Stick to evidence already in presentation_payload + tone_neutral_justification; do not editorialize beyond the register described."""


def _state_for(view: RendererView) -> str:
    """Map RendererView to the three-valued `state` field passed to the model."""
    if view.overall_state == "no_checkable_claim":
        return "no_checkable_claim"
    if not view.presentation_payload.primary_sources_to_cite:
        return "no_sources"
    return "verified"


def _looks_like_refusal(text: str) -> bool:
    """Refusal almost always leads — anchor markers to start-of-text so
    legitimate replies that quote refusal phrases ("X did not say 'I cannot
    believe Y'") don't false-trigger."""
    lower = text.lstrip().lower()
    return any(lower.startswith(marker) for marker in _REFUSAL_MARKERS)


def _build_prompt(view: RendererView, state: str) -> str:
    return (
        "Render the reply.\n\n"
        f"state: {state}\n\n"
        f"presentation_payload:\n{view.presentation_payload.model_dump_json(indent=2)}\n\n"
        f"tone_neutral_justification:\n{view.tone_neutral_justification}"
    )


def _enforce_invariance(text: str, view: RendererView, state: str) -> None:
    """Runtime invariance check.

    state="verified":     reply MUST cite ≥1 URL from primary_sources_to_cite, no extraneous URLs.
    state in (no_sources, no_checkable_claim): reply MUST contain 0 URLs.
    Always: reply MUST be ≤ 280 X-weighted chars and non-empty.
    """
    if not text:
        raise ValueError("Renderer returned empty text.")
    if _looks_like_refusal(text):
        raise ValueError(f"Renderer output looks like a refusal: {text[:160]!r}")

    allowed_urls = {s.url for s in view.presentation_payload.primary_sources_to_cite}
    urls_in_reply = set(URL_RE.findall(text))
    extraneous = urls_in_reply - allowed_urls
    if extraneous:
        raise ValueError(
            f"Renderer emitted URL(s) not in primary_sources_to_cite: {sorted(extraneous)}"
        )

    if state == "verified":
        if not (urls_in_reply & allowed_urls):
            raise ValueError("state=verified renderer emitted no URL from primary_sources_to_cite.")
    else:
        if urls_in_reply:
            raise ValueError(
                f"state={state} renderer emitted URL(s) (none should be present): {sorted(urls_in_reply)}"
            )

    if x_weighted_length(text) > _X_TWEET_LIMIT:
        raise ValueError(
            f"Rendered reply is {x_weighted_length(text)} X-weighted chars (limit {_X_TWEET_LIMIT})."
        )


def render(view: RendererView, tone: Tone, *, max_invariance_retries: int = 2) -> str:
    """Render a reply. Always uses the model — no hardcoded templates.

    Retry strategy:
    1. Up to `max_invariance_retries` attempts at the tone's normal prompt
       with feedback if the invariance check fails (e.g. missing URL,
       overflow, extraneous URL).
    2. If `call_claude_json` itself raises (parse error, schema validation,
       likely refusal in the JSON), one more attempt with the refusal nudge
       appended to the system prompt — same tone, softer framing emphasizing
       the harm-reduction purpose.
    3. If that also fails, raise — the caller writes a `pipeline_error`
       event so we have visibility into refusal patterns instead of silently
       posting nothing.
    """
    if tone not in _TONE_SYSTEMS:
        raise ValueError(f"Unknown tone {tone!r}")

    state = _state_for(view)
    base_prompt = _build_prompt(view, state)
    last_error: Exception | None = None

    # Pass 1 — normal prompt, invariance-feedback retries
    for attempt in range(max_invariance_retries + 1):
        prompt = base_prompt
        if last_error is not None and isinstance(last_error, ValueError):
            prompt += (
                f"\n\nYour previous attempt failed this hard constraint: {last_error}. "
                "Fix it and try again. Stay within all the other constraints."
            )
        try:
            reply = call_claude_json(
                prompt=prompt,
                schema=RenderedReply,
                system=_TONE_SYSTEMS[tone],
                reasoning_effort=None,
                max_tokens=512,
                timeout=30.0,
            )
        except ValueError as exc:
            # Most likely a refusal that broke JSON parsing — break to pass 2.
            logger.warning("render[%s]: pass-1 call_claude_json failed (%s) — escalating to refusal nudge", tone, exc)
            last_error = exc
            break
        text = reply.text.strip()
        try:
            _enforce_invariance(text, view, state)
            return text
        except ValueError as exc:
            last_error = exc
            logger.info("render[%s]: invariance retry %d/%d (%s)", tone, attempt + 1, max_invariance_retries, exc)

    # Pass 2 — refusal nudge once
    try:
        reply = call_claude_json(
            prompt=base_prompt + "\n\n" + _REFUSAL_NUDGE,
            schema=RenderedReply,
            system=_TONE_SYSTEMS[tone],
            reasoning_effort=None,
            max_tokens=512,
            timeout=30.0,
        )
        text = reply.text.strip()
        _enforce_invariance(text, view, state)
        logger.info("render[%s]: succeeded after refusal nudge", tone)
        return text
    except Exception as exc:
        logger.warning("render[%s]: refusal-nudge pass also failed (%s)", tone, exc)
        # Re-raise the most informative error.
        if last_error is not None:
            raise last_error
        raise
