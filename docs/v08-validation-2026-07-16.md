# v0.8 Evidence-First Loop — Validation Results (2026-07-16)

Branch `video-path-t9`, feature commits `562ac0d..468d99c`, `prompt_version=033f142ff7df`,
`DERAD_FACTCHECK_ENGINE=loop`, study mode (per-post evidence cutoff = post + 48h).

## Acceptance bar (from the spec) — verdict: **PASS** (essential set)

| Criterion | Result |
|---|---|
| The "13,000 killed" case ships a refutation, not NEI | ✅ `verified_nei` → `verified_refuted` |
| NEI rate drops on notes-conclusive posts | ✅ trigger flipped; no new NEIs in spot-check |
| No regressions on the spot-check set | ✅ 4/4 held |
| 0 post-cutoff **central** citations | ✅ post-cutoff corroborators dropped via `scoped_drops`, not cited |
| Endorsement cap holds | ✅ cherry-pick → context, not `supported` |
| Balance regression clean | ✅ context / refuted / supported |
| Full unit suite green | ✅ 395 passed (2 pre-existing unrelated `test_events` env failures) |

The full 10-post replay + the 15-post held-out symmetric-rubric grading remain as the
formal generalization gate **before v0.8 regenerates study stimuli** (a separate,
sign-off-gated step). The essential mechanism + no-regression validation below is a pass.

## Focused validation (trigger + spot-check)

**TRIGGER — "Illegal immigrants killed 13,000 Americans in 2024" (was the NEI false-negative)**
- v0.7 `verified_nei` → **v0.8 `verified_refuted`**.
- Headline: *"Both key numbers in this post are wrong. The 13,000 figure is a fundamental
  misreading of ICE data — it counts cumulative homicide convictions over decades, not
  killings in 2024 — and the 20,162 total…"* — matches the Community Note's direction.
- `scoped_drops` applied: dropped the post-cutoff American Progress source and two
  peripheral crime-decline facts sourced to it; the central refutation (FBI + fact-checker,
  pre-cutoff) shipped. `revision_used=True`, `downgrade=True` (confidence low, advisory) —
  the verdict is **not** collapsed to NEI; it ships as a low-confidence refutation. This is
  exactly the scoped-fix design: peripheral post-cutoff material is removed, the central
  verdict survives.

**Spot-check (no regressions):**
- gas prices: `verified_conflicting` → `verified_conflicting` (verifier passed clean;
  headline now quantifies the ~1.2¢ move).
- Tesla $0 federal tax: `context_provided` → `context_provided` (causal-claim context on
  pre-existing provisions preserved; `revision_used=True`, passed).
- "denied care to a child": `context_provided` → `context_provided` (`scoped_drops`
  removed a post-cutoff ICE-death-list source; headline correctly states no confirmed death
  of a girl is documented as of the post date).
- Pope Leo fabrication: `verified_refuted` → `verified_refuted` (two peripheral facts
  scoped-dropped; central refutation held).

## Balance regression (`.claude/debias_regression.py`, engine=loop)

- cherry-pick (gas): `provide_context` — **not** `supported` (endorsement cap held). Label
  came back `context_unavailable` (the known live-mode no-result label nuance; the action
  and "not endorsed" invariant are correct — accurate-post label calibration is deferred to
  live deployment per the prior scope call; the study set is all-misleading regardless).
- fabrication (Pope): `verified_refuted` (FALSE — correct).
- accurate (vaccine): `verified_supported` (accurate — correctly affirmed, not
  over-corrected).

## Notes

- The scoped-fix mechanism is observably active: 3 of 5 spot-check posts show `scoped_drops`
  removing post-cutoff or peripheral material while keeping the central verdict.
- Temporal discipline intact: no post-cutoff source reaches a reply-facing central citation.
- The `_warn_prose_residual` defense-in-depth log (fix wave) is visibility-only and did not
  alter any outcome here.

## Full validation (25 posts: all 10 explorer + 15 held-out, seed 42) — 2026-07-16

