"""
Text processing utilities — query sanitisation, truncation, tag extraction.

Regex patterns are compiled at module level to avoid repeated compilation.
"""

import re
import functools
from typing import Optional, List
from datetime import datetime

# -- Pre-compiled patterns ------------------------------------------------

_RE_WHITESPACE = re.compile(r'\s+')
_RE_LEADING_NUMBER = re.compile(r'^\d+\.\s*')


@functools.lru_cache(maxsize=32)
def _get_tag_pattern(tag: str) -> re.Pattern:
    """Return a compiled regex for extracting content between XML-style tags."""
    return re.compile(f"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", re.DOTALL)


# -- Public API -----------------------------------------------------------

def extract_content_between_tags(text: str, tag: str) -> Optional[str]:
    """Extract content between XML-style tags.

    Args:
        text: Text to search in.
        tag: Tag name (without ``< >``).

    Returns:
        Content between tags, or ``None`` if not found.
    """
    match = _get_tag_pattern(tag).search(text)
    return match.group(1).strip() if match else None


def sanitize_query(query: str) -> str:
    """Remove empty lines and excessive whitespace from a search query."""
    if not query:
        return ""
    return _RE_WHITESPACE.sub(' ', query.strip())


def parse_queries_from_text(text: str) -> List[str]:
    """Parse search queries from planner output (one per line, optionally numbered)."""
    if not text:
        return []

    lines = [line.strip() for line in text.split('\n') if line.strip()]

    queries = []
    for line in lines:
        clean_line = _RE_LEADING_NUMBER.sub('', line.strip())
        if clean_line and len(clean_line) > 3:
            queries.append(sanitize_query(clean_line))

    return queries


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate *text* to *max_length* characters, appending *suffix* if cut."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def format_timestamp(utc_timestamp: float) -> str:
    """Format a UTC timestamp for human-readable display."""
    try:
        dt = datetime.fromtimestamp(utc_timestamp)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return "Invalid Date"
