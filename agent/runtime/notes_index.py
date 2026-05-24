"""Tweet-level notes index: loading, retrieval, and note selection.

Consumes the artifacts produced by ``agent.cli.embed_notes``:

- ``tweet_ids.npy``    — object array of tweet IDs (row order matches embeddings).
- ``embeddings.npy``   — float32 [num_tweets, dim] matrix of tweet embeddings.
- ``notes_cache.json`` — ``{tweet_id: [{note_id, summary, classification, created_at_millis, current_status}, ...]}``.

Workflow used by the agent:
1. Cosine search over tweet embeddings to find similar tweets.
2. For each retrieved tweet, take its notes from the sidecar, filter to
   ``current_status == CURRENTLY_RATED_HELPFUL``, and keep the most recent N.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


NOTES_INDEX_DIRNAME = "notes_index"
HELPFUL_STATUS = "CURRENTLY_RATED_HELPFUL"


@dataclass
class NotesIndex:
    """In-memory tweet-level index loaded from disk."""
    tweet_ids: List[str]
    embeddings: np.ndarray  # [N, D], L2-normalized
    notes_by_tweet: Dict[str, List[Dict[str, Any]]]
    total_notes: int = 0  # precomputed at load time; avoids O(N) traversal per pipeline call


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return matrix / norms


@functools.lru_cache(maxsize=1)
def load_notes_index(index_dir: Path) -> NotesIndex:
    """Load tweet IDs, embeddings, and the notes sidecar from disk."""
    index_dir = Path(index_dir)
    tweet_ids = np.load(index_dir / "tweet_ids.npy", allow_pickle=True).tolist()
    tweet_ids = [str(tid) for tid in tweet_ids]

    embeddings = np.load(index_dir / "embeddings.npy").astype("float32")
    if embeddings.ndim != 2:
        raise ValueError(f"embeddings.npy must be 2D, got shape {embeddings.shape}")
    if embeddings.shape[0] != len(tweet_ids):
        raise ValueError(
            f"Embeddings rows ({embeddings.shape[0]}) do not match tweet_ids ({len(tweet_ids)})"
        )
    embeddings = _l2_normalize(embeddings)

    cache_path = index_dir / "notes_cache.json"
    if not cache_path.exists():
        raise FileNotFoundError(
            f"Missing notes_cache.json next to embeddings; rerun embed_notes to regenerate it."
        )
    with cache_path.open(encoding="utf-8") as f:
        raw = json.load(f)
    notes_by_tweet = {str(k): list(v or []) for k, v in raw.items()}

    total_notes = sum(len(v) for v in notes_by_tweet.values())
    return NotesIndex(tweet_ids=tweet_ids, embeddings=embeddings, notes_by_tweet=notes_by_tweet, total_notes=total_notes)


def retrieve_tweets(
    query: str,
    embedder: Any,
    index: NotesIndex,
    *,
    k: int = 25,
    exclude_tweet_ids: Optional[Sequence[str]] = None,
    similarity_min: float = 0.0,
) -> List[Tuple[str, float]]:
    """Return ``[(tweet_id, similarity), ...]`` for the top-k tweets.

    Similarity is cosine in [-1, 1] (embeddings are L2-normalized at load).
    """
    query_vec = np.asarray(embedder.embed_query(query), dtype="float32")
    norm = float(np.linalg.norm(query_vec))
    if norm > 0:
        query_vec = query_vec / norm

    sims = index.embeddings @ query_vec

    excluded = {str(t) for t in (exclude_tweet_ids or [])}
    fetch_n = min(len(sims), k * 4)
    if fetch_n == 0:
        return []
    top_idx = np.argpartition(-sims, fetch_n - 1)[:fetch_n]
    top_idx = top_idx[np.argsort(-sims[top_idx])]

    out: List[Tuple[str, float]] = []
    for idx in top_idx:
        tid = index.tweet_ids[int(idx)]
        if tid in excluded:
            continue
        score = float(sims[int(idx)])
        if score < similarity_min:
            continue
        out.append((tid, score))
        if len(out) >= k:
            break
    return out


def select_recent_helpful_notes(
    retrieved: Sequence[Tuple[str, float]],
    index: NotesIndex,
    *,
    per_tweet: int = 10,
) -> List[Dict[str, Any]]:
    """Take the most recent N notes per retrieved tweet and attach similarity.

    The index already contains only CURRENTLY_RATED_HELPFUL notes sorted by
    recency (most recent first), so this is a slice + reshape, not a filter.

    Returns a flat list of note dicts with ``note_id``, ``tweet_id``,
    ``summary``, ``classification``, ``created_at_millis``,
    ``current_status``, and the tweet's retrieval ``similarity``.

    Note: as of Apr 2026 the dataset has ≤14 helpful notes per tweet (87% have
    exactly 1), so per_tweet=10 almost never fires — kept for future growth.
    """
    out: List[Dict[str, Any]] = []
    for tweet_id, similarity in retrieved:
        for note in index.notes_by_tweet.get(tweet_id, [])[:per_tweet]:
            out.append(
                {
                    "note_id": note.get("note_id"),
                    "tweet_id": tweet_id,
                    "summary": note.get("summary", ""),
                    "classification": note.get("classification", ""),
                    "created_at_millis": note.get("created_at_millis"),
                    "current_status": note.get("current_status", ""),
                    "similarity": float(similarity),
                }
            )
    return out


__all__ = [
    "NOTES_INDEX_DIRNAME",
    "HELPFUL_STATUS",
    "NotesIndex",
    "load_notes_index",
    "retrieve_tweets",
    "select_recent_helpful_notes",
]
