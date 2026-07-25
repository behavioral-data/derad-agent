# Bias &amp; Validity Audit — Report and Redesign (v0.8 direction)

*Triggered by a design concern: the agent enumerates "misleadingness hypotheses" and
picks one before gathering evidence — risking (a) priming it to find deception even on
accurate posts, and (b) selecting a conclusion by imagined impact rather than evidence.
Four independent Opus audits (reasoning, evaluation, ground-truth, political-symmetry)
plus empirical probes on the real study data and a live false-positive probe. Full
sub-reports in `.claude/scratch-bias-*.md`.*

---

## 1. The one root cause

Your instinct was right, and it's bigger than the hypothesis step. **The whole
system — the agent's reasoning, the accuracy metric, and the test set — is built
around a single presumption: "this post misleads; find how."** The presumption is
baked in at three independent layers that reinforce each other:

- **Reasoning:** the agent enumerates *ways to mislead*, picks the most damaging one
  as a prior, and every scrutiny gate searches only for what's *wrong* with the post.
- **Metric:** "accuracy" is agreement with a Community Note — and notes only ever say
  "misleading." There is no note that says "this post is accurate," so the metric
  **cannot reward the agent for correctly leaving a true post alone** — it rewards
  producing a correction.
- **Test set:** all 108 study posts are CN-flagged as misleading. With zero
  true-negatives, a bias toward "this misleads" scores as **success**, and the
  false-positive rate is literally unmeasured.

Because all three point the same way, the reported v0.7 scores look strong precisely
where the system is weakest. This is why the concern matters: nothing in the current
pipeline *or its evaluation* can see the failure you're worried about.

---

## 2. Problem catalog (severity · solution)

IDs map to the sub-reports. Only the load-bearing items are listed; full lists in
`.claude/scratch-bias-{reasoning,eval,gt,political}.md`.

### A. Reasoning biases (agent-side — implementable now)

| ID | Sev | Problem | Solution |
|---|---|---|---|
| RB-1 | Critical | Hypothesis step frames the task as "find how it misleads"; "accurate &amp; fairly framed" is not a candidate. | Add a **first-class null hypothesis H0** ("accurate, no correction warranted") with the presumption in its favor; require it to be listed and adjudicated. |
| RB-2 | Critical | Target picked by "if confirmed, most changes understanding" — a prior chosen by *severity*, before evidence, un-abandonable. | Select target by **resolvability + decision-relevance, not severity**; make it **abandonable** when evidence disconfirms; allow finalizing "supported" with a rejected target. |
| RB-3 | Critical | Asymmetric skepticism: accurate/agree conclusions face two "find the flaw" waves; refutations face zero "find the defense" waves. | Add a **symmetric steelman gate**: any refute/context conclusion must survive one wave searching for the post's strongest defense. De-duplicate the mislabeled "symmetric" §6b. |
| RB-5 | Major | Verifier checks internal consistency, never "is this correction *warranted* or manufactured." | Add verifier check **#7 WARRANT** — does the evidence justify a correction over H0? — with authority to push the verdict *toward* accurate, not only downgrade. |
| RB-6 | Major | "There is no record he said this" turns absence of evidence into evidence of absence. | Scope it: **"no record found in [sources] as of [date]"**; forbid escalation to "he didn't say it" without a row identifying a fabricated origin. |
| RB-9 | Major | Implied-claim check inflates insinuation to a "never/always" universal, easiest to refute, and pre-commits to a counter-example. | Require the **fairest** implied reading; ask whether it's even false before refuting; drop the pre-committed conclusion shape. |
| RB-10 | Minor→Major | "Exculpatory context" is a one-directional search prior (presume the record exonerates). | Make it **symmetric**: check the record in both directions, report whichever it supports. |
| PS-1-fix | Critical | (from political audit) A CN-flagged, true-but-framed post can still be **endorsed** (all 3 residual endorsements). | **Cap SUPPORT**: never endorse a flagged post carrying a misleading/editorializing frame; **downgrade true-but-framed → context by rule.** Mechanically removes the endorsement asymmetry. |

### B. Evaluation validity (measurement-side — research decisions)

