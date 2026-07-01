# Tweet Group-Misleadingness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce, per noted tweet, how *misleading* each of the two latent viewpoint groups (A/B) considers it — via Gaussian-kernel-smoothed endorsement of the tweet's misleading vs not-misleading notes.

**Architecture:** Three stages with a hard cache boundary. Stage 1+2 (`run_factors.py`, one read pass) fits a single Expansion matrix factorization for rater/note factors and emits a `ratings_with_factors.parquet`. Stage 3 (`aggregate.py`) is a cheap, re-runnable kernel-smoothing aggregator over that parquet producing `tweet_lean.tsv` / `note_lean.tsv` and variants. Pure math (kernel, grouping, aggregation) lives in small unit-tested modules; the heavy MF reuses the repo's own scorer methods unchanged.

**Tech Stack:** Python 3.9 (`.venv-cn`), numpy 1.26.4, pandas 2.2.2, torch 2.1.2 (CPU), pyarrow, pytest. Reuses `scoring.*` from the cloned Community Notes repo.

## Global Constraints

- Python interpreter: `/projects/bdata/advaitmb/derad-agent/tsv_generation/.venv-cn/bin/python` (3.9.25). Run everything through it.
- Repo scoring source on `sys.path`: `/projects/bdata/advaitmb/derad-agent/tsv_generation/communitynotes/scoring/src`.
- Data root: `/projects/bdata/advaitmb/derad-agent/tsv_generation/cn_data` (inputs already present: `notes-00000.tsv`, `noteStatusHistory-00000.tsv`, `userEnrollment-00000.tsv`, `ratings/ratings-0000{0,1,2}.tsv`).
- Fixed values (verbatim): `SEED = 1`, kernel `BW = 0.1`, `SOMEWHAT = 0.7`, `MISLEADING = "MISINFORMED_OR_POTENTIALLY_MISLEADING"`, `NOT_MISLEADING = "NOT_MISLEADING"`.
- Factor axis: **Expansion model only** (`MFExpansionScorer`), single MF fit, `useStableInitialization=False`. Do **not** coalesce Core+Expansion (different scales — see spec §4).
- Group convention: **Group A = `f_u ≥ 0`** (the minority cluster), **Group B = `f_u < 0`**. Deterministic given `SEED`.
- helpfulNum is cached **raw** (`{0.0, 0.5, 1.0}`); `0.5 → 0.7` remap happens only in Stage 3.
- All package code under `tsv_generation/viewpoint/`; tests under `tsv_generation/viewpoint/tests/`.
- Spec of record: `docs/superpowers/specs/2026-06-24-tweet-group-misleadingness-design.md`.

---

### Task 1: Package skeleton, constants, kernel math

**Files:**
- Create: `viewpoint/__init__.py` (empty)
- Create: `viewpoint/constants.py`
- Create: `viewpoint/kernel.py`
- Create: `viewpoint/tests/__init__.py` (empty)
- Create: `viewpoint/tests/test_kernel.py`
- Create: `.gitignore`

**Interfaces:**
- Produces: `gaussian_kernel(d: np.ndarray|float, bw: float = 0.1) -> np.ndarray|float`; `smoothed_rate(factors: np.ndarray, helpful: np.ndarray, x: float, bw: float = 0.1) -> float` (returns `nan` on empty/zero-weight input); `remap_somewhat(helpfulNum: np.ndarray, somewhat: float = 0.7) -> np.ndarray`. Constants `SEED, BW, SOMEWHAT, MISLEADING, NOT_MISLEADING, SCORING_SRC, CN_DATA, OUT_DIR`.

- [ ] **Step 1: Add pytest to the venv and create the git ignore + branch**

```bash
cd /projects/bdata/advaitmb/derad-agent
git checkout -b viewpoint-lean
cd tsv_generation
.venv-cn/bin/pip install pytest==8.3.4
cat > .gitignore <<'EOF'
cn_data/
.venv-cn/
communitynotes/
*.parquet
__pycache__/
*.pyc
EOF
mkdir -p viewpoint/tests
touch viewpoint/__init__.py viewpoint/tests/__init__.py
```

- [ ] **Step 2: Write `viewpoint/constants.py`**

```python
"""Project-wide constants for the tweet group-misleadingness pipeline."""

SEED = 1
BW = 0.1            # repo CRH Gaussian kernel bandwidth
SOMEWHAT = 0.7      # repo GaussianParams.somewhatHelpfulValue

MISLEADING = "MISINFORMED_OR_POTENTIALLY_MISLEADING"
NOT_MISLEADING = "NOT_MISLEADING"

# Absolute paths (this box).
ROOT = "/projects/bdata/advaitmb/derad-agent/tsv_generation"
SCORING_SRC = f"{ROOT}/communitynotes/scoring/src"
CN_DATA = f"{ROOT}/cn_data"
OUT_DIR = f"{CN_DATA}/viewpoint_out"
```

- [ ] **Step 3: Write the failing kernel test** — `viewpoint/tests/test_kernel.py`

```python
import numpy as np
import pytest
from viewpoint.kernel import gaussian_kernel, smoothed_rate, remap_somewhat


def test_kernel_peak_at_zero():
    assert gaussian_kernel(0.0, bw=0.1) == pytest.approx(1.0 / (0.1 * np.sqrt(2 * np.pi)))


def test_kernel_symmetric_and_decaying():
    assert gaussian_kernel(0.2, 0.1) == pytest.approx(gaussian_kernel(-0.2, 0.1))
    assert gaussian_kernel(0.05, 0.1) > gaussian_kernel(0.30, 0.1)


def test_smoothed_rate_all_helpful_is_one():
    f = np.array([-0.5, 0.0, 0.5])
    h = np.array([1.0, 1.0, 1.0])
    assert smoothed_rate(f, h, x=0.0, bw=0.1) == pytest.approx(1.0)


def test_smoothed_rate_localizes_to_eval_point():
    # rater at x votes helpful; a far rater votes not-helpful -> rate near 1 at x.
    f = np.array([0.5, -0.5])
    h = np.array([1.0, 0.0])
    assert smoothed_rate(f, h, x=0.5, bw=0.1) > 0.99


def test_smoothed_rate_empty_is_nan():
    assert np.isnan(smoothed_rate(np.array([]), np.array([]), x=0.0, bw=0.1))


def test_remap_somewhat():
    out = remap_somewhat(np.array([0.0, 0.5, 1.0]), somewhat=0.7)
    assert list(out) == [0.0, 0.7, 1.0]
```

