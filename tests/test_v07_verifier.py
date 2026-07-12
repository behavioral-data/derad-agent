# tests/test_v07_verifier.py
from unittest import mock

from agent.factcheck.draft import DraftVerdict
from agent.factcheck.verifier import VerifierOutput, apply_downgrade, run_verified_loop

_D = dict(hypotheses=[], target_hypothesis="", action="verify", central_claim="c",
          headline_finding="h", justification="j", primary_sources=[],
          load_bearing_facts=[], evidence_refs=[], verdict_derivation="d",
          confidence="high", verdict_leaning="refuted")


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
