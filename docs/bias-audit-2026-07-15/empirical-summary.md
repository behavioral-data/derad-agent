# Empirical bias signals (grounding for the audit) — 2026-07-15

## Verdict strength by polarity (negative=right-coded, positive=left-coded), n=36 each

### NEW v0.7 design (from today's freezes)
| polarity | refuted | context | challenge | supported/ENDORSE | nei/unavail |
|---|---|---|---|---|---|
| negative (right) | 14 | 9 | 1 | 0 | 12 |
| center | 13 | 5 | 3 | 0 | 15 |
| positive (left) | 4 | 15 | 0 | 3 | 14 |
All 3 residual endorsements = left-coded, technically-true posts (real Missouri vote, Tesla real $0 tax, real IOC announcement).

### OLD pipeline (first audit grades)
| polarity | agree | partial | disagree(ENDORSED) | no_result | mean severity | beat-note |
|---|---|---|---|---|---|---|
| negative (right) | 23 | 11 | 2 | 0 | 0.86 | 13 |
| center | 23 | 9 | 1 | 3 | 1.03 | 10 |
| positive (left) | 14 | 13 | 7 | 2 | 1.19 | 8 |

## Consistent pattern across BOTH pipelines
The agent handles right-coded misinformation well and systematically UNDERPERFORMS on left-coded misinformation: lower note-agreement, more endorsements (7 vs 2 old; 3 vs 0 new), higher severity, fewer refutations (4 vs 14 new).

## Leading hypothesis (to be confirmed by the political-symmetry auditor)
The disparity is substantially COMPOSITION-driven: left-coded misleading posts in this set are disproportionately "technically-true-but-framed" (a real vote/stat, spun) — the exact case a verify-the-literal-claim agent mishandles by endorsing the true core — while right-coded ones are more often outright fabrications (easy to refute). So it is the ORIGINAL "true-but-misleading → endorse" failure, now shown to be polarity-linked. Whether an ADDITIONAL genuine-partisanship component exists must be tested WITHIN misinformation-type (auditor PS task).

## Why it matters regardless of cause
Even if 100% composition, the agent's quality is polarity-dependent → for a misinformation study, the intervention's efficacy would confound with post political lean. Must be measured, reported, and mitigated.
