"""Compose a reply from a list of evidence notes.

Hands the filtered, recency-sorted notes to the response-output LLM
prompt and returns a 3-5 sentence reply plus structured reasons grounded
in the supplied note_ids.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any, Dict, List, Optional, Sequence

from derad_agent.llm.config import get_llm
from derad_agent.llm.prompts import get_style_prompt

from ._helpers import extract_text_from_response, parse_json_response


_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_TAG_RE = re.compile(r"</?[A-Z_]+>")


def _extract_urls(text: str, max_urls: int = 8) -> List[str]:
    if not text:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for url in _URL_RE.findall(unescape(text)):
        cleaned = url.rstrip(".,);:!?]")
        if cleaned and cleaned not in seen:
            out.append(cleaned)
            seen.add(cleaned)
            if len(out) >= max_urls:
                break
    return out


def _clean_reason(raw: str, max_chars: int = 280) -> str:
    text = unescape(raw or "")
    text = _TAG_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def _merge_links(preferred: Any, fallback: Any, max_urls: int = 8) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for source in (preferred, fallback):
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, str):
                continue
            link = item.strip()
            if not link or link in seen:
                continue
            out.append(link)
            seen.add(link)
            if len(out) >= max_urls:
                return out
    return out


def _evidence_payload(notes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for note in notes:
        summary = str(note.get("summary") or "").strip()
        if not summary:
            continue
        payload.append(
            {
                "note": _clean_reason(summary, max_chars=400),
                "note_id": note.get("note_id"),
                "tweet_id": note.get("tweet_id"),
                "evidence_links": _extract_urls(summary),
            }
        )
    return payload


def _normalize_reasons(
    llm_reasons: Any,
    candidate_index: Dict[str, Dict[str, Any]],
    *,
    max_reasons: int = 5,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(llm_reasons, list):
        return out
    for reason in llm_reasons:
        if not isinstance(reason, dict):
            continue
        note_id = reason.get("note_id")
        if note_id is None:
            continue
        canonical = candidate_index.get(str(note_id))
        if canonical is None:
            continue
        text = _clean_reason(str(reason.get("reason") or "")) or str(canonical.get("note") or "")
        if not text:
            continue
        out.append(
            {
                "reason": text,
                "note_id": canonical.get("note_id"),
                "tweet_id": canonical.get("tweet_id"),
                "evidence_links": _merge_links(
                    reason.get("evidence_links"),
                    canonical.get("evidence_links"),
                ),
            }
        )
        if len(out) >= max_reasons:
            break
    return out


def step_compose_reply(
    statement: str,
    notes: Sequence[Dict[str, Any]],
    *,
    style: str = "neutral",
    max_reasons: int = 5,
) -> Dict[str, Any]:
    candidates = _evidence_payload(notes)
    candidate_index = {str(c["note_id"]): c for c in candidates if c.get("note_id") is not None}

    prompt = get_style_prompt(style)
    llm = get_llm(
        temperature=None,
        max_tokens=1400,
        reasoning_effort="low",
        text_verbosity="medium",
    )
    chain = prompt | llm

    raw = chain.invoke(
        {
            "statement": statement,
            "evidence_notes_json": json.dumps(candidates, ensure_ascii=False),
        }
    )
    text = extract_text_from_response(raw)
    try:
        parsed = parse_json_response(text)
    except Exception:
        repair_prompt = (
            "Convert the following text into valid JSON only, preserving the same schema keys. "
            "Return JSON and nothing else.\n\n"
            f"{text}"
        )
        repair_llm = get_llm(
            temperature=None,
            max_tokens=1400,
            reasoning_effort="low",
            text_verbosity="low",
        )
        try:
            repair_raw = repair_llm.invoke(repair_prompt)
            repair_text = extract_text_from_response(repair_raw)
            parsed = parse_json_response(repair_text)
        except Exception:
            parsed = parse_json_response(text)

    if not isinstance(parsed, dict):
        raise ValueError("Compose-reply LLM returned non-object JSON.")

    response_text = str(parsed.get("response") or "").strip()
    if not response_text:
        raise ValueError("Compose-reply LLM returned empty response.")

    reasons = _normalize_reasons(parsed.get("reasons"), candidate_index, max_reasons=max_reasons)
    if candidates and not reasons:
        raise ValueError("Compose-reply LLM returned no valid reasons for non-empty evidence.")

    return {
        "statement": statement,
        "response": response_text,
        "reasons": reasons,
    }


__all__ = ["step_compose_reply"]
