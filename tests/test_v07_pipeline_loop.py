from datetime import datetime, timezone
from unittest import mock

from agent.factcheck.draft import DraftVerdict
from agent.factcheck.loop_tools import ToolRuntime
from agent.factcheck.pipeline_loop import run_pipeline_loop
from agent.factcheck.schema import VerifierReport

_D = dict(hypotheses=["h"], target_hypothesis="h", action="verify", central_claim="c",
          headline_finding="hf", justification="j", primary_sources=[],
          load_bearing_facts=["42%"], evidence_refs=[], verdict_derivation="d",
          confidence="high", verdict_leaning="insufficient")


def _fake_verified_loop(*a, **kw):
    return (DraftVerdict(**_D), ToolRuntime(),
            VerifierReport(passed=True), mock.MagicMock())


def test_run_pipeline_loop_freezes_loop_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.factcheck.pipeline_loop.run_verified_loop", _fake_verified_loop)
    monkeypatch.setattr("agent.factcheck.draft.build_quality_table", lambda urls: [])
    fv = run_pipeline_loop(
        "post text", target_tweet_id="tid1",
        as_of=datetime(2026, 4, 21, tzinfo=timezone.utc),
        evidence_cutoff=datetime(2026, 4, 23, tzinfo=timezone.utc),
        freeze_root=tmp_path,
    )
    assert fv.engine == "loop"
    assert fv.verifier_report is not None and fv.verifier_report.passed
    assert fv.as_of is not None and fv.evidence_cutoff is not None
    assert list(tmp_path.glob("*.json")), "freeze file written"


def test_engine_flag_routes(monkeypatch):
    from agent.app import utils
    monkeypatch.setenv("DERAD_FACTCHECK_ENGINE", "loop")
    with mock.patch("agent.factcheck.pipeline_loop.run_pipeline_loop") as rl:
        utils.run_factcheck("stmt", exclude_tweet_id="1")
    rl.assert_called_once()
