"""Client-tool runtime for the v0.7 loop: fetch_page + the evidence log.

Every retrieval (search hit or page fetch) is logged as a numbered
EvidenceRow; finalize/verifier reference rows by index. Page bodies are
UNTRUSTED and always delivered inside explicit delimiters.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .search import _fetch_clean_page
from .snapshot import fetch_snapshot

logger = logging.getLogger(__name__)

UNTRUSTED_OPEN = "<<<UNTRUSTED PAGE CONTENT>>>"
UNTRUSTED_CLOSE = "<<<END UNTRUSTED PAGE CONTENT>>>"

_BODY_CAP = 4000


@dataclass
class EvidenceRow:
    idx: int
    url: str
    title: str
    snippet: str
    body_markdown: str
    published_at: Optional[str]
    origin: str            # "search" | "fetch" | "post_link"
    via_snapshot: bool = False


@dataclass
class ToolRuntime:
    cutoff: Optional[datetime] = None
    rows: list[EvidenceRow] = field(default_factory=list)

    def _append(self, **kw) -> EvidenceRow:
        row = EvidenceRow(idx=len(self.rows), **kw)
        self.rows.append(row)
        return row

    def record_search_results(self, query: str, results: list[dict]) -> None:
        for r in results:
            self._append(
                url=r.get("url", ""), title=r.get("title", ""),
                snippet=(r.get("snippet") or "")[:400], body_markdown="",
                published_at=None, origin="search",
            )

    def fetch_page(self, url: str, *, origin: str = "fetch") -> str:
        page = None
        via_snapshot = False
        if self.cutoff is not None:
            page = fetch_snapshot(url, self.cutoff)
            via_snapshot = page is not None
        if page is None:
            page = _fetch_clean_page(url)
        if page.status is None or (page.status or 0) >= 400 or not page.body_markdown:
            self._append(url=url, title=page.title or "", snippet="",
                         body_markdown="", published_at=page.published_date,
                         origin=origin, via_snapshot=via_snapshot)
            return (f"FETCH FAILED for {url} (status={page.status}). The URL may be "
                    "paywalled/blocked; try another source or a search instead.")
        row = self._append(
            url=url, title=page.title or "", snippet="",
            body_markdown=page.body_markdown[:_BODY_CAP],
            published_at=page.published_date, origin=origin,
            via_snapshot=via_snapshot,
        )
        return (
            f"evidence_row: {row.idx}\n"
            f"url: {url}\n"
            f"published_date: {row.published_at or 'unknown'}\n"
            f"via_snapshot: {via_snapshot}\n"
            f"{UNTRUSTED_OPEN}\npage-reported title: {row.title}\n\n{row.body_markdown}\n{UNTRUSTED_CLOSE}"
        )
