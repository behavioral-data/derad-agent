# Per-tweet "misleading for Group A vs Group B" — Design

- **Date:** 2026-06-24
- **Status:** Approved & audited (design); pending implementation plan
- **Author:** advaitmb (with Claude Code)
- **Data snapshot:** Community Notes public data, `2026/06/21` (downloaded to `cn_data/`)

## 1. Goal

For each **noted tweet**, produce two scores:

- `mislead_A` — how *misleading* viewpoint **Group A** considers the tweet
- `mislead_B` — how *misleading* viewpoint **Group B** considers the tweet

plus derived `consensus` and `polarity` summaries. Groups A and B are the **two
latent viewpoint clusters** the Community Notes matrix-factorization model already
discovers from rating-agreement patterns — they are *not* pre-labeled left/right.
Labeling which cluster is "left" is an optional, separate anchoring step that this
design does not require.

A tweet's misleadingness is observed indirectly: through how strongly each group
**endorses (rates Helpful) a note that classifies the tweet as misleading**.
Endorsing a "this tweet is misleading" note ⇒ that group considers the tweet
misleading.

## 2. Background

The CN scorer fits `r̂(u,n) = μ + i_u + i_n + f_u·f_n` over the note–rater rating
matrix. The 1-D rater factor `f_u` is a latent viewpoint coordinate learned purely
from *who agrees with whom* about note helpfulness; empirically its dominant axis
tracks political polarization, but the model neither knows nor labels that. The
**sign** of `f_u` partitions raters into two opposing camps — exactly the "two
polarities" this project needs.

Because the factor is the only thing in the data that separates viewpoints, and
because note *status* is already published, the only model outputs we must compute
ourselves are the **per-rater factor `f_u`** (and, as a byproduct of the same fit, the
note factor `f_n`, used only by the optional prior add-on).

## 3. Definitions

### 3.1 Groups

- **Group A** = raters with `f_u ≥ 0` (the *minority* cluster — the MF pins the
  majority of raters to negative factors, so A is the smaller group by construction)
- **Group B** = raters with `f_u < 0`

`f_u` is the **Expansion-model** rater factor (§4 Stage 1); running a single MF on a
**fixed seed** makes the sign convention — and therefore the A/B assignment —
deterministic across reruns. The `f_u = 0` split is the default; an optional variant
drops near-zero "moderate" raters below a magnitude threshold to sharpen the contrast.

### 3.2 Evaluation points

- `x_A` = mean `f_u` over Group A raters (a single positive value)
- `x_B` = mean `f_u` over Group B raters (a single negative value)

All `f_u` come from the **one** Expansion MF, so they share a single consistent scale
(this is precisely why we do not mix models — see §4 Stage 1). Computed once,
globally, so every tweet is scored at the same two coordinates and scores are
comparable across tweets.

### 3.3 The smoothed misleadingness metric

For a misleading-classified note `n`, each rating contributes a helpful value
`h ∈ {Helpful: 1.0, Somewhat: 0.7, NotHelpful: 0.0}`. The loader emits
`helpfulNum ∈ {1.0, 0.5, 0.0}`; we cache that **raw** value (Stage 2) and remap
`0.5 → 0.7` here in Stage 3 (matching the repo's `GaussianParams.somewhatHelpfulValue`),
so the somewhat-value remains a cheap Stage-3 knob. With the repo's CRH Gaussian kernel

```
K(d) = (1 / (bw·√(2π))) · exp( −0.5 · (d / bw)² ),   bw = 0.1
```

the smoothed misleadingness at evaluation point `x_g` (for g ∈ {A, B}) is the
kernel-weighted mean helpful value:

```
mislead_g(n) = Σ_r K(f_{u(r)} − x_g) · h_r  /  Σ_r K(f_{u(r)} − x_g)
```

This is the "smoothed helpfulness rate at `x_g`": ratings from raters near `x_g`
dominate, but nearby ratings lend strength so thin groups still get a stable value.
The same formula applies to **any** rating set — a single note (for `note_lean`) or a
tweet's pooled misleading-note ratings (for the tweet-level `mislead_g`, §4 Stage 3).

### 3.4 Both note classes → net stance

The same smoothing is applied to **not-misleading-classified** notes, yielding
`defend_g` — how strongly group `g` endorses "this tweet is fine." The two classes
point in opposite directions, so we keep them separate and derive a signed **net
stance** per group:

```
netStance_g = mislead_g − defend_g     (defend_g treated as 0 when the tweet has no not-misleading note)
```

`netStance_g ∈ [−1, 1]`: positive = group `g` net-considers the tweet misleading,
negative = net-considers it fine. The treatment is symmetric: an absent class
contributes 0 to `netStance` (so a tweet with only misleading notes has
`netStance_g = mislead_g`; one with only not-misleading notes has `−defend_g`). The
`mislead_*`/`defend_*` columns are still reported as **NaN** when that class is absent
(for transparency); only the `netStance` computation substitutes 0.

**Tag-refined defense (optional, Stage-3).** Not-misleading *notes* are sparse, but
the abundant `notHelpfulNoteNotNeeded` rating tag on a *misleading* note is itself a
"the tweet is fine" vote. An optional variant folds that tag into `defend_g` for a
denser defense signal even where no not-misleading note exists.

## 4. Pipeline / components

**Design principle — "score once, aggregate many times."** The only expensive,
hard-to-repeat work is fitting the factors (Stage 1). Every metric choice and every
optional add-on (prior, not-helpful overweighting, bandwidth, group split, rollup
method, note scope) MUST be a cheap recompute over cached artifacts — never a reason
to re-run the factor fit or even re-read the raw 17 GB of ratings. The expensive work
(Stages 1–2, fused into a single read pass) writes cached artifacts; the hard boundary
sits **before** the cheap, freely-re-runnable Stage 3.

### Stage 1 — `score_factors` (run ONCE per data snapshot)

We do **not** run `main.py` — its pipeline bundles work we don't need (topic-model
NLP, PFlip/PCRH supervised models, diligence + harassment MFs, scoring rules/status,
contributor aggregation). Stage 1 is a minimal driver that reuses the repo's faithful
data loading and matrix factorization and nothing else.

