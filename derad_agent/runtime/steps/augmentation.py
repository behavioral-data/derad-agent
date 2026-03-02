"""Step 3: Document augmentation for tweet-cluster evidence."""

from typing import Any, List, Optional

from langchain_community.vectorstores import FAISS

from derad_agent.shared.logging import RuntimeLogger
from derad_agent.shared.docs import group_docs_by_metadata_key
from derad_agent.shared.constants import MAX_PER_THREAD
from derad_agent.shared.community_notes import (
    build_exclusion_set,
    combined_doc_filter,
)


def step_3_augment_documents(
    docs: List[Any],
    vdb: FAISS,
    filter_before_utc: Optional[float] = None,
    exclude_tweet_id: Optional[str] = None,
    logger: RuntimeLogger = None,
    exclude_tweet_ids: Optional[List[str]] = None,
) -> List[Any]:
    """Augment documents by expanding each retrieved tweet cluster."""
    logger.log_step('augmentation', "Expanding retrieved tweet clusters")

    exclusions = build_exclusion_set(exclude_tweet_id, exclude_tweet_ids)

    def _passes_filter(metadata):
        return combined_doc_filter(metadata, filter_before_utc, exclusions)

    def _search_vdb(vdb, filter_dict, k=20):
        """Use FAISS metadata filtering instead of scanning private docstore."""
        try:
            results = vdb.similarity_search("", k=k, filter=filter_dict)
            return [d for d in results if _passes_filter(d.metadata)]
        except Exception as e:
            logger.log_debug(f"FAISS filter search failed: {e}")
            return []

    grouped_by_tweet = group_docs_by_metadata_key(docs, "tweet_id")
    logger.log_debug(f"Found {len(grouped_by_tweet)} unique tweets")

    augmented_docs = list(docs)

    for tweet_id, tweet_docs in grouped_by_tweet.items():
        logger.log_debug(f"Augmenting tweet cluster {tweet_id}")
        seen_note_ids = {
            doc.metadata.get("note_id")
            for doc in tweet_docs
            if doc.metadata.get("note_id")
        }
        cluster_docs = _search_vdb(vdb, {"tweet_id": tweet_id}, k=MAX_PER_THREAD * 5)
        new_docs = [d for d in cluster_docs if d.metadata.get("note_id") not in seen_note_ids]
        if new_docs:
            augmented_docs.extend(new_docs[:MAX_PER_THREAD])
            logger.log_debug(f"Added {len(new_docs[:MAX_PER_THREAD])} additional notes")

    logger.log_info(f"Augmentation complete: {len(augmented_docs)} total documents")
    return augmented_docs
