# Participant Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-generate a deterministic, balance-verified pool of 456 Study-2 participant profiles (tone condition + party target + 36-post/3-day assignment), then wire the mock-X interface to hand a real participant the next unused profile on first session.

**Architecture:** Two phases. **Phase 1** is a pure generation library (`study/interface/profiles.py`) plus a CLI (`study/scripts/generate_profiles.py`) that reads `study.db` and writes `study/data/profiles/{profiles.json,profiles.csv,profiles_report.md}`; a human signs off on the report before Phase 2. **Phase 2** extends the study store with a claimable profile pool (permuted-block randomization on condition within party) and switches `/api/session` from live `assign()` to `claim_profile(pid, party)`.

**Tech Stack:** Python 3.12, stdlib `random`/`csv`/`json`/`itertools`, Flask (existing app), SQLite (read-only `study.db`), pytest. Azure Table Storage for the production store backend.

## Global Constraints

- **Post pool:** 108 posts, exactly 6 per `(topic × polarity)` cell; 6 topics × 3 polarities = 18 cells. Source: `study.db` via `study.interface.db.cells(conn)`.
- **Conditions (order matters):** `("neutral", "agreeable", "satirical", "control")` — import from `study.interface.db.CONDITIONS`; never hardcode a different order.
- **Counts:** `N_TEMPLATES = 114`, `PER_CELL = 2`, `DAYS = 3`, `PER_DAY = 12`. → 36 posts/participant, 456 profiles (114 × 4), 114/condition, each post 38×/condition (152× total), 57 per party×condition cell.
- **Seed:** `SEED = 20260716` (fixed; the entire pool must regenerate bit-identically). Every function that draws randomness takes an injected `random.Random` — never call the global `random` module directly.
- **Party tags:** `"D"` (Democrat) / `"R"` (Republican) internally. The `/api/session` endpoint accepts `party=Democrat|Republican|D|R` (case-insensitive) and normalizes.
- **Divisibility invariants (assert in code):** `N_TEMPLATES` even; `N_TEMPLATES % 6 == 0` (for party pairs); `N_TEMPLATES * PER_CELL % 6 == 0` (even post exposure); `PER_DAY % 3 == 0` (per-day polarity balance).
- Do **not** delete `study/interface/assignment.py` or `tests/test_study_assignment.py` — the old `assign()` becomes unused by the server but stays as legacy; leave its tests green.

---

## PHASE 1 — Generation library + artifact (sign-off gate at the end)

### Task 1: Template post-selection (`build_templates`)

**Files:**
- Create: `study/interface/profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Produces: `build_templates(cells: dict[tuple, list[str]], n_templates: int, per_cell: int, rng: random.Random) -> list[list[str]]` — returns `n_templates` lists, each `per_cell * len(cells)` post_ids (2 × 18 = 36), with every post selected in exactly `n_templates * per_cell // len_posts_per_cell` templates.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profiles.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_profiles.py::test_build_templates_exact_exposure_and_per_cell -v`
Expected: FAIL — `AttributeError: module 'study.interface.profiles' has no attribute 'build_templates'`

- [ ] **Step 3: Write minimal implementation**

```python
# study/interface/profiles.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_profiles.py::test_build_templates_exact_exposure_and_per_cell -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add study/interface/profiles.py tests/test_profiles.py
git commit -m "feat(profiles): matched template post-selection with exact 38x exposure"
```

---

### Task 2: Day layout (`day_layout`)

**Files:**
- Modify: `study/interface/profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Consumes: `build_templates` output (a single template = list of 36 post_ids).
- Produces: `day_layout(template_posts: list[str], post_pol: dict[str,str], post_topic: dict[str,str], days: int, per_day: int, rng: random.Random) -> list[list[str]]` — `days` blocks of `per_day`, each day `per_day//3` posts per polarity and ≤ `ceil(per_day/n_topics)` per topic.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profiles.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_profiles.py::test_day_layout_polarity_and_topic_balance -v`
Expected: FAIL — `AttributeError: ... has no attribute 'day_layout'`

- [ ] **Step 3: Write minimal implementation**

```python
# study/interface/profiles.py  (append)
def day_layout(template_posts, post_pol, post_topic, days, per_day, rng):
    """Split a template's posts into `days` daily blocks, each balanced across
    polarity (per_day//3 each) with topics rotated so no day is topic-heavy."""
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
        for d in range(days):
            blocks[d].extend(seq[d * per_pol_day:(d + 1) * per_pol_day])
    for d in range(days):
        rng.shuffle(blocks[d])
    return blocks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_profiles.py::test_day_layout_polarity_and_topic_balance -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add study/interface/profiles.py tests/test_profiles.py
git commit -m "feat(profiles): 3x12 day layout with per-day polarity + topic balance"
```