- [ ] **Step 4: Run it; verify failure**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_kernel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'viewpoint.kernel'`

- [ ] **Step 5: Write `viewpoint/kernel.py`**

```python
"""Gaussian kernel smoothing — the core of the misleadingness metric (spec §3.3)."""
import numpy as np


def gaussian_kernel(d, bw=0.1):
    """Unnormalized-position Gaussian kernel weight(s) for distance(s) d."""
    d = np.asarray(d, dtype=np.float64)
    return (1.0 / (bw * np.sqrt(2 * np.pi))) * np.exp(-0.5 * (d / bw) ** 2)


def smoothed_rate(factors, helpful, x, bw=0.1):
    """Kernel-weighted mean helpful value at evaluation point x.

    factors, helpful: 1-D arrays of equal length. Returns nan if empty or all
    weights underflow to 0.
    """
    factors = np.asarray(factors, dtype=np.float64)
    helpful = np.asarray(helpful, dtype=np.float64)
    if factors.size == 0:
        return float("nan")
    w = gaussian_kernel(factors - x, bw)
    denom = w.sum()
    if denom == 0:
        return float("nan")
    return float((w * helpful).sum() / denom)


def remap_somewhat(helpful_num, somewhat=0.7):
    """Map raw helpfulNum {0,0.5,1.0} -> {0, somewhat, 1.0} (spec §3.3)."""
    out = np.asarray(helpful_num, dtype=np.float64).copy()
    out[out == 0.5] = somewhat
    return out
```

- [ ] **Step 6: Run tests; verify pass**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_kernel.py -v`
Expected: PASS (6 passed)

- [ ] **Step 7: Commit**

```bash
cd /projects/bdata/advaitmb/derad-agent
git add tsv_generation/.gitignore tsv_generation/viewpoint/__init__.py tsv_generation/viewpoint/constants.py tsv_generation/viewpoint/kernel.py tsv_generation/viewpoint/tests/
git commit -m "feat(viewpoint): kernel smoothing + constants"
```

---

### Task 2: Group assignment and evaluation points

**Files:**
- Create: `viewpoint/groups.py`
- Create: `viewpoint/tests/test_groups.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `assign_group(f_u: float|np.ndarray, moderate_threshold: float = 0.0) -> object` returning `"A"` / `"B"` / `None` (None only when `|f_u| <= moderate_threshold` and threshold > 0); `eval_points(rater_factors: pd.DataFrame) -> tuple[float, float]` returning `(x_A, x_B)` = mean `f_u` of group A and group B respectively. `rater_factors` has columns `raterParticipantId, f_u, group`.

- [ ] **Step 1: Write the failing test** — `viewpoint/tests/test_groups.py`

```python
import numpy as np
import pandas as pd
import pytest
from viewpoint.groups import assign_group, eval_points


def test_assign_group_sign_default():
    g = assign_group(np.array([0.3, -0.2, 0.0]))
    assert list(g) == ["A", "B", "A"]   # f_u == 0 -> A


def test_assign_group_moderate_threshold_drops_center():
    g = assign_group(np.array([0.3, -0.05, 0.05]), moderate_threshold=0.1)
    assert g[0] == "A" and g[1] is None and g[2] is None


def test_eval_points_are_group_means():
    rf = pd.DataFrame(
        {"raterParticipantId": [1, 2, 3, 4],
         "f_u": [0.2, 0.4, -0.1, -0.3],
         "group": ["A", "A", "B", "B"]}
    )
    x_A, x_B = eval_points(rf)
    assert x_A == pytest.approx(0.3)
    assert x_B == pytest.approx(-0.2)
```

- [ ] **Step 2: Run it; verify failure**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_groups.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'viewpoint.groups'`

- [ ] **Step 3: Write `viewpoint/groups.py`**

```python
"""Group assignment (rater-factor sign) and global evaluation points (spec §3.1-3.2)."""
import numpy as np
import pandas as pd


def assign_group(f_u, moderate_threshold=0.0):
    """Group A = f_u >= 0, Group B = f_u < 0. If moderate_threshold > 0, raters with
    |f_u| <= threshold are unassigned (None). Scalar in -> scalar out; array in -> object array out."""
    arr = np.asarray(f_u, dtype=np.float64)
    scalar = arr.ndim == 0
    arr = np.atleast_1d(arr)
    out = np.empty(arr.shape, dtype=object)
    for i, v in enumerate(arr):
        if moderate_threshold > 0 and abs(v) <= moderate_threshold:
            out[i] = None
        else:
            out[i] = "A" if v >= 0 else "B"
    return out[0] if scalar else out


def eval_points(rater_factors):
    """Return (x_A, x_B): mean f_u over Group A and Group B raters."""
    x_A = float(rater_factors.loc[rater_factors["group"] == "A", "f_u"].mean())
    x_B = float(rater_factors.loc[rater_factors["group"] == "B", "f_u"].mean())
    return x_A, x_B
```

