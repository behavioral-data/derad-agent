# Evaluation-Validity Audit — fact-check agent (v0.7)

**Scope:** validity threats in the *evaluation methodology*, not the pipeline code.
**Docs audited:** `docs/v07-validation-2026-07-10.md`, `docs/design-note-parity-v0.6.md`
(esp. §1, §2, §2.5), `docs/design-review-2026-07-10.md` (D1–D4), `docs/tone-invariance-check-2026-07-10.md`,
and corroborating in-repo grounding in `docs/reviews/review_methods.md`.

**Central worry (confirmed):** every post the agent was validated on is Community-Notes-flagged
as misleading, so the agent is only ever graded on the "this misleads" class. A standing bias
toward "this misleads" scores as success, and the false-positive rate on accurate posts is
literally unmeasured. This is real and it is the top finding — but it is one of several
mutually-reinforcing threats. Below, each is given an ID, a severity, the doc evidence, the
mechanism by which it inflates or distorts the reported numbers, and a concrete fix.

---

## Ranked summary

| ID | Severity | One-line threat |
|---|---|---|
| **EV-1** | CRITICAL | Test set is 100% misleading posts → no true negatives; false-positive rate / specificity unmeasured; base-rate neglect |
| **EV-2** | CRITICAL | Notes-as-gold + "note-parity" measures mimicry, not truth — and the decided fix (D2 symmetric rubric) was never actually run |
| **EV-3** | CRITICAL | Means hide catastrophic per-post failures; 2 "supported" outcomes in the held-out 15 are endorsements by the project's own definition, not surfaced |
| **EV-4** | CRITICAL | Single same-model-family LLM judge, zero human raters, no inter-rater reliability |
| **EV-5** | CRITICAL | Circularity: generator objective and evaluator both point at the note; lint/patch rules structurally overfit to the CN corpus |
| **EV-6** | MAJOR | n=15, single stochastic run, no confidence intervals, uncharacterized regeneration variance |
| **EV-7** | MAJOR | The "held-out 15" is reused across rounds; no fresh out-of-108 sample despite the project's own rule requiring one |
| **EV-8** | MAJOR | Head-to-head baseline is the known-buggy deprecated pipeline (a strawman); no strong baselines |
| **EV-9** | MAJOR | Blinding is likely nominal — the tuned system is de-blindable by house style; no blinding check |
| **EV-10** | MAJOR | Community Notes are themselves unvalidated as ground truth (CRH selection, survivorship, 29% beatable) |
| **EV-11** | MAJOR | 41/108 posts are video the pipeline "cannot see"; held-out video coverage unreported → non-generalization |
| **EV-12** | MAJOR | Secondary cross-family judge dropped in the flagship v0.7 run; its §2.5 dropout was non-random and it disagreed materially |
| **EV-13** | MAJOR | Tone conditions carry an informational confound (satirical fact-shedding) + under-dosed agreeable manipulation |
| **EV-14** | MINOR | `temporal_ok` is near-ceiling and may be self-graded by the pipeline rather than checked structurally |
| **EV-15** | MINOR | No pre-registration; acceptance targets set post-hoc; multiplicity uncorrected; acceptance harness == tuning harness |

---

## CRITICAL

### EV-1 — All test posts are misleading: false-positive rate is structurally unmeasured
**Severity: CRITICAL. Focus 1 (test-set composition, base-rate neglect, true negatives).**

**Evidence.**
- v0.6 §1 grades "all 108 posts, notes = gold standard" and defines the corpus by the presence
  of a note; the held-out set is "15 posts … sampled (seed 42) from the 96 never used for
  tuning" (§2.5) — all 96, and all 108, are CN-flagged misleading posts.
- The design review admits the set cannot exercise the accurate-post path: T3 adds an
  "'post appears accurate — nothing to correct' exit" but scopes it as a "Live-mode
  requirement; **study posts are all CN-flagged so impact there is small.**"
