"""Plan → retrieve → compose pipeline."""

from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional

from derad_agent.llm.config import get_embedder
from derad_agent.shared.logging import RuntimeLogger
from derad_agent.shared.validation import validate_agent_inputs

from .notes_index import (
    load_notes_index,
    retrieve_tweets,
    select_recent_helpful_notes,
)
from .steps import step_1_generate_queries, step_compose_reply


def run_landscape_agent(
    statement: str,
    notes_index_dir: pathlib.Path,
    *,
    k_per_query: int = 25,
    notes_per_tweet: int = 10,
    similarity_min: float = 0.0,
    exclude_tweet_id: Optional[str] = None,
    response_style: str = "neutral",
    verbose: bool = False,
    logger: Optional[Any] = None,
    style: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the simplified landscape pipeline for *statement*.

    Args:
        statement: The claim or post to reply to.
        notes_index_dir: Directory with ``tweet_ids.npy``, ``embeddings.npy``,
            and ``notes_cache.json`` (output of ``derad_agent.cli.embed_notes``).
        k_per_query: Tweets fetched per planner query.
        notes_per_tweet: Cap on CURRENTLY_RATED_HELPFUL notes kept per tweet (latest first).
        similarity_min: Minimum cosine similarity for retrieved tweets.
        exclude_tweet_id: Optional tweet ID to exclude (e.g. self-exclusion).
        response_style: Reply tone (``"neutral"``, ``"bridging"``, ``"agonistic"``).
        verbose: Enable detailed logging.
        logger: Optional custom logger.

    Returns dict with ``statement``, ``queries``, ``retrieved_tweets``,
    ``selected_notes``, and ``reply``.
    """
    if logger is None:
        logger = RuntimeLogger(verbose=verbose)

    validate_agent_inputs(statement, notes_index_dir)

    embedder = get_embedder()
    index = load_notes_index(notes_index_dir)
    logger.log_info(
        f"Loaded notes index: {len(index.tweet_ids):,} tweets, "
        f"{sum(len(v) for v in index.notes_by_tweet.values()):,} notes"
    )

    queries, planner_thinking, _ = step_1_generate_queries(statement=statement, logger=logger)

    excluded = [exclude_tweet_id] if exclude_tweet_id else []

    best_sim_by_tweet: Dict[str, float] = {}
    for query in queries:
        for tweet_id, score in retrieve_tweets(
            query=query,
            embedder=embedder,
            index=index,
            k=k_per_query,
            exclude_tweet_ids=excluded,
            similarity_min=similarity_min,
        ):
            prior = best_sim_by_tweet.get(tweet_id)
            if prior is None or score > prior:
                best_sim_by_tweet[tweet_id] = score

    retrieved = sorted(best_sim_by_tweet.items(), key=lambda kv: kv[1], reverse=True)
    logger.log_info(f"Retrieved {len(retrieved)} unique tweets across {len(queries)} queries")

    selected_notes = select_recent_helpful_notes(
        retrieved=retrieved,
        index=index,
        per_tweet=notes_per_tweet,
    )
    logger.log_info(f"Selected {len(selected_notes)} CURRENTLY_RATED_HELPFUL notes (≤{notes_per_tweet} per tweet)")

    reply = step_compose_reply(
        statement=statement,
        notes=selected_notes,
        response_style=response_style,
    )

    return {
        "statement": statement,
        "queries": queries,
        "planner_thinking": planner_thinking,
        "retrieved_tweets": [
            {"tweet_id": tid, "similarity": round(sim, 4)} for tid, sim in retrieved
        ],
        "selected_notes": selected_notes,
        "reply": reply,
    }