- **Input:** the four `cn_data/` files.
- **Action (`run_factors.py`):**
  1. `LocalDataLoader.get_data()` — faithful load + preprocess (`helpfulNum ∈ {0, 0.5, 1.0}`, ID normalization).
  2. Instantiate `MFExpansionScorer` and run **one** matrix factorization via its
     configured ranker (`scorer._mfRanker.get_new_mf_with_same_args().run_mf(...)`) on
     a **fixed seed**. Expansion does not exclude topics, so `_filter_input` is called
     with an all-`Unassigned` `noteTopics` frame (columns `noteId, noteTopic`) — no
     topic model is built. Group-13 stable-initialization may optionally be enabled for
     convergence robustness (small extra cost); the helpfulness-filtered refit is skipped.
  3. Extract factors from `run_mf`'s `(noteParams, raterParams, globalIntercept)`
     return: `internalRaterFactor1` → `f_u`, `internalNoteFactor1` → `f_n`,
     `internalNoteIntercept` → `i_n`.
- **Why Expansion alone (not coalesced):** Expansion is fit on **core ∪ expansion**
  raters, so it already covers every rater Core would — using it alone gives full
  coverage on **one consistent factor scale**. Coalescing Core+Expansion was rejected:
  the two models are independently sign-flipped and unnormalized across models, so a
  coalesced `f_u` mixes scales and corrupts the eval points and the `bw=0.1` kernel
  distances, for zero coverage gain. (Core-only, or z-standardizing each model before
  coalescing, are possible alternatives but add complexity without benefit here.)
- **Deliberately skipped** (vs `main.py`): topic-model NLP, PFlip/PCRH, diligence &
  harassment MFs, the helpfulness-filtered re-fit, scoring rules/status, pseudo-raters,
  contributor aggregation, PSS. Only the single MF fit remains.
- **Cached outputs (REQUIRED):**
  - `rater_factors.parquet` — `raterParticipantId, f_u, group`
  - `note_factors.parquet` — `noteId, f_n, i_n` (`f_n` feeds the optional prior add-on)
  - *Note status is not produced here* — it comes from the public `noteStatusHistory`.

**Read-once fusion (implementation):** because the driver already holds all ratings
in memory, it emits Stage 2's `ratings_with_factors.parquet` in the **same pass** —
the 17 GB is read **once total**, not twice. The two cached artifacts stay
conceptually distinct so Stage 3 can re-run independently.

### Stage 2 — `build_ratings_with_factors` (fused into Stage 1's single read pass)

- **Input:** raw ratings shards, `notes` (tweetId, classification + sub-tags), `note_factors` (`f_n`), `rater_factors` (`f_u`, group). In practice this runs inside `run_factors.py` so ratings are read once (see Stage 1 read-once fusion).
- **Action:** one streaming pass over all ~87 M ratings; join each rating to its
  `f_u`, group, the note's `tweetId`/`classification`/`f_n`, and helpful value. Keep
  ratings on **all classified notes** (not just misleading) so the misleading-vs-
  not-misleading scope is a cheap Stage-3 slice.
