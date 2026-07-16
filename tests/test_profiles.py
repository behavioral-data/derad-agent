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