- The metrics are entirely misleading-class: "agree with note's thrust," "note-parity,"
  "beat the note" — there is no "correctly left an accurate post alone" metric anywhere.

**How it inflates/distorts.** On a set where the ground truth is always "misleading," a model
that leans toward finding fault is rewarded and never punished. Precision, specificity, and the
false-positive rate (accurate post → wrongly "corrected") are undefined because there are zero
true negatives. Every reported number (agree 12/15, parity 2.07, beat-note 9) is a
*sensitivity-only* number — recall on one class — dressed up as overall quality. Deployed on a
real timeline, where the base rate of misleading posts is a small single-digit percent, an agent
with an unmeasured FP rate could over-correct the overwhelming majority (accurate) posts and the
current eval would not detect it at all. This is base-rate neglect baked into the sampling frame.

**Fix / what a corrected eval looks like.** Build a **balanced, confusion-matrix-capable** set:
1. Add a **true-negative arm of accurate posts**, ideally *hard negatives* matched to the
   misleading arm on topic, format (text/image/video), virality, and political valence — posts
   that look correctable but are accurate. Sources: posts where a note was *proposed and rated
   NOT-helpful/not-shown*, posts with no note on the same topics, and human-adjudicated
   accurate posts sampled from the general timeline.
2. Report the **full confusion matrix**: TP/FP/TN/FN, sensitivity, **specificity**, precision,
   and false-positive rate, per class.
3. Add an explicit gold category **"no correction warranted"** and measure the rate at which the
   agent correctly stays silent / emits the T3 "appears accurate" exit.
4. Report metrics **weighted to a realistic base rate** (or at minimum report class-conditional
   rates separately and state the deployment base rate), so a low FP rate on a large accurate
   majority is visible rather than assumed away.

---

### EV-2 — Notes-as-gold / "note-parity" measures mimicry, not truth — and the decided fix was never run
**Severity: CRITICAL. Focus 3 (metric design) + Focus 6 (circularity).**

**Evidence.**
- The flagship validation still uses notes as gold: replies are "graded against **the gold
  community notes** by a blinded judge," and the headline metrics are "agree with note's thrust"
  and "mean note-parity (0–3)" (v07-validation, head-to-head table).
- This **directly contradicts the design decision** it is supposed to implement. D2:
  "Notes stop being 'gold'; they become the comparison arm," and "Evaluation shifts to a
  symmetric rubric — accuracy against the verifiable record … applied identically to bot replies
  AND notes by graders blind to origin."
- The note is demonstrably not ground truth: the system "beat the note" 9/15 in v0.7 and 31/108
  (29%) in v0.6 §1. A reference that is beaten ~29% of the time is a fallible comparator.
- Internal contradiction inside one table: "agree with note's thrust" (12) and "beat the note"
  (9) are both reported as wins, but "agreeing with" and "beating" the same reference are
  different (and partly opposing) constructs.

**How it inflates/distorts.** Note-parity rewards *reproducing the note's facts*, i.e. it scores
similarity to the note, not correctness against the world. A reply that parrots a note's error
scores 3; a reply that is *more* correct than the note (the 29% beat-note cases) is penalized as
"non-agreement." Because the generator was tuned to reproduce note facts (see EV-5), a high
parity score partly measures *successful tuning to the metric*, not independent accuracy — the
number is inflated by construction. Most importantly, the methodology the reviewers approved
(D2's origin-blind symmetric rubric, notes as a scored comparison arm) was **decided but not
executed**: v0.7 reverted to notes-as-gold + note-parity. The reported scores therefore do not
reflect the sign-off methodology.

**Fix / what a corrected eval looks like.** Actually run D2:
1. Build an **independent, adjudicated ground-truth key per post** from primary sources (correct
   verdict + load-bearing facts + citations + an explicit "genuinely unsettled" option),
   **blind to both the note and the bot**.
2. Score bot replies **and** the community note on the **same rubric** (accuracy vs record,
   targeting of what makes the post misleading, evidence quality, calibration, non-endorsement,
   no new error) by graders **blind to origin**.
3. Report **"concordance with note" as a descriptive similarity statistic only**, cleanly
   separated from **accuracy**. Report note↔key disagreement (~29%) as a first-class finding
   ("AI vs crowd fact-check quality"), which is exactly the evidence that note-as-gold is unsafe.
4. Give "beat the note" a real definition (bot > note against the independent key), not
   "the same LLM judge preferred it."

---

### EV-3 — Averages hide catastrophic per-post failures; the held-out set already contains hidden endorsements
**Severity: CRITICAL. Focus 3 (metric design).**

**Evidence.**
- v07-validation reports means/counts ("mean note-parity 2.07", "agree 12", "disagree 1") as the
  headline; the mechanical-stats table separately records **"substantive outcomes 11/15 (6
  refuted, 3 context, **2 supported**)."**
