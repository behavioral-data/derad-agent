from datetime import datetime, timezone

from agent.factcheck.draft import DraftSource, DraftVerdict, EvidenceRef, assemble_frozen
from agent.factcheck.loop_tools import EvidenceRow
from agent.factcheck.schema import (
    ConsolidatedFindings, RefutedProposition, SourceQualityEntry, TierRef,
)
from agent.factcheck.verdict import derive_action_outcome

_ROWS = [
    EvidenceRow(0, "https://www.eia.gov/petroleum", "EIA weekly", "",
                "Weekly series: $2.81 Jan, $4.04 Apr.", "2026-04-20", "fetch"),
    EvidenceRow(1, "https://news.test/gas", "Gas article", "",
                "Prices fell 8 days.", "2026-04-21", "fetch"),
]

_DRAFT = DraftVerdict(
    hypotheses=["cherry-picked window"], target_hypothesis="cherry-picked window",
    action="provide_context", central_claim="Gas prices fell 8 straight days",
    headline_finding="True but prices are up 44% since January.",
    justification="EIA series shows $2.81 January vs $4.04 April.",
    context_note="The dip is a pullback from a yearly run-up.",
    primary_sources=[DraftSource(url="https://www.eia.gov/petroleum", display_name="EIA")],
    load_bearing_facts=["$2.81 January", "$4.04 April", "44%"],
    evidence_refs=[EvidenceRef(row=0, stance="supports", on_point=True),
                   EvidenceRef(row=1, stance="neutral")],
    verdict_derivation="rows 0-1 → context", confidence="high",
    verdict_leaning="supported",
)


def test_assemble_builds_frozen_context_verdict(monkeypatch):
    monkeypatch.setattr(
        "agent.factcheck.draft.build_quality_table",
        lambda urls: [SourceQualityEntry(url=u, tier="primary-source",
                                         tier_source="editorial-curated", rationale="t")
                      for u in dict.fromkeys(urls)],
    )
    fv = assemble_frozen(
        _DRAFT, _ROWS,
        invocation_id="inv1",
        invocation_time=datetime(2026, 7, 10, tzinfo=timezone.utc),
        target_tweet_id="123", backend_name="test-backend",
    )
    assert fv.engine == "loop"
    assert fv.action == "provide_context"
    assert fv.action_outcome == "context_provided"       # 1 on-point primary source counts as 2
    assert fv.presentation_payload.load_bearing_facts == ("$2.81 January", "$4.04 April", "44%")
    central = [c for c in fv.claims if c.is_central][0]
    assert central.evidence[0].published_at == "2026-04-20"
    assert central.evidence[0].stance == "supports"


def test_on_point_primary_source_counts_double():
    findings = ConsolidatedFindings(refuted_propositions=(
        RefutedProposition(proposition="p",
                           refuting_sources=(TierRef(url="https://cdc.gov/x", tier="primary-source"),),
                           counter_fact="cf", is_central=True),
    ))
    table = [SourceQualityEntry(url="https://cdc.gov/x", tier="primary-source",
                                tier_source="editorial-curated", rationale="r")]
    assert derive_action_outcome("verify", findings, table) == "verified_nei"
    assert derive_action_outcome("verify", findings, table,
                                 on_point_urls=frozenset({"https://cdc.gov/x"})) == "verified_refuted"
