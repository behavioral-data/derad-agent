from __future__ import annotations
from collections import Counter
from study.interface.study_store import InMemoryStudyStore, StoredProfile

CONDS = ("neutral", "agreeable", "satirical", "control")


def _pool():
    profiles, orders = [], {"D": [], "R": []}
    pid = 0
    for party in ("D", "R"):
        for i in range(57):
            for c in CONDS:
                pid += 1
                name = f"P{pid:03d}"
                profiles.append(StoredProfile(name, c, party, [[f"{c}-{i}-post"]]))
                orders[party].append(name)
    return profiles, orders


def test_claim_binds_idempotent_and_balances_condition():
    s = InMemoryStudyStore()
    profs, orders = _pool()
    s.load_profiles(profs, orders)
    a1 = s.claim_profile("PROLIFIC1", "D")
    assert a1 is not None and a1.condition in CONDS
    # idempotent: same pid -> same assignment, no second claim
    assert s.claim_profile("PROLIFIC1", "D").condition == a1.condition
    # claim 8 fresh Dems -> first two blocks of 4 -> each condition twice
    got = [s.claim_profile(f"D{i}", "D").condition for i in range(8)]
    assert Counter(got) == {c: 2 for c in CONDS}


def test_pool_exhaustion_and_release():
    s = InMemoryStudyStore()
    profs, orders = _pool()
    s.load_profiles(profs, orders)
    claimed = [s.claim_profile(f"R{i}", "R") for i in range(228)]
    assert all(claimed) and s.claim_profile("Rextra", "R") is None      # exhausted
    s.release_profile("R0")                                             # frees one
    assert s.claim_profile("Rnew", "R") is not None
