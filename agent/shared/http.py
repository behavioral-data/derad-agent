"""Shared HTTP constants for outbound fetches.

The factcheck pipeline fetches both HTML pages (search.py) and images
(multimodal.py) from third-party hosts. A realistic, current desktop-Chrome
User-Agent is used everywhere because bot-identifying UAs
(e.g. "derad-agent-validator/...") trip WAFs (Cloudflare/Akamai) on news,
reference, and gov sites — those hosts return 403 and the hit gets dropped.

Defined once here so the two fetch sites can't drift.
"""

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