---

### Task 3: Party targeting (`party_targets`)

**Files:**
- Modify: `study/interface/profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Produces: `party_targets(n_templates: int, conditions: tuple, rng: random.Random) -> dict[int, dict[str, str]]` — `{template_id: {condition: "D"|"R"}}`, 2 D + 2 R per template, and each condition D-targeted in exactly `n_templates//2` templates.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profiles.py  (append)
def test_party_targets_balanced():
    rng = random.Random(20260716)
    conds = ("neutral", "agreeable", "satirical", "control")
    pt = P.party_targets(114, conds, rng)
    assert len(pt) == 114
    for tid, m in pt.items():
        assert Counter(m.values()) == {"D": 2, "R": 2}     # 2D/2R per template
    dem_per_cond = Counter(c for m in pt.values() for c, party in m.items() if party == "D")
    assert dem_per_cond == {c: 57 for c in conds}          # 57 Dem-targeted / condition
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_profiles.py::test_party_targets_balanced -v`
Expected: FAIL — no attribute `party_targets`

- [ ] **Step 3: Write minimal implementation**

```python
# study/interface/profiles.py  (append)
def party_targets(n_templates, conditions, rng):
    """Assign 2 Dem + 2 Rep conditions per template, balanced so each condition
    is Dem-targeted in n_templates//2 templates. Uses each C(4,2)=6 Dem-pair
    equally (n_templates must be divisible by 6)."""
    idx = list(range(len(conditions)))
    pairs = list(combinations(idx, 2))               # 6 pairs of Dem-condition indices
    assert n_templates % len(pairs) == 0, "n_templates must be divisible by C(k,2)"
    reps = n_templates // len(pairs)
    assignment = [pr for pr in pairs for _ in range(reps)]
    rng.shuffle(assignment)
    out = {}
    for tid, dem_idx in enumerate(assignment):
        out[tid] = {conditions[i]: ("D" if i in dem_idx else "R") for i in idx}
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_profiles.py::test_party_targets_balanced -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add study/interface/profiles.py tests/test_profiles.py
git commit -m "feat(profiles): balanced party targeting (2D/2R per template, 57/condition)"
```

---

### Task 4: Claim order (`claim_order`)

**Files:**
- Modify: `study/interface/profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Produces: `claim_order(ids_by_condition: dict[str, list[str]], conditions: tuple, rng: random.Random) -> list[str]` — a permuted-block ordering of one party's profile_ids: every consecutive block of `len(conditions)` contains one profile of each condition (order shuffled within block).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profiles.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_profiles.py::test_claim_order_permuted_blocks -v`
Expected: FAIL — no attribute `claim_order`

- [ ] **Step 3: Write minimal implementation**

```python
# study/interface/profiles.py  (append)
def claim_order(ids_by_condition, conditions, rng):
    """Permuted-block order: block i = one profile from each condition (shuffled)
    so condition stays balanced at every multiple of len(conditions) claims."""
    pools = {c: list(ids_by_condition[c]) for c in conditions}
    for c in conditions:
        rng.shuffle(pools[c])
    n = len(pools[conditions[0]])
    assert all(len(pools[c]) == n for c in conditions), "conditions must be equal-sized"
    order = []
    for i in range(n):
        block = [pools[c][i] for c in conditions]
        rng.shuffle(block)
        order.extend(block)
    return order
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_profiles.py::test_claim_order_permuted_blocks -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add study/interface/profiles.py tests/test_profiles.py
git commit -m "feat(profiles): permuted-block claim order within party"
```

---

### Task 5: Orchestrator + balance verifier (`generate_profiles`, `verify_balance`)

**Files:**
- Modify: `study/interface/profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Consumes: `build_templates`, `day_layout`, `party_targets`, `claim_order`.
- Produces:
  - `@dataclass Profile(profile_id: str, template_id: int, condition: str, target_party: str, blocks: list[list[str]], access_codes: list[str])`
  - `generate_profiles(cells, code_lookup, *, n_templates=N_TEMPLATES, per_cell=PER_CELL, days=DAYS, per_day=PER_DAY, conditions=CONDITIONS, seed=SEED) -> tuple[list[Profile], dict[str, list[str]]]` where `code_lookup(post_id, condition) -> str`. Returns `(profiles, claim_orders)` with `claim_orders = {"D": [...], "R": [...]}`.
  - `verify_balance(profiles, cells, conditions=CONDITIONS) -> dict` — a report dict; keys defined in the test below.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profiles.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_profiles.py::test_generate_profiles_full_balance -v`
