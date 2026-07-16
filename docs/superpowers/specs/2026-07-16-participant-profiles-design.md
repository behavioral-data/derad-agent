# Design Spec: Pre-generated Participant Profiles (Study 2)

- **Date:** 2026-07-16
- **Status:** Approved design, pre-implementation
- **Scope:** Generate a deterministic pool of **456** participant profiles (condition + post
  assignment) for the Study 2 confirmatory experiment, plus wire the mock-X interface to
  hand them out. Two phases with a sign-off gate between them.
- **Related:** `study/interface/assignment.py`, `study/interface/study_store.py`,
  `study/interface/db.py`, `docs/interface_azure_deployment.md`,
  `study/docs/power-analysis-2026-07-16.md`,
  `docs/bias-audit-and-redesign-2026-07-15.md`, `study/qualtrics_survey/`.

## 1. Motivation

Study 2 is a between-subjects experiment on **tone** (neutral / agreeable / satirical /
community-notes control) delivered through a mock-X interface. Rather than compute each
participant's condition and post set live (current `assignment.py`), we pre-generate the
**entire allocation before recruitment**. Binding a real participant becomes "claim the
next unused profile." Benefits: the allocation is auditable and reproducible before anyone
is recruited; balance is exact rather than approximate; and the assignment strategy is
documented for the paper's methods/results section.

## 2. Fixed facts (verified against the repo)

- **Post pool:** 108 posts, exactly **6 per `(topic × polarity)` cell**. 6 topics
  (lgbt, immigration, healthcare, cost of living, religion, race) × 3 polarities
  (negative / center / positive) = **18 cells**. Source: `study/data/posts.csv`,
  `db.cells()`.
- **Conditions:** `("neutral", "agreeable", "satirical", "control")` (`db.CONDITIONS`).
- **Per participant:** **36 posts = 2 per cell → 6/topic, 12/polarity; shown 12/day × 3
  days.**
- **Access codes:** each `(post, condition)` has an opaque code in the `access` table
  (`db.code_for`); participant URLs are `/?v=<code>` and never leak post or condition.

## 3. Design decisions (all resolved during brainstorming)

1. **Matched / yoked post-sets.** Build **114** balanced **36-post** *templates*;
   instantiate each template in all 4 conditions → 456 profiles. Post content is identical
   across the four tone arms of a template; only tone varies. (Chosen over independent
   per-profile draws to remove post selection as a confound in the tone comparison.)
2. **Party as a crossed blocking factor.** Prescreen party on Prolific and stratify: a
   balanced **party (2) × tone (4)** factorial, **57 participants per cell** (228 Democrats,
   228 Republicans; 114 per tone arm). Party is orthogonal to post content, so all
   content-balance guarantees are unaffected.
3. **Permuted-block randomization** of participants to profiles within each party sub-pool
   (not sequential claiming), with allocation concealment via the opaque codes.
4. **Per-day polarity balance** (4 negative / 4 center / 4 positive each day) and topic
   spread (2/topic/day); within-day order fixed per template and matched across its four
   conditions.
5. **Determinism.** A single fixed RNG seed; the whole pool regenerates bit-identically.
   The seed is recorded in the artifact and in this spec.
6. **Two phases with a sign-off gate.** Phase 1 generates + verifies the artifact for
   approval; Phase 2 wires the interface to claim from the pool.

### Sample size & dose (from `study/docs/power-analysis-2026-07-16.md`)
n = 456 (114/tone arm, 57/cell) powers **RQ2 (affective-polarization change) as a
confirmatory endpoint** at d = 0.30, 80% power, ANCOVA with a pre-score covariate
(r = 0.6). **Dose = 36 posts/participant** (down from an earlier 54): RQ1 engagement power
is *insensitive* to dose in this range (the weakest contrast stays ≈ 0.94 at 36 vs. 0.95 at
54, because the between-subjects tone contrast is driven by participant count, not
posts/participant), so 36 preserves RQ1 while cutting ~⅓ of per-participant task time.
**Delivery = 3 days** (down from 6): with dose fixed, day-count does not affect power; fewer
days reduces attrition and the external-events window on the polarization measure.
**Upgrade paths** (if requirements tighten): ~608 total (152/arm) for multiple-comparison
protection on the RQ2 family or 90% power; ~1016 total (254/arm) for a truly-small d = 0.20.
**Caveats:** d = 0.30 is the optimistic edge of "small" for polarization interventions;
the RQ2 effect is assumed not to require a dose beyond 36 (no dose-response data exists);
pilot RQ1 effects come from n = 10.

## 4. Allocation algorithm (deterministic, single seed)

### 4.1 Template post-selection (each post exactly 38× across 114 templates)
For each of the 18 cells independently, choose 2 of its 6 posts per template using
**least-used selection** (the primitive already in `assignment.py`): for each template in
sequence, pick the 2 posts with the lowest running usage count, ties broken by seeded RNG,
with per-template post-order shuffled so specific posts do not systematically co-occur.
Because 114 templates × 2 / 6 posts = **38 exactly**, greedy least-used converges to each
post appearing in exactly 38 templates. No rounding drift. (114 × 2 / 6 = 38 integer.)

