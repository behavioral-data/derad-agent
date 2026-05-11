"""Public API for the simplified Community Notes reply workflow."""

from __future__ import annotations

import pathlib
from typing import Any, Dict, Optional

from derad_agent.llm.config import INDEX_ROOT
from .landscape_agent import run_landscape_agent
from .notes_index import NOTES_INDEX_DIRNAME


def get_notes_index_dir(index_root: pathlib.Path = INDEX_ROOT) -> pathlib.Path:
    """Resolve the directory containing tweet-level note index artifacts."""
    return pathlib.Path(index_root) / NOTES_INDEX_DIRNAME


def retrieve_statement_landscape(
    statement: str,
    *,
    index_root: pathlib.Path = INDEX_ROOT,
    notes_index_dir: Optional[pathlib.Path] = None,
    k_per_query: int = 25,
    notes_per_tweet: int = 10,
    similarity_min: float = 0.0,
    exclude_tweet_id: Optional[str] = None,
    style: str = "neutral",
    filter_notes: bool = True,
    verbose: bool = False,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run the plan→retrieve→filter→compose pipeline for *statement*.

    Returns dict with ``statement``, ``queries``, ``retrieved_tweets``,
    ``selected_notes``, and ``reply``.
    """
    target_dir = notes_index_dir or get_notes_index_dir(index_root)
    return run_landscape_agent(
        statement=statement,
        notes_index_dir=target_dir,
        k_per_query=k_per_query,
        notes_per_tweet=notes_per_tweet,
        similarity_min=similarity_min,
        exclude_tweet_id=exclude_tweet_id,
        style=style,
        filter_notes=filter_notes,
        verbose=verbose,
        logger=logger,
    )


__all__ = [
    "get_notes_index_dir",
    "retrieve_statement_landscape",
]