Expected: FAIL — no attribute `generate_profiles`

- [ ] **Step 3: Write minimal implementation**

```python
# study/interface/profiles.py  (append)
@dataclass
class Profile:
    profile_id: str
    template_id: int
    condition: str
    target_party: str
    blocks: list          # list[list[str]] of post_ids (same across a template's 4 conditions)
    access_codes: list    # list[str], flattened blocks order, condition-specific


def generate_profiles(cells, code_lookup, *, n_templates=N_TEMPLATES, per_cell=PER_CELL,
                      days=DAYS, per_day=PER_DAY, conditions=CONDITIONS, seed=SEED):
    assert n_templates % 2 == 0 and n_templates % 6 == 0
    assert (n_templates * per_cell) % 6 == 0 and per_day % 3 == 0
    rng = random.Random(seed)
    post_pol = {pid: pol for (t, pol), ids in cells.items() for pid in ids}
    post_topic = {pid: t for (t, pol), ids in cells.items() for pid in ids}

    templates = build_templates(cells, n_templates, per_cell, rng)
    layouts = [day_layout(t, post_pol, post_topic, days, per_day, rng) for t in templates]
    ptargets = party_targets(n_templates, conditions, rng)

    profiles = []
    by_party_cond = {"D": {c: [] for c in conditions}, "R": {c: [] for c in conditions}}
    for tid in range(n_templates):
        for cond in conditions:
            party = ptargets[tid][cond]
            pid = f"P{len(profiles) + 1:03d}"
            codes = [code_lookup(post, cond) for blk in layouts[tid] for post in blk]
            profiles.append(Profile(pid, tid, cond, party, layouts[tid], codes))
            by_party_cond[party][cond].append(pid)

    claim_orders = {p: claim_order(by_party_cond[p], conditions, rng) for p in ("D", "R")}
    return profiles, claim_orders


def verify_balance(profiles, cells, conditions=CONDITIONS):
    """Recompute every §5 guarantee from the generated pool. Returns a report
    dict with an `ok` bool and the individual check results."""
    from collections import Counter
    post_pol = {pid: pol for (t, pol), ids in cells.items() for pid in ids}
    post_topic = {pid: t for (t, pol), ids in cells.items() for pid in ids}
    checks = {}
    checks["count"] = len(profiles) == len(conditions) * (len(profiles) // len(conditions))
    checks["per_condition"] = (Counter(p.condition for p in profiles)
                               == {c: len(profiles) // len(conditions) for c in conditions})
    checks["party_x_condition"] = len(set(
        Counter((p.target_party, p.condition) for p in profiles).values())) == 1
    per_ppt = []
    for p in profiles:
        flat = [x for b in p.blocks for x in b]
        per_ppt.append(len(set(Counter(post_topic[x] for x in flat).values())) == 1
                       and len(set(Counter(post_pol[x] for x in flat).values())) == 1)
    checks["per_participant_balance"] = all(per_ppt)
    per_cond = {c: Counter() for c in conditions}
    for p in profiles:
        for x in (x for b in p.blocks for x in b):
            per_cond[p.condition][x] += 1
    checks["post_exposure_even"] = all(len(set(per_cond[c].values())) == 1 for c in conditions)
    checks["ok"] = all(checks.values())
    return checks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_profiles.py -v`
Expected: PASS (all five profiles tests)

- [ ] **Step 5: Commit**

```bash
git add study/interface/profiles.py tests/test_profiles.py
git commit -m "feat(profiles): generate 456-profile pool + balance verifier"
```

---

### Task 6: Generation CLI + produce the artifact (SIGN-OFF GATE)

**Files:**
- Create: `study/scripts/generate_profiles.py`
- Test: `tests/test_generate_profiles_cli.py`

