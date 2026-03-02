"""Step 4: Build statement-level landscape output from retrieved note space."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from html import unescape
from typing import Any, Dict, List, Optional, Sequence, Tuple

from derad_agent.llm.config import get_llm
from derad_agent.llm.prompts import get_landscape_output_prompt

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
        "bucket": _bucket_from_axis(_safe_float(point.get("misleadingness_axis"), 0.0)),
        "note_id": point.get("note_id"),
        "tweet_id": point.get("tweet_id"),
        "classification": point.get("classification"),
        "similarity": _safe_float(point.get("similarity"), 0.0),
        "misleadingness_axis": _safe_float(point.get("misleadingness_axis"), 0.0),
        "evidence_links": links,
    }


def _axis_band(axis: float) -> str:
    if axis <= -0.6:
        return "strong_misleading"
    if axis <= -0.2:
        return "mild_misleading"
    if axis < 0.2:
        return "mixed_unclear"
    if axis < 0.6:
        return "mild_not_misleading"
    return "strong_not_misleading"


def _build_key_reasons(points: Sequence[Dict[str, Any]], max_reasons: int = 5) -> List[Dict[str, Any]]:
    """
    Select representative notes across the landscape.
    Strategy:
    - dedupe by summary text
    - bucket into axis bands spanning misleading -> mixed -> not-misleading
    - rank within each band by priority
    - round-robin across bands to preserve landscape diversity
    """
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_previews: set[str] = set()
    for point in points:
        rec = _candidate_reason_record(point)
        if not rec:
            continue
        dedupe_key = str(rec["reason"]).lower()
        if dedupe_key in seen_previews:
            continue
        seen_previews.add(dedupe_key)
        band = _axis_band(_safe_float(rec.get("misleadingness_axis"), 0.0))
        grouped[band].append(rec)

    bands = [
        "strong_misleading",
        "mild_misleading",
        "mixed_unclear",
        "mild_not_misleading",
        "strong_not_misleading",
    ]
    for band in bands:
        grouped[band].sort(key=_reason_priority, reverse=True)

    reasons: List[Dict[str, Any]] = []
    while len(reasons) < max_reasons:
        added_this_round = False
        for band in bands:
            if not grouped[band]:
                continue
            reasons.append(grouped[band].pop(0))
            added_this_round = True
            if len(reasons) >= max_reasons:
                break
        if not added_this_round:
            break
    return reasons


def _build_landscape_stats(
    statement: str,
    misleadingness_landscape: Dict[str, Any],
    bucket_landscape: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    points = list(misleadingness_landscape.get("points") or [])
    clusters = list(misleadingness_landscape.get("tweet_clusters") or [])
    ranges = dict(misleadingness_landscape.get("ranges") or {})
    axis_quantiles = dict(ranges.get("misleadingness_axis_quantiles") or {})
    sim_quantiles = dict(ranges.get("similarity_quantiles") or {})
    thresholds = dict(misleadingness_landscape.get("thresholds") or {})

    total = len(points)
    misleading = sum(1 for p in points if _bucket_from_axis(_safe_float(p.get("misleadingness_axis"), 0.0)) == "misleading")
    not_misleading = sum(
        1 for p in points if _bucket_from_axis(_safe_float(p.get("misleadingness_axis"), 0.0)) == "not_misleading"
    )
    mixed = total - misleading - not_misleading
    dominant_bucket = "mixed_unclear"
    dominant_count = mixed
    if misleading >= not_misleading and misleading >= mixed:
        dominant_bucket = "misleading"
        dominant_count = misleading
    elif not_misleading >= misleading and not_misleading >= mixed:
        dominant_bucket = "not_misleading"
        dominant_count = not_misleading

    classification_counts: Dict[str, int] = {}
    for p in points:
        label = str(p.get("classification") or "UNKNOWN")
        classification_counts[label] = classification_counts.get(label, 0) + 1
    top_classifications = sorted(
        classification_counts.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )[:5]
    avg_axis = round(
        sum(_safe_float(p.get("misleadingness_axis"), 0.0) for p in points) / total,
        4,
    ) if total else 0.0
    avg_similarity = round(
        sum(_safe_float(p.get("similarity"), 0.0) for p in points) / total,
        4,
    ) if total else 0.0

    multi_note_clusters = sum(1 for c in clusters if int(c.get("note_count") or 0) > 1)
    top_clusters = [
        {
            "tweet_id": c.get("tweet_id"),
            "note_count": int(c.get("note_count") or 0),
            "centroid_misleadingness": _safe_float(c.get("centroid_misleadingness"), 0.0),
            "avg_similarity": _safe_float(c.get("avg_similarity"), 0.0),
        }
        for c in clusters[:5]
    ]
    bucket_summary = dict((bucket_landscape or {}).get("buckets") or {})

    return {
        "statement": statement,
        "thresholds": thresholds,
        "note_count": total,
        "tweet_cluster_count": len(clusters),
        "aggregate": {
            "avg_misleadingness_axis": avg_axis,
            "avg_similarity": avg_similarity,
        },
        "distribution": {
            "misleading_count": misleading,
            "not_misleading_count": not_misleading,
            "mixed_unclear_count": mixed,
            "misleading_pct": round((misleading / total) * 100, 1) if total else 0.0,
            "not_misleading_pct": round((not_misleading / total) * 100, 1) if total else 0.0,
            "mixed_unclear_pct": round((mixed / total) * 100, 1) if total else 0.0,
            "dominant_bucket": dominant_bucket,
            "dominant_bucket_pct": round((dominant_count / total) * 100, 1) if total else 0.0,
        },
        "classification_frequency": [{"classification": k, "count": v} for k, v in top_classifications],
        "quantiles": {
            "misleadingness_axis": axis_quantiles,
            "similarity": sim_quantiles,
        },
        "cluster_stats": {
            "clusters_with_multiple_notes": multi_note_clusters,
            "top_clusters": top_clusters,
        },
        "bucket_summary": bucket_summary,
    }


def _llm_landscape_output(
    statement: str,
    stats: Dict[str, Any],
    candidate_reasons: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    prompt = get_landscape_output_prompt()
    llm = get_llm(
        temperature=None,
        max_tokens=1400,
        reasoning_effort="low",
        text_verbosity="medium",
    )
    chain = prompt | llm
    input_payload = {
        "statement": statement,
        "landscape_stats_json": json.dumps(stats, ensure_ascii=False),
        "top_points_json": json.dumps(candidate_reasons, ensure_ascii=False),
    }

    raw = chain.invoke(input_payload)
    text = extract_text_from_response(raw)
    try:
        parsed = parse_json_response(text)
    except Exception:
        # Strict repair retry: ask model to emit only valid JSON from its own prior output.
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
        repair_raw = repair_llm.invoke(repair_prompt)
        repair_text = extract_text_from_response(repair_raw)
        parsed = parse_json_response(repair_text)

    if not isinstance(parsed, dict):
        raise ValueError("Landscape output LLM returned non-object JSON.")
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
        bucket = str(reason.get("bucket") or canonical.get("bucket") or "mixed_unclear")
        if bucket not in {"misleading", "not_misleading", "mixed_unclear"}:
            bucket = str(canonical.get("bucket") or "mixed_unclear")

        out.append(
            {
                "reason": reason_text,
                "bucket": bucket,
                "note_id": canonical.get("note_id"),
                "tweet_id": canonical.get("tweet_id"),
                "classification": canonical.get("classification"),
                "similarity": canonical.get("similarity"),
                "misleadingness_axis": canonical.get("misleadingness_axis"),
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
    bucket_landscape: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    points = list(misleadingness_landscape.get("points") or [])
    stats = _build_landscape_stats(
        statement=statement,
        misleadingness_landscape=misleadingness_landscape,
        bucket_landscape=bucket_landscape,
    )
    candidate_reasons = _build_key_reasons(points, max_reasons=20)
    parsed = _llm_landscape_output(
        statement=statement,
        stats=stats,
        candidate_reasons=candidate_reasons,
    )
    parsed_summary = str(parsed.get("landscape_summary") or "").strip()
    if not parsed_summary:
        raise ValueError("Landscape output LLM returned empty landscape_summary.")

    key_reasons = _normalize_llm_reasons(parsed.get("key_reasons"), candidate_reasons, max_reasons=5)
    if points and not key_reasons:
        raise ValueError("Landscape output LLM returned no valid key_reasons for non-empty evidence.")

    return {
        "statement": statement,
        "landscape_summary": parsed_summary,
        "key_reasons": key_reasons,
    }
