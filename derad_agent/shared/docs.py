"""
Document processing utilities — deduplication, grouping.
"""

from typing import Any, Dict, List
from collections import defaultdict


def group_docs_by_metadata_key(docs: List[Any], key: str) -> Dict[str, List[Any]]:
    """Group documents by a metadata key.

    Args:
        docs: List of documents with ``.metadata`` attribute.
        key: Metadata key to group by.

    Returns:
        Dictionary mapping key values to document lists.
    """
    grouped = defaultdict(list)
    for doc in docs:
        value = doc.metadata.get(key)
        if value is not None:
            grouped[value].append(doc)
    return dict(grouped)
