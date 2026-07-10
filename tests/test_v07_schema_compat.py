import glob
import json

from agent.factcheck.schema import Evidence, FrozenVerdict, VerifierReport


def test_old_freezes_still_parse():
    paths = sorted(glob.glob("data/freezes/*.json"))[:5]
    assert paths, "expected existing freezes on disk"
    for p in paths:
        fv = FrozenVerdict.model_validate(json.load(open(p)))
        assert fv.engine == "staged"          # default for legacy freezes
        assert fv.verifier_report is None


def test_new_fields_roundtrip():
    e = Evidence(question="q", source_url="https://x.test", snippet="s",
                 stance="neutral", published_at="2026-04-20", origin="fetch",
                 via_snapshot=True)
    assert e.published_at == "2026-04-20"
    r = VerifierReport(passed=False, required_revisions="fix X", downgrade=True)
    assert r.revision_used is False
