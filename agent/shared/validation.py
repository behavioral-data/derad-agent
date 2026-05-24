"""Input validation utilities for the landscape pipeline."""

from typing import List

from .text import sanitize_query


def validate_agent_inputs(statement: str, index_dir) -> None:
    """Raise ValueError if statement is empty."""
    if not statement or not statement.strip():
        raise ValueError("Statement cannot be empty")


def validate_search_queries(
    queries: List[str],
    min_queries: int = 1,
    max_queries: int = 6,
) -> List[str]:
    """Sanitize and validate search queries; raise if fewer than min_queries remain."""
    clean = [sanitize_query(q) for q in queries]
    clean = [q for q in clean if len(q) > 3]
    if len(clean) < min_queries:
        raise ValueError(
            f"At least {min_queries} valid queries required, got {len(clean)}"
        )
    return clean[:max_queries]
