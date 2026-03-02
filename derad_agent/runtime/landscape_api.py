"""Public API for the landscape-first Community Notes workflow."""

from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional, Sequence

from derad_agent.indexing.index_builder import build_global_index
from derad_agent.llm.config import INDEX_ROOT, NOTES_TSV_ROOT
from .landscape_agent import run_landscape_agent


def build_landscape_index(
    *,
    tsv_root: Optional[pathlib.Path] = None,
    tsv_files: Optional[Sequence[pathlib.Path]] = None,
    index_root: Optional[pathlib.Path] = None,
    max_retries: Optional[int] = None,
    initial_retry_delay: Optional[int] = None,
    embedding_batch_size: Optional[int] = None,
    per_batch_sleep_seconds: Optional[float] = None,
) -> None:
    """Build the global FAISS index used by landscape retrieval."""
    build_global_index(
        tsv_root=tsv_root or NOTES_TSV_ROOT,
        tsv_files=tsv_files,
        index_root=index_root or INDEX_ROOT,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
        embedding_batch_size=embedding_batch_size,
        per_batch_sleep_seconds=per_batch_sleep_seconds,
    )


def retrieve_statement_landscape(
    statement: str,
    *,
    index_root: pathlib.Path = INDEX_ROOT,
    filter_docs_before_utc: Optional[float] = None,
    exclude_tweet_id: Optional[str] = None,
    include_classifications: Optional[List[str]] = None,
    similarity_min: float = 0.0,
    max_points: int = 300,
    verbose: bool = False,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return the full statement-level landscape analysis payload."""
    from derad_agent.indexing.index_builder import get_global_index_dir

    return run_landscape_agent(
        statement=statement,
        user_dir=get_global_index_dir(index_root),
        filter_docs_before_utc=filter_docs_before_utc,
        exclude_tweet_id=exclude_tweet_id,
        include_classifications=include_classifications,
        similarity_min=similarity_min,
        max_points=max_points,
        verbose=verbose,
        logger=logger,
    )


__all__ = ["build_landscape_index", "retrieve_statement_landscape", "run_landscape_agent"]

