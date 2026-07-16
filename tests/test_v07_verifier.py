# tests/test_v07_verifier.py
from unittest import mock

import agent.factcheck.verifier as vmod
from agent.factcheck.draft import DraftSource, DraftVerdict, EvidenceRef
from agent.factcheck.loop_tools import EvidenceRow, ToolRuntime
from agent.factcheck.verifier import (
    VerifierOutput, _to_report, apply_downgrade, apply_scoped_drops, run_verified_loop,
)

_D = dict(central_question="", action="verify", central_claim="c",
          headline_finding="h", justification="j", primary_sources=[],
          load_bearing_facts=[], evidence_refs=[], verdict_derivation="d",
          confidence="high", verdict_leaning="refuted")


def _draft(**kw):
    base = dict(
        central_question="q", action="verify", central_claim="c",
        headline_finding="h", justification="j",
        primary_sources=[DraftSource(url="https://fbi.gov/x", display_name="FBI"),
                         DraftSource(url="https://americanprogress.org/y", display_name="AP")],
        load_bearing_facts=["13,000 is a category error"],
        peripheral_facts=["US had ~16,935 homicides in 2024"],
        evidence_refs=[EvidenceRef(row=0, stance="refutes", on_point=True)],
        verdict_derivation="d", confidence="high", verdict_leaning="refuted",
    )
    base.update(kw)
    return DraftVerdict(**base)


def test_apply_downgrade_is_advisory():
    src = DraftVerdict(**_D)
    out = apply_downgrade(src)
    assert out.confidence == "low"
    # ADVISORY: verdict_leaning must NOT flip — flipping it collapsed
    # substantive verdicts to *_nei outcomes while the payload stayed
    # substantive (the v0.6 incoherence). Downgrade state lives in
    # verifier_report.downgrade + confidence.
    assert out.verdict_leaning == src.verdict_leaning
    # justification is UNCHANGED — no leaked prose prefix
    assert out.justification == src.justification


def test_run_verified_loop_pass_no_revision():
    draft = DraftVerdict(**_D)
    with mock.patch("agent.factcheck.verifier.run_loop",
                    return_value=(draft, mock.MagicMock(rows=[]), mock.MagicMock(), [])), \
         mock.patch("agent.factcheck.verifier.verify_draft",
                    return_value=VerifierOutput(passed=True)):
        got, _, report, _ = run_verified_loop("p", client=object(), ctx=None,
                                              as_of=None, cutoff=None, model="m")
    assert got is draft and report.passed and report.revision_used is False


def test_run_verified_loop_one_revision_then_pass():
    d1, d2 = DraftVerdict(**_D), DraftVerdict(**{**_D, "justification": "j2"})
    with mock.patch("agent.factcheck.verifier.run_loop",
                    return_value=(d1, mock.MagicMock(rows=[]), mock.MagicMock(), [])), \
         mock.patch("agent.factcheck.verifier.revise_in_loop",
                    return_value=(d2, mock.MagicMock())) as rev, \
         mock.patch("agent.factcheck.verifier.verify_draft",
                    side_effect=[VerifierOutput(passed=False, required_revisions="fix"),
                                 VerifierOutput(passed=True)]):
        got, _, report, _ = run_verified_loop("p", client=object(), ctx=None,
                                              as_of=None, cutoff=None, model="m")
    rev.assert_called_once()
    assert got.justification == "j2" and report.passed and report.revision_used


def test_run_verified_loop_downgrades_after_failed_revision():
    d1 = DraftVerdict(**_D)
    with mock.patch("agent.factcheck.verifier.run_loop",
                    return_value=(d1, mock.MagicMock(rows=[]), mock.MagicMock(), [])), \
         mock.patch("agent.factcheck.verifier.revise_in_loop",
                    return_value=(d1, mock.MagicMock())), \
         mock.patch("agent.factcheck.verifier.verify_draft",
                    return_value=VerifierOutput(passed=False, required_revisions="fix")):
        got, _, report, _ = run_verified_loop("p", client=object(), ctx=None,
                                              as_of=None, cutoff=None, model="m")
    assert got.confidence == "low"                    # downgraded (advisory)
    assert got.verdict_leaning == "refuted"           # outcome NOT collapsed
    assert not report.passed and report.revision_used and report.downgrade