- [ ] **Step 4: Run tests; verify pass**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_groups.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /projects/bdata/advaitmb/derad-agent
git add tsv_generation/viewpoint/groups.py tsv_generation/viewpoint/tests/test_groups.py
git commit -m "feat(viewpoint): group assignment + evaluation points"
```

---

### Task 3: Stage-3 tweet aggregation core

**Files:**
- Create: `viewpoint/aggregate.py`
- Create: `viewpoint/tests/test_aggregate.py`

**Interfaces:**
- Consumes: `smoothed_rate` is not called directly; this module uses `gaussian_kernel` (Task 1) for a vectorized groupby. `remap_somewhat` (Task 1).
- Produces: `aggregate_tweets(rwf: pd.DataFrame, x_A: float, x_B: float, bw: float = 0.1, somewhat: float = 0.7, source: str|None = None, defense_tag: bool = False) -> pd.DataFrame`. Input `rwf` (ratings_with_factors) must have columns `tweetId, classification, f_u, group, helpfulNum` and (if `source`/`defense_tag` used) `ratingSourceBucketed, notHelpfulNoteNotNeeded`. Output is indexed by `tweetId` with columns `mislead_A, mislead_B, defend_A, defend_B, netStance_A, netStance_B, consensus, polarity, nA, nB, nMisleadingNotes, nNotMisleadingNotes`.

- [ ] **Step 1: Write the failing test** — `viewpoint/tests/test_aggregate.py`

```python
import numpy as np
import pandas as pd
import pytest
from viewpoint.aggregate import aggregate_tweets
from viewpoint.constants import MISLEADING, NOT_MISLEADING

X_A, X_B = 0.5, -0.5

def _row(tweet, note, cls, f_u, group, h, src="DEFAULT", nn=0):
    return {"tweetId": tweet, "noteId": note, "classification": cls,
            "f_u": f_u, "group": group, "helpfulNum": h,
            "ratingSourceBucketed": src, "notHelpfulNoteNotNeeded": nn}


def test_both_groups_find_misleading_note_helpful():
    rows = [
        _row(1, 10, MISLEADING, 0.5, "A", 1.0),
        _row(1, 10, MISLEADING, 0.5, "A", 1.0),
        _row(1, 10, MISLEADING, -0.5, "B", 1.0),
        _row(1, 10, MISLEADING, -0.5, "B", 1.0),
    ]
    out = aggregate_tweets(pd.DataFrame(rows), X_A, X_B)
    assert out.loc[1, "mislead_A"] == pytest.approx(1.0)
    assert out.loc[1, "mislead_B"] == pytest.approx(1.0)
    assert out.loc[1, "consensus"] == pytest.approx(1.0)
    assert out.loc[1, "polarity"] == pytest.approx(0.0)
    assert out.loc[1, "nA"] == 2 and out.loc[1, "nB"] == 2
    assert np.isnan(out.loc[1, "defend_A"])
    assert out.loc[1, "netStance_A"] == pytest.approx(1.0)


def test_polarized_group_a_flags_group_b_does_not():
    rows = [
        _row(2, 20, MISLEADING, 0.5, "A", 1.0),
        _row(2, 20, MISLEADING, 0.5, "A", 1.0),
        _row(2, 20, MISLEADING, -0.5, "B", 0.0),
        _row(2, 20, MISLEADING, -0.5, "B", 0.0),
    ]
    out = aggregate_tweets(pd.DataFrame(rows), X_A, X_B)
    assert out.loc[2, "mislead_A"] == pytest.approx(1.0)
    assert out.loc[2, "mislead_B"] == pytest.approx(0.0)
    assert out.loc[2, "polarity"] == pytest.approx(1.0)   # netStance_A - netStance_B = 1 - 0


def test_net_stance_uses_defend_when_present():
    rows = [
        _row(3, 30, MISLEADING, 0.5, "A", 1.0),
        _row(3, 31, NOT_MISLEADING, 0.5, "A", 1.0),   # group A also endorses a "fine" note
    ]
    out = aggregate_tweets(pd.DataFrame(rows), X_A, X_B)
    assert out.loc[3, "mislead_A"] == pytest.approx(1.0)
    assert out.loc[3, "defend_A"] == pytest.approx(1.0)
    assert out.loc[3, "netStance_A"] == pytest.approx(0.0)   # 1 - 1
    assert out.loc[3, "nMisleadingNotes"] == 1
    assert out.loc[3, "nNotMisleadingNotes"] == 1


def test_population_sampled_filter():
    rows = [
        _row(4, 40, MISLEADING, 0.5, "A", 1.0, src="DEFAULT"),
        _row(4, 40, MISLEADING, 0.5, "A", 0.0, src="POPULATION_SAMPLED"),
    ]
    out = aggregate_tweets(pd.DataFrame(rows), X_A, X_B, source="POPULATION_SAMPLED")
    assert out.loc[4, "mislead_A"] == pytest.approx(0.0)   # only the sampled (h=0) row counts
```

- [ ] **Step 2: Run it; verify failure**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_aggregate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'viewpoint.aggregate'`

- [ ] **Step 3: Write `viewpoint/aggregate.py`**

