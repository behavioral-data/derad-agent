"""
Community Notes helpers shared across indexing and runtime.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from .validation import validate_timestamp


def normalize_note_id(value: Any) -> Optional[str]:
    """Return a stripped string ID, or ``None`` if *value* is empty/None."""
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def build_exclusion_set(
    exclude_tweet_id: Optional[str] = None,
    exclude_tweet_ids: Optional[List[str]] = None,
) -> Set[str]:
    """Merge single and list tweet-ID exclusions into a normalized set."""
    exclusions: Set[str] = set()
    if exclude_tweet_id:
        norm = normalize_note_id(exclude_tweet_id)
        if norm:
            exclusions.add(norm)
    if exclude_tweet_ids:
        for tid in exclude_tweet_ids:
            norm = normalize_note_id(tid)
            if norm:
                exclusions.add(norm)
    return exclusions


def passes_time_filter(metadata: Dict[str, Any], filter_before_utc: Optional[float]) -> bool:
    """Return ``True`` if the document was created before *filter_before_utc* (or if no filter is set)."""
    if filter_before_utc is None:
        return True
    created_utc = validate_timestamp(metadata.get("created_utc"))
    if created_utc is None:
        return False
    return created_utc < filter_before_utc


def passes_tweet_filter(metadata: Dict[str, Any], exclusions: Set[str]) -> bool:
    """Return ``True`` if the document's tweet ID is not in *exclusions*."""
    if not exclusions:
        return True
    tweet_id = normalize_note_id(metadata.get("tweet_id"))
    return tweet_id not in exclusions


def passes_classification_filter(
    metadata: Dict[str, Any],
    include_classifications: Optional[Iterable[str]] = None,
) -> bool:
    """Return ``True`` if the document's classification is in the allowed set (or if no filter is set)."""
    if not include_classifications:
        return True
    allowed = {str(v).strip().upper() for v in include_classifications if str(v).strip()}
    if not allowed:
        return True
    classification = str(metadata.get("classification") or "").strip().upper()
    return classification in allowed


def combined_doc_filter(
    metadata: Dict[str, Any],
    filter_before_utc: Optional[float] = None,
    exclusions: Optional[Set[str]] = None,
    include_classifications: Optional[Iterable[str]] = None,
) -> bool:
    """Apply all document filters (time, tweet exclusion, classification) in a single pass."""
    return (
        passes_time_filter(metadata, filter_before_utc)
        and passes_tweet_filter(metadata, exclusions or set())
        and passes_classification_filter(metadata, include_classifications)
    )

