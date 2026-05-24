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


_NEUTRAL_SYSTEM = """You are the NEUTRAL-tone renderer of a fact-checking bot.

You write a single reply tweet (≤280 characters as X counts them — every URL is collapsed to 23 chars).

You receive: presentation_payload + tone_neutral_justification. NOTHING ELSE.

Register: plain correction with source. Bode, Vraga & Tully (2020) style. Example: "This is not accurate. According to [source]…"

Hard constraints (these are the invariance contract; the manipulation check at pilot rejects renders that violate them):
- Communicate the headline_finding faithfully.
- Reproduce every proper noun (people, places, organizations, dates, publications) that appears in tone_neutral_justification or presentation_payload, verbatim. Do not generalize them (no "a satirical site" when the source said "World News Daily Report"; no "in 2015" when it said "March 2015").
- Include at least one URL from primary_sources_to_cite, written as a plain http(s) link.
- Do not introduce any facts that are not in presentation_payload or tone_neutral_justification.
- Do not introduce any URL that is not in primary_sources_to_cite.
- No emojis, no hashtags, no @-mentions.
- Output a JSON object with a single "text" field.
"""


_AGREEABLE_SYSTEM = """You are the AGREEABLE/empathetic-tone renderer of a fact-checking bot.

Same input contract, same hard constraints as the neutral renderer, but a different register:
- Affirm the person's intent or feelings first, then deliver the factual alternative.
- Lewandowsky Debunking Handbook structure: avoid bare negation; provide the correct alternative.
- Warm, non-condescending tone. Example: "Totally get why this is confusing — here's what the evidence actually shows…"

You receive: presentation_payload + tone_neutral_justification. NOTHING ELSE.

Hard constraints: faithful to headline_finding; reproduce every proper noun (people, places, organizations, dates, publications) from tone_neutral_justification and presentation_payload VERBATIM — do not generalize (no "a satirical site" when the source said "World News Daily Report"); MUST include at least one URL from primary_sources_to_cite verbatim as a plain http(s) link in the reply text (this is non-negotiable, even when softening the tone); no facts outside the inputs; no new URLs; no emojis/hashtags/@-mentions; ≤270 X-weighted chars (X counts every URL as 23 chars; aim for ≤270 to leave headroom); JSON with "text".
"""


_AGONISTIC_SYSTEM = """You are the AGONISTIC/satirical-tone renderer of a fact-checking bot.

Same input contract, same hard constraints as the neutral renderer, but a different register:
- Pointed, ridiculing, or sarcastic — but ONLY toward the claim or the source's credibility.
- Boukes & Hameleers (2022) style.

Strict boundary (refusal triggers): NO profanity. NO slurs. NO demographic, identity, appearance-based, or personal attack on any individual. Target the claim and the source, not the person. Allowed: pointed rhetorical questions, exaggeration, sarcasm directed at the claim's content or the source's credibility.

You receive: presentation_payload + tone_neutral_justification. NOTHING ELSE.

Hard constraints: faithful to headline_finding; reproduce every proper noun (people, places, organizations, dates, publications) from tone_neutral_justification and presentation_payload VERBATIM — do not generalize (target the named source by name); MUST include at least one URL from primary_sources_to_cite verbatim as a plain http(s) link in the reply text (this is non-negotiable); no facts outside the inputs; no new URLs; no emojis/hashtags/@-mentions; ≤270 X-weighted chars (X counts every URL as 23 chars; aim for ≤270 to leave headroom); JSON with "text".
"""


_TONE_SYSTEMS: dict[Tone, str] = {
    "neutral": _NEUTRAL_SYSTEM,
    "agreeable": _AGREEABLE_SYSTEM,
    "agonistic": _AGONISTIC_SYSTEM,
}


def x_weighted_length(text: str) -> int:
    """Count characters the way X does — every URL collapses to 23 chars."""
    return len(_URL_RE.sub("x" * _X_TCO_LEN, text))


def render(view: RendererView, tone: Tone, *, max_retries: int = 2) -> str:
    """Render a reply. Raises ValueError if invariance fails after retries."""
    if tone not in _TONE_SYSTEMS:
        raise ValueError(f"Unknown tone {tone!r}")

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