```python
"""Stage 3: pool a tweet's notes per class, kernel-smooth at the two group centroids,
derive net stance / consensus / polarity (spec §3.3-3.4, §4 Stage 3)."""
import numpy as np
import pandas as pd

from .kernel import gaussian_kernel, remap_somewhat
from .constants import BW, SOMEWHAT, MISLEADING, NOT_MISLEADING


def _smoothed_by_tweet(df, x_A, x_B, bw):
    """Per-tweet kernel-weighted mean of df['h'] at x_A and x_B, plus raw A/B counts.
    Returns a DataFrame indexed by tweetId with columns rate_A, rate_B, nA, nB, nNotes."""
    if len(df) == 0:
        return pd.DataFrame(columns=["rate_A", "rate_B", "nA", "nB", "nNotes"])
    f = df["f_u"].to_numpy()
    h = df["h"].to_numpy()
    wA = gaussian_kernel(f - x_A, bw)
    wB = gaussian_kernel(f - x_B, bw)
    tmp = pd.DataFrame({
        "tweetId": df["tweetId"].to_numpy(),
        "wA": wA, "wAh": wA * h,
        "wB": wB, "wBh": wB * h,
        "isA": (df["group"] == "A").to_numpy(dtype=int),
        "isB": (df["group"] == "B").to_numpy(dtype=int),
    })
    notes = df.groupby("tweetId")["noteId"].nunique().rename("nNotes")
    g = tmp.groupby("tweetId").sum()
    out = pd.DataFrame({
        "rate_A": g["wAh"] / g["wA"],
        "rate_B": g["wBh"] / g["wB"],
        "nA": g["isA"].astype(int),
        "nB": g["isB"].astype(int),
    })
    return out.join(notes)


def aggregate_tweets(rwf, x_A, x_B, bw=BW, somewhat=SOMEWHAT, source=None, defense_tag=False):
    df = rwf
    if source is not None:
        df = df[df["ratingSourceBucketed"] == source]
    df = df.copy()
    df["h"] = remap_somewhat(df["helpfulNum"].to_numpy(), somewhat)

    mis = _smoothed_by_tweet(df[df["classification"] == MISLEADING], x_A, x_B, bw)
    notmis = df[df["classification"] == NOT_MISLEADING]
    if defense_tag:
        # Treat a NoteNotNeeded tag on a MISLEADING note as a "tweet is fine" vote (h=1).
        extra = df[(df["classification"] == MISLEADING) & (df["notHelpfulNoteNotNeeded"] == 1)].copy()
        extra["h"] = 1.0
        notmis = pd.concat([notmis, extra], ignore_index=True)
    dfd = _smoothed_by_tweet(notmis, x_A, x_B, bw)

    out = pd.DataFrame(index=mis.index.union(dfd.index))
    out["mislead_A"] = mis["rate_A"]
    out["mislead_B"] = mis["rate_B"]
    out["defend_A"] = dfd["rate_A"]
    out["defend_B"] = dfd["rate_B"]
    out["nA"] = mis["nA"]
    out["nB"] = mis["nB"]
    out["nMisleadingNotes"] = mis["nNotes"]
    out["nNotMisleadingNotes"] = dfd["nNotes"]
    for col in ["nA", "nB", "nMisleadingNotes", "nNotMisleadingNotes"]:
        out[col] = out[col].fillna(0).astype(int)

    net_A = out["mislead_A"].fillna(0) - out["defend_A"].fillna(0)
    net_B = out["mislead_B"].fillna(0) - out["defend_B"].fillna(0)
    out["netStance_A"] = net_A
    out["netStance_B"] = net_B
    out["consensus"] = np.minimum(out["mislead_A"], out["mislead_B"])  # NaN if either NaN
    out["polarity"] = net_A - net_B
    out.index.name = "tweetId"
    return out
```

- [ ] **Step 4: Run tests; verify pass**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_aggregate.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd /projects/bdata/advaitmb/derad-agent
git add tsv_generation/viewpoint/aggregate.py tsv_generation/viewpoint/tests/test_aggregate.py
git commit -m "feat(viewpoint): stage-3 tweet aggregation (mislead/defend/netStance)"
```

---

### Task 4: Note-level detail, status attach, output writer + CLI

**Files:**
- Modify: `viewpoint/aggregate.py` (add `aggregate_notes`, `attach_status`, `write_outputs`, and a `__main__` CLI)
- Modify: `viewpoint/tests/test_aggregate.py` (add tests)

**Interfaces:**
- Consumes: `aggregate_tweets` (Task 3); `note_factors` with `noteId, f_n`; `noteStatusHistory` with `noteId, currentStatus`.
- Produces: `aggregate_notes(rwf, x_A, x_B, note_factors, bw, somewhat) -> pd.DataFrame` (one row per note: `noteId, tweetId, classification, mislead_A, mislead_B, nA, nB, noteFactor_fn, currentStatus` — `defend_*` reuse the same columns for not-misleading notes); `attach_status(tweet_df, nsh, notes) -> pd.DataFrame` (adds `communityFlagged`); `write_outputs(tweet_df, note_df, out_dir, suffix="") -> None`. CLI: `python -m viewpoint.aggregate --rwf <path> --note-factors <path> --status <path> --notes <path> --out <dir> [--source POPULATION_SAMPLED] [--defense-tag]`.

- [ ] **Step 1: Write the failing tests** — append to `viewpoint/tests/test_aggregate.py`

```python
from viewpoint.aggregate import aggregate_notes, attach_status


def test_aggregate_notes_one_row_per_note_with_fn():
    rows = [
        _row(1, 10, MISLEADING, 0.5, "A", 1.0),
        _row(1, 11, NOT_MISLEADING, -0.5, "B", 1.0),
    ]
    nf = pd.DataFrame({"noteId": [10, 11], "f_n": [0.12, -0.03]})
    out = aggregate_notes(pd.DataFrame(rows), X_A, X_B, nf)
    assert set(out["noteId"]) == {10, 11}
    assert out.set_index("noteId").loc[10, "noteFactor_fn"] == pytest.approx(0.12)
    assert out.set_index("noteId").loc[10, "classification"] == MISLEADING


def test_attach_status_sets_community_flagged():
    tweet_df = pd.DataFrame(index=pd.Index([1, 2], name="tweetId"))
    notes = pd.DataFrame({"noteId": [10, 20], "tweetId": [1, 2],
                          "classification": [MISLEADING, MISLEADING]})
    nsh = pd.DataFrame({"noteId": [10, 20],
                        "currentStatus": ["CURRENTLY_RATED_HELPFUL", "NEEDS_MORE_RATINGS"]})
    out = attach_status(tweet_df, nsh, notes)
    assert bool(out.loc[1, "communityFlagged"]) is True
    assert bool(out.loc[2, "communityFlagged"]) is False