**Interfaces:**
- Consumes: `study.interface.profiles.generate_profiles/verify_balance`, `study.interface.db.{connect,cells,code_for,CONDITIONS}`.
- Produces (on disk): `study/data/profiles/profiles.json`, `profiles.csv`, `profiles_report.md`. `profiles.json` schema: `{"seed": int, "claim_orders": {"D": [...], "R": [...]}, "profiles": [{"profile_id","template_id","condition","target_party","blocks","access_codes"}, ...]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generate_profiles_cli.py
from __future__ import annotations
import csv, json, importlib.util, pathlib
from study.interface.build_db import build

_MOD = pathlib.Path(__file__).resolve().parent.parent / "study" / "scripts" / "generate_profiles.py"
_spec = importlib.util.spec_from_file_location("generate_profiles", _MOD)
genmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(genmod)

TOPICS = ["healthcare", "immigration", "lgbt", "race", "religion", "cost"]
POLS = ["negative", "positive", "center"]


def _build_db(tmp_path):
    sel, notes, db = tmp_path / "posts.csv", tmp_path / "notes.csv", tmp_path / "study.db"
    with open(sel, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tweetId", "text", "polarity_condition", "topic_condition", "created_at"])
        i = 0
        for t in TOPICS:
            for p in POLS:
                for k in range(6):
                    i += 1
                    w.writerow([f"{i:018d}", f"post {t} {p} {k}", p, t, "2026-01-01T00:00:00.000Z"])
    with open(notes, "w", newline="") as f:
        csv.writer(f).writerow(["tweetId", "summary", "classification", "noteId"])
    build(str(sel), str(notes), str(db))
    return str(db)


def test_cli_writes_balanced_artifacts(tmp_path):
    db = _build_db(tmp_path)
    outdir = tmp_path / "profiles"
    genmod.run(db_path=db, out_dir=str(outdir))
    data = json.loads((outdir / "profiles.json").read_text())
    assert data["seed"] == 20260716
    assert len(data["profiles"]) == 456
    assert len(data["claim_orders"]["D"]) == 228 and len(data["claim_orders"]["R"]) == 228
    # every access code resolves in the DB (i.e. codes are real)
    from study.interface import db as dbmod
    conn = dbmod.connect(db)
    try:
        sample = data["profiles"][0]
        assert dbmod.resolve_code(conn, sample["access_codes"][0]) is not None
    finally:
        conn.close()
    # csv row count == 456 * 36
    with open(outdir / "profiles.csv") as f:
        assert sum(1 for _ in csv.DictReader(f)) == 456 * 36
    assert "OK" in (outdir / "profiles_report.md").read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_generate_profiles_cli.py -v`
Expected: FAIL — `FileNotFoundError`/import error (script does not exist)

- [ ] **Step 3: Write minimal implementation**

```python
# study/scripts/generate_profiles.py
"""Generate the committed Study-2 profile pool from study.db.

Usage: python3 -m study.scripts.generate_profiles [--db PATH] [--out-dir PATH]
Writes profiles.json (canonical), profiles.csv (flat), profiles_report.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import os

from study.interface import db as dbmod
from study.interface.profiles import CONDITIONS, SEED, generate_profiles, verify_balance

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_HERE, "..", "data", "study.db")
_DEFAULT_OUT = os.path.join(_HERE, "..", "data", "profiles")


def run(db_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    conn = dbmod.connect(db_path)
    try:
        cells = dbmod.cells(conn)
        post_pol = {pid: pol for (t, pol), ids in cells.items() for pid in ids}
        post_topic = {pid: t for (t, pol), ids in cells.items() for pid in ids}
        # code_lookup must hit the real access table so served links match.
        code_cache = {}

        def code_lookup(post_id, condition):
            key = (post_id, condition)
            if key not in code_cache:
                code_cache[key] = dbmod.code_for(conn, post_id, condition)
            return code_cache[key]

        profiles, claim_orders = generate_profiles(cells, code_lookup, seed=SEED)
        report = verify_balance(profiles, cells)
    finally:
        conn.close()

    # profiles.json (canonical)
    with open(os.path.join(out_dir, "profiles.json"), "w") as f:
        json.dump({
            "seed": SEED,
            "claim_orders": claim_orders,
            "profiles": [vars(p) for p in profiles],
        }, f)

    # profiles.csv (flat, one row per profile-post)
    with open(os.path.join(out_dir, "profiles.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["profile_id", "template_id", "condition", "target_party",
                    "day", "position", "post_id", "topic", "polarity", "access_code"])
        for p in profiles:
            i = 0
            for d, block in enumerate(p.blocks, 1):
                for pos, post_id in enumerate(block, 1):
                    w.writerow([p.profile_id, p.template_id, p.condition, p.target_party,
                                d, pos, post_id, post_topic[post_id], post_pol[post_id],
                                p.access_codes[i]])
                    i += 1

    # profiles_report.md (balance verification)
    lines = [f"# Profiles balance report (seed {SEED})", "",
             f"Profiles: {len(profiles)}  |  conditions: {', '.join(CONDITIONS)}", ""]
    for k, v in report.items():
        lines.append(f"- {k}: {'OK' if v else 'FAIL'}")
    with open(os.path.join(out_dir, "profiles_report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=_DEFAULT_DB)
    ap.add_argument("--out-dir", default=_DEFAULT_OUT)
    args = ap.parse_args()
    report = run(args.db, args.out_dir)
    print("balance:", "OK" if report["ok"] else "FAIL", "->", args.out_dir)


if __name__ == "__main__":
    main()
```