def test_run_verified_loop_retries_mechanically_failed_revision():
    # revise returns None (finalize never validated) → ONE retry of the same
    # demanded revision with a resubmit instruction; second attempt lands.
    d1, d2 = DraftVerdict(**_D), DraftVerdict(**{**_D, "justification": "j2"})
    with mock.patch("agent.factcheck.verifier.run_loop",
                    return_value=(d1, mock.MagicMock(rows=[]), mock.MagicMock(), [])), \
         mock.patch("agent.factcheck.verifier.revise_in_loop",
                    side_effect=[(None, mock.MagicMock()), (d2, mock.MagicMock())]) as rev, \
         mock.patch("agent.factcheck.verifier.verify_draft",
                    side_effect=[VerifierOutput(passed=False, required_revisions="fix"),
                                 VerifierOutput(passed=True)]):
        got, _, report, _ = run_verified_loop("p", client=object(), ctx=None,
                                              as_of=None, cutoff=None, model="m")
    assert rev.call_count == 2
    assert "resubmit the COMPLETE corrected draft" in rev.call_args_list[1].args[1]
    assert got.justification == "j2" and report.passed and report.revision_used


def test_scrub_temporal_leak_neutralizes_payload():
    from agent.factcheck.verifier import scrub_temporal_leak
    src = DraftVerdict(**{**_D, "verdict_leaning": "refuted", "context_note": "leaky note",
                          "load_bearing_facts": ["post-cutoff fact"],
                          "load_bearing_evidence_snippet": "leaked quote"})
    out = scrub_temporal_leak(src)
    assert out.verdict_leaning == "insufficient" and out.confidence == "low"
    assert out.context_note is None and out.load_bearing_evidence_snippet == ""
    assert out.load_bearing_facts == []
    assert "time it was posted" in out.headline_finding
    assert "leaky" not in (out.justification + out.headline_finding + (out.context_note or ""))


def test_run_verified_loop_scrubs_on_unfixed_temporal_leak():
    d1 = DraftVerdict(**{**_D, "verdict_leaning": "refuted"})
    leak = VerifierOutput(passed=False, required_revisions="fix leak",
                          temporal_leaks=["cites a source published after the cutoff"])
    with mock.patch("agent.factcheck.verifier.run_loop",
                    return_value=(d1, mock.MagicMock(rows=[]), mock.MagicMock(), [])), \
         mock.patch("agent.factcheck.verifier.revise_in_loop",
                    return_value=(d1, mock.MagicMock())), \
         mock.patch("agent.factcheck.verifier.verify_draft", return_value=leak):
        got, _, report, _ = run_verified_loop("p", client=object(), ctx=None,
                                              as_of=None, cutoff=None, model="m")
    assert got.verdict_leaning == "insufficient"       # scrubbed (not advisory)
    assert "time it was posted" in got.headline_finding
    assert report.temporal_leaks and report.downgrade


def test_apply_scoped_drops_removes_peripheral_fact_and_source():
    rows = [EvidenceRow(idx=0, url="https://americanprogress.org/y", title="", snippet="",
                        body_markdown="b", published_at="2026-05-05", origin="fetch")]
    d = _draft()
    out = apply_scoped_drops(
        d,
        ["US had ~16,935 homicides in 2024", "https://americanprogress.org/y"],
        rows,
    )
    assert "US had ~16,935 homicides in 2024" not in out.peripheral_facts
    assert all(s.url != "https://americanprogress.org/y" for s in out.primary_sources)
    assert all(r.row != 0 for r in out.evidence_refs)   # ref to dropped url removed
    # central fact + verdict untouched
    assert out.load_bearing_facts == ["13,000 is a category error"]
    assert out.verdict_leaning == "refuted"


def test_to_report_carries_scoped_drops():
    out = VerifierOutput(passed=True, scoped_drops=["drop me"])
    rep = _to_report(out, False)
    assert rep.scoped_drops == ("drop me",)
    assert rep.passed is True


def test_run_verified_loop_scoped_fix_passes_without_scrub(monkeypatch):
    # Verifier returns a PERIPHERAL-only defect: passed=True + a scoped drop,
    # no central temporal leak. Expect: verdict kept, drop applied, no scrub.
    d = _draft()
    rt = ToolRuntime(cutoff=None)
    rt.rows = [EvidenceRow(idx=0, url="https://americanprogress.org/y", title="", snippet="",
                           body_markdown="b", published_at="2026-05-05", origin="fetch")]
    monkeypatch.setattr(vmod, "run_loop", lambda *a, **k: (d, rt, object(), []))
    monkeypatch.setattr(vmod, "verify_draft", lambda *a, **k: VerifierOutput(
        passed=True, scoped_drops=["US had ~16,935 homicides in 2024"]))
    draft, runtime, report, stats = vmod.run_verified_loop(
        "post", client=None, ctx=None, as_of=None, cutoff=None)
    assert report.passed is True
    assert draft.verdict_leaning == "refuted"                 # NOT scrubbed to insufficient
    assert "US had ~16,935 homicides in 2024" not in draft.peripheral_facts
    assert report.scoped_drops == ("US had ~16,935 homicides in 2024",)
