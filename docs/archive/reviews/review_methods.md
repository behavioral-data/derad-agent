# Independent research-methods review — "Note-Parity" stimulus-generation design (v0.6)

**Reviewer role:** external methods reviewer (HCI / computational social science, misinformation interventions). I did not write this design. The bot replies are *stimuli* in a human-subjects experiment; I review for research validity, not code quality.

**Documents reviewed:** `docs/design-note-parity-v0.6.md`, `docs/v12s-playbook.md`.
**Grounding checked in-repo:** `docs/conditions.md`, `study/docs/spec-mock-x-interface.md`, `study/interface/static/app.js`, `study/docs/plan-mock-x-interface.md`.

## The single fact that reframes the whole review

The study's four conditions are **`neutral / agreeable / satirical / control`**, where `control` is **the post's real Community Note**, rendered as X's native "Readers added context" card (`spec-mock-x-interface.md:27-41`, `plan-mock-x-interface.md:671`). The three tone arms are @eddiexbot replies where **"tone differs only in the reply body."**

So Community Notes are simultaneously: (a) the **optimization target** of the bot (playbook §7 tells the generator to draft the note a top contributor would write and confirm the reply carries its load-bearing facts), (b) the **validation reference** ("note-parity", "agree with note"), and (c) a live **comparison condition** shown to participants. That triple role is the structural core of most of what follows. It is not a hypothetical — it is wired into the interface.

---

## Dimension 1 — Construct validity of the objective

**Finding 1.1 (CRITICAL). Optimizing note-parity homogenizes the bot vs. note contrast into "notes with extra steps."**
The bot's explicit generation objective is to reproduce the note's load-bearing facts (playbook §7). The note is also condition `control`. You have therefore engineered the informational content of the bot arms to converge on the content of the comparison arm. A finding of "bot ≈ note" is then preordained by construction, and a finding of "bot ≠ note" reduces to delivery differences (source, format, tone), not fact-checking substance. The study cannot cleanly support any claim of the form "AI fact-checkers vs. community notes" as distinct epistemic approaches, because you built them to be the same approach with different wrappers.
*Fix:* Decide what the bot-vs-note contrast is *for*. If it is a real research question, the bot must NOT be tuned to the note — give it an evidence-grounded objective (below) and let note-parity be a measured *outcome*, not a target. If the bot-vs-note contrast is not central, drop `control`-as-note or reframe it explicitly as "same content, different messenger/format," and pre-register that you are testing messenger/format, not fact-check quality.

**Finding 1.2 (CRITICAL). The bot-vs-note comparison is triply confounded even before homogenization.**
`control` differs from the tone arms on *source* (crowd "Readers added context" vs. an explicitly labeled bot @eddiexbot), *format* (attached context card vs. a threaded reply), AND *content* (homogenized per 1.1). Any bot-vs-note effect conflates all three. This contrast is currently uninterpretable.
*Fix:* If you want a defensible bot-vs-note comparison, hold source/format constant (e.g., also render the note as an @eddiexbot reply, or render a bot verdict as a context card) and vary only authorship/provenance — or accept that only the *tone* contrast (within-bot, source/format constant) is clean and scope claims to that.