### 4.2 Day layout (36 → 3 × 12)
Per template: each day gets **4 posts from each polarity** (3 days × 4 = 12 per polarity =
the 12 the template holds). Within each polarity, distribute the 12 posts (2 per topic ×
6 topics) across days round-robin by topic so a day's four same-polarity posts come from
different topics, and each topic appears ~2×/day overall. Within-day order is a seeded
shuffle, **fixed per template and reused across all four conditions** (order becomes a
template property, fully crossed with condition).

### 4.3 Cross with conditions
Instantiate each template in all four conditions with identical posts / day layout / order.
→ 456 profiles, exactly 114 per condition.

### 4.4 Party targeting (2D + 2R per template; 57D + 57R per condition)
Assign each of the 456 `(template, condition)` profiles a **target party** so that:
- within each condition, 57 templates are Dem-targeted and 57 Rep-targeted, and
- each template is targeted to **2 Democrats and 2 Republicans** across its four conditions.

This is a 114 × 4 label matrix with row sums (2D, 2R) and column sums (57D, 57R) — a feasible
degree sequence, constructed deterministically by rotating the C(4,2)=6 Dem-pairs so each
condition is Dem-targeted in exactly 57 templates. Result: party ⟂ template ⟂ condition.

## 5. Balance guarantees (provable, verified in the report)

| Level | Guarantee |
|---|---|
| Within participant | 6 posts/topic, 12/polarity; each day 4/4/4 by polarity |
| Within condition | each post shown exactly **38×** |
| Across conditions | post-sets **identical** (matched); each post 38× in *every* condition, **152× total** |
| Conditions | exactly **114** participants each |
| Party × condition | exactly **57** per cell (228 Dem, 228 Rep) |
| Party × template | 2 Dem + 2 Rep per template |

Total exposures: 456 × 36 = 16,416 = 108 posts × 152. ✓

## 6. Artifacts (Phase 1, committed under `study/data/profiles/`)

456 profiles as:
- **`profiles.json`** — canonical. One object per profile:
  `{profile_id, template_id, condition, target_party, blocks[3][12] (post_ids),
  access_codes[36]}`.
- **`profiles.csv`** — flat, one row per profile-post:
  `profile_id, template_id, condition, target_party, day, position, post_id, tweetId,
  topic, polarity, access_code`.
- **`profiles_report.md`** — the §5 balance tables computed from the generated pool (the
  verification signed off before Phase 2), plus the seed and a co-occurrence/position
  sanity check.

## 7. Interface wiring (Phase 2, after allocation sign-off)

- **Load** the pool into the study store as *unclaimed* profiles (new `studyprofiles`
  concept; assignments still land in `studyassignments` on claim so the existing
  session/thread/exposure path is unchanged).
- **Recruitment:** two Prolific studies, prescreen-filtered to Democrats and Republicans;
  each entry link tags `party`. The `/api/session` claim reads `party`. Because this is a
  3-day longitudinal study, **over-recruit** (enroll a buffer above 228/party to net 228
  completes); the release-and-reclaim mechanism (below) recycles abandoned profiles.
- **Claim:** on first `/api/session` for a new `PROLIFIC_PID`, atomically claim the next
  unused profile from that party's sub-pool via **permuted-block randomization on
  condition** (random template within). Bind `pid → profile` by writing the profile's
  condition + blocks into `studyassignments`. Idempotent per `pid`.
- **Validation gate:** cross-check the tagged party against the pre-survey party item
  (QID1); flag/exclude mismatches. Independents are screened out by design.
- **Attrition:** a profile claimed by a participant later marked incomplete/rejected is
  released back to *its own party* sub-pool for re-claim. If attrition exhausts the pool,
  additional templates (115, 116, …) are generated deterministically from the same seed
  stream.
- `assign()`'s live computation is replaced by the claim; `/browse` and legacy QA links
  keep working.

### 7.1 Recruitment cost estimate
See §Cost estimate at the end (computed against current Prolific rates + per-day task-time
estimates).

## 8. Preregistered analysis (documented for the paper)

- **RQ1 (engagement — enjoyment / informativeness / quality, post-level):**
  `rating ~ condition * party + (1 | participant) + (1 | post)` (crossed random effects for
  participants and posts — required to realize the matched design and to generalize over
  the post population).
- **RQ2 (de-antagonizing — pre/post affective polarization change, participant-level,
  CONFIRMATORY):** ANCOVA with the pre-score as covariate + condition + party +
  condition × party. Split outcomes into *antagonistic* and *agonistic* subscales. The
  pre-score covariate is the basis of the n = 456 power (see §3).
- **RQ3 (AI vs. crowd):** condition contrast of the three bot arms vs. control; interpreted
  as a multi-dimensional (source + format + persona) contrast, not a single-factor one.
