"""Single-pass landscape runtime orchestrator."""

from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional

try:
    from langchain_community.vectorstores import FAISS
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "langchain-community is required for derad_agent. "
        "Install it via `pip install langchain-community`."
    ) from exc

from derad_agent.llm.config import INDEX_NAME, get_embedder
from derad_agent.shared.logging import RuntimeLogger
from derad_agent.shared.validation import validate_agent_inputs
from .misleadingness import build_bucket_landscape, build_misleadingness_landscape
from .steps import (
    step_1_generate_queries,
    step_2_retrieve_documents,
    step_3_augment_documents,
    step_4_build_landscape_output,
)


def _safe_similarity(doc: Any) -> float:
    meta = dict(getattr(doc, "metadata", {}) or {})
    try:
        return float(meta.get("retrieval_similarity"))
    except (TypeError, ValueError):
        return -1.0


def _dedupe_documents(documents: List[Any]) -> List[Any]:
    """Keep one canonical document per note_id, preferring higher similarity."""
    dedup_index: Dict[str, int] = {}
    deduped: List[Any] = []
    for doc in documents:
        meta = dict(getattr(doc, "metadata", {}) or {})
        note_id = meta.get("note_id")
        if note_id:
            key = f"note:{note_id}"
        else:
            tweet_id = meta.get("tweet_id", "unknown")
            content = (getattr(doc, "page_content", "") or "").strip()
            key = f"tweet_content:{tweet_id}:{content}"

        existing_idx = dedup_index.get(key)
        if existing_idx is None:
            dedup_index[key] = len(deduped)
            deduped.append(doc)
            continue
        if _safe_similarity(doc) > _safe_similarity(deduped[existing_idx]):
            deduped[existing_idx] = doc
    return deduped


def run_landscape_agent(
    statement: str,
    user_dir: pathlib.Path,
    *,
    filter_docs_before_utc: Optional[float] = None,
    exclude_tweet_id: Optional[str] = None,
    include_classifications: Optional[List[str]] = None,
    similarity_min: float = 0.0,
    max_points: int = 300,
    verbose: bool = False,
    logger: Optional[Any] = None,
    style: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the single-pass landscape retrieval and scoring pipeline.

    Orchestrates query planning, semantic retrieval, tweet-cluster
    expansion, deduplication, misleadingness scoring, and LLM-based
    landscape output synthesis.

    Args:
        statement: The claim or statement to analyse.
        user_dir: Path to the FAISS index directory (parent of ``faiss_idx/``).
        filter_docs_before_utc: Only include notes created before this timestamp.
        exclude_tweet_id: Exclude notes from this tweet.
        include_classifications: Restrict to these classification labels.
        similarity_min: Minimum cosine similarity for seed notes.
        max_points: Cap on the number of landscape points returned.
        verbose: Enable detailed logging.
        logger: Optional custom logger (defaults to ``RuntimeLogger``).

    Returns:
        Dictionary with ``statement``, ``queries``, ``iterations``,
        ``documents``, ``misleadingness_landscape``, ``bucket_landscape``,
        and ``statement_landscape``.
    """
    if logger is None:
        logger = RuntimeLogger(verbose=verbose)

    validate_agent_inputs(statement, user_dir)
    emb = get_embedder()
    index_path = user_dir / INDEX_NAME
    vdb = FAISS.load_local(
        folder_path=str(index_path),
        embeddings=emb,
        allow_dangerous_deserialization=True,
    )

    queries, planner_thinking, _ = step_1_generate_queries(statement=statement, logger=logger)
    docs = step_2_retrieve_documents(
        queries=queries,
        vdb=vdb,
        emb=emb,
        filter_before_utc=filter_docs_before_utc,
        exclude_tweet_id=exclude_tweet_id,
        logger=logger,
        exclude_tweet_ids=[],
        include_classifications=include_classifications,
        similarity_min=similarity_min,
    )
    docs = step_3_augment_documents(
        docs=docs,
        vdb=vdb,
        filter_before_utc=filter_docs_before_utc,
        exclude_tweet_id=exclude_tweet_id,
        logger=logger,
        exclude_tweet_ids=[],
    )
    deduped_docs = _dedupe_documents(docs)

    misleadingness_landscape = build_misleadingness_landscape(
        statement=statement,
        documents=deduped_docs,
        similarity_min=similarity_min,
        max_points=max_points,
    )
    bucket_landscape = build_bucket_landscape(statement, deduped_docs)
    statement_landscape = step_4_build_landscape_output(
        statement=statement,
        misleadingness_landscape=misleadingness_landscape,
        style=style,
    )

    return {
        "statement": statement,
        "queries": queries,
        "iterations": [
            {
                "iteration": 0,
                "queries": queries,
                "planner_thinking": planner_thinking,
                "new_docs_count": len(docs),
                "cumulative_docs_count": len(deduped_docs),
            }
        ],
        "documents": deduped_docs,
        "misleadingness_landscape": misleadingness_landscape,
        "bucket_landscape": bucket_landscape,
        "statement_landscape": statement_landscape,
    }

