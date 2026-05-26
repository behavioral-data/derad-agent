"""X-tweet character utilities — shared between renderer, audit, and poster.

The renderer's invariance check, the audit's URL scan, and the poster's
weighted-length check all need to agree on (a) which substrings count as
URLs and (b) how many characters X charges for one. Diverging definitions
have produced silent truncation bugs in the past, so everything imports
from here.
"""

import re

X_TCO_LEN = 23  # every URL collapses to 23 chars in X's weighted length
X_TWEET_LIMIT = 25000
URL_RE = re.compile(r"https?://[^\s<>\"')]+")


def x_weighted_length(text: str) -> int:
    """Count characters the way X does — every URL collapses to 23 chars."""
    return len(URL_RE.sub("x" * X_TCO_LEN, text))
