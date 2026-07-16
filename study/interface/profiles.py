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


def day_layout(template_posts, post_pol, post_topic, days, per_day, rng):
    """Split a template's posts into `days` daily blocks, each balanced across
    polarity (per_day//3 each) with topics rotated so no day is topic-heavy.

    Precondition: each polarity must contribute exactly days * (per_day // 3) posts.
    Fails loudly if a polarity has more posts (extras would be silently dropped)."""
    topics = sorted({post_topic[p] for p in template_posts})
    n_topics = len(topics)
    per_pol_day = per_day // 3
    by_pol = {}
    for p in template_posts:
        by_pol.setdefault(post_pol[p], []).append(p)
    blocks = [[] for _ in range(days)]
    for k, pol in enumerate(sorted(by_pol)):
        by_topic = {}
        for p in by_pol[pol]:
            by_topic.setdefault(post_topic[p], []).append(p)
        rot = (per_pol_day * k) % n_topics          # rotate topic order per polarity
        ordered = topics[rot:] + topics[:rot]
        seq = []
        depth = max(len(v) for v in by_topic.values())
        for i in range(depth):
            for t in ordered:
                if i < len(by_topic.get(t, [])):
                    seq.append(by_topic[t][i])
        assert len(seq) == days * per_pol_day, (
            f"day_layout: polarity has {len(seq)} posts, expected {days * per_pol_day} "
            f"(days*per_day//3) — would drop/duplicate posts")
        for d in range(days):
            blocks[d].extend(seq[d * per_pol_day:(d + 1) * per_pol_day])
    for d in range(days):
        rng.shuffle(blocks[d])
    return blocks
