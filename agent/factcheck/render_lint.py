"""R-4 / R-5 mechanical render lints (v0.7).

R-4 (substance): every numeral in a rendered reply must exist in the frozen
payload/justification; internal pipeline vocabulary must never reach the user.
R-5 (cross-tone): every load-bearing fact must appear in EVERY tone variant.
Pure string functions — no LLM, no imports from search/loop (renderer-safe)."""
from __future__ import annotations

import json
import re

from .schema import PresentationPayload

_NUMERAL_RE = re.compile(r"[$€£]?\d[\d,]*(?:\.\d+)?%?")
_PIPELINE_LEAK_MARKERS = (
    "failed to load", "fetch_page", "evidence row", "tool call",
    "pipeline", "finalize", "cutoff",
)


def _normalize(tok: str) -> str:
    return tok.replace(",", "")


def extract_numerals(text: str) -> set[str]:
    return {_normalize(m.group(0)) for m in _NUMERAL_RE.finditer(text)}


def lint_substance(text: str, payload: PresentationPayload, justification: str) -> list[str]:
    allowed = extract_numerals(payload.model_dump_json() + " " + justification)
    violations: list[str] = []
    for tok in sorted(extract_numerals(text)):
        if tok not in allowed:
            violations.append(f"numeral {tok!r} not present in frozen payload/justification")
    low = text.lower()
    for marker in _PIPELINE_LEAK_MARKERS:
        if marker in low:
            violations.append(f"internal pipeline vocabulary leaked: {marker!r}")
    return violations


def _fact_in(fact: str, text: str) -> bool:
    if fact.casefold() in text.casefold():
        return True
    fact_nums = extract_numerals(fact)
    if fact_nums and fact_nums <= extract_numerals(text):
        return True
    return False


def lint_cross_tone(texts: dict[str, str], load_bearing_facts) -> list[str]:
    violations: list[str] = []
    for tone, text in texts.items():
        for fact in load_bearing_facts:
            if not _fact_in(fact, text):
                violations.append(f"{tone} missing fact {fact!r}")
    return violations
