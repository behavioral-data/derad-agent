# Ground-truth & circularity audit — "notes as GT" vs "notes as comparison arm"

**Scope:** the tension between the user's request ("keep high accuracy scores while
considering the Community Notes as ground truth") and decision **D2**
(`design-review-2026-07-10.md`), which removed note-parity tuning and made the notes a
study *comparison arm*. Sources audited: `design-review-2026-07-10.md`,
`v07-validation-2026-07-10.md`, `design-note-parity-v0.6.md`,
`agent/factcheck/prompts/loop_playbook.md` §7, plus the repo's vendored Community Notes
algorithm docs (`tsv_generation/communitynotes/documentation/under-the-hood/`) and a
grep of the pipeline/study code.

---

## Bottom line

You cannot hold all three of these at once:

1. **Notes are infallible ground truth** (so "accuracy" = agreement with notes),
2. **The agent beats the notes** (the project's own data: 31/108 = 29% beat-note in the
   full audit; 9/15 beat-note in v0.7), and
3. **The study is an uncontaminated bot-vs-notes comparison** (D2).

Pick at most two. The project's own measurements already picked #2 — which *falsifies*
#1. A reference the system-under-test beats 29% of the time is, by definition, not
ground truth; it is a strong-but-fallible reference. So "notes as GT" is not a design
choice you can still make — it was empirically ruled out before the question was asked.

The good news: the thing the user actually wants — **a high, defensible accuracy number
in the paper** — is *more* achievable *without* notes-as-GT, not less. Notes-as-GT caps
the agent's accuracy at 100% = "identical to the note" and forces it to inherit every
note bias. An independent-record accuracy metric has no such ceiling (the agent can be
*more* accurate than the crowd) and survives peer review. The reconciliation in §3
gives the user a defensible number **and** a high note-agreement number, honestly
labelled as two different things.

### State of play (the live contradiction)

