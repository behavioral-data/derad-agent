"""
Streaming reader + canonical normalization for Community Notes TSV input.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Set

from derad_agent.shared.validation import validate_timestamp_millis


def _to_binary_flag(value: Any) -> int:
    if value is None:
        return 0
    token = str(value).strip()
    if token in {"1", "true", "True", "TRUE"}:
        return 1
    return 0


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_label_flags(row: Dict[str, Any]) -> Dict[str, int]:
    flags: Dict[str, int] = {}
    for key, raw in row.items():
        if key.startswith("misleading") or key.startswith("notMisleading"):
            flags[key] = _to_binary_flag(raw)
    return flags


def normalize_note_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize a raw Community Notes TSV row into a canonical record.

    Returns ``None`` if the row is missing required fields (tweet ID,
    note ID, or summary text).
    """
    tweet_id = _clean_text(row.get("tweetId"))
    note_id = _clean_text(row.get("noteId"))
    summary = _clean_text(row.get("summary"))
    if not tweet_id or not note_id or not summary:
        return None

    created_utc = validate_timestamp_millis(row.get("createdAtMillis"))
    return {
        "tweet_id": tweet_id,
        "note_id": note_id,
        "author_participant_id": _clean_text(row.get("noteAuthorParticipantId")),
        "created_utc": created_utc,
        "classification": _clean_text(row.get("classification")),
        "summary": summary,
        "is_media_note": _to_binary_flag(row.get("isMediaNote")),
        "is_collaborative_note": _to_binary_flag(row.get("isCollaborativeNote")),
        "believable": _to_binary_flag(row.get("believable")),
        "harmful": _to_binary_flag(row.get("harmful")),
        "trustworthy_sources": _to_binary_flag(row.get("trustworthySources")),
        "label_flags": _extract_label_flags(row),
    }


def iter_notes_tsv_rows(tsv_path: Path) -> Generator[Dict[str, Any], None, None]:
    """Yield normalized note records from a single Community Notes TSV file."""
    with tsv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            normalized = normalize_note_row(row)
            if normalized is not None:
                yield normalized


def iter_notes_from_paths(tsv_paths: Iterable[Path]) -> Generator[Dict[str, Any], None, None]:
    """Yield normalized note records from multiple TSV files in order."""
    for tsv_path in tsv_paths:
        yield from iter_notes_tsv_rows(tsv_path)


def list_tweet_ids(tsv_paths: Iterable[Path]) -> List[str]:
    """Return a sorted list of unique tweet IDs found across *tsv_paths*."""
    tweet_ids: Set[str] = set()
    for record in iter_notes_from_paths(tsv_paths):
        tweet_id = record.get("tweet_id")
        if tweet_id:
            tweet_ids.add(tweet_id)
    return sorted(tweet_ids)

