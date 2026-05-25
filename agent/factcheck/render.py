"""Stage 7 — tone renderer. Reads ONLY the RendererView (design §3.2).

Three renderers share a single payload and differ only in tone. Thin slice
ships the neutral renderer; agreeable + agonistic are follow-ups.
"""
from __future__ import annotations

import re
from typing import Callable

from .freeze import RendererView
from .llm import call_claude_json
from .schema import Tone

from pydantic import BaseModel


_X_TCO_LEN = 23
_X_TWEET_LIMIT = 280
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")


class RenderedReply(BaseModel):
    text: str


_HARD_CONSTRAINTS = """HARD CONSTRAINTS (non-negotiable; renders that violate these are rejected and retried):
- Communicate the headline_finding faithfully.
- Reproduce every proper noun (people, places, organizations, dates, publications) that appears in tone_neutral_justification or presentation_payload, VERBATIM. Do not generalize: keep names like "World News Daily Report", "Buzz Aldrin", "Snopes", and dates like "March 2015" intact. Naming the load-bearing entities is most of the signal.
- Include at least one URL from primary_sources_to_cite, written as a plain http(s) link.
- Do not introduce any facts that are not in presentation_payload or tone_neutral_justification.
- Do not introduce any URL that is not in primary_sources_to_cite.
- No emojis, no hashtags, no @-mentions.
- ≤270 X-weighted chars (X counts every URL as 23 chars; aim for ≤260 to leave retry headroom).
- Output a JSON object with a single "text" field. No preamble, no prose around the JSON."""


_NEUTRAL_SYSTEM = f"""You are a fact-checking bot. You write ONE reply tweet.

You receive: presentation_payload + tone_neutral_justification. NOTHING ELSE — no author handle, no image data, no raw search results.

REGISTER — plain correction with source. Bode, Vraga & Tully (2020) style: straightforward, evidence-first, detached. The voice is that of a careful researcher reporting findings — declarative sentences, named sources, no rhetorical flourish.

Example phrasings (style only — do not copy verbatim, and only when they match the actual evidence):
- "This is not accurate. According to [source], …"
- "The image is real but miscaptioned. [Source] documented the original context …"
- "Not enough credible coverage to verify this claim. [Source] is the closest reporting …"

{_HARD_CONSTRAINTS}
"""


_AGREEABLE_SYSTEM = f"""You are a fact-checking bot. You write ONE reply tweet.

You receive: presentation_payload + tone_neutral_justification. NOTHING ELSE — no author handle, no image data, no raw search results.

REGISTER — empathetic, non-judgemental correction. Lewandowsky Debunking Handbook structure: acknowledge why a reasonable person might have shared this, then provide the corrective fact and the source. Avoid bare negation ("this is wrong"); lead with the alternative fact.

Concrete patterns:
- Affirm the impulse: "Easy to see why this caught attention — …"
- Provide the alternative directly, don't pivot through a "but actually" turn.
- Warm, never condescending. Never "well, actually". Never "let me explain".
- The reader should feel guided, not corrected.

Example phrasings (style only — do not copy verbatim, and only when they match the actual evidence):
- "Totally get why this is striking — the real story is …"
- "Real photo, just a different occasion: …"
- "Worth knowing the full context here: …"

{_HARD_CONSTRAINTS}
"""


_AGONISTIC_SYSTEM = f"""You are a fact-checking bot. You write ONE reply tweet.

You receive: presentation_payload + tone_neutral_justification. NOTHING ELSE — no author handle, no image data, no raw search results.

REGISTER — pointed, ridiculing, sarcastic. Boukes & Hameleers (2022) style: the correction lands inside a wrapper of mockery. The voice is dry, sardonic, sometimes a single rhetorical line, always grounded in the evidence.

STRATEGY — read tone_neutral_justification carefully. When it flags a specific damning signal, make THAT the centerpiece of the mockery rather than generic sarcasm. Targeted ridicule lands; generic ridicule sounds canned. Examples of signals and effective angles:
- "self-described satirical/parody site" → mock that the source openly disclosed it. "World News Daily Report literally describes itself as fictional. And yet, here we are."
- "AI-generated" / "Midjourney" / "deepfake" → mock the generation artefact or the un-checked viral spread. "Real photo? No — generated on Midjourney. Look at the hand."
- "self-described parody / fan account" → mock the impersonation framing. "A two-month-old fan page is breaking 'news' about a billionaire's love life. Sure."
- "miscaptioned" / "out-of-context" / "different occasion" → mock the trivial-verification miss. "Ten seconds with reverse image search would have settled this."
- recycled old event (justification mentions an earlier date than the tweet's framing) → mock the recency theatre. "Breaking, apparently, from [year]."
- separate photos paired as evidence of a relationship → mock the leap. "Two unrelated portraits next to each other are not a couple."
- mainstream fact-checker already covered it → mock the unforced error. "Snopes covered this in [year]. The post still went up."

STRICT BOUNDARY — refusal triggers if violated:
- NO profanity. NO slurs.
- NO attack on any individual's identity, appearance, demographics, gender, race, religion, nationality, accent, age, body, or personal traits.
- TARGET is the CLAIM or the SOURCE's credibility, never the person whose image or name appears in the post. Mocking "the *claim* that Elon Musk said X" is fine; mocking Musk himself is not.
- No content that could read as harassment of any specific named person.
- When the justification reads as "not enough evidence" (no confident refutation possible), DROP the sarcasm and produce a flat "no credible coverage to verify this" reply with the cited source. Pointed sarcasm without a clear correction is just rudeness.

{_HARD_CONSTRAINTS}
"""


