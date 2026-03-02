"""
Step 2: Document retrieval.
"""

from typing import Any, List, Optional

from langchain_community.vectorstores import FAISS

from derad_agent.shared.logging import RuntimeLogger
from derad_agent.shared.text import truncate_text, format_timestamp
from ..retriever import retrieve_with_expansion


def step_2_retrieve_documents(
    queries: List[str],
    vdb: FAISS,
    emb: Any,
    filter_before_utc: Optional[float] = None,
    exclude_tweet_id: Optional[str] = None,
    logger: RuntimeLogger = None,
    exclude_tweet_ids: Optional[List[str]] = None,
    include_classifications: Optional[List[str]] = None,
    similarity_min: float = 0.0,
) -> List[Any]:
    """Retrieve documents for each query via FAISS + expansion."""
    logger.log_step('retrieval', f"Retrieving documents for {len(queries)} queries")

    if filter_before_utc:
        filter_date = format_timestamp(filter_before_utc)
        logger.log_info(f"Time filtering enabled: before {filter_date}")

    docs = []
    for q_idx, query in enumerate(queries):
        if not query.strip():
            logger.log_warning(f"Empty query at index {q_idx}, skipping")
            continue

        logger.log_debug(f"Query {q_idx + 1}/{len(queries)}: '{truncate_text(query, 50)}'")

        retrieved_docs = retrieve_with_expansion(
            query=query,
            vdb=vdb,
            emb=emb,
            filter_before_utc=filter_before_utc,
            exclude_tweet_id=exclude_tweet_id,
            exclude_tweet_ids=exclude_tweet_ids,
            include_classifications=include_classifications,
            similarity_min=similarity_min,
        )

        for doc in retrieved_docs:
            meta = getattr(doc, "metadata", None)
            if meta is None:
                doc.metadata = {"retrieval_query": query}
            elif isinstance(meta, dict):
                meta.setdefault("retrieval_query", query)
            else:
                doc.metadata = {**dict(meta), "retrieval_query": meta.get("retrieval_query", query)}

        docs.extend(retrieved_docs)
        logger.log_debug(f"Found {len(retrieved_docs)} docs, total: {len(docs)}")

    logger.log_info(f"Retrieval complete: {len(docs)} total documents")
    return docs
