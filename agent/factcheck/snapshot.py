"""archive.org snapshot fetching (v0.7, study mode).

`fetch_snapshot(url, before)` returns the page as it existed at/before the
evidence cutoff — the strongest control against in-place article updates
smuggling post-cutoff content under a pre-cutoff publication date. Also the
fallback fetch path for WAF-blocked domains.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import requests

from .search import FetchedPage, _fetch_clean_page

logger = logging.getLogger(__name__)

_CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"

# Circuit breaker: archive.org rate-limits by IP under sequential load, and
# every timed-out lookup burns the loop's wall-clock budget (masquerading as
# bad pipeline quality). After _CDX_TRIP consecutive failures, skip snapshot
# lookups for _CDX_COOLDOWN_S; live fetch + published_at screening still runs.
_CDX_TRIP = 3
_CDX_COOLDOWN_S = 600.0
_cdx_consecutive_failures = 0
_cdx_disabled_until = 0.0


def _cdx_record(success: bool) -> None:
    global _cdx_consecutive_failures, _cdx_disabled_until
    if success:
        _cdx_consecutive_failures = 0
        return
    _cdx_consecutive_failures += 1
    if _cdx_consecutive_failures >= _CDX_TRIP:
        _cdx_disabled_until = time.monotonic() + _CDX_COOLDOWN_S
        logger.warning(
            "snapshot: %d consecutive CDX failures — disabling snapshot lookups "
            "for %.0fs (live fetch + date screening continues)",
            _cdx_consecutive_failures, _CDX_COOLDOWN_S,
        )


def snapshot_lookup(url: str, before: datetime, *, timeout_s: float = 6.0) -> Optional[str]:
    """Newest capture at/before `before`, as a raw-content (id_) snapshot URL."""
    if time.monotonic() < _cdx_disabled_until:
        return None
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
    except Exception as exc:
        _cdx_record(success=False)
        logger.info("snapshot_lookup failed for %s: %s", url, str(exc)[:160])
        return None
    _cdx_record(success=True)
    if not rows or len(rows) < 2:      # first row is the header
        return None
    capture_ts, original = rows[-1][0], rows[-1][1]
    return f"https://web.archive.org/web/{capture_ts}id_/{original}"


def fetch_snapshot(url: str, before: datetime, *, timeout_s: float = 12.0) -> Optional[FetchedPage]:
    # Lookup uses its own (shorter) default timeout — a slow CDX must fail
    # fast so the loop's wall-clock budget goes to real work.
    snap_url = snapshot_lookup(url, before)
    if snap_url is None:
        return None
    page = _fetch_clean_page(snap_url, timeout_s=timeout_s)
    if page.status is None or (page.status or 0) >= 400:
        return None
    # Report the ORIGINAL url — the snapshot is an implementation detail.
    return FetchedPage(page.status, url, page.title, page.body_markdown, page.published_date)
