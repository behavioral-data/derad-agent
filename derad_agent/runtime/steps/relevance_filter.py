"""Step: filter notes to those relevant to the statement via one LLM call."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from derad_agent.llm.config import get_llm
from derad_agent.llm.prompts import get_relevance_filter_prompt

from ._helpers import extract_text_from_response, parse_json_response


def step_filter_notes_by_relevance(
    statement: str,
    notes: Sequence[Dict[str, Any]],
    *,
    logger: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return the subset of *notes* relevant to *statement*.

    Sends ``{note_id, summary}`` pairs to the LLM and keeps only the IDs it
    returns. Falls back to the full input list if the LLM call or JSON parse
    fails, so the pipeline never silently drops all evidence.
    """
    if not notes:
        return []

    _SUMMARY_CAP = 300  # chars; enough to judge relevance, keeps input tokens low
    payload = [
        {"note_id": n.get("note_id"), "summary": (n.get("summary") or "")[:_SUMMARY_CAP]}
        for n in notes
        if n.get("note_id") is not None
    ]
    valid_ids = {str(p["note_id"]) for p in payload}

    if logger:
        logger.log_step("relevance_filter", f"Filtering {len(notes)} notes for relevance")

    # max_tokens must cover the full keep_note_ids list. Each ~19-digit ID is
    # ~8 tokens, plus quotes, commas, and JSON wrapper. Budget 12 tokens/note
    # + 256 overhead. Bucket to fixed sizes so lru_cache reuses instances across
    # calls with similar payload sizes rather than creating a new LLM per call.
    _raw = max(512, min(8192, len(payload) * 12 + 256))
    _buckets = (512, 1024, 2048, 4096, 8192)
    max_tokens = next(b for b in _buckets if b >= _raw)
    prompt = get_relevance_filter_prompt()
    llm = get_llm(max_tokens=max_tokens)
    chain = prompt | llm

    try:
        raw = chain.invoke({
            "statement": statement,
            "notes_json": json.dumps(payload, ensure_ascii=False),
        })
        text = extract_text_from_response(raw)
        parsed = parse_json_response(text)
        keep_ids = {
            str(nid) for nid in (parsed.get("keep_note_ids") or []) if nid is not None
        }
        keep_ids &= valid_ids  # drop any IDs the LLM hallucinated
    except Exception:
        if logger:
            logger.log_warning("Relevance filter failed — keeping all notes")
        return list(notes)

    filtered = [n for n in notes if str(n.get("note_id")) in keep_ids]
    if logger:
        logger.log_info(f"Relevance filter: {len(notes)} → {len(filtered)} notes kept")
    return filtered


__all__ = ["step_filter_notes_by_relevance"]
