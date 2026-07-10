import glob
import json
import os

from agent.factcheck.schema import Evidence, FrozenVerdict, VerifierReport

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "legacy_freezes")


def test_old_freezes_still_parse():
    # Committed fixtures — always present, so this is the hard guarantee.
    # These are all pre-v0.7 freezes, so the legacy defaults must hold.
    fixture_paths = sorted(glob.glob(os.path.join(FIXTURE_DIR, "*.json")))
    assert fixture_paths, "expected committed legacy-freeze fixtures"
    for p in fixture_paths:
        fv = FrozenVerdict.model_validate(json.load(open(p)))
        assert fv.engine == "staged"          # default for legacy freezes
        assert fv.verifier_report is None
    # Opportunistically also parse real freezes when they exist on disk
    # (data/freezes/ is gitignored, so it may be absent in worktrees/CI).
    # These may be of EITHER engine — the compat property is that they parse.
    for p in sorted(glob.glob("data/freezes/*.json"))[:5]:
        FrozenVerdict.model_validate(json.load(open(p)))


def test_new_fields_roundtrip():
    e = Evidence(question="q", source_url="https://x.test", snippet="s",
                 stance="neutral", published_at="2026-04-20", origin="fetch",
                 via_snapshot=True)
    assert e.published_at == "2026-04-20"
    r = VerifierReport(passed=False, required_revisions="fix X", downgrade=True)
    assert r.revision_used is False