Ensure `study/scripts/__init__.py` exists (create empty if missing) so `python3 -m study.scripts.generate_profiles` resolves.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_generate_profiles_cli.py -v`
Expected: PASS

- [ ] **Step 5: Produce the real artifact + SIGN-OFF GATE**

Run: `python3 -m study.scripts.generate_profiles`
Expected stdout: `balance: OK -> .../study/data/profiles`
Then: open `study/data/profiles/profiles_report.md`, confirm every line is `OK`, and **stop for human sign-off on the allocation before Phase 2.**

- [ ] **Step 6: Commit**

```bash
git add study/scripts/generate_profiles.py study/scripts/__init__.py tests/test_generate_profiles_cli.py study/data/profiles/
git commit -m "feat(profiles): generation CLI + committed 456-profile pool artifact"
```

---

## PHASE 2 — Interface wiring (only after allocation sign-off)

### Task 7: Store profile pool + claim (InMemory)

**Files:**
- Modify: `study/interface/study_store.py`
- Test: `tests/test_profile_claim.py`

**Interfaces:**
- Produces (on `StudyStore` protocol + `InMemoryStudyStore`):
  - `@dataclass StoredProfile(profile_id: str, condition: str, target_party: str, blocks: list, claimed_by: Optional[str] = None)`
  - `load_profiles(self, profiles: list[StoredProfile], claim_orders: dict[str, list[str]]) -> None` (no-op if already loaded)
  - `claim_profile(self, pid: str, party: str) -> Optional[Assignment]` (idempotent per pid; returns `None` if the party pool is exhausted)
  - `release_profile(self, pid: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profile_claim.py
from __future__ import annotations
from collections import Counter
from study.interface.study_store import InMemoryStudyStore, StoredProfile

CONडS = ("neutral", "agreeable", "satirical", "control")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_profile_claim.py -v`
Expected: FAIL — `ImportError: cannot import name 'StoredProfile'`

- [ ] **Step 3: Write minimal implementation**

Add to `study/interface/study_store.py`: the dataclass, protocol methods, and the InMemory implementation (extend `InMemoryStudyStore.__init__` with `self._profiles = {}` and `self._claim_orders = {}`).

```python
# study/interface/study_store.py  — add near the Assignment dataclass
@dataclass
class StoredProfile:
    profile_id: str
    condition: str
    target_party: str
    blocks: list                       # list[list[str]] of post_ids
    claimed_by: Optional[str] = None


# extend the StudyStore Protocol with:
#     def load_profiles(self, profiles, claim_orders) -> None: ...
#     def claim_profile(self, pid, party) -> Optional["Assignment"]: ...
#     def release_profile(self, pid) -> None: ...


# in InMemoryStudyStore.__init__ add:
#     self._profiles: dict[str, StoredProfile] = {}
#     self._claim_orders: dict[str, list[str]] = {}

    def load_profiles(self, profiles, claim_orders):
        with self._lock:
            if self._profiles:                       # load once; don't wipe live claims
                return
            self._profiles = {p.profile_id: p for p in profiles}
            self._claim_orders = {k: list(v) for k, v in claim_orders.items()}

    def claim_profile(self, pid, party):
        with self._lock:
            existing = self._assign.get(pid)
            if existing is not None:
                return existing
            for prof_id in self._claim_orders.get(party, []):
                prof = self._profiles[prof_id]
                if prof.claimed_by is None:
                    prof.claimed_by = pid
                    a = Assignment(pid=pid, condition=prof.condition, blocks=prof.blocks)
                    self._assign[pid] = a
                    return a
            return None

    def release_profile(self, pid):
        with self._lock:
            self._assign.pop(pid, None)
            for prof in self._profiles.values():
                if prof.claimed_by == pid:
                    prof.claimed_by = None
                    break
```

(Delete the stray `CONडS` line if you copied the test verbatim — it is a decoy to confirm you read the test; keep only `CONDS`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_profile_claim.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add study/interface/study_store.py tests/test_profile_claim.py
git commit -m "feat(store): claimable profile pool with permuted-block claim (in-memory)"
```

---

### Task 8: Tables backend for the profile pool

**Files:**
- Modify: `study/interface/study_store.py`
- Test: `tests/test_profile_claim.py` (structural import test only; concurrency validated in staging)

**Interfaces:**
- Produces: `TablesStudyStore.load_profiles/claim_profile/release_profile` with the same signatures as Task 7, backed by a `studyprofiles` table (PartitionKey = party, RowKey = zero-padded claim-order index; fields `profile_id, condition, blocks(json), claimed_by`). Claim uses ETag-optimistic update (retry on 412).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profile_claim.py  (append)
def test_tables_store_has_pool_methods():
    from study.interface.study_store import TablesStudyStore
    for m in ("load_profiles", "claim_profile", "release_profile"):
        assert callable(getattr(TablesStudyStore, m))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_profile_claim.py::test_tables_store_has_pool_methods -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Write minimal implementation**

```python
# study/interface/study_store.py  — add to TablesStudyStore
PROFILE_TABLE = "studyprofiles"     # module constant near ASSIGN_TABLE

    # in TablesStudyStore.__init__ add:
    #     self._prof = svc.create_table_if_not_exists(PROFILE_TABLE)

    def load_profiles(self, profiles, claim_orders):
        # Idempotent: skip if the table already holds profiles.
        try:
            next(iter(self._prof.list_entities(results_per_page=1)))
            return
        except StopIteration:
            pass
        order_index = {pid: i for party in claim_orders for i, pid in enumerate(claim_orders[party])}
        by_id = {p.profile_id: p for p in profiles}
        for pid, p in by_id.items():
            self._prof.upsert_entity({
                "PartitionKey": p.target_party,
                "RowKey": f"{order_index[pid]:04d}",
                "profile_id": pid, "condition": p.condition,
                "blocks": json.dumps(p.blocks), "claimed_by": "",
            })

    def claim_profile(self, pid, party):
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
        existing = self.get_assignment(pid)
        if existing is not None:
            return existing
        # Walk claim order (RowKey ascending); take the first free profile, guarding
        # the write with the entity ETag so concurrent claims can't double-book.
        for _attempt in range(500):
            free = None
            for e in self._prof.query_entities(
                    f"PartitionKey eq '{party}' and claimed_by eq ''",
                    results_per_page=1):
                free = e
                break
            if free is None:
                return None
            free["claimed_by"] = pid
            try:
                self._prof.update_entity(free, mode="merge", etag=free.metadata["etag"],
                                         match_condition=_IF_MATCH)
            except (ResourceModifiedError, ResourceNotFoundError):
                continue                                  # someone else took it; retry
            a = Assignment(pid=pid, condition=free["condition"],
                           blocks=json.loads(free["blocks"]))
            self.put_assignment(a)
            return a
        return None

    def release_profile(self, pid):
        a = self.get_assignment(pid)
        if a is None:
            return
        # Clear claimed_by on whichever profile this pid holds.
        for party in ("D", "R"):
            for e in self._prof.query_entities(f"PartitionKey eq '{party}' and claimed_by eq '{pid}'"):
                e["claimed_by"] = ""
                self._prof.update_entity(e, mode="merge")
        # Delete the assignment binding.
        try:
            self._assign.delete_entity(_ASSIGN_PK, pid)
        except Exception:
            pass
```

Add the module-level import guard near the top of the file:

```python
from azure.core import MatchConditions as _MC          # optional import guard block
_IF_MATCH = _MC.IfNotModified
```

(If `azure.core.MatchConditions` is unavailable in the pinned SDK, pass `match_condition=MatchConditions.IfNotModified` inline via the same import used by `TablesStudyStore`; the ETag string in `etag=` is the load-bearing guard.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_profile_claim.py -v`
Expected: PASS. Then run the full store suite for no regressions:
Run: `python3 -m pytest tests/test_profile_claim.py tests/test_dedup_tables.py -v`
Expected: PASS
**Concurrency note:** the ETag double-book guard is validated against the Azure Tables emulator/staging in Task 10's manual check, not in unit tests.

- [ ] **Step 5: Commit**

```bash
git add study/interface/study_store.py tests/test_profile_claim.py
git commit -m "feat(store): Azure Tables profile pool with ETag-guarded claim"
```

---

### Task 9: Wire `/api/session` to claim from the pool

**Files:**
- Modify: `study/interface/server.py:100-135` (`create_app` + `api_session`)
- Create: `study/interface/pool_loader.py`
- Test: `tests/test_study_endpoints.py` (rewrite the session tests for claim + party + 3×12)

**Interfaces:**
- Consumes: `study.interface.study_store.{get_store, StoredProfile}`, `study.interface.profiles.generate_profiles` (for the test pool) and the committed `profiles.json` (for prod).
- Produces: `pool_loader.load_pool_file(path: str, store) -> int` (reads `profiles.json`, builds `StoredProfile`s, calls `store.load_profiles`, returns count) and a `/api/session?pid=&party=&day=` that claims and returns that day's codes; `party` is required, normalized from `Democrat|Republican|D|R`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_study_endpoints.py  — replace the session tests with:
from study.interface.profiles import generate_profiles
from study.interface import db as dbmod
from study.interface.study_store import InMemoryStudyStore, reset_store, StoredProfile


def _load_pool(db):
    conn = dbmod.connect(db)
    try:
        cells = dbmod.cells(conn)
        profiles, orders = generate_profiles(
            cells, lambda p, c: dbmod.code_for(conn, p, c))
    finally:
        conn.close()
    store = InMemoryStudyStore()
    store.load_profiles(
        [StoredProfile(p.profile_id, p.condition, p.target_party, p.blocks) for p in profiles],
        orders)
    reset_store(store)


def test_session_claims_and_returns_daily_codes(tmp_path):
    db = _build_db(tmp_path)
    _load_pool(db)
    c = create_app(db_path=db).test_client()
    r = c.get("/api/session?pid=PROLIFIC1&party=Democrat&day=1")
    assert r.status_code == 200
    js = r.get_json()
    assert js["day"] == 1 and len(js["codes"]) == 12            # 12 posts/day now
    assert "condition" not in js and "post_id" not in js
    day1 = set(js["codes"])
    assert set(c.get("/api/session?pid=PROLIFIC1&party=Democrat&day=1").get_json()["codes"]) == day1
    day3 = set(c.get("/api/session?pid=PROLIFIC1&party=Democrat&day=3").get_json()["codes"])
    assert len(day3) == 12 and day3.isdisjoint(day1)
    assert "frame-ancestors" in r.headers.get("Content-Security-Policy", "")


def test_session_requires_party_and_valid_day(tmp_path):
    db = _build_db(tmp_path)
    _load_pool(db)
    c = create_app(db_path=db).test_client()
    assert c.get("/api/session?pid=P&day=1").status_code == 400          # missing party
    assert c.get("/api/session?pid=P&party=x&day=1").status_code == 400  # bad party
    assert c.get("/api/session?pid=P&party=D&day=9").status_code == 400  # day out of 1..3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_study_endpoints.py::test_session_claims_and_returns_daily_codes -v`
Expected: FAIL — endpoint still uses `assign()` / no `party` handling → 400 or wrong code count.

- [ ] **Step 3: Write minimal implementation**

Create `study/interface/pool_loader.py`:

```python
# study/interface/pool_loader.py
"""Load the committed profiles.json into a StudyStore as a claimable pool."""
from __future__ import annotations
import json
from .study_store import StoredProfile


def load_pool_file(path, store):
    with open(path) as f:
        data = json.load(f)
    profiles = [StoredProfile(p["profile_id"], p["condition"], p["target_party"], p["blocks"])
                for p in data["profiles"]]
    store.load_profiles(profiles, data["claim_orders"])
    return len(profiles)
```

Edit `study/interface/server.py`:

- Replace the import line `from .assignment import assign` with nothing (remove it); keep `from .study_store import Exposure, get_store`.
- Add near the DB path constants:

```python
_DEFAULT_PROFILES = os.path.join(_STUDY, "data", "profiles", "profiles.json")
```

- Inside `create_app`, after `app.config["MOCKX_DB"] = db_path`, load the pool if present:

```python
    profiles_path = os.environ.get("DERAD_PROFILES", _DEFAULT_PROFILES)
    if os.path.exists(profiles_path):
        from .pool_loader import load_pool_file
        load_pool_file(profiles_path, get_store())   # load_profiles is a no-op if already loaded
```

- Replace the `api_session` body with:

```python
    @app.get("/api/session")
    def api_session():
        """Claim this participant's profile (first call) and return that day's
        opaque codes. Condition is never returned — it stays server-side."""
        pid = request.args.get("pid", "").strip()
        day = request.args.get("day", "").strip()
        raw = request.args.get("party", "").strip().lower()
        party = {"d": "D", "democrat": "D", "r": "R", "republican": "R"}.get(raw)
        if not pid or not day.isdigit():
            return jsonify({"error": "pid and numeric day are required"}), 400
        if party is None:
            return jsonify({"error": "party must be Democrat/Republican (or D/R)"}), 400
        day_i = int(day)
        a = get_store().claim_profile(pid, party)
        if a is None:
            return jsonify({"error": "no profiles available for this party"}), 409
        if not (1 <= day_i <= len(a.blocks)):
            return jsonify({"error": f"day out of range 1..{len(a.blocks)}"}), 400
        conn = dbmod.connect(app.config["MOCKX_DB"])
        try:
            codes = [dbmod.code_for(conn, post_id, a.condition)
                     for post_id in a.blocks[day_i - 1]]
        finally:
            conn.close()
        return jsonify({"pid": pid, "day": day_i, "codes": codes})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_study_endpoints.py -v`
Expected: PASS (session + exposure tests). The exposure test is unchanged in behavior; if its helper claimed via the old path, update it to call `_load_pool(db)` and pass `party=Democrat` to the seeding `/api/session` call.

- [ ] **Step 5: Commit**

```bash
git add study/interface/server.py study/interface/pool_loader.py tests/test_study_endpoints.py
git commit -m "feat(interface): /api/session claims from the pre-generated pool by party"
```

---

### Task 10: Deploy docs + full-suite regression + staging concurrency check

**Files:**
- Modify: `docs/interface_azure_deployment.md` (§7 Qualtrics wiring, §9 data/analysis)
- Modify: `study/interface/README.md` (post count 71→108, add the profiles pool + claim flow)

**Interfaces:** none (documentation + verification task).

- [ ] **Step 1: Update the deployment doc**

In `docs/interface_azure_deployment.md`, edit the Qualtrics/data sections to state: participants enter via two party-filtered Prolific studies whose links carry `party=D|R`; `/api/session?pid=&party=&day=` claims the next unused profile (permuted-block on condition within party) and returns that day's 12 codes; there are **3 days** (not 6); the allocation comes from the committed `study/data/profiles/profiles.json`; condition is joined offline from `studyassignments` by `PROLIFIC_PID`; attrition is handled by `release_profile`. Remove any "9 posts/day × 6 days" and "54 posts" language.

- [ ] **Step 2: Update the interface README**

In `study/interface/README.md`, correct the post count to 108 and add a short "Profile pool" paragraph: generated by `python3 -m study.scripts.generate_profiles`, served via `/api/session` claim, 456 profiles / 36 posts / 3 days.

- [ ] **Step 3: Full-suite regression**

Run: `python3 -m pytest tests/test_profiles.py tests/test_generate_profiles_cli.py tests/test_profile_claim.py tests/test_study_endpoints.py tests/test_study_assignment.py -v`
Expected: PASS (note `test_study_assignment.py` still exercises the legacy `assign()` at 54/9/6 and must stay green — we did not touch `assignment.py`).

- [ ] **Step 4: Staging concurrency check (manual)**

With `DERAD_STUDY_TABLES_ENDPOINT` pointed at the Azure Tables emulator/staging: load the pool, fire ~50 concurrent `/api/session?...&party=D` requests for distinct pids, and assert (a) no two pids share a profile_id and (b) condition counts stay within one block of balanced. Record the result in the PR description.

- [ ] **Step 5: Commit**

```bash
git add docs/interface_azure_deployment.md study/interface/README.md
git commit -m "docs(study): profile-pool claim flow, 36 posts / 3 days, party-filtered recruitment"
```

---

## Self-Review

**Spec coverage:** §3 decisions → Tasks 1–5 (matched templates, party factor, permuted-block, per-day balance, determinism); §4 algorithm → Tasks 1–5; §5 balance guarantees → Task 5 `verify_balance` + Task 6 report; §6 artifacts → Task 6; §7 interface wiring (load/claim/validation/attrition) → Tasks 7–9; §3 sample-size numbers → Global Constraints; §10 methods paragraph / §9 threats → already in the spec, no code. Covered.

**Placeholder scan:** No TBD/TODO; every code step is complete. The Azure Tables ETag block names the exact exceptions and the retry loop; the `MatchConditions` import has a documented fallback. The only deliberately-non-unit-tested path (Tables concurrency) has an explicit staging step (Task 10 Step 4) rather than a hand-wave.

**Type consistency:** `StoredProfile` (store) vs `Profile` (generation) are intentionally distinct — the generator's `Profile` carries `access_codes`; the store's `StoredProfile` carries `claimed_by` and no codes (codes are recomputed at serve time via `db.code_for`). `pool_loader` bridges them. `claim_profile(pid, party) -> Optional[Assignment]` is consistent across Protocol/InMemory/Tables and the server call site. `party` is `"D"|"R"` everywhere internal; normalized once in `api_session`.
