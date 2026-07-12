# D4 — Tone-invariance verification (LLM-judge, current 108×3 renders)

Method: all 108 posts × {neutral, satirical, agreeable} bodies (sources stripped),
variant order randomized per post (seed 44), tone labels withheld; 6 independent Opus
judges graded verdict direction/strength, load-bearing fact sets, perceived register,
and substance equivalence. Key: `tone_key.json`; grades: `tone_grades_all.json`
(session scratchpad; summary here is self-contained).

## Results

| metric | value |
|---|---|
| severity 0 (equivalent / cosmetic) | 47/108 (44%) |
| severity 1 (strength or supporting-fact drift) | 53/108 (49%) |
| **severity 2 (different reader takeaway)** | **8/108 (7%)** |
| **verdict-direction flips across variants** | **5 posts** |
| mean verdict strength (neutral / agreeable / satirical) | 4.28 / 4.17 / 4.11 |
| blind register identification | neutral 108/108 · satirical 108/108 · **agreeable 76/108** (32 read as neutral) |

Judges varied in how strictly they mapped severity-1 to the boolean
`substance_equivalent` flag (77/108 true), so severity counts are the comparable
metric.

## Findings

1. **Invariance holds at the verdict-label level, not the content level.** Mean
   strength is stable across tones (no systematic softening), but 7% of triplets give
   a reader a different takeaway and 5 posts flip verdict direction outright —
   including the canonical gas-price post, where neutral/agreeable *endorse* the
   streak and satirical *challenges* it with numbers absent from the other two.
2. **Cross-variant fact conflicts — the renderer generates beyond the frozen
   payload.** Same source, different numbers ("12,000+" vs "350,000+" statements;
   Guttmacher 1.5M vs 762K); one variant confabulates provenance ("PitchBook failed
   to load during fact-checking" — internal pipeline detail leaked into a
   user-facing reply, and contradicting its siblings' explanation).
3. **Systematic satirical fact-shedding.** Across all six batches, the satirical
   variant tends to drop one load-bearing number or named source to make room for the
   joke (death tolls, inflation figures, scope caveats, corroborating sources). This
   is an informational-content confound *correlated with condition*: satirical
   replies systematically carry fewer facts than neutral ones.
4. **Agreeable register is under-dosed:** 30% of agreeable replies read as neutral to
   a blind judge. Manipulation strength should be confirmed in the planned human
   validation; may need register strengthening.

## Design consequences (folded into v0.7)

- **R-4 render-substance lint (mechanical):** every numeral and named source in a
  rendered reply must appear in the frozen payload; no internal pipeline details in
  user-facing text. Reject/retry the render otherwise.
- **R-5 cross-tone fact-set check:** the load-bearing facts of the frozen
  `headline_finding`/justification must appear in EVERY tone variant; a variant may
  not add substantive facts absent from the payload.
- **Render-as-transformation (recommended):** generate the neutral render first, then
  produce satirical/agreeable as register *transformations of the neutral text* with
  facts held fixed — three independent generations from the payload is what allows
  divergence.
- The 8 severity-2 / 5 flip posts are moot for fielding **iff** stimuli are
  regenerated under v0.7 with these lints; if any current renders are fielded, those
  posts must be re-rendered first.

Affected posts (severity 2): 2032022382068535386, 2013888094349434888,
2016954023190950396, 2067837054596092030, 2067622979983307080, 2046585043263344943,
2016511641827914005, 2054497824377909410. Direction flips additionally:
2057084694991114514.
