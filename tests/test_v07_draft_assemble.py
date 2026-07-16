from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

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
    central_question="cherry-picked window",
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


def test_draft_verdict_requires_decision_fields():
    # finalize must force commitment: action, central_claim, primary_sources,
    # load_bearing_facts, evidence_refs, verdict_derivation, confidence,
    # verdict_leaning are all required.
    with pytest.raises(ValidationError):
        DraftVerdict(central_claim="c", headline_finding="h", justification="j")


def test_draftverdict_has_central_question_and_peripheral_facts():
    d = DraftVerdict(
        central_question="Were gas prices down on May 8, 2026?",
        action="verify", central_claim="Gas prices were way down",
        headline_finding="Gas prices rose, not fell.",
        justification="AAA shows a rise.",
        primary_sources=[DraftSource(url="https://gasprices.aaa.com/", display_name="AAA")],
        load_bearing_facts=["national average $4.55 on May 8"],
        peripheral_facts=["S&P 500 +0.84%"],
        evidence_refs=[], verdict_derivation="…",
        confidence="high", verdict_leaning="refuted",
    )
    assert d.central_question.startswith("Were gas prices")
    assert d.peripheral_facts == ["S&P 500 +0.84%"]
    assert not hasattr(d, "hypotheses")
    assert not hasattr(d, "target_hypothesis")


# ---- label-fidelity reconciliation (PS-4/EV-14 fix) ----
from agent.factcheck.verdict import reconcile_outcome_with_finding


def test_reconcile_promotes_verifier_passed_decisive_verdicts():
    # verifier passed + decisive leaning + no-result label → promoted to the finding
    assert reconcile_outcome_with_finding("verified_nei", "verify", "refuted",
                                          verifier_passed=True) == "verified_refuted"
    assert reconcile_outcome_with_finding("verified_nei", "verify", "supported",
                                          verifier_passed=True) == "verified_supported"
    assert reconcile_outcome_with_finding("context_unavailable", "provide_context", "insufficient",
                                          verifier_passed=True) == "context_provided"
    assert reconcile_outcome_with_finding("challenge_unavailable", "challenge_opinion", "insufficient",
                                          verifier_passed=True) == "challenged"


def test_reconcile_leaves_conservative_labels_when_verifier_did_not_pass():
    # downgraded/scrubbed (verifier_passed=False) keeps the conservative no-result label
    assert reconcile_outcome_with_finding("verified_nei", "verify", "refuted",
                                          verifier_passed=False) == "verified_nei"
    # 'insufficient' leaning stays no-result even when the verifier passed
    assert reconcile_outcome_with_finding("verified_nei", "verify", "insufficient",
                                          verifier_passed=True) == "verified_nei"
    # a non-no-result outcome is never touched
    assert reconcile_outcome_with_finding("verified_supported", "verify", "supported",
                                          verifier_passed=True) == "verified_supported"


def test_reconcile_non_verify_survives_advisory_downgrade():
    # A substantive challenge/context finding survives an ADVISORY downgrade
    # (confidence lowered, payload not scrubbed) the same way a verify verdict
    # does — it must not collapse to *_unavailable on a minor verifier concern.
    assert reconcile_outcome_with_finding(
        "challenge_unavailable", "challenge_opinion", "insufficient",
        verifier_passed=False, verifier_advisory_downgrade=True) == "challenged"
    assert reconcile_outcome_with_finding(
        "context_unavailable", "provide_context", "insufficient",
        verifier_passed=False, verifier_advisory_downgrade=True) == "context_provided"


def test_reconcile_advisory_downgrade_does_not_rescue_verify_or_scrub():
    # verify does NOT get promoted by an advisory downgrade (it already has
    # advisory-downgrade coherence via apply_downgrade keeping verdict_leaning).
    assert reconcile_outcome_with_finding(
        "verified_nei", "verify", "refuted",
        verifier_passed=False, verifier_advisory_downgrade=True) == "verified_nei"
    # No pass and no advisory downgrade (e.g. a scrub → advisory flag is False) →
    # non-verify keeps the conservative no-result label.
    assert reconcile_outcome_with_finding(
        "challenge_unavailable", "challenge_opinion", "insufficient",
        verifier_passed=False, verifier_advisory_downgrade=False) == "challenge_unavailable"


def test_assemble_promotes_label_for_verifier_passed_weakly_sourced_refutation(monkeypatch):
    from agent.factcheck.schema import VerifierReport
    monkeypatch.setattr(
        "agent.factcheck.draft.build_quality_table",
        # classify everything 'unknown' → no reliable sources → derive_action_outcome
        # would return verified_nei despite a decisive refuted leaning
        lambda urls: [SourceQualityEntry(url=u, tier="unknown",
                                         tier_source="model-prior", rationale="t")
                      for u in dict.fromkeys(urls)],
    )
    draft = DraftVerdict(
        central_question="fabricated", action="verify",
        central_claim="X donated to campaign Y",
        headline_finding="FALSE — no record of the donation exists.",
        justification="FEC and state records show no such donation.",
        primary_sources=[DraftSource(url="https://fec.gov/x", display_name="FEC")],
        load_bearing_facts=["no FEC record"],
        evidence_refs=[EvidenceRef(row=0, stance="refutes", on_point=True)],
        verdict_derivation="row 0 → false", confidence="high", verdict_leaning="refuted",
    )
    rows = [EvidenceRow(0, "https://fec.gov/x", "FEC search", "", "No results.", "2026-01-01", "fetch")]
    fv = assemble_frozen(
        draft, rows, invocation_id="i", invocation_time=datetime(2026, 7, 15, tzinfo=timezone.utc),
        target_tweet_id="t", backend_name="b",
        verifier_report=VerifierReport(passed=True),
    )
    assert fv.action_outcome == "verified_refuted"   # promoted; not silently verified_nei


def test_assemble_keeps_nei_when_no_verifier_report():
    # no verifier_report (default None) → no promotion → conservative label preserved
    from agent.factcheck.schema import SourceQualityEntry as SQE
    import agent.factcheck.draft as d
    orig = d.build_quality_table
    d.build_quality_table = lambda urls: [SQE(url=u, tier="unknown", tier_source="model-prior", rationale="t") for u in dict.fromkeys(urls)]
    try:
        draft = DraftVerdict(
            central_question="h", action="verify", central_claim="c",
            headline_finding="FALSE", justification="j",
            primary_sources=[DraftSource(url="https://x.test/a", display_name="X")],
            load_bearing_facts=[], evidence_refs=[EvidenceRef(row=0, stance="refutes")],
            verdict_derivation="d", confidence="high", verdict_leaning="refuted",
        )
        rows = [EvidenceRow(0, "https://x.test/a", "t", "", "b", "2026-01-01", "fetch")]
        fv = assemble_frozen(draft, rows, invocation_id="i",
                             invocation_time=datetime(2026, 7, 15, tzinfo=timezone.utc),
                             target_tweet_id="t", backend_name="b")
        assert fv.action_outcome == "verified_nei"   # unchanged without a passed verifier
    finally:
        d.build_quality_table = orig
