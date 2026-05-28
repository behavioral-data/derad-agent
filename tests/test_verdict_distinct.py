"""Distinct-URL counting in verdict._count_reliable.

If reconcile lists the same URL twice in a bucket, the reliable-sources
gate must NOT clear on one distinct source.
"""
from __future__ import annotations

from agent.factcheck.schema import (
    ConsolidatedFindings,
    SourceQualityEntry,
    VerifiedProposition,
    TierRef,
)
from agent.factcheck.verdict import derive_action_outcome


def _quality(*entries: tuple[str, str]) -> list[SourceQualityEntry]:
    return [
        SourceQualityEntry(url=u, tier=t, tier_source="model-prior", rationale="")
        for (u, t) in entries
    ]


def _verified_findings(*urls: str) -> ConsolidatedFindings:
    return ConsolidatedFindings(
        verified_propositions=(
            VerifiedProposition(
                proposition="The sky is blue.",
                supporting_sources=tuple(
                    TierRef(url=u, tier="reputable-news") for u in urls
                ),
                is_central=True,
            ),
        ),
    )


def test_same_reputable_url_twice_does_not_clear_threshold():
    url = "https://nytimes.com/article-a"
    findings = _verified_findings(url, url)
    table = _quality((url, "reputable-news"))
    assert derive_action_outcome("verify", findings, table) == "verified_nei"


def test_two_distinct_reputable_urls_clear_threshold():
    a = "https://nytimes.com/article-a"
    b = "https://reuters.com/article-b"
    findings = _verified_findings(a, b)
    table = _quality((a, "reputable-news"), (b, "reputable-news"))
    assert derive_action_outcome("verify", findings, table) == "verified_supported"


def test_dup_reputable_plus_one_low_quality_does_not_clear():
    a = "https://nytimes.com/article-a"
    low = "https://random-blog.example/post"
    findings = _verified_findings(a, a, low)
    table = _quality((a, "reputable-news"), (low, "low-quality"))
    assert derive_action_outcome("verify", findings, table) == "verified_nei"


def test_canonicalization_collapses_trailing_punctuation():
    # canonicalize_url strips trailing sentence punctuation; two textual
    # variants of the same URL must collapse to one distinct source.
    a = "https://nytimes.com/article-a"
    a_punct = "https://nytimes.com/article-a."
    findings = _verified_findings(a, a_punct)
    table = _quality((a, "reputable-news"))
    assert derive_action_outcome("verify", findings, table) == "verified_nei"