- **Capture generously:** since adding a column later forces re-running this join,
  pull every plausibly-useful column now so all downstream analysis is free at Stage 3.
- **Cached output (REQUIRED):** `ratings_with_factors.parquet` — columns:
  - core: `noteId, tweetId, classification, raterFactor (f_u), group, helpfulNum (raw 0/0.5/1.0), noteFactor (f_n)`
  - `ratingSourceBucketed` (`DEFAULT` / `POPULATION_SAMPLED`) — enables the de-biased variant
  - rating tags (per-rating int flags): all `notHelpful*` and `helpful*` tags, notably
    `notHelpfulNoteNotNeeded` ("tweet is fine"), `notHelpfulIncorrect` ("note is wrong"),
    `notHelpfulSourcesMissingOrUnreliable`
  - note "why-misleading" sub-tags (from `notes`): `misleadingFactualError`,
    `misleadingMissingImportantContext`, `misleadingManipulatedMedia`,
    `misleadingUnverifiedClaimAsFact`, `misleadingSatire`, `misleadingOther`
    (+ `notMisleading*` sub-tags)

  This single artifact is the input to **every** metric variant.
- **Deferred (cheaply addable later):** topic assignment is *per-note*, not
  per-rating, so it does **not** require re-running this 87 M-row join — it can be
  added as a small `noteId → topic` side table joined at Stage 3 whenever wanted.

### Stage 3 — `aggregate` (cheap; rerun freely for every knob / add-on)

- **Input:** `ratings_with_factors.parquet`, `noteStatusHistory` (for status).
- **Action:** remap `helpfulNum 0.5 → 0.7`; compute `x_A`, `x_B` (group-mean factors).
  For each tweet, **pool the ratings across all its misleading notes** and kernel-smooth
  once at `x_A`/`x_B` (§3.3) → `mislead_A`, `mislead_B`; pool its not-misleading notes
  likewise → `defend_A`, `defend_B` (NaN when the tweet has no note of that class).
  Count the raw Group-A/Group-B ratings in the misleading pool → `nA`, `nB`. Attach
  public `currentStatus`. Derive `netStance_g = mislead_g − defend_g` (an absent class
  contributes 0), `consensus = min(mislead_A, mislead_B)` (deliberately on `mislead`,
  not `netStance` — it measures both groups *positively* endorsing the misleading
  reading), and `polarity = netStance_A − netStance_B`.
  Pooling per tweet (rather than per-note then averaging) removes any note-weighting
  ambiguity and equals the per-note value for the single-misleading-note majority;
  per-note detail is still emitted in `note_lean`.
- **Variants (all cheap Stage-3 reruns over the same parquet):**
  - **Population-sampled:** repeat on `ratingSourceBucketed == POPULATION_SAMPLED`
    rows only → a selection-bias-free `tweet_lean.popsampled.tsv` to compare against
    the self-selected default.
  - **Tag-refined defense:** fold `notHelpfulNoteNotNeeded` into `defend_g` (§3.4).
- **Output:** `note_lean.tsv`, `tweet_lean.tsv` (+ `tweet_lean.popsampled.tsv`).
- **Re-runnability guarantee:** all of §7's metric knobs and both optional add-ons
  are Stage-3 parameters. Toggling any of them reruns only this stage
  (seconds–minutes over the cached parquet) — no scorer run, no raw-TSV re-read.

## 5. Output schemas

### `tweet_lean.tsv` (one row per tweet with ≥1 classified note)

| column | meaning |
|--------|---------|
| `tweetId` | the tweet |
| `mislead_A`, `mislead_B` | smoothed misleadingness per group over the tweet's misleading notes, in [0,1]; NaN if it has none |
| `defend_A`, `defend_B` | smoothed "tweet is fine" endorsement per group (not-misleading notes); NaN if none |
| `netStance_A`, `netStance_B` | `mislead − defend` per group, in [−1,1] |
| `consensus` | `min(mislead_A, mislead_B)` — floor both groups agree on |
| `polarity` | `netStance_A − netStance_B` — sign = which group flags it more; magnitude = how one-sided |
| `nA`, `nB` | raw Group-A/B rating counts in the misleading-note pool (confidence) |
| `nMisleadingNotes`, `nNotMisleadingNotes` | note counts by class on the tweet |
| `communityFlagged` | from public `currentStatus`: has a CRH misleading note |

A parallel **`tweet_lean.popsampled.tsv`** has the same schema, computed on
population-sampled ratings only.

### `note_lean.tsv` (one row per note; backing detail)