```

- [ ] **Step 2: Run; verify failure**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_aggregate.py -k "notes or status" -v`
Expected: FAIL — `ImportError: cannot import name 'aggregate_notes'`

- [ ] **Step 3: Append to `viewpoint/aggregate.py`**

```python
import argparse
import os


def aggregate_notes(rwf, x_A, x_B, note_factors, bw=BW, somewhat=SOMEWHAT):
    """One row per note with per-group smoothed rate + note factor."""
    df = rwf.copy()
    df["h"] = remap_somewhat(df["helpfulNum"].to_numpy(), somewhat)
    f = df["f_u"].to_numpy()
    df["wA"] = gaussian_kernel(f - x_A, bw)
    df["wB"] = gaussian_kernel(f - x_B, bw)
    df["wAh"] = df["wA"] * df["h"]
    df["wBh"] = df["wB"] * df["h"]
    df["isA"] = (df["group"] == "A").astype(int)
    df["isB"] = (df["group"] == "B").astype(int)
    g = df.groupby(["noteId", "tweetId", "classification"], observed=True).agg(
        wA=("wA", "sum"), wAh=("wAh", "sum"), wB=("wB", "sum"), wBh=("wBh", "sum"),
        nA=("isA", "sum"), nB=("isB", "sum"),
    ).reset_index()
    g["mislead_A"] = g["wAh"] / g["wA"]
    g["mislead_B"] = g["wBh"] / g["wB"]
    g = g.merge(note_factors[["noteId", "f_n"]], on="noteId", how="left")
    g = g.rename(columns={"f_n": "noteFactor_fn"})
    return g[["noteId", "tweetId", "classification", "mislead_A", "mislead_B",
              "nA", "nB", "noteFactor_fn"]]


def attach_status(tweet_df, nsh, notes):
    """Add communityFlagged = tweet has a misleading note that is CRH (public status)."""
    merged = notes.merge(nsh[["noteId", "currentStatus"]], on="noteId", how="left")
    crh_mis = merged[(merged["classification"] == MISLEADING) &
                     (merged["currentStatus"] == "CURRENTLY_RATED_HELPFUL")]
    flagged = set(crh_mis["tweetId"].unique())
    out = tweet_df.copy()
    out["communityFlagged"] = [t in flagged for t in out.index]
    return out


def write_outputs(tweet_df, note_df, out_dir, suffix=""):
    os.makedirs(out_dir, exist_ok=True)
    tname = f"tweet_lean{suffix}.tsv"
    tweet_df.reset_index().to_csv(os.path.join(out_dir, tname), sep="\t", index=False)
    if note_df is not None:
        note_df.to_csv(os.path.join(out_dir, "note_lean.tsv"), sep="\t", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rwf", required=True)
    ap.add_argument("--note-factors", required=True)
    ap.add_argument("--rater-factors", required=True)
    ap.add_argument("--status", required=True)
    ap.add_argument("--notes", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", default=None)
    ap.add_argument("--defense-tag", action="store_true")
    args = ap.parse_args()

    from .groups import eval_points
    rwf = pd.read_parquet(args.rwf)
    rater_factors = pd.read_parquet(args.rater_factors)
    note_factors = pd.read_parquet(args.note_factors)
    nsh = pd.read_csv(args.status, sep="\t", usecols=["noteId", "currentStatus"])
    notes = pd.read_csv(args.notes, sep="\t", usecols=["noteId", "tweetId", "classification"])

    x_A, x_B = eval_points(rater_factors)
    tweet_df = aggregate_tweets(rwf, x_A, x_B, source=args.source, defense_tag=args.defense_tag)
    tweet_df = attach_status(tweet_df, nsh, notes)
    suffix = ".popsampled" if args.source == "POPULATION_SAMPLED" else ""
    note_df = None if suffix else aggregate_notes(rwf, x_A, x_B, note_factors)
    if note_df is not None:
        note_df = note_df.merge(nsh, on="noteId", how="left")
    write_outputs(tweet_df, note_df, args.out, suffix=suffix)
    print(f"x_A={x_A:.4f} x_B={x_B:.4f} | tweets={len(tweet_df)} | wrote to {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests; verify pass**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_aggregate.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
cd /projects/bdata/advaitmb/derad-agent
git add tsv_generation/viewpoint/aggregate.py tsv_generation/viewpoint/tests/test_aggregate.py
git commit -m "feat(viewpoint): note-level detail, status attach, CLI"
```

---

### Task 5: Stage-2 join (`ratings_with_factors`)

**Files:**
- Create: `viewpoint/stage2.py`
- Create: `viewpoint/tests/test_stage2.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `build_ratings_with_factors(ratings: pd.DataFrame, notes: pd.DataFrame, rater_factors: pd.DataFrame, note_factors: pd.DataFrame) -> pd.DataFrame`. Inner-joins ratings to `rater_factors` (drops un-factored raters) and to classified `notes`; output columns: `noteId, tweetId, classification, raterParticipantId, f_u, group, helpfulNum, f_n, ratingSourceBucketed, notHelpfulNoteNotNeeded, notHelpfulIncorrect` plus the note sub-tags present in `notes`.

- [ ] **Step 1: Write the failing test** — `viewpoint/tests/test_stage2.py`

```python
import pandas as pd
from viewpoint.stage2 import build_ratings_with_factors
from viewpoint.constants import MISLEADING, NOT_MISLEADING


