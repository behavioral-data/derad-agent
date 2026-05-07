"""Step 4: Build statement-level landscape output from retrieved note space."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from html import unescape
from typing import Any, Dict, List, Optional, Sequence, Tuple

from derad_agent.llm.config import get_llm
from derad_agent.llm.prompts import get_response_output_prompt, get_style_prompt

from ._helpers import extract_text_from_response, parse_json_response


_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_TAG_RE = re.compile(r"</?[A-Z_]+>")


def _bucket_from_axis(axis: float) -> str:
    if axis <= -0.2:
        return "misleading"
    if axis >= 0.2:
        return "not_misleading"
    return "mixed_unclear"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _reason_priority(point: Dict[str, Any]) -> Tuple[float, float, float]:
    similarity = _safe_float(point.get("similarity"), 0.0)
    axis = abs(_safe_float(point.get("misleadingness_axis"), 0.0))
    # Favor high-similarity points with strong directional signal.
    return (similarity, axis, similarity * (0.5 + 0.5 * axis))


def _clean_reason_text(raw_text: str) -> str:
    text = unescape(raw_text or "")
    text = _TAG_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    return text


def _extract_urls(text: str, max_urls: int = 8) -> List[str]:
    if not text:
        return []
    normalized = unescape(text)
    raw_urls = _URL_RE.findall(normalized)
    urls: List[str] = []
    seen: set[str] = set()
    for url in raw_urls:
        cleaned = url.rstrip(".,);:!?]")
        if cleaned and cleaned not in seen:
            urls.append(cleaned)
            seen.add(cleaned)
            if len(urls) >= max_urls:
                break
    return urls


def _candidate_reason_record(point: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    preview = str(point.get("summary_preview") or "").strip()
    if not preview:
        return None
    clean_reason = _clean_reason_text(preview)
    if not clean_reason:
        return None
    links = _merge_links(point.get("evidence_links"), _extract_urls(preview))
    return {
        "reason": clean_reason,
        "note_id": point.get("note_id"),
        "tweet_id": point.get("tweet_id"),
        "similarity": _safe_float(point.get("similarity"), 0.0),
        "misleadingness_axis": _safe_float(point.get("misleadingness_axis"), 0.0),
        "evidence_links": links,
    }


def _build_key_reasons(points: Sequence[Dict[str, Any]], max_reasons: int = 5) -> List[Dict[str, Any]]:
    """Select representative notes via proportional sampling.

    Buckets notes into misleading / not_misleading / mixed_unclear,
    allocates slots proportionally to each bucket's share of the total,
    then picks the highest-priority candidates within each bucket.
    """
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_previews: set[str] = set()
    for point in points:
        rec = _candidate_reason_record(point)
        if not rec:
            continue
        dedupe_key = str(rec["reason"]).lower()
        if dedupe_key in seen_previews:
            continue
        seen_previews.add(dedupe_key)
        bucket = _bucket_from_axis(_safe_float(rec.get("misleadingness_axis"), 0.0))
        buckets[bucket].append(rec)

    for bucket in buckets:
        buckets[bucket].sort(key=_reason_priority, reverse=True)

    total = sum(len(v) for v in buckets.values())
    if total == 0:
        return []

    bucket_names = ["misleading", "not_misleading", "mixed_unclear"]
    allocations: Dict[str, int] = {}
    allocated = 0
    for name in bucket_names:
        count = len(buckets.get(name, []))
        slots = int(round(count / total * max_reasons))
        slots = min(slots, count)
        allocations[name] = slots
        allocated += slots

    # Distribute remainder (or trim excess) to the largest bucket.
    largest = max(bucket_names, key=lambda n: len(buckets.get(n, [])))
    diff = max_reasons - allocated
    allocations[largest] = max(0, min(allocations[largest] + diff, len(buckets.get(largest, []))))

    reasons: List[Dict[str, Any]] = []
    for name in bucket_names:
        reasons.extend(buckets.get(name, [])[:allocations[name]])

    # Final sort: highest priority first across all selected reasons.
    reasons.sort(key=_reason_priority, reverse=True)
    return reasons[:max_reasons]


def _llm_response_output(
    statement: str,
    candidate_reasons: Sequence[Dict[str, Any]],
    style: Optional[str] = None,
) -> Dict[str, Any]:
    prompt = get_style_prompt(style) if style else get_response_output_prompt()
    llm = get_llm(
        temperature=None,
        max_tokens=1400,
        reasoning_effort="low",
        text_verbosity="medium",
    )
    chain = prompt | llm
    llm_notes = [
        {
            "note": rec.get("reason", ""),
            "note_id": rec.get("note_id"),
            "tweet_id": rec.get("tweet_id"),
            "evidence_links": rec.get("evidence_links", []),
        }
        for rec in candidate_reasons
    ]
    input_payload = {
        "statement": statement,
        "evidence_notes_json": json.dumps(llm_notes, ensure_ascii=False),
    }

    raw = chain.invoke(input_payload)
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
        raise ValueError("Response output LLM returned non-object JSON.")
    return parsed


def _normalize_llm_reasons(
    llm_reasons: Any,
    candidate_reasons: Sequence[Dict[str, Any]],
    max_reasons: int = 5,
) -> List[Dict[str, Any]]:
    candidate_by_note = {str(r.get("note_id")): r for r in candidate_reasons if r.get("note_id") is not None}
    out: List[Dict[str, Any]] = []
    if not isinstance(llm_reasons, list):
        return out

    for reason in llm_reasons:
        if not isinstance(reason, dict):
            continue
        note_id = reason.get("note_id")
        if note_id is None:
            continue
        canonical = candidate_by_note.get(str(note_id))
        if canonical is None:
            continue
        reason_text = _clean_reason_text(str(reason.get("reason") or canonical.get("reason") or ""))
        if not reason_text:
            continue

        out.append(
            {
                "reason": reason_text,
                "note_id": canonical.get("note_id"),
                "tweet_id": canonical.get("tweet_id"),
                "evidence_links": _merge_links(reason.get("evidence_links"), canonical.get("evidence_links")),
            }
        )
        if len(out) >= max_reasons:
            break
    return out


def _merge_links(preferred_links: Any, fallback_links: Any, max_urls: int = 8) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for source in (preferred_links, fallback_links):
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


def step_4_build_landscape_output(
    statement: str,
    misleadingness_landscape: Dict[str, Any],
    style: Optional[str] = None,
) -> Dict[str, Any]:
    points = list(misleadingness_landscape.get("points") or [])
    candidate_reasons = _build_key_reasons(points, max_reasons=20)
    parsed = _llm_response_output(
        statement=statement,
        candidate_reasons=candidate_reasons,
        style=style,
    )
    response_text = str(parsed.get("response") or "").strip()
    if not response_text:
        raise ValueError("Response output LLM returned empty response.")

    reasons = _normalize_llm_reasons(parsed.get("reasons"), candidate_reasons, max_reasons=5)
    if points and not reasons:
        raise ValueError("Response output LLM returned no valid reasons for non-empty evidence.")

    return {
        "statement": statement,
        "response": response_text,
        "reasons": reasons,
    }
