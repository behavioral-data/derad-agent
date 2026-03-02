from __future__ import annotations

from typing import Any, Dict, Generator, Optional, Tuple

from derad_agent.shared.validation import validate_timestamp as _normalize_timestamp


def _note_text(record: Dict[str, Any]) -> str:
    lines = [
        f"<TWEET_ID> {record.get('tweet_id', 'unknown')} </TWEET_ID>",
        f"<NOTE_ID> {record.get('note_id', 'unknown')} </NOTE_ID>",
    ]
    classification = (record.get("classification") or "").strip()
    if classification:
        lines.append(f"<CLASSIFICATION> {classification} </CLASSIFICATION>")
    lines.append("<NOTE_SUMMARY>")
    lines.append((record.get("summary") or "").strip())
    lines.append("</NOTE_SUMMARY>")
    return "\n".join(lines).strip()


def _note_meta(record: Dict[str, Any], source_label: str) -> Dict[str, Any]:
    tweet_id = record.get("tweet_id")
    note_id = record.get("note_id")
    return {
        "tweet_id": tweet_id,
        "note_id": note_id,
        "thread_key": tweet_id,
        "source_file": source_label,
        "created_utc": _normalize_timestamp(record.get("created_utc")),
        "classification": record.get("classification"),
        "author_participant_id": record.get("author_participant_id"),
        "label_flags": dict(record.get("label_flags") or {}),
        "believable": int(record.get("believable") or 0),
        "harmful": int(record.get("harmful") or 0),
        "trustworthy_sources": int(record.get("trustworthy_sources") or 0),
    }


def chunk_record(
    record: Dict[str, Any],
    *,
    source_hint: Optional[str] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """Yield (chunk_text, metadata) pairs from a normalized Community Notes record."""
    source_label = source_hint or f"tsv:{record.get('tweet_id', 'unknown')}"
    summary = (record.get("summary") or "").strip()
    if not summary:
        return
    yield _note_text(record), _note_meta(record, source_label)
