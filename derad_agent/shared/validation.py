"""
Input validation utilities for the landscape pipeline.
"""

from typing import Any, Optional, List

from .text import sanitize_query


def validate_timestamp(timestamp: Any) -> Optional[float]:
    """Validate and convert a timestamp to float.

    Args:
        timestamp: Timestamp value (string, int, float, or ``None``).

    Returns:
        Float timestamp, or ``None`` if invalid / missing.
    """
    if timestamp is None:
        return None

    try:
        if isinstance(timestamp, str):
            timestamp = timestamp.strip()
            if not timestamp or timestamp.lower() in ('none', 'null', ''):
                return None
            return float(timestamp)
        return float(timestamp)
    except (ValueError, TypeError, AttributeError):
        return None


def validate_timestamp_millis(timestamp_ms: Any) -> Optional[float]:
    """Validate and convert a millisecond timestamp to Unix seconds."""
    ts = validate_timestamp(timestamp_ms)
    if ts is None:
        return None
    # Community Notes uses millisecond epoch values.
    return ts / 1000.0 if ts > 10_000_000_000 else ts


def validate_agent_inputs(
    statement: str,
    user_dir,
) -> None:
    """Validate inputs to the landscape pipeline.

    Raises:
        ValueError: If any input is invalid.
    """
    if not statement or not statement.strip():
        raise ValueError("Statement cannot be empty")

    if not user_dir.exists():
        raise ValueError(f"User directory does not exist: {user_dir}")


def validate_search_queries(
    queries: List[str],
    min_queries: int = 1,
    max_queries: int = 6,
) -> List[str]:
    """Validate and clean search queries.

    Returns:
        Validated and cleaned list of queries.

    Raises:
        ValueError: If fewer than *min_queries* valid queries remain.
    """
    clean_queries = []
    for query in queries:
        cleaned = sanitize_query(query)
        if cleaned and len(cleaned) > 3:
            clean_queries.append(cleaned)

    if len(clean_queries) < min_queries:
        raise ValueError(
            f"At least {min_queries} valid queries required, got {len(clean_queries)}"
        )

    if len(clean_queries) > max_queries:
        clean_queries = clean_queries[:max_queries]

    return clean_queries