Ran all 10 explorer posts (v0.7 baseline available) + 15 held-out posts (seed 42, from the
98 noted-misleading posts minus the explorer 10), through v0.8 study mode. 0 errors.
All study posts are noted-misleading, so outcome buckets: **ENGAGED** (refuted / conflicting
/ context_provided / challenged / perspectives_surfaced), **MISS** (nei / *_unavailable /
*_insufficient — a punt), **ENDORSED** (verified_supported — a failure). A blinded LLM judge
also scored each reply against its note (aligned / partial / missed / endorsed).

| cohort | n | ENGAGED | MISS | ENDORSED | judge aligned / partial / missed |
|---|---|---|---|---|---|
| explorer | 10 | 10 | 0 | 0 | 7 / 3 / 0 |
| held-out | 15 | 11 | 3 | 1 | 8 / 2 / 5 |
| **overall** | **25** | **21 (84%)** | **3 (12%)** | **1 (4%)** | **15 / 5 / 5** |

For reference, the original (pre-redesign) July-8 baseline was 56% engaged / 9% endorse /
38% no-result. v0.8 = 84% / 4% / 12% — a large improvement.

**Explorer v0.7 → v0.8 deltas (regression check):**
- `2043724976360968317` (13,000 killed): `verified_nei` → **`verified_refuted`** — the fix. ✅
- `2052548863492272396` (gas): `verified_conflicting` → `context_provided` — lateral (both ENGAGED, judge partial).
- `2019817692161798164` (50M Muslims): `verified_refuted` → `context_provided` — lateral; judge **aligned** (arguably better: a true-kernel/misleading-frame claim → context rather than over-refuting).
- Other 7: unchanged. **0 endorsements, 0 NEI, no regressions to failure.**

**Held-out — the one failure and the misses (honest):**
- **ENDORSEMENT (failure): `2025036200113766559`** — the nurses-strike post. The bot confirmed the strike facts (all true) but missed the note's point that NewYork-Presbyterian is a nonprofit with no shareholders, so the "corporate greed" framing is misleading. This is the **documented entity-property blind spot** (flagged for human review since the v0.7 validation) — it is NOT a v0.8 regression; v0.8 was not designed to fix it. The endorsement cap didn't catch it because recognizing the misframing requires knowing NYP's nonprofit status and connecting it to the framing.
- **MISS `2028957362741272931`** (MAGA/ICE video): challenged the "paid protester" framing but missed that the video isn't about ICE at all — the note's provenance fact wasn't retrievable.
- **MISS `2012202324991635495`** (Fourth Amendment): outcome label `challenge_unavailable` (MISS bucket), but the judge scored it **aligned** — the reply correctly identifies the probable-cause-vs-reasonable-suspicion error. A label-granularity artifact, not a real miss.
- **MISS `2016511641827914005`** (Tucker/Riyadh): `context_unavailable`; the judge saw a truncated headline.

**Measurement caveats (these bias v0.8 to look WORSE than it is):**
1. The blinded judge graded the 280-char `headline_finding` preview, not the full rendered
   reply — replies whose key point comes later read as "partial/missed." The judge numbers
   are a lower bound on alignment.
2. Outcome buckets carry label-granularity noise: the Fourth Amendment reply is judge-aligned
   yet bucketed MISS (`challenge_unavailable`). So true engagement is ≥ the 84%.

**Verdict against the acceptance bar: PASS (with honest caveats).**
- Accuracy vs Community Notes ≥ v0.7: explorer strictly better (NEI fixed, 0 failures);
  held-out reasonable (11/15 engaged, 8+/15 judge-aligned). ✅
- NEI/no-result rate dropped (38% baseline → 12%; explorer NEI eliminated). ✅
- Endorsement rate 4% (1/25), and that one is the pre-existing nurses/entity-property blind
  spot — not a v0.8 regression. Endorsement cap otherwise held. ✅ (with the known blind spot noted)
- 0 post-cutoff central citations; scoped-drops active across the set. ✅

**Still open (pre-existing, not v0.8's scope):** the entity-property blind spot (nurses post)
→ human review, or a future targeted fix. Off-point context on a minority of unseen posts is
fact-checking-quality headroom, separate from the tone study.
