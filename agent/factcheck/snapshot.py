"""archive.org snapshot fetching (v0.7, study mode).

`fetch_snapshot(url, before)` returns the page as it existed at/before the
evidence cutoff — the strongest control against in-place article updates
smuggling post-cutoff content under a pre-cutoff publication date. Also the
fallback fetch path for WAF-blocked domains.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests

from .search import FetchedPage, _fetch_clean_page

logger = logging.getLogger(__name__)

_CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"


def snapshot_lookup(url: str, before: datetime, *, timeout_s: float = 10.0) -> Optional[str]:
    """Newest capture at/before `before`, as a raw-content (id_) snapshot URL."""
    ts = before.strftime("%Y%m%d%H%M%S")
    try:
        resp = requests.get(
            _CDX_ENDPOINT,
            params={
                "url": url, "to": ts, "limit": "-1", "output": "json",
                "filter": "statuscode:200", "fl": "timestamp,original",
            },
            timeout=timeout_s,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        logger.info("snapshot_lookup failed for %s", url, exc_info=True)
        return None
    if not rows or len(rows) < 2:      # first row is the header
        return None
    capture_ts, original = rows[-1][0], rows[-1][1]
    return f"https://web.archive.org/web/{capture_ts}id_/{original}"


def fetch_snapshot(url: str, before: datetime, *, timeout_s: float = 12.0) -> Optional[FetchedPage]:
    snap_url = snapshot_lookup(url, before, timeout_s=timeout_s)
    if snap_url is None:
        return None
    page = _fetch_clean_page(snap_url, timeout_s=timeout_s)
    if page.status is None or (page.status or 0) >= 400:
        return None
    # Report the ORIGINAL url — the snapshot is an implementation detail.
    return FetchedPage(page.status, url, page.title, page.body_markdown, page.published_date)
