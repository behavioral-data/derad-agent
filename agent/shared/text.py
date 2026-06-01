"""X-tweet character utilities — shared between renderer, audit, and poster.

The renderer's invariance check, the audit's URL scan, and the poster's
weighted-length check all need to agree on (a) which substrings count as
URLs and (b) how many characters X charges for one. Diverging definitions
have produced silent truncation bugs in the past, so everything imports
from here.
"""

import re
from urllib.parse import urlsplit, urlunsplit

X_TCO_LEN = 23  # every URL collapses to 23 chars in X's weighted length
X_TWEET_LIMIT = 25000
URL_RE = re.compile(r"https?://[^\s<>\"']+")


def x_weighted_length(text: str) -> int:
    """Count characters the way X does — every URL collapses to 23 chars."""
    return len(URL_RE.sub("x" * X_TCO_LEN, text))


_TRAILING_PUNCT = ".,;:!?"


def canonicalize_url(url: str) -> str:
    """Normalize a URL for equality comparison across the factcheck pipeline.

    Strips trailing sentence punctuation, an unbalanced trailing `)`, the
    fragment, and a default port; lowercases scheme + host. Path/query case
    is preserved. Returns the original string on parse failure."""
    if not url:
        return url
    s = url
    while s and s[-1] in _TRAILING_PUNCT:
        s = s[:-1]
    # Drop trailing `)` only when unbalanced — keeps `…Mercury_(element)` intact
    # but strips a sentence-final `…snopes.com)`.
    if s.endswith(")") and s.count("(") < s.count(")"):
        s = s[:-1]
        while s and s[-1] in _TRAILING_PUNCT:
            s = s[:-1]
    try:
        parts = urlsplit(s)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    netloc = host
    if parts.port is not None:
        default_port = {"http": 80, "https": 443}.get(scheme)
        if parts.port != default_port:
            netloc = f"{host}:{parts.port}"
    userinfo = ""
    if parts.username is not None:
        userinfo = parts.username
        if parts.password is not None:
            userinfo += f":{parts.password}"
        netloc = f"{userinfo}@{netloc}"
    return urlunsplit((scheme, netloc, parts.path, parts.query, ""))