| ID | Sev | Problem | Solution |
|---|---|---|---|
| EV-1 | Critical | All test posts misleading → false-positive rate/specificity **unmeasured**; base-rate neglect. | Add a **hard-negative accurate-post arm** matched on topic/format/valence; report the full confusion matrix (specificity, FPR, precision); a "no correction warranted" gold class. |
| EV-2 | Critical | Note-parity/agree-with-note measures **mimicry**, not truth; the D2 independent rubric was decided but **never run**. | Build &amp; run the **independent-gold rubric**; score bot *and* note against it, blind to origin. |
| EV-3 | Critical | Means hide catastrophic per-post failures; the 2 held-out endorsements were absorbed into the 2.07 mean. | **Per-post zero-tolerance floors** reported as counts (zero endorsements, zero false accusations, zero temporal leaks); a floor breach fails the run regardless of mean. |
| EV-4 | Critical | Single same-model-family judge; no humans; no inter-rater reliability. | **Human raters as the primary instrument** (≥2, report IRR) + a **cross-family** LLM panel as secondary. |
| EV-6/7 | Major | n=15, single stochastic run, no CIs; the "held-out 15" reused across rounds. | Powered confirmatory run with CIs; **a fresh, never-inspected sample outside the 108**; report tuning vs held-out separately. |
| EV-8 | Major | Head-to-head baseline is the known-buggy old pipeline (a strawman). | Add real comparators on the same rubric — the note itself, a strong retrieval baseline, a different-family agent. |
| EV-11/13 | Major | Video coverage unreported; tone conditions carry a fact-shedding confound. | Stratify all metrics by modality; enforce R-4/R-5 across tones + a human manipulation check. |

### C. Ground-truth / circularity

| ID | Sev | Problem | Solution |
|---|---|---|---|
| GT-1/2 | Critical | Can't hold all of: notes-as-GT ∧ agent-beats-notes(29%) ∧ clean bot-vs-notes study. Notes-as-GT is empirically false. | Separate **reference-for-accuracy (independent gold)** from **object-of-comparison (notes)**; never let notes play both roles. |
| GT-7 | Critical | Notes only ever say "misleading" — notes-as-GT can never score "correctly found accurate"; it **rewards manufacturing a correction**. | Independent gold that includes "post is accurate" determinations; accuracy sample must contain non-misleading posts. |
| GT-6/9/10/11 | High | Notes carry bridging-selection, terseness, survivorship, and known-error (low-accuracy) biases the agent would inherit. | Treat notes as a **strong-but-fallible reference**; report **note-agreement as a separate "crowd concordance" descriptor**, never as "accuracy." |

### D. Political / topical symmetry (largely reassuring)

- **PS-1 (high confidence):** the top-line left/right outcome asymmetry (refute 14 vs 4)
  is **predominantly set composition, not partisanship** — within matched
  misinformation types the agent is roughly symmetric (it refuted 4 left-coded
  falsehoods; softened true-kernel distortions to context on both sides equally).
- **PS-2 (low–medium confidence):** a **small residual softness on left-coded
  true-but-framed posts** (deflating caveat on the right vs endorsement on the left).
  Rests on ~3–5 items — a hypothesis for a powered re-test, addressed by the
  cap-SUPPORT rule + the valence-swap check below.
- **PS-3/5 (study design):** the set **confounds polarity with type** and contains
  **zero left-coded fabrications**, so part of the partisanship question is
  structurally untestable. Fix: balance misinformation type within each polarity cell.
- **New agent guard:** a **polarity-blind valence-swap self-check** — re-run the
  verifier on a valence-swapped minimal paraphrase and require equal verdict
  strength/tone; ship as a paired minimal-pair CI gate.

---

## 3. Empirical evidence

**Verdict strength by polarity (both pipelines, set balanced 36/36/36):** consistent
softness on left-coded posts — old pipeline endorsed 7/36 left-coded vs 2/36
right-coded; v0.7 endorses 3/36 vs 0/36. **Within-type analysis shows this is mostly
composition** (left-coded posts are disproportionately true-but-framed with true cores;
right-coded are disproportionately outright distortions/fabrications).

