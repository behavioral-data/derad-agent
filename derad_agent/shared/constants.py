"""
Documented constants for the landscape retrieval pipeline.

All magic numbers are centralised here with explanations.
Modules that previously defined these inline (e.g. llm/config.py) now
import from here and re-export for backward compatibility.
"""

# -- Retrieval ----------------------------------------------------------

K_SEMANTIC: int = 8
"""Number of semantically similar seed documents to retrieve per query.
8 balances recall with noise — higher values add marginal threads that
dilute relevance, while lower values risk missing key evidence."""

MAX_PER_THREAD: int = 6
"""Maximum target-user chunks to fetch per thread during expansion.
Caps context length while still capturing multi-turn exchanges."""

# -- Chunking -----------------------------------------------------------

CHUNK_MAX_TOKENS: int = 600
"""Maximum token count per text chunk sent to the embedding model.
Sized to fit one opinion-in-context block within a single embedding
window (text-embedding-3-large supports 8191 tokens)."""

POST_SNIP_TOKENS: int = 200
"""Token budget for the post snippet included in each chunk header."""

CTX_PARENT_TOKENS: int = 120
"""Token budget for the parent-comment context window."""

CTX_TOP_TOKENS: int = 80
"""Token budget for the top-level comment context window."""

USER_SPLIT_TOKENS: int = 400
"""Token budget for splitting a single user comment across chunks."""

SPECTRUM_BUCKETS = [
    "StronglyOppose",
    "Oppose",
    "MixedUnclear",
    "Support",
    "StronglySupport",
]