- **Monitoring:** attrition by arm, realized (completed) balance vs. assigned balance, and
  per-post × condition rating-completion (differential item missingness).

## 9. Threats to validity (for the limitations section)

1. **Differential attrition by condition** — primary residual internal-validity threat over
   the multi-day study; monitor by arm and run a completion-based sensitivity analysis.
   Larger n does not remove this (3 days rather than 6 reduces, but does not eliminate, it).
2. **Control-arm bundle** — Community Notes differs from the bot arms on source, format, and
   persona simultaneously.
3. **Stimulus scope** — all stimuli are Community-Notes-flagged-misleading posts on 6
   topics; the bot's false-positive rate is structurally unmeasured (see the bias-audit
   doc). Generalization is bounded.
4. **Power / dose** — n = 456 powers RQ2 for d ≥ 0.30 (ANCOVA) and RQ1/medium interactions
   well; a truly-small polarization effect (d < 0.30), a dose-dependent effect needing > 36
   posts, or a multiple-comparison-corrected RQ2 family remains a risk (see §3 upgrade
   paths). Pilot RQ1 effects are from n = 10.
5. **Ecological validity** — a paid mock-interface rating task is not organic engagement.
6. **Self-reported party** — Prolific prescreen vs. in-survey; mitigated by the cross-check.

## 10. Paper-ready methods paragraph (draft)

> We recruited 456 participants (228 self-identified Democrats, 228 Republicans, prescreened
> on Prolific) into a between-subjects 2 (party) × 4 (tone: neutral, agreeable, satirical,
> Community-Notes control) design, 57 per cell (114 per tone arm). The sample size was set to
> detect a small-to-moderate effect (d = 0.30) on affective-polarization change at 80% power
> via ANCOVA with a baseline covariate. Stimuli were 108 political posts, each flagged as
> misleading by X's Community Notes, balanced across six topics (LGBT, immigration,
> healthcare, cost of living, religion, race) and three viewpoint-polarity levels (left /
> center / right). We constructed 114 matched post-set *templates*; each template comprised
> 36 posts — two drawn from every topic × polarity cell — split into three daily blocks of
> twelve, with each day balanced across polarity. Every template was instantiated in all four
> tone conditions with identical posts, day layout, and order, so that post content was held
> fixed across the tone manipulation; each post appeared exactly 38 times per condition (152
> times overall). Participants were assigned to profiles by permuted-block randomization on
> condition within party, with allocation concealed. The full 456-profile allocation was
> generated deterministically from a fixed seed prior to recruitment (seed recorded in the
> released materials).

## 11. Open items / assumptions

- **Sample size 456** (114/arm, 57/cell), **dose 36 posts**, **3 days × 12/day**. Revisit
  n if the RQ2 primary family needs multiple-comparison protection or 90% power (→ ~608) or
  a smaller target effect (→ ~1016). See `study/docs/power-analysis-2026-07-16.md`.
- **Balanced 228 Dem / 228 Rep** (recommended for the factorial).
- Exact RNG seed value fixed at implementation time and recorded in `profiles_report.md`
  and §10.
- Prolific two-study vs. single-screener recruitment mechanics, and the over-recruitment
  buffer, confirmed during Phase 2.

## 12. Out of scope

- Survey instrument edits (agonism measure, daily-loop rewiring) — tracked separately.
- Regeneration of the 108 stimuli / fact-check pipeline changes.
- The power-analysis computation itself lives in `study/docs/power-analysis-2026-07-16.md`.

## Cost estimate (Prolific, 2026 rates)

Rates: recommended **$12/hr** ($8/hr floor), **+33.3% service fee** on participant reward;
VAT typically N/A for a US institution (confirm with grants office).

**Per-participant task time** (36 posts / 3 days, 12/day; ~45–60 s per post read+rate):

| Session | Contents | Time |
|---|---|---|
| Day 1 | pre-survey + 12 posts | ~16.5–21 min |
| Day 2 | 12 posts | ~10.5–14 min |
| Day 3 | 12 posts + post-survey | ~19.5–24 min |
| **Total** | | **~47–59 min** |

**Cost at recommended $12/hr (incl. 33.3% fee):** ~$12.40–$15.73 per completer →
**~$5,650–$7,175 for 456 completers.** Add a 3-day over-recruitment buffer (75–85%
retention) → **budget ≈ $6,100–$8,000.** At the $8/hr floor it is ~$3,800–$4,800 (not
recommended — Prolific flags underpayment and it hurts data quality).

For comparison, the earlier 54-posts / 6-days plan would have been ~$7,840 for the same 456
*before* heavier 6-day attrition — so this package roughly halves spend and dropout risk.

**Practical:** validate the per-post time with a soft launch (~10–20 participants) before
full field — Prolific auto-flags if median completion exceeds the estimate you set. Scaling
to the 608 upgrade (multiplicity/90%) would raise the estimate proportionally (~$8–9k
central).
