"""Compose a reply from a list of evidence notes.

Hands the filtered, recency-sorted notes to the response-output LLM
prompt and returns a two-sentence reply plus structured reasons grounded
in the supplied note_ids.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any, Dict, List, Optional, Sequence

from derad_agent.llm.config import get_llm
from derad_agent.llm.prompts import get_style_prompt, get_no_factcheck_prompt

from ._helpers import extract_text_from_response, parse_json_response

logger = logging.getLogger(__name__)

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
                "created_at_millis": note.get("created_at_millis") or 0,
                "evidence_links": _extract_urls(summary),
            }
        )
    # Most-recent first so the LLM weights new notes more heavily for accuracy.
    payload.sort(key=lambda n: n["created_at_millis"], reverse=True)
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
        reasoning_effort="medium",
        text_verbosity="medium",
    )
    chain = prompt | llm

    invoke_vars: Dict[str, str] = {
        "statement": statement,
        "evidence_notes_json": json.dumps(candidates, ensure_ascii=False),
        "current_date": datetime.now(timezone.utc).strftime("%B %d, %Y"),
    }

    try:
        formatted = prompt.format_messages(**invoke_vars)
        logger.info(
            "compose_reply request — style=%s notes=%d\n%s",
            style, len(candidates),
            "\n---\n".join(f"[{m.type}] {m.content}" for m in formatted),
        )
    except Exception:
        pass

    raw = chain.invoke(invoke_vars)
    text = extract_text_from_response(raw)
    try:
        parsed = parse_json_response(text)
    except Exception as _parse_exc:
        logger.warning("JSON parse failed, attempting repair: %s", _parse_exc)
        repair_prompt = (
            "Convert the following text into valid JSON only, preserving the same schema keys. "
            "Return JSON and nothing else.\n\n"
            f"{text}"
        )
        repair_llm = get_llm(
            temperature=None,
            max_tokens=1400,
            reasoning_effort="medium",
            text_verbosity="low",
        )
        repair_raw = repair_llm.invoke(repair_prompt)
        parsed = parse_json_response(extract_text_from_response(repair_raw))

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


def step_compose_no_factcheck_reply(
    statement: str,
    style: str = "neutral",
    *,
    reason: str = "no_claim",
) -> Dict[str, Any]:
    """Generate a tone-appropriate reply when no grounded reply is available.

    ``reason`` controls the template family used:
      - ``"no_claim"``: planner gate fired — tweet has no factcheckable claim.
      - ``"no_notes"``: search ran but no relevant Community Notes were found.
    """
    prompt = get_no_factcheck_prompt(style, reason=reason)
    llm = get_llm(
        temperature=None,
        max_tokens=200,
        reasoning_effort="medium",
        text_verbosity="low",
    )
    chain = prompt | llm

    try:
        try:
            formatted = prompt.format_messages(statement=statement)
            logger.info(
                "compose_no_factcheck_reply request — style=%s reason=%s\n%s",
                style, reason,
                "\n---\n".join(f"[{m.type}] {m.content}" for m in formatted),
            )
        except Exception:
            pass
        raw = chain.invoke({"statement": statement})
        text = extract_text_from_response(raw)
        parsed = parse_json_response(text)
        response_text = str(parsed.get("response") or "").strip()
    except Exception as exc:
        logger.warning("No-factcheck reply generation failed (reason=%s): %s", reason, exc)
        response_text = ""

    if not response_text:
        response_text = "No Community Notes corrections found for this post."

    return {
        "statement": statement,
        "response": response_text,
        "reasons": [],
    }


__all__ = ["step_compose_reply", "step_compose_no_factcheck_reply"]
