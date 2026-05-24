"""Text processing utilities — query sanitisation."""

import re

_RE_WHITESPACE = re.compile(r'\s+')


def sanitize_query(query: str) -> str:
    """Collapse whitespace in a search query."""
    if not query:
        return ""
    return _RE_WHITESPACE.sub(' ', query.strip())