**False-positive probe (12 genuinely-accurate, fairly-framed posts across 6 topics ×
lean):** **0 false positives — 12/12 correctly handled.** 10 affirmed as
`verified_supported`; 2 labeled `verified_nei` but with affirming findings ("This claim
is accurate…") — the label→finding fidelity bug (PS-4/EV-14), *not* a manufactured
correction. No accurate post was refuted, challenged, or given an unnecessary
"context" caveat. **Symmetric across lean:** all 4 left-coded, all 3 right-coded, and
all 5 neutral accurate posts were correctly accepted — including politically-charged
but true claims (immigrants' lower crime rates → "if anything an understatement"; the
racial wealth gap → "strongly supported"; perennial federal deficits → "accurate").

*Interpretation:* empirically **better than the reasoning audit predicts** — the
accuracy-exit and verifier are already catching the presumption-of-guilt on *clean*
accurate posts, and there is no measurable left/right false-positive asymmetry. Two
caveats keep the structural fixes warranted: (1) these 12 are *unambiguously* true and
fairly framed — the probe does not test genuinely borderline/ambiguous posts, where the
structural presumption-of-guilt is most likely to bite; (2) 2/12 mislabeled-as-NEI
confirms the verdict labels still understate affirmations. The acute false-positive fear
is substantially allayed on clear cases; the fixes matter for ambiguous cases and —
critically — because the metric and test set still *cannot measure this at all*.

---

## 4. The redesign (v0.8)

### 4a. Agent — from "presumption of guilt" to "evidence-led with a real null"

1. **H0 as the default hypothesis.** Every post starts from "accurate and fairly
   framed; no correction warranted," displaced only by evidence. The hypothesis set
   lists deception candidates *and* H0; the finalize schema records each hypothesis's
   disposition (confirmed / disconfirmed / insufficient).
2. **Evidence-led target selection.** Choose what to investigate by resolvability +
   decision-relevance, never by how damning it would be; abandon a target the
   evidence disconfirms; "supported / no correction" is a first-class outcome, not the
   failure fallback.
3. **Symmetric adversarial gates.** One steelman wave for every correction, mirroring
   the existing devil's-advocate wave for every agreement — equal pressure both ways.
4. **Cap SUPPORT + true-but-framed → context by rule.** A flagged post with a
   misleading frame can never be endorsed; this mechanically closes the endorsement
   asymmetry that drove both the original failure and the residual polarity softness.
5. **Verifier gains a WARRANT check** and the authority to move a verdict toward
   accurate — the independent pass now guards against manufactured corrections, not
   just over-statements.
6. **Calibrated language:** scoped "no record found as of [date]"; fairest (not most
   extreme) implied claim; symmetric exculpatory/inculpatory search.
7. **Polarity-blind valence-swap self-check** as a standing bias gate.

### 4b. Measurement — the part that actually protects the study

1. **Independent gold standard** (human fact-checkers, primary sources, blind to the
   note, including "accurate" determinations) → the **headline accuracy number**,
   uncapped and defensible.
2. **Note-agreement reported separately** as "crowd concordance" — still a high number,
   honestly labeled; disagreements adjudicated against the gold (the honest home for
   the 29% beat-note).
3. **Accurate-post arm** for true-negatives → confusion matrix, specificity, FPR.
4. **Per-post floors** (zero endorsements / false accusations / temporal leaks) as
   pass/fail gates independent of any mean.
5. **Human raters + cross-family judge panel + IRR**; **fresh out-of-108 confirmatory
   sample**; CIs; pre-registered primary metric.
6. **Fix the study set:** balance misinformation type within each polarity cell; add
   left-coded fabrications and right-coded true-but-framed posts.

---

## 5. Reconciling "keep high accuracy, notes as ground truth"

Said plainly, because it's the crux: **notes-as-ground-truth is the trap, and it
doesn't even give you the high number you want.** It caps accuracy at "identical to a
terse, one-directional, consensus-selected note," forces the agent to inherit every
note bias, and — since notes never say "accurate" — actively rewards the false-positive
behavior this whole audit is about.

The design that gives you a *higher, defensible* number is: **independent-gold accuracy
as the headline** (no ceiling — the agent can and does exceed the notes), with
**note-agreement reported alongside as convergent validity**. You still get to report a
strong "the agent agrees with community notes X% of the time" — you just don't call it
accuracy, and you gain a real accuracy number that survives peer review and keeps the
bot-vs-notes comparison uncontaminated (per your own D2 decision).

The honest trade-off: this requires building the independent gold set (human
adjudication of a bounded sample — the 15 held-out + a fresh ~30–50, not all 108). That
is real work you were hoping to avoid by leaning on the notes. It is also the only path
that answers the bias concern rather than hiding it.

---

## 6. Sequencing

- **Now (agent-side, code):** H0 + evidence-led selection + symmetric steelman gate +
  cap-SUPPORT + verifier WARRANT + calibrated language + valence-swap gate. All
  implementable on the v0.7 architecture; re-validated by the FP probe + valence-swap
  CI gate.
- **Before any published number (measurement, your decisions):** independent gold,
  accurate-post arm, per-post floors, human raters, fresh sample, study-set rebalance.

The agent changes stop the bias at the source; the measurement changes are what let you
*prove* it's stopped — and give you the defensible accuracy number.