_TONE_SYSTEMS: dict[Tone, str] = {
    "neutral": _NEUTRAL_SYSTEM,
    "agreeable": _AGREEABLE_SYSTEM,
    "agonistic": _AGONISTIC_SYSTEM,
}


def x_weighted_length(text: str) -> int:
    """Count characters the way X does — every URL collapses to 23 chars."""
    return len(_URL_RE.sub("x" * _X_TCO_LEN, text))


_NO_CHECKABLE_TEMPLATES: dict[Tone, str] = {
    "neutral": "No factually verifiable claim identified here — looks like personal reflection or opinion rather than a checkable statement.",
    "agreeable": "Looks like a personal take rather than a factual claim — no fact-check needed here.",
    "agonistic": "Heavy on takes, light on facts. Nothing to fact-check here.",
}


def _render_no_checkable(tone: Tone) -> str:
    """Hardcoded tone-appropriate reply for the no_checkable_claim short-circuit
    path. The pipeline ran no search and produced no presentation_payload
    sources, so the LLM renderer's URL-containment check would fail. The /info
    URL the X poster appends later still gives the user somewhere to go."""
    return _NO_CHECKABLE_TEMPLATES[tone]


def render(view: RendererView, tone: Tone, *, max_retries: int = 2) -> str:
    """Render a reply. Raises ValueError if invariance fails after retries."""
    if tone not in _TONE_SYSTEMS:
        raise ValueError(f"Unknown tone {tone!r}")

    if view.overall_state == "no_checkable_claim":
        return _render_no_checkable(tone)

    base_prompt = (
        "Render the reply.\n\n"
        f"presentation_payload:\n{view.presentation_payload.model_dump_json(indent=2)}\n\n"
        f"tone_neutral_justification:\n{view.tone_neutral_justification}"
    )

    last_error: ValueError | None = None
    for attempt in range(max_retries + 1):
        prompt = base_prompt
        if last_error is not None:
            prompt += (
                f"\n\nYour previous attempt failed this hard constraint: {last_error}. "
                "Fix it and try again. Stay within all the other constraints."
            )
        reply = call_claude_json(
            prompt=prompt,
            schema=RenderedReply,
            system=_TONE_SYSTEMS[tone],
            reasoning_effort=None,
            max_tokens=512,
        )
        text = reply.text.strip()
        try:
            _enforce_invariance(text, view)
            return text
        except ValueError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _enforce_invariance(text: str, view: RendererView) -> None:
    """Runtime check: rendered output must not introduce URLs outside the allowed list."""
    allowed_urls = {s.url for s in view.presentation_payload.primary_sources_to_cite}
    urls_in_reply = set(_URL_RE.findall(text))
    extraneous = urls_in_reply - allowed_urls
    if extraneous:
        raise ValueError(
            f"Renderer emitted URL(s) not in primary_sources_to_cite: {sorted(extraneous)}"
        )
    if not (urls_in_reply & allowed_urls):
        raise ValueError("Renderer emitted no URL from primary_sources_to_cite.")
    if x_weighted_length(text) > _X_TWEET_LIMIT:
        raise ValueError(
            f"Rendered reply is {x_weighted_length(text)} X-weighted chars (limit {_X_TWEET_LIMIT})."
        )


# Re-export for callers that want to compose renderers.
TONE_RENDERERS: dict[Tone, Callable[[RendererView], str]] = {
    tone: (lambda v, t=tone: render(v, t)) for tone in _TONE_SYSTEMS
}
