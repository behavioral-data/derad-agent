"""Dataset-native misleadingness scoring and 1D landscape aggregation."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from html import unescape
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

_URL_RE = re.compile(r"https?://[^\s<>\"]+")


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_similarity(distance: Optional[float]) -> Optional[float]:
    if distance is None or distance < 0:
        return None
    return 1.0 / (1.0 + distance)


def score_misleadingness_axis(meta: Mapping[str, Any]) -> float:
    """Score misleadingness in [-1, 1]. -1=misleading, +1=not-misleading."""
    classification = str(meta.get("classification") or "").strip().upper()
    flags = dict(meta.get("label_flags") or {})

    score = 0.0
    if "NOT_MISLEADING" in classification:
        score += 0.8
    elif "MISINFORMED" in classification or "MISLEADING" in classification:
        score -= 0.8

    misleading_count = sum(1 for k, v in flags.items() if k.startswith("misleading") and str(v) == "1")
    not_misleading_count = sum(1 for k, v in flags.items() if k.startswith("notMisleading") and str(v) == "1")
    score += min(0.4, 0.12 * not_misleading_count)
    score -= min(0.4, 0.12 * misleading_count)
    return _clamp(score)


def _quantiles(values: Sequence[float], ps: Iterable[float]) -> Dict[str, Optional[float]]:
    seq = sorted(float(v) for v in values if v is not None)
    if not seq:
        return {f"p{int(p * 100)}": None for p in ps}

    out: Dict[str, Optional[float]] = {}
    for p in ps:
        if p <= 0:
            q = seq[0]
        elif p >= 1:
            q = seq[-1]
        else:
            pos = p * (len(seq) - 1)
            lo = int(math.floor(pos))
            hi = int(math.ceil(pos))
            q = seq[lo] if lo == hi else seq[lo] + (seq[hi] - seq[lo]) * (pos - lo)
        out[f"p{int(p * 100)}"] = round(q, 4)
    return out


def _extract_urls(text: str, max_urls: int = 8) -> List[str]:
    if not text:
        return []
    raw_urls = _URL_RE.findall(unescape(text))
    out: List[str] = []
    seen: set[str] = set()
    for url in raw_urls:
        cleaned = url.rstrip(".,);:!?]")
        if cleaned and cleaned not in seen:
            out.append(cleaned)
            seen.add(cleaned)
            if len(out) >= max_urls:
                break
    return out


def build_misleadingness_landscape(
    statement: str,
    documents: Sequence[Any],
    *,
    similarity_min: float = 0.0,
    max_points: Optional[int] = None,
) -> Dict[str, Any]:
    """Build note-level 1D misleadingness landscape."""
    points: List[Dict[str, Any]] = []
    for doc in documents:
        meta = dict(getattr(doc, "metadata", {}) or {})
        note_text = (getattr(doc, "page_content", "") or "").strip()
        note_id = meta.get("note_id")
        tweet_id = meta.get("tweet_id")
        if not note_id or not tweet_id:
            continue

        similarity = _safe_float(meta.get("retrieval_similarity"))
        if similarity is None:
            similarity = _normalized_similarity(_safe_float(meta.get("retrieval_distance")))
        similarity = float(similarity) if similarity is not None else 0.0
        if similarity < similarity_min:
            continue

        flags = dict(meta.get("label_flags") or {})
        points.append(
            {
                "note_id": str(note_id),
                "tweet_id": str(tweet_id),
                "misleadingness_axis": round(score_misleadingness_axis(meta), 4),
                "similarity": round(similarity, 4),
                "classification": str(meta.get("classification") or ""),
                "misleading_flags": sum(1 for k, v in flags.items() if k.startswith("misleading") and str(v) == "1"),
                "not_misleading_flags": sum(
                    1 for k, v in flags.items() if k.startswith("notMisleading") and str(v) == "1"
                ),
                "summary_preview": note_text[:280],
                "evidence_links": _extract_urls(note_text),
            }
        )

    points.sort(key=lambda p: (p["similarity"], abs(p["misleadingness_axis"])), reverse=True)
    if max_points and max_points > 0:
        points = points[:max_points]

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for point in points:
        grouped[point["tweet_id"]].append(point)

    tweet_clusters: List[Dict[str, Any]] = []
    for tweet_id, members in grouped.items():
        tweet_clusters.append(
            {
                "tweet_id": tweet_id,
                "note_count": len(members),
                "centroid_misleadingness": round(mean(m["misleadingness_axis"] for m in members), 4),
                "avg_similarity": round(mean(m["similarity"] for m in members), 4),
            }
        )
    tweet_clusters.sort(key=lambda c: (c["note_count"], c["avg_similarity"]), reverse=True)

    return {
        "statement": statement,
        "thresholds": {"similarity_min": similarity_min},
        "points": points,
        "tweet_clusters": tweet_clusters,
        "ranges": {
            "misleadingness_axis_quantiles": _quantiles(
                [p["misleadingness_axis"] for p in points], (0.1, 0.25, 0.5, 0.75, 0.9)
            ),
            "similarity_quantiles": _quantiles([p["similarity"] for p in points], (0.1, 0.25, 0.5, 0.75, 0.9)),
        },
    }


def build_bucket_landscape(statement: str, documents: List[Any]) -> Dict[str, Any]:
    """Build tweet-level misleadingness buckets from retrieved notes."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for doc in documents:
        meta = dict(getattr(doc, "metadata", {}) or {})
        tweet_id = meta.get("tweet_id")
        if not tweet_id:
            continue
        grouped[tweet_id].append(
            {
                "summary": (getattr(doc, "page_content", "") or "").strip(),
                "classification": meta.get("classification"),
                "label_flags": dict(meta.get("label_flags") or {}),
                "note_id": meta.get("note_id"),
            }
        )

    clusters: List[Dict[str, Any]] = []
    bucket_counts = {
        "StronglyMisleading": 0,
        "Misleading": 0,
        "MixedUnclear": 0,
        "NotMisleading": 0,
        "StronglyNotMisleading": 0,
    }

    for tweet_id, notes in grouped.items():
        scores = [
            score_misleadingness_axis({"classification": n.get("classification"), "label_flags": n.get("label_flags")})
            for n in notes
        ]
        avg_misleadingness = mean(scores) if scores else 0.0
        if avg_misleadingness >= 0.6:
            bucket = "StronglyNotMisleading"
        elif avg_misleadingness >= 0.2:
            bucket = "NotMisleading"
        elif avg_misleadingness <= -0.6:
            bucket = "StronglyMisleading"
        elif avg_misleadingness <= -0.2:
            bucket = "Misleading"
        else:
            bucket = "MixedUnclear"

        bucket_counts[bucket] += 1
        clusters.append(
            {
                "tweet_id": tweet_id,
                "avg_misleadingness": round(avg_misleadingness, 3),
                "bucket": bucket,
                "representative_notes": [{"note_id": n.get("note_id"), "summary": n.get("summary", "")[:280]} for n in notes[:3]],
            }
        )

    clusters.sort(key=lambda c: (c["bucket"], -abs(c["avg_misleadingness"])))
    return {
        "statement": statement,
        "tweet_clusters": clusters,
        "buckets": bucket_counts,
        "method_notes": "Direct dataset labels only: classification and misleading/notMisleading flags.",
    }

