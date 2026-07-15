"""Mechanical render lint — R-4 substance check (renderer-safe, no LLM).

R-4 (substance): every numeral in a rendered reply must exist in the frozen
payload/justification, and internal pipeline vocabulary must never reach the
user. R-5 (cross-tone fact preservation) lives inline in
render.render_all_tones — it requires a majority of the headline's
verdict-critical numerals to survive each register, not every load-bearing
fact. Pure string functions — no imports from search/loop."""
from __future__ import annotations

import json
import re

from .schema import PresentationPayload

_NUMERAL_RE = re.compile(r"[$€£]?\d[\d,]*(?:\.\d+)?%?")
# Internal-only vocabulary that must never reach user-facing text. Keep each
# marker specific to pipeline internals: generic words ("pipeline", "cutoff",
# "finalize") collide with natural energy/finance prose, and a false positive
# here trips neutral-fallback — a research-validity risk, not just noise.
_PIPELINE_LEAK_MARKERS = (
    "failed to load", "fetch_page", "evidence row", "tool call",
    "tool_use", "evidence log",
)


def _normalize(tok: str) -> str:
    return tok.replace(",", "")


def _strip_decoration(tok: str) -> str:
    """Drop leading currency symbols and trailing %, leaving the bare number.

    Comparisons are decoration-stripped on BOTH sides so a rendered "44
    percent" or bare "2.81" still matches the payload's "44%" / "$2.81".
    `extract_numerals` output keeps decorations — only comparisons strip."""
    return tok.lstrip("$€£").rstrip("%")


def extract_numerals(text: str) -> set[str]:
    return {_normalize(m.group(0)) for m in _NUMERAL_RE.finditer(text)}


def lint_substance(text: str, payload: PresentationPayload, justification: str) -> list[str]:
    allowed = {
        _strip_decoration(tok)
        for tok in extract_numerals(payload.model_dump_json() + " " + justification)
    }
    violations: list[str] = []
    for tok in sorted(extract_numerals(text)):
        if _strip_decoration(tok) not in allowed:
            violations.append(f"numeral {tok!r} not present in frozen payload/justification")
    low = text.lower()
    for marker in _PIPELINE_LEAK_MARKERS:
        if marker in low:
            violations.append(f"internal pipeline vocabulary leaked: {marker!r}")
    return violations


# NOTE: cross-tone fact preservation (R-5) is enforced inline in
# render.render_all_tones, anchored to the headline_finding's verdict-critical
# numerals (a majority must survive each register) rather than to every
# load_bearing_fact. The earlier generic `lint_cross_tone`/`_fact_in` helpers
# were removed when that inline check replaced them.
