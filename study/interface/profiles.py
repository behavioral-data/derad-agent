"""Deterministic generation of the Study-2 participant-profile pool.

Pure functions (no I/O). Given the (topic x polarity) cell map they produce
114 matched 36-post templates, a 3x12 day layout per template, party targets,
a permuted-block claim order, and the 456 Profile objects. See
docs/superpowers/specs/2026-07-16-participant-profiles-design.md.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations

from .db import CONDITIONS

SEED = 20260716


def build_templates(cells, n_templates, per_cell, rng):
    """`n_templates` lists of post_ids, `per_cell` least-used posts per cell.
    Exact even exposure: n_templates*per_cell must be divisible by posts/cell."""
    usage = {pid: 0 for ids in cells.values() for pid in ids}
    templates = []
    for _ in range(n_templates):
        chosen = []
        for _cell, posts in cells.items():
            ranked = sorted(posts, key=lambda p: (usage[p], rng.random()))
            for p in ranked[:per_cell]:
                usage[p] += 1
                chosen.append(p)
        templates.append(chosen)
    return templates
