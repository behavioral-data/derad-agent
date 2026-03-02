"""Semantic retrieval with tweet-cluster expansion.

Given a search query and a FAISS vector store, retrieves the top-k
semantically similar seed notes, then expands each seed's tweet cluster
to capture the full conversation context.  Results are deduplicated,
similarity-thresholded, and annotated with retrieval metadata.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_community.vectorstores.faiss import FAISS
from langchain_core.embeddings import Embeddings

from derad_agent.shared.constants import K_SEMANTIC, MAX_PER_THREAD
from derad_agent.shared.community_notes import (
    build_exclusion_set,
    combined_doc_filter,
)

# Maximum docs to fetch when expanding a thread (effectively "all")
_MAX_THREAD_EXPANSION = 9999


def _distance_to_similarity(distance: Optional[float]) -> Optional[float]:
    if distance is None:
        return None
    try:
        numeric = float(distance)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    return 1.0 / (1.0 + numeric)


def _attach_retrieval_metadata(
    doc: Any,
    *,
    distance: Optional[float],
    similarity: Optional[float],
    source: str,
) -> None:
    meta = dict(getattr(doc, "metadata", {}) or {})
    if distance is not None:
        meta["retrieval_distance"] = round(float(distance), 6)
    if similarity is not None:
        meta["retrieval_similarity"] = round(float(similarity), 6)
    meta["retrieval_source"] = source
    doc.metadata = meta


def _seed_search_with_scores(vdb: FAISS, query_vector: Sequence[float], k: int) -> List[Tuple[Any, Optional[float]]]:
    if hasattr(vdb, "similarity_search_with_score_by_vector"):
        scored = vdb.similarity_search_with_score_by_vector(query_vector, k=k)
        return [(doc, score) for doc, score in scored]
    docs = vdb.similarity_search_by_vector(query_vector, k=k)
    return [(doc, None) for doc in docs]


def retrieve_with_expansion(
    query: str,
    vdb: FAISS,
    emb: Embeddings,
    k_semantic: int = K_SEMANTIC,
    max_per_thread: int = MAX_PER_THREAD,
    filter_before_utc: Optional[float] = None,
    exclude_tweet_id: Optional[str] = None,
    exclude_tweet_ids: Optional[List[str]] = None,
    include_classifications: Optional[List[str]] = None,
    similarity_min: float = 0.0,
) -> List:
    """Retrieve seed notes via semantic search and expand each tweet cluster.

    Embeds *query*, finds the top-*k_semantic* seed notes in *vdb*,
    applies similarity thresholding and metadata filters, then pulls in
    all remaining notes from each seed's tweet cluster (up to
    *max_per_thread* per cluster).  Results are deduplicated, preferring
    the copy with the highest retrieval similarity.

    Args:
        query: Natural-language search query.
        vdb: Loaded FAISS vector store.
        emb: Embedding model used to vectorise the query.
        k_semantic: Number of seed notes to retrieve per query.
        max_per_thread: Maximum notes to keep per tweet cluster.
        filter_before_utc: Only include notes created before this UTC timestamp.
        exclude_tweet_id: Single tweet ID to exclude.
        exclude_tweet_ids: List of tweet IDs to exclude.
        include_classifications: Restrict to these classification labels.
        similarity_min: Minimum similarity score for seed notes.

    Returns:
        Deduplicated list of documents with retrieval metadata attached.
    """
    exclusions = build_exclusion_set(exclude_tweet_id, exclude_tweet_ids)

    def _filter(metadata):
        return combined_doc_filter(
            metadata,
            filter_before_utc,
            exclusions,
            include_classifications=include_classifications,
        )

    # Embed query once
    query_vector = emb.embed_query(query)

    # Get initial seed documents and apply filtering.
    # Thresholding happens before thread expansion.
    seed_scored = _seed_search_with_scores(vdb, query_vector, k=k_semantic)
    seed = []
    thread_seed_similarity: Dict[str, float] = {}
    for doc, distance in seed_scored:
        similarity = _distance_to_similarity(distance)
        if similarity is not None and similarity < similarity_min:
            continue
        if not _filter(doc.metadata):
            continue
        _attach_retrieval_metadata(
            doc,
            distance=distance,
            similarity=similarity,
            source="semantic_seed",
        )
        seed.append(doc)
        thread_key = str(doc.metadata.get("thread_key") or "")
        if thread_key:
            existing = thread_seed_similarity.get(thread_key, 0.0)
            thread_seed_similarity[thread_key] = max(existing, similarity or 0.0)

    thread_keys = {d.metadata["thread_key"] for d in seed}

    # Fetch additional notes for each retrieved tweet cluster.
    extras = []
    for tk in thread_keys:
        cluster_docs = vdb.similarity_search(
            "",
            k=_MAX_THREAD_EXPANSION,
            filter={"thread_key": tk},
        )[:max_per_thread]
        cluster_docs = [d for d in cluster_docs if _filter(d.metadata)]
        inherited_similarity = thread_seed_similarity.get(str(tk), 0.0)
        for doc in cluster_docs:
            _attach_retrieval_metadata(
                doc,
                distance=None,
                similarity=inherited_similarity,
                source="thread_expansion",
            )
        extras += cluster_docs

    # Dedupe
    seen = set()
    dedup_index: Dict[str, int] = {}
    all_docs = []
    for d in seed + extras:
        note_id = d.metadata.get("note_id")
        tweet_id = d.metadata.get("tweet_id", "unknown")
        dedup_key = f"note-{note_id}" if note_id else f"tweet-{tweet_id}"

        if dedup_key not in seen:
            all_docs.append(d)
            dedup_index[dedup_key] = len(all_docs) - 1
            seen.add(dedup_key)
        else:
            # Keep the higher-similarity duplicate when available.
            existing_idx = dedup_index.get(dedup_key)
            if existing_idx is not None:
                existing_sim = all_docs[existing_idx].metadata.get("retrieval_similarity")
                candidate_sim = d.metadata.get("retrieval_similarity")
                if candidate_sim is not None and (
                    existing_sim is None or float(candidate_sim) > float(existing_sim)
                ):
                    all_docs[existing_idx] = d
    return all_docs
