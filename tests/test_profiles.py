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
