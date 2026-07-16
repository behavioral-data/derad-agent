from __future__ import annotations
import random
from collections import Counter
from study.interface import profiles as P

TOPICS = ["healthcare", "immigration", "lgbt", "race", "religion", "cost"]
POLS = ["negative", "positive", "center"]
CELLS = {(t, p): [f"{t}|{p}|{i}" for i in range(6)] for t in TOPICS for p in POLS}
POST_POL = {pid: p for (t, p), ids in CELLS.items() for pid in ids}
POST_TOPIC = {pid: t for (t, p), ids in CELLS.items() for pid in ids}


def test_build_templates_exact_exposure_and_per_cell():
    rng = random.Random(20260716)
    templates = P.build_templates(CELLS, n_templates=114, per_cell=2, rng=rng)
    assert len(templates) == 114
    for tmpl in templates:
        assert len(tmpl) == 36
        # exactly 2 posts from each cell
        by_cell = Counter((POST_TOPIC[p], POST_POL[p]) for p in tmpl)
        assert set(by_cell.values()) == {2}
        assert len(by_cell) == 18
    # every post used exactly 114*2/6 = 38 times
    usage = Counter(p for tmpl in templates for p in tmpl)
    assert set(usage.values()) == {38}
    assert len(usage) == 108


def test_day_layout_polarity_and_topic_balance():
    rng = random.Random(1)
    templates = P.build_templates(CELLS, 114, 2, random.Random(20260716))
    for tmpl in templates[:10]:
        blocks = P.day_layout(tmpl, POST_POL, POST_TOPIC, days=3, per_day=12, rng=rng)
        assert len(blocks) == 3 and all(len(b) == 12 for b in blocks)
        # union of all days == the template (no loss/dupe)
        assert sorted(p for b in blocks for p in b) == sorted(tmpl)
        for day in blocks:
            pol = Counter(POST_POL[p] for p in day)
            assert set(pol.values()) == {4}          # 4/4/4 per polarity
            top = Counter(POST_TOPIC[p] for p in day)
            assert max(top.values()) <= 2            # topics spread, <=2/topic/day


def test_party_targets_balanced():
    rng = random.Random(20260716)
    conds = ("neutral", "agreeable", "satirical", "control")
    pt = P.party_targets(114, conds, rng)
    assert len(pt) == 114
    for tid, m in pt.items():
        assert Counter(m.values()) == {"D": 2, "R": 2}     # 2D/2R per template
    dem_per_cond = Counter(c for m in pt.values() for c, party in m.items() if party == "D")
    assert dem_per_cond == {c: 57 for c in conds}          # 57 Dem-targeted / condition


def test_claim_order_permuted_blocks():
    rng = random.Random(3)
    conds = ("neutral", "agreeable", "satirical", "control")
    cond_of = {}
    ids_by_cond = {}
    for c in conds:
        ids = [f"{c}-{i}" for i in range(57)]
        ids_by_cond[c] = ids
        for x in ids:
            cond_of[x] = c
    order = P.claim_order(ids_by_cond, conds, rng)
    assert len(order) == 228 and len(set(order)) == 228
    # each block of 4 has one of each condition
    for b in range(0, 228, 4):
        block = order[b:b + 4]
        assert Counter(cond_of[x] for x in block) == {c: 1 for c in conds}


def test_claim_order_requires_equal_pools():
    import pytest
    conds = ("neutral", "agreeable", "satirical", "control")
    bad = {"neutral": ["a", "b"], "agreeable": ["c"], "satirical": ["d", "e"], "control": ["f", "g"]}
    with pytest.raises(AssertionError):
        P.claim_order(bad, conds, random.Random(0))


def _code(post_id, condition):
    return f"{post_id}~{condition}"[:24]


def test_generate_profiles_full_balance():
    profiles, orders = P.generate_profiles(CELLS, _code, seed=20260716)
    conds = ("neutral", "agreeable", "satirical", "control")
    assert len(profiles) == 456
    assert Counter(p.condition for p in profiles) == {c: 114 for c in conds}
    # party x condition == 57
    assert Counter((p.target_party, p.condition) for p in profiles) == {
        (party, c): 57 for party in ("D", "R") for c in conds}
    # per participant: 36 posts, 6/topic, 12/polarity, 3x12 blocks
    for p in profiles:
        flat = [x for b in p.blocks for x in b]
        assert len(flat) == 36 and len(p.access_codes) == 36
        assert Counter(POST_TOPIC[x] for x in flat) == {t: 6 for t in TOPICS}
        assert Counter(POST_POL[x] for x in flat) == {pol: 12 for pol in POLS}
        assert len(p.blocks) == 3 and all(len(b) == 12 for b in p.blocks)
    # each post 38x per condition
    per_cond = {c: Counter() for c in conds}
    for p in profiles:
        for x in (x for b in p.blocks for x in b):
            per_cond[p.condition][x] += 1
    for c in conds:
        assert set(per_cond[c].values()) == {38} and len(per_cond[c]) == 108
    # claim orders cover every profile once, split by party
    assert len(orders["D"]) == 228 and len(orders["R"]) == 228
    assert set(orders["D"]) | set(orders["R"]) == {p.profile_id for p in profiles}
    # determinism
    again, _ = P.generate_profiles(CELLS, _code, seed=20260716)
    assert [p.profile_id for p in again] == [p.profile_id for p in profiles]
    assert [p.blocks for p in again] == [p.blocks for p in profiles]

    rep = P.verify_balance(profiles, CELLS)
    assert rep["ok"] is True


def test_verify_balance_detects_imbalance():
    import dataclasses
    profiles, _ = P.generate_profiles(CELLS, _code, seed=20260716)
    assert P.verify_balance(profiles, CELLS)["ok"] is True            # good pool passes
    all_dem = [dataclasses.replace(p, target_party="D") for p in profiles]
    assert P.verify_balance(all_dem, CELLS)["ok"] is False            # party imbalance caught
    two_templates = [p for p in profiles if p.template_id < 2]
    assert P.verify_balance(two_templates, CELLS)["ok"] is False      # under-generation caught