`noteId, tweetId, classification, mislead_A/B (or defend_A/B), nA, nB, noteFactor_fn,
author-set misleading sub-tags, currentStatus`

## 6. Interpretation

- **High A & High B** → both groups consider it misleading: genuine cross-viewpoint
  consensus (≈ what X actually displays).
- **High A, Low B** (or vice versa) → polarized: one group flags it, the other does
  not. `polarity` is the headline "how strongly / which side" measure.
- **`netStance` sharpens this:** a low `mislead_B` plus a high `defend_B` means
  Group B *actively considers the tweet fine* (strong polarity), versus low
  `mislead_B` with no defense (Group B simply didn't engage). The most polarized
  tweets have `netStance_A` and `netStance_B` of opposite sign — dueling corrections.
- **`consensus`** is the misleadingness both sides agree on; **`polarity`** is the
  disagreement.

## 7. Parameters (defaults; changeable)

| parameter | default | alternatives |
|-----------|---------|--------------|
| factor axis | Expansion model, fixed seed | Core-only; z-standardized Core+Expansion |
| group split | `f_u = 0` | drop near-zero (moderate) raters below a magnitude threshold |
| eval points | group means `x_A`, `x_B` | symmetric ±percentile; per-note centroids |
| somewhat-helpful value | `0.7` | `0.5` |
| kernel bandwidth | `0.1` (repo CRH) | `0.2` (repo CRNH) |
| metric refinements | none (pure smoothed rate) | add repo prior `min(.4, max(f_n·f_point, .05))` + not-helpful overweighting to match GaussianScorer exactly |
| note scope | both classes (`mislead_*` + `defend_*` → net stance) | misleading-only |
| rating source | all ratings (`DEFAULT` + sampled) | `POPULATION_SAMPLED` only (de-biased) |
| defense signal | not-misleading notes only | also fold in `notHelpfulNoteNotNeeded` tag |
| tweet rollup | pool the tweet's notes per class, smooth once | per-note then weighted-mean; most-rated note only |

**Cost of changing each knob.** Every parameter above is a **Stage-3** knob —
changing it reruns only the cheap aggregation over `ratings_with_factors.parquet`
(seconds–minutes), with no MF run and no raw-TSV re-read. Only choices that change
the *factors themselves* require rerunning Stage 1 (the fused read+MF pass): the
**factor axis** (the Expansion model + seed) and any **rating subsampling**. Settle
those before the Stage-1 run; iterate everything else freely afterward.

## 8. Caveats & limitations

- **Coverage:** only the ~1.87 M tweets that received a note are scorable.
  Un-noted tweets are invisible to this data.
- **Group identity:** A/B are internally consistent within one scorer run, but
  *which* cluster is "A" is arbitrary (the model flips factor signs so most raters
  are negative). A fixed convention is pinned per run; mapping A→"left" needs
  optional external anchoring (e.g. known-lean reference accounts).
- **Semantics:** the signal is *note reception*. "Group X endorses the correction"
  is read as "Group X considers the tweet misleading." Stated plainly in outputs.
- **No PSS:** the lean driver never runs Post-Selection Similarity, which in
  production coalesces anomalously-correlated raters before fitting. Our factors
  therefore differ slightly from production; the group *sign* we rely on is robust to
  this. (`POPULATION_SAMPLED` is a separate de-biasing cut — see below.)
- **Self-selection:** raters choose what to rate; counts `nA`/`nB` are exposed so
  low-coverage tweets can be filtered. The `POPULATION_SAMPLED` variant addresses
  this directly, but those ratings are a small fraction of the total — the de-biased
  table covers far fewer tweets and is best used to *validate* the default, not
  replace it.
- **Defense coverage:** `defend_*` exists only for the minority of tweets with a
  not-misleading note (or, in the tag-refined variant, a `notHelpfulNoteNotNeeded`
  signal). Where absent, `netStance` falls back to `mislead`; don't read missing
  defense as "no defense."

## 9. Validation / sanity checks

- Group balance: report `|A|`, `|B|`, `x_A`, `x_B`.
- Spot-check: tweets with high `consensus` should align with notes currently shown
  on X (`communityFlagged = True`).
- Distribution of `polarity` should be roughly centered with two-sided tails.
- Cross-check `polarity` sign against note factor `f_n` sign (should correlate).
- Factor coverage: report the share of ratings whose rater has an Expansion factor —
  un-factored raters are dropped from `nA`/`nB` and from the smoothing.

## 10. Out of scope

- Absolute left/right labeling / axis anchoring.
- Tweets without notes.
- Re-deriving note statuses (taken from public `noteStatusHistory`).
- Temporal dynamics (single snapshot only).