def test_join_keeps_only_factored_raters_and_classified_notes():
    ratings = pd.DataFrame({
        "noteId": [10, 10, 11, 12],
        "raterParticipantId": [1, 2, 1, 1],       # rater 2 has no factor; note 12 not classified-joined
        "helpfulNum": [1.0, 0.0, 0.5, 1.0],
        "ratingSourceBucketed": ["DEFAULT"] * 4,
        "notHelpfulNoteNotNeeded": [0, 0, 0, 0],
        "notHelpfulIncorrect": [0, 0, 0, 0],
    })
    notes = pd.DataFrame({"noteId": [10, 11], "tweetId": [100, 100],
                          "classification": [MISLEADING, NOT_MISLEADING]})
    rater_factors = pd.DataFrame({"raterParticipantId": [1], "f_u": [0.3], "group": ["A"]})
    note_factors = pd.DataFrame({"noteId": [10, 11], "f_n": [0.1, -0.2]})

    out = build_ratings_with_factors(ratings, notes, rater_factors, note_factors)
    # rater 2 dropped (no factor); note 12 dropped (not in notes); 2 rows remain (rater 1 on notes 10,11)
    assert len(out) == 2
    assert set(out["noteId"]) == {10, 11}
    assert set(out["tweetId"]) == {100}
    assert out.set_index("noteId").loc[10, "f_n"] == 0.1
    assert (out["f_u"] == 0.3).all()
```

- [ ] **Step 2: Run; verify failure**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_stage2.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'viewpoint.stage2'`

- [ ] **Step 3: Write `viewpoint/stage2.py`**

```python
"""Stage 2: join ratings to rater & note factors + note metadata (spec §4 Stage 2).
Runs inside run_factors.py on the already-loaded ratings (no raw re-read)."""
import pandas as pd
from .constants import MISLEADING, NOT_MISLEADING

_RATING_TAGS = ["notHelpfulNoteNotNeeded", "notHelpfulIncorrect",
                "notHelpfulSourcesMissingOrUnreliable"]
_NOTE_SUBTAGS = ["misleadingFactualError", "misleadingMissingImportantContext",
                 "misleadingManipulatedMedia", "misleadingUnverifiedClaimAsFact",
                 "misleadingSatire", "misleadingOther"]


def build_ratings_with_factors(ratings, notes, rater_factors, note_factors):
    classified = notes[notes["classification"].isin([MISLEADING, NOT_MISLEADING])]
    note_cols = ["noteId", "tweetId", "classification"] + [c for c in _NOTE_SUBTAGS if c in classified.columns]
    rating_cols = (["noteId", "raterParticipantId", "helpfulNum"]
                   + [c for c in (["ratingSourceBucketed"] + _RATING_TAGS) if c in ratings.columns])
    out = (ratings[rating_cols]
           .merge(rater_factors[["raterParticipantId", "f_u", "group"]],
                  on="raterParticipantId", how="inner")          # drops un-factored raters
           .merge(classified[note_cols], on="noteId", how="inner")  # classified notes only
           .merge(note_factors[["noteId", "f_n"]], on="noteId", how="left"))
    return out
```

