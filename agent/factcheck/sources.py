"""Source-reliability lookup (design §4.5.2 — four-step lookup).

Thin-slice implementation: a small canned domain → tier table covering the
common fact-checker / satirical / aggregator domains we expect to see. The
full four-step lookup (IFCN list → Wikipedia RSP → MBFC → model prior +
meta-search) is a follow-up.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .schema import SourceQualityEntry, SourceTier, TierSource


_DOMAIN_TABLE: dict[str, tuple[SourceTier, TierSource, str]] = {
    # IFCN signatories (a small subset for the thin slice).
    "snopes.com": ("fact-checker", "ifcn", "IFCN signatory."),
    "www.snopes.com": ("fact-checker", "ifcn", "IFCN signatory."),
    "factcheck.org": ("fact-checker", "ifcn", "IFCN signatory."),
    "www.factcheck.org": ("fact-checker", "ifcn", "IFCN signatory."),
    "politifact.com": ("fact-checker", "ifcn", "IFCN signatory."),
    "www.politifact.com": ("fact-checker", "ifcn", "IFCN signatory."),
    "thequint.com": ("fact-checker", "ifcn", "IFCN signatory."),
    "www.thequint.com": ("fact-checker", "ifcn", "IFCN signatory."),
    "africacheck.org": ("fact-checker", "ifcn", "IFCN signatory."),
    "www.africacheck.org": ("fact-checker", "ifcn", "IFCN signatory."),
    "vishvasnews.com": ("fact-checker", "ifcn", "IFCN signatory."),
    "fullfact.org": ("fact-checker", "ifcn", "IFCN signatory."),

    # Reputable news (Wikipedia RSP-style classification).
    "reuters.com": ("reputable-news", "wikipedia-rsp", "Wikipedia perennial sources: generally reliable."),
    "apnews.com": ("reputable-news", "wikipedia-rsp", "Wikipedia perennial sources: generally reliable."),
    "bbc.com": ("reputable-news", "wikipedia-rsp", "Wikipedia perennial sources: generally reliable."),
    "bbc.co.uk": ("reputable-news", "wikipedia-rsp", "Wikipedia perennial sources: generally reliable."),
    "nytimes.com": ("reputable-news", "wikipedia-rsp", "Wikipedia perennial sources: generally reliable."),
    "washingtonpost.com": ("reputable-news", "wikipedia-rsp", "Wikipedia perennial sources: generally reliable."),
    "theguardian.com": ("reputable-news", "wikipedia-rsp", "Wikipedia perennial sources: generally reliable."),

    # Known satirical / fictional sites.
    "worldnewsdailyreport.com": ("satirical", "wikipedia-rsp", "Self-described satirical/fictional news site."),
    "theonion.com": ("satirical", "wikipedia-rsp", "Self-described satirical news site."),
    "babylonbee.com": ("satirical", "wikipedia-rsp", "Self-described satirical news site."),

    # Aggregators / low-quality.
    "worldrecordacademy.org": ("low-quality", "wikipedia-rsp", "Aggregator with no editorial oversight."),
}


def _normalize_domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower()


def classify_url(url: str) -> SourceQualityEntry:
    """Return a SourceQualityEntry for a URL. Falls through to `unknown`."""
    host = _normalize_domain(url)
    if host in _DOMAIN_TABLE:
        tier, tier_source, rationale = _DOMAIN_TABLE[host]
        return SourceQualityEntry(url=url, tier=tier, tier_source=tier_source, rationale=rationale)
    return SourceQualityEntry(
        url=url,
        tier="unknown",
        tier_source="model-prior",
        rationale=f"No entry for {host!r} in the thin-slice domain table.",
    )


def build_quality_table(urls: list[str]) -> list[SourceQualityEntry]:
    """Build the source_quality_table for a set of URLs, de-duplicated in input order."""
    seen: set[str] = set()
    table: list[SourceQualityEntry] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        table.append(classify_url(url))
    return table