- By the project's own definition a "supported" outcome on a misleading post *is a failure*:
  v0.6 §1 — "**Every one of the 11 `verified_supported` outcomes is a failure** — the pipeline
  verified a true literal kernel of a misleading post and thereby endorsed the framing." The
  whole devil's-advocate gate (v0.6 §Stage 4.5) exists "to address the 11/11 `verified_supported`
  failure rate."
- Since every held-out post is misleading (EV-1), each of the **2 supported outcomes is, by
  construction, an endorsement of a misleading post** — the single worst outcome for a
  fact-checker — yet the head-to-head table shows only "disagree 1" and the mean parity of 2.07
  absorbs them.

**How it inflates/distorts.** A mean of 2.07/3 is satisfiable with several 0s offset by 3s; a
"≥ 2.0 mean" target (v0.6 §5) explicitly permits catastrophic per-post failures. The endorsement
— the exact harm the project is built to prevent, and the one that would make a human-subjects
stimulus actively misinforming — is averaged away and *not surfaced* in the headline. Reporting
2 supported outcomes as parity-points while calling the run a 12/15 success materially overstates
safety. It also creates an internal inconsistency (2 supported vs. 1 disagree) that the doc never
reconciles.

**Fix / what a corrected eval looks like.** Replace averages-first reporting with **hard,
zero-tolerance per-post floors reported as counts**, per T8:
- **Zero endorsements** of misleading posts (any "supported"-class outcome on a misleading post
  is a disqualifying failure, human-verified).
- **Zero unqualified false accusations** (P-A/T4 violations).
- **Zero temporal leaks** into reply-facing text.
Report the **full per-post distribution** (not just the mean), broken down per category
(video/text; quote/statistic/causal; polarizing/not). A run with any floor violation fails,
regardless of mean parity.

---

### EV-4 — Single same-family LLM judge, no human raters, no inter-rater reliability
**Severity: CRITICAL. Focus 2 (LLM-judge bias).**

**Evidence.**
- The flagship v0.7 numbers rest on one judge: replies "graded … by **a blinded judge**"
  (singular). No second judge, no human, in v07-validation.
- Generator and judge share a family: the pipeline is a "live Anthropic loop" (v07-validation
  setup) and the primary judge is "blinded Claude" (v0.6 §2.5). Self-preference across a model
  family is a known LLM-judge bias.
- No human raters anywhere in scoring. D3's "human review pass" is a go/no-go screen over
  *stimuli*, not a measurement of accuracy/quality.
- No reliability statistic is reported. The tone check used "6 independent Opus judges" but
  concedes "Judges varied in how strictly they mapped severity-1 …" and gives no κ / α; the
  "6 independent grading agents" (v0.6 §1) are almost certainly one model at different
  seeds/prompts — correlated, not independent.

**How it inflates/distorts.** Every headline number is a single measurement from an instrument
of unknown reliability, produced by the same model family that generated the outputs. Where the
metric is bot-vs-human-note (note-parity, beat-note), same-family self-preference **systematically
tilts toward the AI arm**. And LLM judges share the generator's blind spots: the docs' own
smoking gun is the nurses-strike post, which "every system (old pipeline, simulation, v0.7, both
2026-07-09 judges) has missed" — direct evidence the judges fail to catch the errors that matter
most (endorsing misinformation). An instrument that misses the same errors as the system it grades
cannot certify that system.

