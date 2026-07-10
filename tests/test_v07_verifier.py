# tests/test_v07_verifier.py
from unittest import mock

from agent.factcheck.draft import DraftVerdict
from agent.factcheck.verifier import VerifierOutput, apply_downgrade, run_verified_loop

_D = dict(hypotheses=[], target_hypothesis="", action="verify", central_claim="c",
          headline_finding="h", justification="j", primary_sources=[],
          load_bearing_facts=[], evidence_refs=[], verdict_derivation="d",
          confidence="high", verdict_leaning="refuted")


def test_apply_downgrade_weakens():
    out = apply_downgrade(DraftVerdict(**_D))
    assert out.confidence == "low"
    assert out.verdict_leaning == "insufficient"
    assert out.justification.startswith("[downgraded by verifier]")


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
    assert got.verdict_leaning == "insufficient"      # downgraded
    assert not report.passed and report.revision_used and report.downgrade
