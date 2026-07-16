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