**Fix / what a corrected eval looks like.**
1. **Human expert adjudication is the primary instrument** for (a) building the ground-truth key
   and (b) go/no-go on every rendered reply. LLM judges may triage, not certify.
2. Use **≥2 trained human raters** on accuracy; report **inter-rater reliability** (Cohen's/
   Fleiss' κ or Krippendorff's α) and **judge-vs-human agreement** on a subset.
3. Use a **panel of cross-family LLM judges** (e.g., Claude + GPT + Gemini) as a secondary signal;
   report per-judge results and agreement; never report a single same-family judge as the result.
4. Stop calling correlated same-model runs "independent graders."

---

### EV-5 — Circularity: objective and evaluator both point at the note; rules overfit the corpus
**Severity: CRITICAL. Focus 6 (circularity).**

**Evidence.**
- Generation was iteratively tuned to note-parity (v0.6 §2: V1→V1.1→V1.2, "V1 wins where it
  counts for this project — note-parity"), and validation *measures* note-parity (§2.5, v07).
  The methods review states it plainly: "both ends still point at the note … optimizing to the
  evaluation signal at the construct level, which no amount of held-out sampling fixes."
- D2 claims to remove the circular objective ("note-parity tuning is circular and is REMOVED"),
  yet the v0.6 §Stage 4.5 design still ships a **"Note-parity self-critique: reconcile must draft
  `hypothetical_note` … and confirm the payload carries its load-bearing facts,"** and v07-validation
  still *scores* note-parity as primary (EV-2). Residual note-tuning is therefore still live.
- The lint/patch rules (R-1/R-2/R-3, P-A/P-B/P-C/P-D) were "derived from, and retested on, the
  same posts" (§2.5 contamination vector 2) and are baked into the fielded pipeline. Sanitizing
  *literal answer strings* (V1.2-S) does not remove the **structural** encoding of the CN corpus'
  failure taxonomy into the rules.
- The tuning-12 and held-out-15 are both drawn from the same 108 CN posts — the whole eval lives
  inside the distribution the design was shaped on.

**How it inflates/distorts.** When the generator is optimized toward the same target the evaluator
measures, "success" is partly a restatement of the objective. Even with literal answers scrubbed,
the rule *structure* encodes what the corpus' hardest posts needed, so held-out posts from the
same corpus benefit from corpus-shaped priors — inflating apparent generalization. "Beat the note"
counted as a positive while the design was tuned to reproduce note facts makes high parity partly
a measure of tuning success, not accuracy.

**Fix / what a corrected eval looks like.** Break the loop at the construct level:
1. Acceptance metric must be **independent of the generation objective** — a grounded rubric +
   human adjudication (EV-2/EV-4), never a restatement of note-parity.
2. Confirm via the T7 **prompt-hash artifact** that fielded prompts contain no study-derived
   structure that encodes answers.
3. Run the confirmatory accuracy eval on posts **outside the 108, ideally outside CN entirely**
   (a held-out time window, or another platform's flagged+unflagged posts), so corpus-shaped
   rule structure cannot help.

---

## MAJOR

### EV-6 — Underpowered, single-draw, no confidence intervals, uncharacterized regeneration variance
**Severity: MAJOR. Focus 5 (power & generalization).**

**Evidence.**
- Every headline is a count out of 15 (agree 8→12, beat-note 4→9, parity 1.33→2.07); "disagree
  1 vs 1 (same post)" — the entire tail is a single post. No CIs, no significance tests, no power
  analysis.
- The pipeline is a **single live run**: "live Anthropic loop + web_search + page fetching +
  archive.org snapshots"; archive.org was "rate-limited; CDX breaker cycled 5×." Re-running would
  retrieve different evidence and could change verdicts — inherent search nondeterminism.
- The same 15 posts under near-identical systems moved from parity **1.80** (§2.5 primary,
  V1.2-S) to **2.07** (v07), and the doc frames the design-phase number as a range "1.80–2.07
  depending on judge." That 0.27 swing on n=15 *is itself the variance signal* — yet "the
  simulation transferred" is asserted from a single draw at the top of that range.

**How it inflates/distorts.** With n=15, a 4-post swing has a wide confidence interval; several
reported deltas may not be distinguishable from noise. A single stochastic draw can land at the
favorable end of the run-to-run distribution (the 1.80→2.07 gap shows the distribution is wide),
so "reproduced the simulation" may reflect one lucky retrieval rather than a stable property.

**Fix / what a corrected eval looks like.** Treat n=15 as a smoke test.
1. Power a **confirmatory eval** (full-108, better full-170, plus the added true negatives from
   EV-1); report **Wilson CIs** on proportions and **bootstrap CIs** on means, with
   pre-registered tests.
2. **Characterize regeneration variance**: re-run ~20 posts k≥5 times; report variance in verdict,
   parity, and citation set. If that variance is large relative to any claimed effect, the result
   is not stable.
3. Separate **stimulus reproducibility** (freeze = yes) from **method reproducibility** (live
   search = no) in any write-up.

---

### EV-7 — The "held-out 15" is reused across rounds; no fresh out-of-108 sample
**Severity: MAJOR. Focus 4/6 (contamination, generalization).**

**Evidence.**
- The same seed-42 15-post set is used for the §2.5 decontaminated validation (V1.2-S) **and** the
  v0.7 validation ("the 15 held-out posts (seed 42; never used for design tuning)"), and its
  results fed the "Before the 108-post regeneration" follow-up ticket both times.
- v07-validation explicitly compares against and matches the design-phase numbers on those same
  posts ("The design-phase held-out numbers … are reproduced"), i.e. the set has been examined
  repeatedly.
- The project's **own rule** says this is not enough: "generalization claims for the paper require
  a fresh sample from the CN snapshot outside the 108" (§2.5, Rules going forward) — which the v0.7
  validation did **not** do.

**How it inflates/distorts.** Repeated looks at the same "held-out" set across design iterations
turn it into a de-facto development set: decisions (follow-up tickets, "temporal-leak payload
softening," verifier calibration) are made in response to its results, so it no longer provides an
unbiased generalization estimate. The multiple-comparisons risk compounds (EV-15).

**Fix.** Freeze a **new, never-inspected** confirmatory set from the CN snapshot outside the 108
(and outside CN entirely for the strongest claim, per EV-5), draw it once, evaluate once, and
report that as the generalization result. Keep the seed-42 set as a fixed dev set, reported
separately and never as generalization evidence.

---

### EV-8 — Head-to-head baseline is a known-buggy strawman
**Severity: MAJOR. Focus 4 (contamination/comparison design).**

**Evidence.**
- The comparison is "head-to-head vs **the original production replies from the July 8 run**" —
  i.e. the deprecated pipeline whose failures motivated the entire redesign.
- That baseline is documented as buggy: v0.6 §1 notes "19 of those 'NEI' runs only read fine
  because of a renderer bug that leaks reconcile's substantive payload," plus effort inversion,
  no date anchor, dropped fetch-failed sources, etc. (§1 defects list).
- Result: "judged better overall 12 (1 tie)" and "beat the note 4→9" against that baseline.

**How it inflates/distorts.** "12/15 better than the old broken pipeline" measures *distance from
a strawman*, not accuracy. Large relative gains are expected when the comparator is the system you
already diagnosed as defective. There is no comparison to a *strong* alternative (a competent
retrieval baseline, a different-family agent, or the note scored on the same rubric), so the
numbers overstate how good v0.7 is in absolute terms.

**Fix.** Add real comparators scored on the same grounded rubric, origin-blind: (a) the community
note itself, (b) a simple strong retrieval/RAG baseline, (c) ideally a different-family agent.
Report v0.7 against these, not primarily against the deprecated pipeline.

---

### EV-9 — Blinding is likely nominal (de-blindable by house style)
**Severity: MAJOR. Focus 4 (blinding).**

**Evidence.**
- Blinding is procedural: "randomized A/B per post, key withheld" (v07); "per-post randomized A/B
  labels (assignment key withheld)" (§2.5). Good on paper.
- But the tuned system has strong structural tells baked in by design: R-1 "the longest
  decision-relevant baseline must LEAD the reply," note-parity-driven inclusion of exact
  counter-numbers, contemporaneous voice, provenance-account naming, more sources. The methods
  review flags this: "the tuned system has a recognizable house style … a judge can infer which
  arm is new without the key."
- No blinding/manipulation check is reported (no test that the judge cannot guess the arm).

**How it inflates/distorts.** If a same-family judge (EV-4) can recognize the new system's house
style, it can apply its prior that "the fact-check that leads with a baseline number and names
sources is the good one," de-blinding the comparison and converting a style preference into an
apparent accuracy win. Withholding the key does not blind if the arms are stylistically separable.

**Fix.** Run a **blinding check**: have judges guess which arm is the new system; > chance means
blinding failed. If it fails, normalize style/length across arms (or match on length and
source-count) before grading, and rely on human graders instructed to score substance, not polish.

---

### EV-10 — Community Notes are themselves unvalidated as ground truth
**Severity: MAJOR. Focus 3 (notes-as-ground-truth) + additional.**

**Evidence.**
- The corpus keeps only posts with a `CURRENTLY_RATED_HELPFUL` note (methods review, citing
  `spec-mock-x-interface.md:53`), i.e. notes that passed bridging/cross-partisan selection — a
  strong selection filter with contributor-population skew and survivorship (posts whose correct
  rebuttal is hard often have weak or no note and are excluded).
- The design's own data shows notes are fallible: agent "beats the note" 29% (§1) / 9/15 (v07),
  cites the note's own URLs only 11% (§1).
- The nurses-strike post: the note's nonprofit/no-shareholders point was missed by every system
  and both judges — but this also demonstrates notes can be incomplete/miscalibrated, and there is
  no independent check that the notes are correct.

**How it inflates/distorts.** Any metric anchored on notes imports the notes' selection bias and
errors. Where a note is wrong or partial, "agree with note" rewards agreeing with an error and
"disagree" can penalize a correct reply; note-parity caps the measured quality at the note's
ceiling. Because CRH selection also biases *which* posts are in the corpus at all, the corpus is
not a representative sample of misleading content — it is the subset the crowd could bridge on.

**Fix.** Do not use notes as the ruler (EV-2). Build an **independent adjudicated key**; score the
note against it too; **report note↔key disagreement as a headline finding**; and **condition on
note bridging/helpfulness strength** as a moderator so note heterogeneity is modeled, not assumed
away.

---

### EV-11 — 41/108 posts are video the pipeline cannot see; held-out coverage unreported
**Severity: MAJOR. Focus 5 (generalization).**

**Evidence.**
- "**41/108 posts are videos the pipeline cannot see at all**" (v0.6 §1 defect 4; T9 in the design
  review calls the regeneration "not valid without the video path"; still an open blocker per
  v07-validation follow-up ticket 2 and §2.5).
- The v0.7 validation does not report how many of the held-out 15 are video, nor stratify results
  by modality. Its two documented losses in §2.5 were "posts whose checkable core lived in
  attached media the simulation couldn't see."

**How it inflates/distorts.** If the 15 under-represent video (or video posts were quietly the
"losses"), the reported numbers reflect the tractable text subset and over-state performance on
the full corpus, a third of which the pipeline processes blind against exactly the content the note
addresses. A claim about "the 108" from a text-weighted 15 does not generalize.

**Fix.** Report modality coverage of every eval set; **stratify all metrics by video vs non-video**;
do not ship (or count as validated) replies for posts processed blind until the T9 multimodal path
is real and separately validated; ensure the confirmatory set matches the full corpus' modality mix.

---

### EV-12 — Secondary judge dropped in the flagship run; its dropout was non-random and it disagreed
**Severity: MAJOR. Focus 2 (judge robustness).**

**Evidence.**
- §2.5 had a cross-family second judge ("gpt-5 on Azure"); the flagship v0.7 validation has none.
- That secondary judge **disagreed materially** with the primary: gpt-5 gave agree 8/14 and parity
  1.57 vs the primary's 12/15 and 1.80 on the same posts — and the design review's own framing
  ("agree 12/15, parity 1.80–2.07 **depending on judge**") concedes judge dependence.
- Its dropout was **non-random**: "1 of 15 posts lost to its content filter." Content filters fire
  on the most sensitive posts — plausibly exactly where a fact-checker behaves worst — so dropping
  to n=14 without accounting for it is informative missingness. (Memory also records that a
  fallback search model "silently refuses on sensitive queries," the same failure class biasing
  this corpus.)

**How it inflates/distorts.** Reporting only the more favorable same-family judge in the flagship
run is a form of judge cherry-picking: the secondary judge's lower scores (1.57 vs 2.07) are the
disconfirming evidence, and it was removed. Silently analyzing n=14 treats the filtered post as
missing-at-random when it is likely missing *because* it is hard/sensitive, biasing the secondary
result upward too.

**Fix.** Keep a **cross-family panel in the confirmatory run**; report **all** judges and their
agreement (EV-4). Treat filtered/refused posts as **informative missingness**: report them
explicitly, and grade them with a judge that can (or with humans) rather than dropping them.

---

### EV-13 — Tone conditions carry an informational confound and an under-dosed manipulation
**Severity: MAJOR. Additional (feeds the downstream causal study).**

**Evidence.**
- The tone-invariance check found **"systematic satirical fact-shedding … the satirical variant
  tends to drop one load-bearing number or named source … an informational-content confound
  *correlated with condition*."** Also "7% different-takeaway, 5 verdict-direction flips," and
  "cross-variant fact conflicts — the renderer generates beyond the frozen payload."
- The agreeable manipulation is under-dosed: **"30% of agreeable replies read as neutral to a
  blind judge"** (agreeable blind-ID 76/108).
- The fixes (R-4/R-5, render-as-transformation) are adopted "into v0.7," but human validation is
  deferred ("Human validation later, per plan") and the currently-fielded 108×3 renders include
  8 severity-2 and 5 flip posts.

**How it inflates/distorts.** If tone (the study's independent variable) is confounded with how
many facts the reply carries, any downstream effect attributed to *tone* is partly an effect of
*information content* — an internal-validity threat to the actual experiment the fact-check eval
feeds. Simultaneously, if the agreeable manipulation is too weak to be perceived (30% read as
neutral), the study may fail a manipulation check / be underpowered to detect a tone effect at all
(construct validity). Verdict-direction flips across tones mean "substance held fixed across tones"
is asserted, not demonstrated.

**Fix.** Before fielding: (a) rubric-code all rendered replies for presence of *every* load-bearing
fact, verdict direction, and confidence, and confirm identical across the three tones per post
(enforce R-4/R-5, re-render the 8 severity-2 / 5 flip posts); (b) run a **human manipulation-check
pilot** — tones must differ on *perceived tone* but NOT on *perceived verdict strength/direction*;
strengthen the agreeable register until it is reliably identified; (c) match length / reading-level
/ claim-count across tones so tone does not smuggle in content.

---

## MINOR

### EV-14 — `temporal_ok` is near-ceiling and possibly self-graded, not structurally checked
**Severity: MINOR. Focus 3/5.**

**Evidence.** `temporal_ok` moves 14/15 → 15/15 (a one-post change, within noise) and is reported
as a top-line success. The pipeline contains its own "independent verifier" and "verifier-flagged
temporal leaks 2"; it is unclear whether reported `temporal_ok` is the blinded judge's reading of
the prose or the pipeline's own verifier signal. v0.6 §5's acceptance criterion "100% temporal_ok"
is likewise ambiguous.

**How it distorts.** If `temporal_ok` is the pipeline verifying itself, it is not an independent
metric (self-grading). An LLM reading prose also won't catch a live-linked source whose *actual*
publication date is post-cutoff.

**Fix.** Make temporal validity a **structural check** (compare each cited URL's real publication
date against the cutoff, per R-2), computed independently of the pipeline, and report it as such.
De-emphasize a near-ceiling metric as evidence of improvement.

### EV-15 — No pre-registration; post-hoc targets; uncorrected multiplicity; acceptance == tuning harness
**Severity: MINOR (methodological hygiene, amplifies the above).**

**Evidence.** Acceptance targets ("≥85% agree, mean note-parity ≥ 2.0, 100% temporal_ok," v0.6 §5)
were set after the V1/V1.1/V1.2/V1.2-S/v0.7 iteration rounds; many metrics × judges × rounds are
reported with no multiplicity correction; and the acceptance harness is "the *same* 6-grader ×
judge harness used for tuning" (methods review 3.5) — hitting the bar is optimizing to the metric.

**How it distorts.** A garden-of-forking-paths across iterations plus post-hoc thresholds inflates
the chance that a favorable result is selection, not signal; reusing the tuning harness for
acceptance means "passing" can be metric-gaming.

**Fix.** **Pre-register** the primary metric, analysis, and go/no-go thresholds before the
confirmatory run; correct for multiple comparisons; make the **acceptance harness disjoint from the
tuning harness** (different graders/rubric instance, human-adjudicated).

---

## What a corrected evaluation looks like (consolidated)

1. **Balanced set with true negatives** (EV-1): misleading + hard-negative accurate posts, matched
   on topic/format/virality/valence; report the full confusion matrix (specificity, FPR,
   precision), weighted to a realistic deployment base rate.
2. **Ground truth independent of the note** (EV-2, EV-5, EV-10): a per-post adjudicated key from
   primary sources, blind to note and bot, with an "unsettled" option and inter-annotator κ/α.
3. **Score bot AND note on one origin-blind rubric** (D2, as decided but never run): accuracy,
   targeting, evidence quality, calibration, non-endorsement, no new error. "Note concordance" is
   a descriptive similarity stat, not the accuracy metric.
4. **Per-post zero-tolerance floors** (EV-3): zero endorsements, zero false accusations, zero
   temporal leaks — reported as counts alongside the full distribution, never folded into a mean.
5. **Humans as the primary instrument + cross-family LLM panel** (EV-4, EV-12): ≥2 human raters,
   reported IRR and human-vs-judge agreement; all judges reported; filtered posts treated as
   informative missingness.
6. **Powered, pre-registered, interval-reported confirmatory run** on a **fresh out-of-108 (ideally
   out-of-CN) set** (EV-6, EV-7, EV-15), with CIs, tests, multiplicity correction, and a disjoint
   acceptance harness.
7. **Strong baselines, not the strawman** (EV-8), and a **blinding check** proving arms aren't
   de-blindable by style (EV-9).
8. **Modality stratification** with the video path validated before video posts count (EV-11);
   **structural** temporal checks (EV-14).
9. **Manipulation checks for the tone conditions** proving substance is constant and the register
   is perceptible (EV-13).

**Bottom line.** The reported v0.7 numbers (agree 12/15, parity 2.07, beat-note 9, "better overall"
12) are sensitivity-only measurements, on an all-misleading set, against a strawman baseline,
scored by a single same-family LLM judge using a mimicry metric that the project had already
decided to abandon — with catastrophic per-post failures (2 endorsements) absorbed into the mean.
They cannot support any claim about the agent's real-world accuracy, its false-positive behavior,
or "AI vs community notes," and they overstate performance in every direction the study cares about.
