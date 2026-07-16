# Study 2 Power Analysis (2026-07-16)

**Purpose.** Decide whether n = 200 (25 per party×tone cell, 50 per tone arm) is adequately
powered, and quantify what we can and cannot detect. Script:
`scratchpad/power_analysis.py` (deterministic, seed 20260716).

## Method

- **Design.** Between-subjects party (2) × tone (4: neutral, agreeable, satirical, control),
  25/cell, 50/tone arm, 100/party; each participant rates k = 54 posts.
- **Key structural fact.** Tone is *between-subjects*, so the 54 ratings per participant
  sharpen each participant's mean but do **not** add independent tone evidence. The
  sufficient statistic for a tone contrast is the participant mean, whose sampling variance
  is `Var_between + Var_resid / k`. Power is therefore governed by the **number of
  participants**, not the number of ratings.
- **Anchors (RQ1).** Study-1 pilot mixed models (`daily_survey.ipynb`): between-participant
  variance, residual variance, and tone effects in slider points (below). Pilot n = 10, so
  these point estimates have very wide CIs and may be optimistic (small-sample/winner's
  curse) — treated as one anchor, swept across an effect grid.
- **RQ2** has no pilot anchor → standardized-effect (Cohen's d) grid, with an ANCOVA variant
  using a pre-score covariate (r = 0.6, typical for polarization scales).
- Two-sided α = .05; exact noncentral-t power + Monte-Carlo check (4000 reps) that applies
  Holm correction across the primary contrast family. "Holm/3" ≈ correcting over 3 primary
  contrasts.

## RQ1 — engagement (enjoyment / informativeness / satisfaction), n = 50/arm

Participant-mean SD ≈ 0.83–0.99 (ICC 0.23–0.36). Power at the **pilot** effect sizes:

| Contrast | enjoyment | informativeness | satisfaction |
|---|---|---|---|
| satirical − agreeable (Δ≈1.0 pt, d≈0.6) | **1.00** | **1.00** | **1.00** |
| satirical − neutral (Δ≈0.5 pt, d≈0.3) | 0.65 (Holm 0.48) | 0.85 (0.72) | 0.87 (0.74) |
| neutral − agreeable (Δ≈0.5 pt, d≈0.35) | 0.84 (0.71) | 0.70 (0.53) | 0.95 (0.88) |
| omnibus 4-way ANOVA | **1.00** | — | — |

- **MDE @ 80% power:** ≈ 0.47–0.56 slider points (d ≈ 0.28–0.34) uncorrected;
  ≈ 0.55–0.65 pts (d ≈ 0.32–0.40) Holm-corrected.
- **n/arm for the small satirical−neutral contrast @ 80%:** enjoyment 71 (Holm 95),
  informativeness 45 (60), satisfaction 42 (56).

**Read.** The headline H1 (*satirical is highest*) is well covered at 50/arm: the omnibus and
every satirical-vs-agreeable contrast are ~100%, and satirical-vs-neutral/control is adequate
for informativeness/satisfaction. The one soft spot is **satirical-vs-neutral on enjoyment**
(~0.60 under Holm) — the smallest gap. It firms up at ~65–70/arm.

## RQ2 — affective-polarization change, n = 50/arm

| d | power (change score) | power (ANCOVA r=0.6) |
|---|---|---|
| 0.10 | 0.08 | 0.09 |
| 0.20 | 0.17 | 0.24 |
| 0.30 | 0.32 | 0.46 |
| 0.40 | 0.51 | 0.70 |
| 0.50 | 0.70 | 0.87 |

- **MDE @ 80%:** d ≈ 0.57 (change score), d ≈ 0.45 (ANCOVA).
- **n/arm needed:** d=0.30 → 176 (ANCOVA 113); d=0.20 → 394 (ANCOVA 253).

**Read.** At 50/arm we can only detect **large** polarization effects (d ≳ 0.45–0.5).
Affective-polarization interventions typically produce **small** effects (d ≈ 0.1–0.3), for
which this sample is badly underpowered. Powering RQ2 for a small effect (d = 0.3) needs
≈ 110–175/arm (≈ 450–700 total).

## Party × condition interaction (25/cell)

Even a d = 0.5 *simple* difference at 25/cell is only ~0.41 power, and true interaction
(difference-of-differences) contrasts are strictly harder. **Interactions are exploratory
only** at this n.

## Caveats

- Pilot effects come from n = 10 with CIs spanning zero → possibly inflated; if the true
  satirical−neutral effect is d ≈ 0.2, even RQ1 needs more (~90+/arm).
- Control arm assumed ≈ neutral for the RQ1 Monte-Carlo (no pilot control data).
- ANCOVA gain assumes pre/post r = 0.6; verify against the pre/post battery once piloted.

## Recommendation

- **If RQ1 (engagement) is the primary confirmatory endpoint:** n = 200 is adequate; a
  modest bump to **~260 (65/arm)** removes the only soft spot (satirical−neutral enjoyment
  under multiplicity).
- **If RQ2 (de-antagonizing polarization) is co-primary:** n = 200 is underpowered for
  realistic small effects. Either (a) scale to ~450–700 total, (b) reframe RQ2 as
  secondary/exploratory, or (c) increase RQ2 sensitivity — ANCOVA (already planned) plus a
  well-targeted antagonism/agonism DV (the agonism-item gap flagged in the survey review
  matters here: a sharper DV yields a larger, more detectable effect).
- **Interactions (party × tone):** exploratory regardless.

## Decision (2026-07-16)

**Scale up so RQ2 is confirmatory: n = 456** (114 per tone arm, 57 per party×tone cell,
228 Democrats / 228 Republicans). This powers the RQ2 polarization-change tone contrast at
**d = 0.30, 80%, ANCOVA (r = 0.6)** — the clean, party-divisible point matching the
~450-participant target. Bonus at this n: RQ1's weakest contrast (satirical−neutral
enjoyment) ≈ 0.94, and party × tone interactions ≈ 0.91 for a medium (d = 0.5) effect.

Recorded upgrade paths if requirements tighten:
- **~608 total (152/arm)** — adds multiple-comparison protection on the RQ2 primary family,
  or lifts RQ2 to 90% power.
- **~1016 total (254/arm)** — powers a truly-small polarization effect (d = 0.20).

Caveat carried into limitations: d = 0.30 is the optimistic edge of "small" for this
literature, and the pilot RQ1 effects come from n = 10.

## Figures

- `power-analysis-2026-07-16/rq1_power_vs_effect.png` — RQ1 power vs satirical lift (n=50).
- `power-analysis-2026-07-16/rq1_power_vs_n.png` — RQ1 power vs n for the small contrast.
- `power-analysis-2026-07-16/rq2_power_vs_effect.png` — RQ2 power vs d (change vs ANCOVA).