- [ ] **Step 4: Run tests; verify pass**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_stage2.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
cd /projects/bdata/advaitmb/derad-agent
git add tsv_generation/viewpoint/stage2.py tsv_generation/viewpoint/tests/test_stage2.py
git commit -m "feat(viewpoint): stage-2 ratings-with-factors join"
```

---

### Task 6: Stage-1+2 driver (`run_factors.py`)

**Files:**
- Create: `viewpoint/run_factors.py`

**Interfaces:**
- Consumes: `build_ratings_with_factors` (Task 5); `assign_group` (Task 2); repo `scoring.*`.
- Produces: writes `rater_factors.parquet` (`raterParticipantId, f_u, group`), `note_factors.parquet` (`noteId, f_n, i_n`), `ratings_with_factors.parquet` to `OUT_DIR`. CLI: `python -m viewpoint.run_factors [--sample-ratings F]`.

> This task is integration over the repo's MF; it is validated by a smoke run on a small sample (Step 4), not by a pure unit test. The reused repo methods (`_filter_input`, `_prepare_data_for_scoring`, `_run_stable_matrix_factorization`) were confirmed against the source.

- [ ] **Step 1: Write `viewpoint/run_factors.py`**

```python
"""Stage 1+2 driver: one read pass -> Expansion MF factors + ratings_with_factors.

Reuses the repo scorer's own filtering and MF methods (no reimplementation):
  scorer._filter_input -> _prepare_data_for_scoring -> _run_stable_matrix_factorization.
Skips topic model, PFlip/PCRH, diligence/harassment MFs, refit, status, contributor, PSS.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

from .constants import SEED, SCORING_SRC, CN_DATA, OUT_DIR
from .groups import assign_group
from .stage2 import build_ratings_with_factors

sys.path.insert(0, SCORING_SRC)
import scoring.constants as c                       # noqa: E402
from scoring.process_data import LocalDataLoader     # noqa: E402
from scoring.mf_expansion_scorer import MFExpansionScorer  # noqa: E402


def run(sample_ratings=0.0):
    os.makedirs(OUT_DIR, exist_ok=True)

    # --- load once (normalized participant IDs; consistent across factors & join) ---
    loader = LocalDataLoader(
        notesPath=f"{CN_DATA}/notes-00000.tsv",
        ratingsPath=f"{CN_DATA}/ratings",
        noteStatusHistoryPath=f"{CN_DATA}/noteStatusHistory-00000.tsv",
        userEnrollmentPath=f"{CN_DATA}/userEnrollment-00000.tsv",
        headers=True,
    )
    notes, ratings, nsh, userEnrollment = loader.get_data()
    if sample_ratings > 0:
        ratings = ratings.sample(frac=sample_ratings, random_state=SEED)

    # --- Stage 1: single Expansion MF (stable-init OFF for speed) ---
    scorer = MFExpansionScorer(seed=SEED, useStableInitialization=False)
    emptyTopics = pd.DataFrame({c.noteIdKey: [], c.noteTopicKey: []})  # unused by Expansion
    ratings_f, _ = scorer._filter_input(emptyTopics, ratings, nsh, userEnrollment)
    prep = scorer._prepare_data_for_scoring(
        ratings_f[[c.noteIdKey, c.raterParticipantIdKey, c.helpfulNumKey,
                   c.createdAtMillisKey, c.helpfulnessLevelKey,
                   c.notHelpfulIncorrectTagKey, c.notHelpfulIrrelevantSourcesTagKey,
                   c.notHelpfulSourcesMissingOrUnreliableTagKey,
                   c.notHelpfulSpamHarassmentOrAbuseTagKey, c.notHelpfulOtherTagKey]]
    )
    noteParams, raterParams, _gi = scorer._run_stable_matrix_factorization(
        prep[[c.noteIdKey, c.raterParticipantIdKey, c.helpfulNumKey]],
        userEnrollment[[c.participantIdKey, c.modelingGroupKey]],
    )

    rater_factors = raterParams[[c.raterParticipantIdKey, c.internalRaterFactor1Key]].rename(
        columns={c.raterParticipantIdKey: "raterParticipantId", c.internalRaterFactor1Key: "f_u"}
    ).dropna(subset=["f_u"])
    rater_factors["group"] = assign_group(rater_factors["f_u"].to_numpy())
    note_factors = noteParams[[c.noteIdKey, c.internalNoteFactor1Key, c.internalNoteInterceptKey]].rename(
        columns={c.noteIdKey: "noteId", c.internalNoteFactor1Key: "f_n",
                 c.internalNoteInterceptKey: "i_n"}
    )
    rater_factors.to_parquet(f"{OUT_DIR}/rater_factors.parquet", index=False)
    note_factors.to_parquet(f"{OUT_DIR}/note_factors.parquet", index=False)

    # --- Stage 2 (fused): join the already-loaded ratings to factors ---
    notes_renamed = notes.rename(columns={c.noteIdKey: "noteId", c.tweetIdKey: "tweetId",
                                          c.classificationKey: "classification"})
    ratings_renamed = ratings.rename(columns={c.noteIdKey: "noteId",
                                              c.raterParticipantIdKey: "raterParticipantId",
                                              c.helpfulNumKey: "helpfulNum",
                                              c.ratingSourceBucketedKey: "ratingSourceBucketed"})
    rwf = build_ratings_with_factors(ratings_renamed, notes_renamed, rater_factors, note_factors)
    rwf.to_parquet(f"{OUT_DIR}/ratings_with_factors.parquet", index=False)

    n_pos = int((rater_factors["group"] == "A").sum())
    n_neg = int((rater_factors["group"] == "B").sum())
    print(f"factored raters: {len(rater_factors)} (A={n_pos}, B={n_neg}); "
          f"ratings_with_factors rows: {len(rwf)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-ratings", type=float, default=0.0)
    run(ap.parse_args().sample_ratings)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Confirm `LocalDataLoader`/scorer signatures match this code**

Run: `.venv-cn/bin/python -c "import sys; sys.path.insert(0,'communitynotes/scoring/src'); import inspect; from scoring.process_data import LocalDataLoader; from scoring.mf_expansion_scorer import MFExpansionScorer; print(inspect.signature(LocalDataLoader.__init__)); print(inspect.signature(MFExpansionScorer.__init__))"`
Expected: prints both signatures. If `LocalDataLoader.__init__` parameter names differ from `notesPath/ratingsPath/noteStatusHistoryPath/userEnrollmentPath/headers`, update the constructor call in Step 1 to match before proceeding.

- [ ] **Step 3: Confirm column constants exist**

Run: `.venv-cn/bin/python -c "import sys; sys.path.insert(0,'communitynotes/scoring/src'); import scoring.constants as c; print(c.internalRaterFactor1Key, c.internalNoteFactor1Key, c.internalNoteInterceptKey, c.tweetIdKey, c.classificationKey, c.ratingSourceBucketedKey)"`
Expected: `internalRaterFactor1 internalNoteFactor1 internalNoteIntercept tweetId classification ratingSourceBucketed`

- [ ] **Step 4: Smoke run on a 5% sample (background)**

Run: `cd tsv_generation && .venv-cn/bin/python -m viewpoint.run_factors --sample-ratings 0.05 2>&1 | tee cn_data/run_factors_smoke.log`
Expected: completes; prints a line like `factored raters: <N> (A=<a>, B=<b>); ratings_with_factors rows: <M>` with both `a>0` and `b>0`, and `cn_data/viewpoint_out/{rater_factors,note_factors,ratings_with_factors}.parquet` exist.

- [ ] **Step 5: Verify smoke outputs structurally**

Run: `.venv-cn/bin/python -c "import pandas as pd; d='cn_data/viewpoint_out'; rf=pd.read_parquet(f'{d}/rater_factors.parquet'); rwf=pd.read_parquet(f'{d}/ratings_with_factors.parquet'); print(rf.columns.tolist()); print(rwf.columns.tolist()); print('groups', rf.group.value_counts().to_dict()); assert {'raterParticipantId','f_u','group'} <= set(rf.columns); assert {'tweetId','classification','f_u','group','helpfulNum','f_n'} <= set(rwf.columns)"`
Expected: prints column lists; both groups present; assertions pass.

- [ ] **Step 6: Commit**

```bash
cd /projects/bdata/advaitmb/derad-agent
git add tsv_generation/viewpoint/run_factors.py
git commit -m "feat(viewpoint): stage 1+2 driver (Expansion MF + fused join)"
```

---

### Task 7: Full real run + validation script

**Files:**
- Create: `viewpoint/validate.py`
- Create: `viewpoint/tests/test_validate.py`

**Interfaces:**
- Consumes: `tweet_lean.tsv`, `note_lean.tsv` from Stage 3.
- Produces: `run_checks(tweet_df: pd.DataFrame) -> dict` returning `{n_tweets, frac_flagged_high_consensus, polarity_mean, polarity_two_sided}`; a `__main__` that prints the §9 sanity checks.

- [ ] **Step 1: Write the failing test** — `viewpoint/tests/test_validate.py`

```python
import numpy as np
import pandas as pd
from viewpoint.validate import run_checks


def test_run_checks_reports_expected_keys():
    df = pd.DataFrame({
        "consensus": [0.9, 0.1, np.nan],
        "polarity": [0.2, -0.3, 0.0],
        "communityFlagged": [True, False, False],
    })
    out = run_checks(df)
    assert set(out) == {"n_tweets", "frac_flagged_high_consensus", "polarity_mean", "polarity_two_sided"}
    assert out["n_tweets"] == 3
    assert out["polarity_two_sided"] is True   # has both >0 and <0 values
```

- [ ] **Step 2: Run; verify failure**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_validate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'viewpoint.validate'`

- [ ] **Step 3: Write `viewpoint/validate.py`**

```python
"""Spec §9 sanity checks over a tweet_lean table."""
import argparse
import numpy as np
import pandas as pd


def run_checks(tweet_df):
    high = tweet_df[tweet_df["consensus"] >= 0.5]
    frac = float(high["communityFlagged"].mean()) if len(high) else float("nan")
    pol = tweet_df["polarity"].dropna()
    return {
        "n_tweets": int(len(tweet_df)),
        "frac_flagged_high_consensus": frac,
        "polarity_mean": float(pol.mean()) if len(pol) else float("nan"),
        "polarity_two_sided": bool((pol > 0).any() and (pol < 0).any()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tweet-lean", required=True)
    args = ap.parse_args()
    df = pd.read_csv(args.tweet_lean, sep="\t")
    for k, v in run_checks(df).items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests; verify pass**

Run: `.venv-cn/bin/python -m pytest viewpoint/tests/test_validate.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Full Stage-1+2 run on all ratings (background)**

Run: `cd tsv_generation && .venv-cn/bin/python -m viewpoint.run_factors 2>&1 | tee cn_data/run_factors_full.log`
Expected: completes; prints factored-rater counts (both groups large) and `ratings_with_factors` row count in the tens of millions.

- [ ] **Step 6: Stage-3 default + population-sampled variant**

```bash
cd tsv_generation
D=cn_data/viewpoint_out
.venv-cn/bin/python -m viewpoint.aggregate --rwf $D/ratings_with_factors.parquet \
  --note-factors $D/note_factors.parquet --rater-factors $D/rater_factors.parquet \
  --status cn_data/noteStatusHistory-00000.tsv --notes cn_data/notes-00000.tsv --out $D
.venv-cn/bin/python -m viewpoint.aggregate --rwf $D/ratings_with_factors.parquet \
  --note-factors $D/note_factors.parquet --rater-factors $D/rater_factors.parquet \
  --status cn_data/noteStatusHistory-00000.tsv --notes cn_data/notes-00000.tsv --out $D \
  --source POPULATION_SAMPLED
```
Expected: writes `tweet_lean.tsv`, `note_lean.tsv`, `tweet_lean.popsampled.tsv`; prints `x_A>0`, `x_B<0`, tweet counts.

- [ ] **Step 7: Run validation checks**

Run: `.venv-cn/bin/python -m viewpoint.validate --tweet-lean cn_data/viewpoint_out/tweet_lean.tsv`
Expected: `frac_flagged_high_consensus` is high (CRH-shown notes should have high cross-group consensus), `polarity_two_sided: True`.

- [ ] **Step 8: Commit**

```bash
cd /projects/bdata/advaitmb/derad-agent
git add tsv_generation/viewpoint/validate.py tsv_generation/viewpoint/tests/test_validate.py
git commit -m "feat(viewpoint): §9 validation checks + full-run wiring"
```

---

## Notes on the full run (Task 7, Steps 5-6)

- Stage 1+2 is the only expensive step (data read + one Expansion MF on CPU). Run it in the background; it does not need to be repeated to change any Stage-3 knob.
- All Stage-3 variants (population-sampled, tag-refined defense via `--defense-tag`, different bandwidth/somewhat by editing the CLI call) reread only `ratings_with_factors.parquet` — seconds to minutes.
- If `run_factors` runs out of patience on the full set, rerun Task 7 Step 5 with `--sample-ratings 0.3` first to sanity-check end-to-end, then the full run.

## Spec-coverage notes (intentional deferrals)

These spec items are deliberately **not** in v1; they are cosmetic or optional and add no risk to the core deliverable. Listed for transparency.

1. **`note_lean` author-set sub-tag columns** (spec §5): `note_lean` ships the core per-note detail; the note's `misleading*` sub-tag flags are a trivial `notes`-on-`noteId` left-join that can be added to `aggregate_notes` later. Deferred to keep Task 4 focused.
2. **Two §9 checks not in `validate.py`**: "cross-check `polarity` sign vs note factor `f_n`" and "factor coverage = share of ratings whose rater has a factor." `run_factors` already prints A/B rater counts; the other two are quick additions (join `note_lean` for `f_n`; compute `len(rwf)/len(loaded ratings)`). Deferred.
3. **Optional metric refinements** (spec §7 "metric refinements"): the GaussianScorer prior `min(.4, max(f_n·f_point, .05))` and not-helpful overweighting are the spec's *optional* add-ons (default is "none — pure smoothed rate", which this plan implements). They plug into Stage 3 (`aggregate._smoothed_by_tweet`): blend the prior into the weighted mean and scale not-helpful weights. They remain pure Stage-3 changes (no Stage-1 re-run), exactly as the spec promises.