**Finding 1.3 (MAJOR). Note-parity is the wrong construct for "fact-check quality."**
Community Notes carry known, documented biases you would be importing wholesale: helpfulness/bridging selection (only cross-partisan-consensus notes are shown — the note-selection here is exactly `CURRENTLY_RATED_HELPFUL`, `spec-mock-x-interface.md:53`), contributor-population skew, terse declarative style, single-source habits, and survivorship (posts whose correct rebuttal is hard often have weak or no notes; you kept only the 170 that *have* a CRH note). Tuning toward this artifact caps the bot at the note's ceiling and inherits its blind spots. The design's own numbers show the note is not that ceiling: the agent "beats the note" 29% of the time (see Dimension 5).
*Fix — objective I would use instead:* score the reply against the **claim and the evidentiary record**, not the note:
- verdict accuracy vs. an independently adjudicated ground-truth key (Dimension 5);
- evidential support (every load-bearing fact traceable to a citable primary/authoritative source);
- targeting (addresses what actually makes the post misleading);
- calibration (confidence matches evidence; hedges genuinely-unsettled claims — the design's own "unresolved-at-post-time" instinct);
- non-endorsement of the misleading framing;
- no new error introduced.
The note becomes one input to building that key, and a comparator scored on the same rubric — never the ruler.

**Finding 1.4 (MAJOR). The objective is baked into both the generator and the evaluator.**
Generation optimizes note-parity (playbook §7) and validation measures note-parity with LLM judges (§2.5). The §2.5 "decontamination" removed *literal answer strings* from prompts but did not remove this structural alignment: both ends still point at the note. That is optimizing to the evaluation signal at the construct level, which no amount of held-out sampling fixes.
*Fix:* the acceptance metric must be independent of the generation objective (grounded rubric + human adjudication), not a restatement of it.

---

## Dimension 2 — Stimulus validity

**Finding 2.1 (CRITICAL). The reply is an implausible artifact for its displayed timing, and the timing is internally inconsistent.**
The interface stamps the bot reply as posted **minutes after** the post (`app.js:271-273`: `replyDate = post.created_at + offsetMin`, "shortly after the post"), yet the evidence cutoff is **post_date + 48h** (design §Stage 0; playbook §1). A reply displayed as posted 2–6 minutes after a viral post cannot have cited a fact-check article published up to two days later. Beyond the date arithmetic, a *minutes-old* reply carrying exact figures ($2.81→$4.03, +44%, "highest since 2022"), the specific EIA series, Vienna-Convention context, and up to five sources is not what real early replies look like — real fast replies are partial, messy, and rarely sourced. This is anachronism-by-implausibility even when every cited date is technically ≤ cutoff.
*Fix:* Reconcile the display clock with the evidence clock — either stamp replies at cutoff-consistent times (e.g., "1d"/"2d" after, not minutes) or tighten the cutoff to match a plausible "within hours" reply. Run a plausibility pilot: show naive raters the reply + its timestamp and ask "does this look like a real reply posted then?" Treat detectable implausibility as a stimulus defect.

**Finding 2.2 (CRITICAL). "Substance held fixed across tones" is asserted, not demonstrated — and is theoretically unlikely for the agreeable arm.**
The three tones are rendered from one frozen verdict ("tone differs only in the reply body"). But when the substance *is a contradiction of the post*, tone and substance do not separate cleanly: an "agreeable" rendering of a correction imports hedges, concessions, and politeness that change the *perceived strength and even direction* of the verdict; a "satirical" rendering adds ridicule (an independent persuasion channel) and may bury the load-bearing number for the joke. If perceived correction-strength differs by tone, then tone is confounded with substance and the central manipulation is invalid.
*Fix — mandatory manipulation check before launch:* (a) independent rubric-code all 324 rendered replies for presence/completeness of *every* load-bearing fact, verdict direction, and stated confidence — confirm identical across the three tones per post; (b) human manipulation-check pilot rating perceived stance/correction strength per tone (substance nominally constant) — tones must differ on *perceived tone* but NOT on *perceived verdict strength/direction*; (c) length / reading-level / claim-count parity across tones (tone must not smuggle in length or complexity differences). Report all three.

**Finding 2.3 (MAJOR). One authorial voice across 108–170 replies is a detection / demand-characteristic risk.**
All replies come from one pipeline, one core prompt, largely one model, with strong structural tells (R-1 "lead with the baseline number," note-parity-driven inclusion of exact counter-figures). In a feed, many ostensibly independent replies sharing one voice invites participants — especially bot-primed crowdworkers — to infer a single source, collapsing per-post variation.
*Fix:* stylometric homogeneity check across the corpus; a "spot the common author / spot the bot" pilot; and confirm the study's exposure design (one post per participant vs. many) — the risk is far worse if participants see multiple replies.

**Finding 2.4 (MAJOR). Whether the 5 sources are even shown to participants is unresolved, and both answers are a problem.**
The design touts up-to-5 sources; the playbook mandates "NO URLs in body." I found no source-list rendering in the client. If sources are hidden, the sourcing work is invisible and a confident verdict with no visible support is *less* plausible/credible. If sources are shown as live links, participants can click through to publication dates that post-date the reply's minutes-after timestamp (2.1) — a direct anachronism tell.
*Fix:* decide and document how sources render; if shown, snapshot/date them cutoff-consistently (see Dimension 4 archiving); if hidden, note that the sourcing pipeline is not part of the stimulus and adjust claims accordingly.

**Finding 2.5 (MAJOR). 170 study posts vs. 108 evaluated; ~41 are video the pipeline cannot see.**
The interface serves 170 posts (`spec` §Data source), all CRH-noted; the redesign is validated on 108 (12 tuning + 96 held-out), and 41/108 are video the pipeline "cannot see at all" until the P2 video work lands. Serving stimuli the generator checked blind against exactly the content the note addresses is a stimulus-validity hole.
*Fix:* reconcile the post set; do not ship bot replies for posts the pipeline processed blind (video/media) until multimodal access is real and validated; report coverage explicitly.

---

## Dimension 3 — Evaluation methodology (§2.5)

**What is genuinely good:** naming three contamination vectors (answer leakage, adaptive overfitting, judge circularity) and acting on them; sanitizing the playbook; sampling held-out posts from the 96 non-tuning set with a recorded seed; blinded randomized A/B labels with a withheld key; a cross-family second judge. This is above the median for the subfield.

**Finding 3.1 (MAJOR). Underpowered: n=15 / n=14, no CIs, no tests, no power analysis.**
Headline effects are counts out of 15 (parity 1.20→1.80; head-to-head 11/13 non-ties). The head-to-head sign test is ~p=.02, fine; the parity means and agree-rate deltas move only a handful of posts and carry no interval. You cannot ground full-108 acceptance thresholds on this. The doc half-concedes ("more modest") then proceeds as if licensed.
*Fix:* treat §2.5 as a smoke test. The real evaluation is the full-108 (better: full-170) regeneration, analyzed with CIs and pre-registered tests, tuning-12 reported separately from held-out.

**Finding 3.2 (CRITICAL). LLM judges are the only evaluators of fact-check quality — with demonstrated shared blind spots.**
No human raters anywhere. The generator is Claude/Opus; the primary judge is Claude — correlated failure modes. The "cross-family" gpt-5 judge is still an LLM, and the doc *itself reports a shared miss* (both systems endorsed the nurses-strike post, both judges missed it). That is direct evidence LLM judges fail to catch the errors that matter most (endorsing misinformation). Certifying human-subjects stimuli with the same class of model that generated them is not adequate.
*Fix:* human expert adjudication is required for (a) building the ground-truth key and (b) the final go/no-go on every rendered reply (Dimension 4 ethics). LLM judges may triage but not certify.

**Finding 3.3 (MAJOR). No reliability statistics.**
"Both judges ranked it ahead" and "6 independent grading agents" are reported as vote tallies. No inter-judge agreement (κ / Krippendorff's α), no judge-vs-human agreement, and "6 independent graders" are presumably one model at different seeds/prompts — correlated, not independent. Reliability of the entire measurement instrument is therefore unknown.
*Fix:* report α between judges and, critically, judge-vs-human agreement on a subset; define what the 6 graders are and their agreement; stop calling correlated same-model runs "independent."

**Finding 3.4 (MAJOR). Collapsing to agree/partial/disagree + a parity mean hides catastrophic per-post failures and enables gaming.**
A single endorsement of a misleading post is a disqualifying stimulus error (you'd show participants a "fact-check" that confirms misinformation), yet it averages away in a mean-parity ≥ 2.0 target — which is satisfiable with several 0s offset by 3s. No per-category breakdown (video/text, quote/statistic/causal, polarizing/not).
*Fix:* per-post, per-category reporting; endorsements and factual errors as hard zero-tolerance gates, not means; replace "mean parity ≥ 2.0" with a floor (e.g., zero posts < 2, or every < 2 human-reviewed).

**Finding 3.5 (MAJOR). The acceptance criteria are gameable and mostly the wrong targets.**
- "≥85% agree" = agree *with the note* → rewards paraphrasing the note (homogenization, 1.1), trivially gameable, and *punishes* the 29% beat-note cases. Wrong target.
- "0 endorsements" — right and essential, but must be human-verified given 3.2.
- "mean note-parity ≥ 2.0" — gameable mean (above).
- "100% temporal_ok" — must be a *structural* check (compare each cited URL's actual pub date to cutoff, which the design's R-2 proposes — good) not an LLM reading the prose; an LLM won't catch a live link dated post-cutoff.
- Cross-cutting: the acceptance harness is the *same* "6-grader × judge harness" used for tuning → hitting the bar is optimizing to the metric.
*Fix:* acceptance on a grounded rubric + human adjudication, disjoint from the tuning harness; keep 0-endorsement and structural temporal checks; drop or invert "agree-with-note" as an acceptance gate.

**Finding 3.6 (MINOR→MAJOR). Blinding likely leaks via style.**
Withholding the A/B key is good, but the tuned system has a recognizable house style (leads with baseline, exact counter-numbers); a judge can infer which arm is new without the key.
*Fix:* run a blinding-check (ask the judge to guess the tuned arm; > chance = blinding failed) and report it.

**Finding 3.7 (MINOR). Tuning set is selected on the dependent variable.**
The 12 posts are the current system's *worst failures*, so improvement there is partly regression to the mean. The doc flags this (good); just make sure the paper never reports tuning-12 gains as generalization.

---

## Dimension 4 — Reproducibility, reporting, ethics

**Finding 4.1 (CRITICAL). Live search makes the *method* non-reproducible; the freeze only makes the *artifacts* reproducible.**
The same pipeline run twice retrieves different evidence (result churn, deletions, WAF/paywall variance, archive availability). The 108/170 stimuli are a single non-reproducible draw. The freeze + audit trail (evidence rows with URLs + pub dates + `verdict_derivation`) correctly makes the *specific stimuli* auditable and re-servable — but it does not make "our method produces X" a reproducible claim.
*Fix:* the paper must explicitly separate **stimulus reproducibility** (freeze = yes) from **method reproducibility** (search nondeterminism = no). Characterize **regeneration variance**: re-run ~20 posts k times, report variance in verdict / parity / citations; if that variance is large relative to the tone effect, the stimuli are on shaky ground. **Archive every cited page (WARC / archive.org) at freeze time** so links stay valid, dated, and auditable for reviewers and for the deployed UI.

**Finding 4.2 (MAJOR). Methods-section reporting requirements not yet met.**
Must pin and report: exact model IDs + versions + dates for *every* stage (extract, search controller, reconcile, renderer) **and every judge** — the project's own memory records that the search backend default changed over time and that a fallback model "silently refuses on sensitive queries," which would bias exactly this corpus; all prompts (frozen V1.2-S + all stage prompts) as supplementary; search backend, per-row retrieval date, top_k, wall-clock, reasoning-effort per stage; grading rubric, judge prompts, seed (42 — good), A/B key; the exact tuning-12 / held-out-96 / §2.5-15 partition (the doc commits to separate reporting — good); per-stimulus provenance (sources, fetch-failed/archived flags, verdict, confidence).

**Finding 4.3 (CRITICAL, ethics/IRB). Confident, possibly-wrong verdicts shown to human subjects under an authoritative frame = injecting misinformation.**
The design's own taxonomy lists `factual_error_in_reply`, `endorsed_misleading_claim`, and a shared miss both judges passed. Showing a participant a confident "fact-check" that is actually wrong can leave them *worse* informed on a real, sensitive topic (economy, immigration, an ICE death, healthcare). Required safeguards this design does not yet specify:
1. **100% human expert review** of all 324 rendered replies pre-launch, with a hard gate on any factual error or endorsement (LLM certification insufficient, 3.2).
2. **Debrief** disclosing the replies were AI-generated study stimuli (not real contemporaneous fact-checks) and providing the best-available correct information per post; consent language flagging that some shown content is misleading and some is experimental AI.
3. **Content warnings / distress handling** for sensitive posts, especially the *satirical* and *agreeable* tones on deaths/accidents.
4. **Representation check:** posts are real; author handles are already synthetic/anonymized (good, `spec` posts table) — but confirm no real note contributor or quoted third party is misrepresented, and that attaching a fabricated bot "fact-check" to a real post cannot defame a real, identifiable account.
5. **Pre-registered exclusion/stopping rule** for any stimulus later found wrong, and a plan to not leave participants net-misinformed (a post-study corrective).

---

## Dimension 5 — Gold-standard circularity

**Finding 5.1 (CRITICAL). Notes are treated as ground truth while the design's own data shows they are not.**
The agent "beats the note" 31/108 (29%) and cites ≥1 of the note's own URLs only 11%. If a system beats the reference ~29% of the time, the reference is a strong-but-fallible comparator, not ground truth. Using it as generation target (caps quality, penalizes the 29%), as validation reference ("agree with note" mislabels correct-beyond-note as non-agreement), and as comparison arm is triply circular. Worse, "beat_note" is itself **incoherent as framed**: if the note is the gold standard, "beating" it is undefined — the doc adjudicates it with the same LLM judge that has no independent grounding (and demonstrably shares the generator's blind spots, 3.2).

**Finding 5.2 (MAJOR). Note-quality is heterogeneous and that heterogeneity is ignored.**
CRH selection (`spec:53`) means notes vary from barely-bridged to strongly-rated; treating all as equal gold is wrong.

*Fix — how to measure quality when the reference is imperfect:*
1. **Build an independent, adjudicated ground-truth key per post:** ≥2 trained annotators (domain experts where needed) establish, from primary sources, the correct verdict + load-bearing facts + citations + an explicit "genuinely unsettled" option — **blind to both the note and the bot**; resolve disagreements; report inter-annotator agreement. This key, not the note, is gold.
2. **Score the note AND all three bot tones against that key** on the grounded rubric (1.3). Now "bot vs. note" is fair and non-circular, and "beat the note" has a real meaning.
3. **Treat CN as comparator + covariate,** and report where note and key disagree (~29%) as a *first-class finding* — LLM-agent vs. crowd fact-check quality is genuinely interesting and is exactly the evidence that CN-as-gold is unsafe.
4. **For contested claims,** use the "unsettled" category and score *calibration* (appropriate hedging) rather than forcing agree/disagree — extend the design's own "unresolved-at-post-time" verdict framing into evaluation.
5. **Condition on note bridging/helpfulness strength** as a moderator.

---

## Verdict

**With changes — several of them blocking.** The stimulus-*engineering* is thoughtful and above-average (structural hindsight partition, freeze/audit trail, explicit contamination review). But as a stimulus-generation method for this specific study it has three disqualifying problems in its current form:

1. **Note-parity as the objective + Community Notes as the comparison arm** homogenizes the conditions and makes the bot-vs-note contrast, and the note-as-gold-standard, circular (Dim 1, 5). Blocking.
2. **No human evaluation anywhere**, for stimuli shown to humans, with LLM judges shown to share the generator's blind spots (Dim 3.2) and no reliability stats (Dim 3.3). Blocking.
3. **Ethics/IRB safeguards** for showing confident, possibly-wrong verdicts are unspecified (Dim 4.3). Blocking until in place.

Plus must-fix (not necessarily blocking) items: verify substance-invariance across tones (2.2), reconcile the minutes-after display clock with the +48h evidence clock (2.1), characterize search-driven regeneration variance and archive sources (4.1), power the evaluation and stop using "agree-with-note" as an acceptance gate (3.1, 3.5), and reconcile 170-served vs 108-evaluated with video coverage (2.5).

**Conditions for approval (checklist):**
- [ ] Re-scope or de-circularize the note's role: either stop tuning the bot to the note (grounded objective) or drop/reframe note-as-`control`; hold source/format constant for any bot-vs-note claim.
- [ ] Independent adjudicated ground-truth key; score bot AND note against it; report note↔key disagreement.
- [ ] Manipulation check proving substance is constant and only perceived-tone (not perceived-verdict-strength) varies across the three arms.
- [ ] 100% human expert pre-launch review of all rendered replies; zero-tolerance gate on endorsements/factual errors; debrief + consent + content warnings + corrective.
- [ ] Reconcile display timestamp with evidence cutoff; decide/document source rendering; archive cited pages.
- [ ] Power the evaluation with CIs/tests and human-vs-judge agreement; drop gameable/circular acceptance criteria; per-category, per-post hard-failure reporting.
- [ ] Full model/version/prompt/seed reporting; regeneration-variance characterization; reconcile post-set coverage (170 vs 108, video).

I would not approve it as-is, and I would approve it once the seven boxes above are met.