- **D2 is decided and partly implemented.** The *generation* side is clean: I grepped
  the pipeline — `note-parity`, `hypothetical_note`, `note_parity`, `beat_note` no
  longer appear in any `agent/factcheck/*.py`. Loop playbook §7 no longer references
  notes and explicitly forbids reconstructing them ("Do NOT model this on, search for,
  or attempt to reconstruct any Community Note or crowd fact-check").
- **The *measurement* side is not.** `v07-validation-2026-07-10.md` still reports
  **note-parity (0–3), "agree with note's thrust", and "beat the note" as its headline
  head-to-head metrics** — the exact note-as-GT grading D2 said to retire. The
  symmetric independent rubric D2 promised is still listed as *"Remaining"* in the
  design-review status ("the symmetric-rubric evaluation harness run"). **The user's
  request is, in effect, a request to keep the un-migrated old harness and not finish
  the D2 work.** That is where every problem below actually lives.

---

## Problem catalog

Severity key: **Critical** = invalidates a headline claim / breaks the study design;
**High** = biases the reported number materially; **Medium** = real but boundable.

### Cluster A — Circularity

#### GT-1 — Triple collision: tune-to-notes ∧ grade-against-notes ∧ compare-bot-vs-notes
**Severity: Critical.** These three activities are pairwise incompatible:

- **tune ∧ grade** → Goodhart / teaching-to-the-test. The "accuracy" number measures how
  well you optimised for the metric, not correctness. It is training-on-the-test-set at
  the prompt level.
- **tune ∧ compare** → the comparison is rigged by construction. If the treatment (bot)
  is built to reproduce the control (notes), "does the bot match the notes?" is
  trivially yes, and the study can make **no** independent claim about whether AI
  fact-checks substitute for or complement crowd notes. The independent variable is
  contaminated by the dependent variable.
- **grade ∧ compare** → the rubric privileges one arm. If notes define accuracy, the
  notes score maximally *by definition* and the bot's accuracy is ceilinged at "equals
  the note." You can never report the bot as *more* accurate — which is precisely the
  finding the data keeps producing (GT-2).

**Evidence:** D2 itself names this ("note-parity tuning is circular and is REMOVED …
notes become the comparison arm"); methods reviewer verdict "Not approvable as-is —
objective circularity" (`design-review` reviewer table).
**Fix:** enforce a hard separation of *reference for accuracy* (independent record, §3)
from *object of comparison* (notes). Never let notes play both roles. Generation stays
note-free (already true); measurement must stop using notes as the answer key (not yet
true — GT-3).

#### GT-2 — "Beat the note" is logically incoherent under notes-as-GT
**Severity: Critical.** You cannot simultaneously call notes GT and report the system
beating GT. Under notes-as-GT the phrase has no referent; the moment you accept the
beat-note counts as meaningful, you have conceded notes are a fallible reference, which
contradicts the user's premise.
**Evidence:** `design-note-parity-v0.6.md` §1 "Beats the note 31/108 (29%)";
`v07-validation` "beat the note 4 → 9"; both are reported *next to* note-parity as if
both were valid — they can't both be.
**Fix:** re-express "beat-note" as an **adjudicated-disagreement outcome** measured
against the independent record: on posts where bot and note diverge, an independent
adjudicator (not the note) decides who is correct. Then "bot beat the note in X cases"
is a defensible empirical finding, not a contradiction.

#### GT-3 — Measurement harness still grades against notes (D2 not finished)
**Severity: Critical (this is the actual locus of the problem).** The generation
pipeline was decontaminated but the evaluation was not. The headline v0.7 numbers the
user is looking at *are* the note-as-GT numbers.
**Evidence:** `v07-validation-2026-07-10.md` head-to-head table = agree-with-note /
note-parity / beat-note; design-review status lists the symmetric rubric as still
Remaining.
**Fix:** build and run the D2 symmetric-rubric harness (§3) *before* any accuracy number
goes in the paper. Treat the current v0.7 note-parity numbers as an internal
regression/sanity metric only, never as the reported accuracy.

#### GT-4 — Already-realised contamination (answer leakage, adaptive overfit, judge circularity)
**Severity: High (historical, must not recur).** The tune∧grade circularity has already
bitten once and is documented.
**Evidence:** `design-note-parity-v0.6.md` §2.5: (1) *answer leakage* — playbook
examples literally contained the gas note's numbers ("$2.78 → $4.02, +45%"), the Epstein
implied claim, the Noviello/Goetz charged words, so those retest "fixes" are unusable;
(2) *adaptive overfitting* — patch rules derived from and retested on the same 12
worst-case posts; (3) *judge circularity* — the round-1–3 judge knew variant identities
and graded whether its own recommendations worked.
**Fix:** the §2.5 decontamination rules are correct — keep them and extend to the eval:
production prompts carry zero study-derived examples (done via V1.2-S sanitisation); the
12 tuning posts reported separately from the 96 held-out; generalisation claims require a
*fresh* CN-snapshot sample outside the 108; graders blind to arm identity and with no
stake in the outcome.

#### GT-5 — Process decontamination ≠ metric decontamination
**Severity: High.** Loop playbook §7 forbidding note-*reconstruction* is necessary but
**not sufficient**. Even a bot that never looks at a note is still circular-graded if the
scoring function is "agreement with the note." The bias just moves from generation-time
to evaluation-time.
**Evidence:** §7 forbids modelling/reconstructing notes (generation clean), yet
`v07-validation` still scores note-parity (evaluation dirty). Also the audit's "cites ≥1
of the note's own URLs — 12/108" shows URL-overlap was tracked; rewarding it would
reward mimicry through the back door.
**Fix:** the accuracy metric must reference the independent record, not the note. Report
note-URL overlap only as a *diagnostic of independence* (low overlap + high independent
accuracy = the bot is finding correct answers on its own, the strong result), never as a
score to maximise.

### Cluster B — Note-bias inheritance (what the agent absorbs if notes = GT)

The Community Notes helpfulness algorithm is vendored in-repo, so these are grounded in
the actual mechanism, not folklore.

#### GT-6 — Helpfulness-vote / bridging selection bias
**Severity: High.** Only notes that clear a cross-perspective ("bridging") threshold are
ever shown, so the published corpus is the subset a *politically diverse* rater set
agrees on — selected for palatability across viewpoints, not for correctness.
**Evidence:** `ranking-notes.md:23` "Notes … with a Note Helpfulness Score of 0.40 and
above earn the status of Helpful"; `:72–73` threshold 0.40 admits **<10%** of notes and
requires `abs(factor) < 0.50` for "broad support"; `:100` "Matrix factorization
identifies notes that are liked by people who normally disagree"; `helpful-notes.md:24`
"0.45+ intercept score." A correct-but-partisan correction that one side rejects never
becomes GT.
**Fix:** don't equate "consensus-approved" with "correct." Use the independent record as
GT; keep note-agreement as a *separate* "concordance with crowd consensus" metric, and
expect (and report) legitimate divergences where the bot is right but non-consensus.

#### GT-7 — One-directional corpus: notes can only ever say "misleading," never "accurate"
**Severity: Critical (design-level conflict).** Only notes that flag a post as
*potentially misleading* are eligible for display. There is **no such thing** as a
published note saying "this post is accurate." So notes-as-GT is structurally incapable
of ever scoring the correct verdict *"the post holds up."*
**Evidence:** `ranking-notes.md:23` "only notes that indicate a post is 'potentially
misleading' and earn the status of Helpful are eligible to be displayed." This directly
collides with loop playbook **§6b Accuracy exit / symmetric skepticism**, which requires
the agent to sometimes finalize `verify`+supported ("the post holds up … Never invent a
correction"). Under notes-as-GT, every correct "accurate" verdict is graded as a miss,
and the metric actively rewards manufacturing a correction — the exact failure §6b and
red-team fix T3 were built to prevent.
**Fix:** the accuracy rubric must be able to score "correctly found the post accurate."
That requires the independent record as GT (§3). It also means the accuracy sample must
include some non-misleading or partly-true posts, which the all-CN-flagged 108 by
construction under-represents (see GT-10).

#### GT-8 — Contributor demographics / politics skew
**Severity: Medium–High.** Which posts get noted, and the angle of the correction,
reflect a non-representative contributor population (self-selected, skewed
geography/language/politics/engagement). CN's own guardrails run representative surveys
*because* the contributor pool is not representative.
**Evidence:** `contributor-scores.md` (rater filtering / diversity-of-perspectives
gating); `guardrails.md:74` (pre-rollout representative surveys of political viewpoints —
an explicit acknowledgement the raw pool needs correction).
**Fix:** same as GT-6 — independent GT; report demographic-sensitivity of note-agreement
as a limitation, not as accuracy.

#### GT-9 — Terseness / length ceiling penalises the agent's main value-add
**Severity: High.** Notes are ~280 chars and typically single-source. Grading
"accuracy" as note-parity rewards terse, single-source corrections and *penalises* the
completeness (baseline numbers, multiple primaries, attributed disputes, calibrated
hedging) the agent is explicitly designed to add.
**Evidence:** the agent's own design optimises completeness (playbook §5 evidence
weighting, §7 completeness self-critique, R-1 baseline retention); the "beat the note"
cases in `v07-validation` (Karmelo Anthony provenance, Caitlin Clark template, Australia
causal attribution) are precisely completeness/provenance wins that a terse note can't
express. Note-parity as headline *caps* exactly these.
**Fix:** the independent rubric scores completeness/specificity/evidence-quality on their
own axes (D2 lists them), so the agent gets credit for exceeding the note, not penalised
for it.

#### GT-10 — Survivorship: the 108 are posts the crowd already succeeded on
**Severity: High.** The study posts are all CN-flagged and note-bearing — i.e. posts
that (a) drew a contributor, (b) reached Helpful status. That is a survivorship-selected
sample of the crowd's *successes*. Grading the bot only here overstates crowd
performance and hides the bot's real marginal value: posts the crowd never noted (most
of the platform) are invisible.
**Evidence:** design-review T3 "study posts are all CN-flagged"; `ranking-notes.md`
Helpful-status gating; CN publishes <10% of notes.
**Fix:** for the *headline generalisable accuracy* claim, draw a fresh sample that
includes posts *without* helpful notes (from the CN snapshot's unnoted/failed-note
population), fact-checked independently. Report the 108 (all-noted) separately as the
"head-to-head where a note exists" condition. This is also the §2.5 "fresh sample for
generalisation" rule.

#### GT-11 — Notes are sometimes wrong; under GT their errors become "correct answers"
**Severity: Critical.** If notes are GT, every inaccurate note is a right answer the bot
is penalised for correcting and rewarded for parroting — inverting the study's purpose.
**Evidence:** the project's own 29% beat-note (v0.6) / 9-of-15 (v0.7); and CN's *own*
system concedes fallibility — `guardrails.md:50` circuit breakers fire when "a number of
notes with the status of Helpful are evaluated as low Accuracy." The GT can be
low-accuracy by its own maintainers' admission.
**Fix:** treat notes as a fallible reference. Independent adjudication of every bot-note
disagreement (GT-2 fix) converts note errors from "penalties" into findings.

#### GT-12 — Temporal asymmetry: notes are written with more hindsight than the agent gets
**Severity: Medium.** Notes surface with delay and are often written days later, citing
sources published after the agent's `created_at + 48h` evidence cutoff. Grading bot
facts against note facts penalises the bot for not knowing post-cutoff information the
note legitimately used.
**Evidence:** D1 (notes surface with delay; bot display offset added to match); playbook
§1 temporal contract (+48h cutoff, contemporaneous voice); design-review **T8** already
concedes this — "Note-parity graded only against note facts *knowable at reply time*."
That fix is a patch on a broken frame; the frame (notes-as-GT) is what forces it.
**Fix:** the independent rubric evaluates each reply *as of its own cutoff* against
contemporaneously-knowable primary evidence — no hindsight leakage from either arm, and
no need to hand-restrict the note's facts.

#### GT-13 — Source/citation-overlap bias
**Severity: Medium.** Rewarding overlap with the note's cited URLs rewards using the same
(often secondary) sources rather than the best primary source. The agent is designed to
prefer primary series (EIA/BLS/BEA/CDC/dockets) — frequently *better* than a note's
secondary link.
**Evidence:** audit "cites ≥1 of the note's own URLs 12/108"; playbook §4(c) primary-data
targeting; v0.6 verdict weighting gives primary sources double weight.
**Fix:** evidence-quality is scored against a source-tier standard in the independent
rubric; note-URL overlap is a diagnostic only (GT-5 fix).

---

## Recommended measurement design

The one idea that dissolves every Critical above: **score accuracy against a third,
independent reference that is neither the bot nor the note; make the note a scored
candidate, not the answer key.**

### Two references, two metrics — never conflated

| construct | reference | metric | role |
|---|---|---|---|
| **Independent correctness** | human-built gold from **primary sources**, blind to the note | accuracy / relevance / evidence-quality / specificity / temporal-validity (the D2 rubric) | **the headline accuracy number** |
| **Crowd concordance** | the Community Note | note-agreement / parity | secondary *convergent-validity* descriptor, explicitly **not** "accuracy" |

### Pipeline

1. **Independent gold construction (the defensible number).** For a defined sample,
   human fact-checkers (LLM-assisted retrieval, human verdict) research each post from
   scratch against primary sources **without reading the note**, and record: correct
   determination (including "post is accurate"), the load-bearing facts, and the best
   sources. This is the accuracy reference. *(Cost driver — see options in §4.)*
2. **Blind symmetric grading.** Bot replies **and** notes are scored against that gold by
   graders blind to origin, on the D2 rubric (accuracy vs verifiable record, relevance to
   the misleading mechanism, evidence quality, specificity, temporal validity). This is
   exactly D2's "applied identically to bot replies AND notes by graders blind to
   origin." Human raters on the sample, not LLM-judge-only (T8).
3. **Disagreement adjudication (turns GT-2/GT-11 into findings).** Where bot and note
   diverge, adjudicate against the gold. Report a table: *bot correct / note correct /
   both partial / both wrong.* This is the honest, publishable home for the 29%
   beat-note.
4. **Note-agreement as a labelled secondary metric.** Still report parity/agree-thrust —
   but titled "Agreement with Community Notes (crowd concordance)," never "accuracy," and
   framed for convergent validity: high independent-accuracy **and** high note-agreement
   ⇒ strong triangulated result; divergence ⇒ examined, not penalised.
5. **Generation stays note-free (keep D2 + §7).** No note reconstruction, no note-derived
   examples, no hypothetical_note field (already removed from code). The bot-vs-notes
   *conditions participants see* use note-free, note-untuned replies, so the experimental
   contrast is uncontaminated.
6. **Anti-mimicry by construction.** Mimicry is un-rewarded at generation (§7 forbids it)
   and at evaluation (gold ≠ note). Note-URL overlap reported only as an independence
   diagnostic (GT-5/GT-13).
7. **Sampling for the generalisation claim (GT-10).** Headline accuracy on a fresh
   CN-snapshot sample that **includes posts without helpful notes** and some
   true/partly-true posts. The all-noted 108 reported separately as the "head-to-head
   where a note exists" condition. Tuning-12 vs held-out-96 always split (§2.5).

### What to report in the paper

- **Headline:** "Independent adjudicators, blind to source and scoring against primary
  sources, rated N% of agent replies accurate — vs M% for the Community Notes on the same
  posts." (No ceiling; can exceed notes; survives review.)
- **Convergence:** agent–note agreement = X% (crowd concordance), with the disagreement
  adjudication table.
- **Diagnostics:** note-URL overlap (independence), per-post floors from T8 (zero
  endorsements of misleading posts; zero unqualified false accusations), temporal_ok,
  hedge-when-uncertain rate.

---

## Reconciling with the user's constraint honestly

The user asked for **"high accuracy scores while considering the Community Notes as
ground truth."** That phrasing bundles two wishes that pull apart under scrutiny: (i) a
high, defensible number for the paper, and (ii) using notes as the yardstick. (i) is very
much achievable; (ii) is the part that breaks. The honest trade-off, stated plainly:

- **You cannot have notes-as-GT *and* a number the agent can exceed *and* a clean
  bot-vs-notes study.** The data already shows the agent exceeding notes ~29% of the
  time, so notes-as-GT is off the table on evidence, independent of D2.
- **Notes-as-GT does not even maximise the number.** It caps accuracy at "identical to a
  terse, one-directional, consensus-selected note" and forces the agent to inherit
  GT-6…GT-13. The independent-record metric has no ceiling and lets the agent's
  completeness/provenance wins *raise* the score.

### Options (pick one — I recommend A)

- **Option A — Independent gold headline + note-agreement secondary (recommended).**
  Fully satisfies D2 and the user's real goal. Headline accuracy is defensible and
  un-ceilinged; note-agreement is still reported (a genuinely high number, honestly
  labelled) so the user keeps their "notes" number too. *Cost:* human adjudication of a
  sample (bound it — e.g. the 15 held-out + a fresh ~30–50, not all 108). This is the
  only option with no Critical left open.

- **Option B — Note-agreement headline, but renamed and de-circularised (cheaper,
  weaker).** Keep the existing note-parity harness as the headline but (1) rename it
  "Agreement with Community Notes," not "accuracy," (2) never tune to notes (already
  true), and (3) bolt on a small independent-adjudicated disagreement analysis showing
  agreement *under-counts* the agent's correctness. Cheaper, no new gold set, but the
  headline is a concordance not an accuracy — reviewers will (rightly) reject it if
  called "accuracy," and GT-7/GT-9/GT-11 remain baked into the number.

- **Option C — Notes-as-GT + tune-to-notes (do NOT do this — named for completeness).**
  Yields a superficially high "accuracy," but it is circular (GT-1/GT-4), kills the
  bot-vs-notes comparison (the study's whole point), inherits every note bias, and cannot
  represent the 29% beat-note. This is the trap the request, taken literally, walks into.

**Recommendation:** Option A. It is the only one that gives the user a high number they
can defend in front of a methods reviewer, keeps the D2 comparison uncontaminated,
refuses to reward mimicry, and treats the notes as what the project's own data shows them
to be — a strong but fallible reference, not ground truth. Practical next step: finish the
D2 symmetric-rubric harness (currently "Remaining"), build the independent gold on the 15
held-out + a fresh sample, and re-run `v07-validation` reporting independent accuracy as
the headline with note-agreement demoted to convergent validity.
