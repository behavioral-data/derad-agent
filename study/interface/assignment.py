"""Participant assignment: pick a balanced condition and a stratified set of
posts, split into daily blocks. Idempotent per participant id.

Design (from the study protocol):
  - condition: one of neutral/agreeable/satirical/control, balanced across
    enrolled participants (least-used, ties broken randomly). Between-subjects:
    all of a participant's posts are shown in that one condition.
  - posts: 3 posts drawn from EACH (topic x polarity) cell -> with 6 topics x
    3 polarities x 6 posts, that is 18 cells x 3 = 54 posts, giving exactly
    9/topic and 18/polarity. Within a cell, the least-exposed posts (across
    prior participants) are favored so exposure stays even.
  - daily blocks: the 54 posts are shuffled and partitioned into `days` blocks
    of `per_day` (6 x 9 = 54), one block viewed per day.
"""
from __future__ import annotations

import random
from typing import Optional

from .db import CONDITIONS
from .study_store import Assignment, StudyStore


def assign(
    pid: str,
    store: StudyStore,
    cell_map: dict,
    *,
    conditions: tuple = CONDITIONS,
    per_cell: int = 3,
    days: int = 6,
    per_day: int = 9,
    rng: Optional[random.Random] = None,
) -> Assignment:
    """Return this participant's assignment, creating it on first call.

    `cell_map` is {(topic, polarity): [post_id, ...]} (see db.cells).
    """
    existing = store.get_assignment(pid)
    if existing is not None:
        return existing

    rng = rng or random
    prior = store.all_assignments()

    # Balance the condition across enrolled participants.
    cond_counts = {c: 0 for c in conditions}
    post_counts: dict[str, int] = {}
    for a in prior:
        if a.condition in cond_counts:
            cond_counts[a.condition] += 1
        for ppid in a.post_ids:
            post_counts[ppid] = post_counts.get(ppid, 0) + 1
    fewest = min(cond_counts.values())
    condition = rng.choice([c for c, n in cond_counts.items() if n == fewest])

    # Stratified pick: `per_cell` least-exposed posts from each cell.
    chosen: list[str] = []
    for _cell, posts in cell_map.items():
        ranked = sorted(posts, key=lambda p: (post_counts.get(p, 0), rng.random()))
        chosen.extend(ranked[:per_cell])

    # Partition into daily blocks.
    rng.shuffle(chosen)
    blocks = [chosen[i * per_day:(i + 1) * per_day] for i in range(days)]

    a = Assignment(pid=pid, condition=condition, blocks=blocks)
    store.put_assignment(a)
    return a
