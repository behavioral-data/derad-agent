"""Assignment engine: stratified 54-post draw, daily blocks, balanced condition,
even cross-participant exposure, idempotency."""
from __future__ import annotations

import random
from collections import Counter

from study.interface.assignment import assign
from study.interface.study_store import InMemoryStudyStore

TOPICS = ["healthcare", "immigration", "lgbt", "race", "religion", "cost"]
POLARITIES = ["negative", "positive", "center"]
# 6 topics x 3 polarities x 6 posts = 108, mirroring the real study set.
CELLS = {
    (t, p): [f"{t}|{p}|{i}" for i in range(6)]
    for t in TOPICS for p in POLARITIES
}
POST_TOPIC = {pid: t for (t, p), posts in CELLS.items() for pid in posts}
POST_POL = {pid: p for (t, p), posts in CELLS.items() for pid in posts}


def _assign(store, pid, seed=0):
    return assign(pid, store, CELLS, rng=random.Random(seed))


def test_strata_and_blocks():
    a = _assign(InMemoryStudyStore(), "P1")
    posts = a.post_ids
    assert len(posts) == 54
    assert len(set(posts)) == 54                       # all distinct
    assert Counter(POST_TOPIC[p] for p in posts) == {t: 9 for t in TOPICS}
    assert Counter(POST_POL[p] for p in posts) == {p: 18 for p in POLARITIES}
    assert len(a.blocks) == 6 and all(len(b) == 9 for b in a.blocks)


def test_idempotent_per_participant():
    store = InMemoryStudyStore()
    first = _assign(store, "P1")
    again = _assign(store, "P1", seed=999)   # different seed must not re-roll
    assert again.condition == first.condition
    assert again.blocks == first.blocks
    assert len(store.all_assignments()) == 1


def test_condition_balanced_within_one():
    store = InMemoryStudyStore()
    for i in range(40):
        assign(f"P{i}", store, CELLS, rng=random.Random(i))
    counts = Counter(a.condition for a in store.all_assignments())
    assert set(counts) == {"neutral", "agreeable", "satirical", "control"}
    assert max(counts.values()) - min(counts.values()) <= 1


def test_exposure_even_across_posts():
    store = InMemoryStudyStore()
    for i in range(12):
        assign(f"P{i}", store, CELLS, rng=random.Random(i))
    seen = Counter(p for a in store.all_assignments() for p in a.post_ids)
    # every post assigned; within each cell the 6 posts stay within 1 of each other
    assert len(seen) == 108
    for (t, p), posts in CELLS.items():
        cell_counts = [seen[pid] for pid in posts]
        assert max(cell_counts) - min(cell_counts) <= 1
