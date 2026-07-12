# Fact-check pipeline redesign — "Note-Parity" design (v0.6 proposal)

*Produced from: full code audit of `agent/factcheck/*`, graded comparison of all 108
agent replies vs their Community Notes (6 independent grading agents), and a controlled
design experiment (3 candidate designs × 12 hardest posts × live web search, blind to
notes, judged against notes) plus one patch iteration (V1.1).*

---

## 1. Where the current pipeline stands (all 108 posts, notes = gold standard)

| | current agent |
|---|---|
| Matches the note's thrust | 60/108 (56%) |
| Partial (weaker/different point) | 33/108 (31%) |
| **Endorses the misleading post** | **10/108 (9%)** |
| No result | 5/108 (5%) |
| Clearly worse than note (sev ≥2) | 36/108 (33%) |
| Beats the note | 31/108 (29%) |
| Cites ≥1 of the note's own URLs | 12/108 (11%) |

Two structural facts stand out:

- **Every one of the 11 `verified_supported` outcomes is a failure** — the pipeline
  verified a true literal kernel of a misleading post and thereby endorsed the framing.
- **38% of runs end in a no-result outcome** (`verified_nei` etc.) even though every
  post has a note with concrete sources; and 19 of those "NEI" runs only read fine
  because of a renderer bug that leaks reconcile's substantive payload through anyway.

### Failure taxonomy (graded, n=108, multi-label)

| failure mode | n | root cause in code |
|---|---|---|
| missing_key_source | 40 | 3 hits/query, ≤6 generic queries, fetch-failure drops, no primary-data targeting |
| missed_the_point | 28 | checks the literal sentence, not what makes the post misleading |
| central_claim_misextraction | 24 | extraction at effort=low, pre-search, exactly-one-central-claim |
| search_query_weak | 18 | templated seeds ("fact check: {tweet}"), no dates/quotes/site targeting |
| wrong_action | 13 | `verify` chosen for true-but-misleading posts |
| less_specific_than_note | 12 | no requirement to produce counter-numbers |
| endorsed_misleading_claim | 7 | no devil's-advocate gate on "supported" |
| no_result_despite_findable_evidence | 7 | stance drift + rigid ≥2-source thresholds discard retrieved evidence |
| factual_error_in_reply | 4 | renderer asserts beyond evidence |
| temporal_error | 4 (+pervasive anachronism risk) | no date contract anywhere |

The dominant failure chain: **wrong target → wrong queries → wrong/no evidence → wrong
verdict.** Verification is strong when aimed right (25/28 `verified_refuted` agree).

Additional code-level defects found in the audit (independent of grading):

1. **No stage knows the current date or the post date as an anchor** (`extract.py`,
   `verify.py`, `reconcile.py` prompts; `search.py` system prompts).
2. **`_validate_hits` drops any URL whose live fetch fails** (403/paywall/timeout) —
   even server-stamped citations; no archive fallback (`search.py:498-552`).
3. **The post's own linked article is never fetched** — `expanded_urls` ride along as
   bare strings while the reconcile prompt claims they're evidence (`reconcile.py:109`).
4. **41/108 posts are videos the pipeline cannot see at all.**
5. **`wikipedia.org` hard-mapped to low-quality**; `aggregator`/`unknown` never count
   toward thresholds (`sources.py`, `source_lists.json`).
6. **Verdict/reply incoherence**: organic NEI keeps the full substantive payload; the
   renderer is told "say nothing was found" while holding a refutation — freeze label,
   analytics, and user-facing text diverge (`pipeline.py:394-441`, `render.py:61-77`).
7. **Stage-5 audit failure nukes everything to NEI** instead of repairing the specific
   drift (`pipeline.py:460-470`).
8. **Effort inversion**: extraction/controller at `reasoning_effort=low`; the
   highest-leverage decisions get the least compute.

---

## 2. Design experiment (12 hardest posts + 2 controls, blind to notes, judged vs notes)

Three candidate designs, each run by Opus agents with live web search under a strict
"evidence published ≤ post_date+48h" rule:

- **V1-FULL** — misleadingness-hypothesis targeting + temporal contract + retrieval
  overhaul + weighted sufficiency + devil's-advocate gate + note-parity self-critique.
- **V2-RETRIEVAL** — current claim-selection philosophy + temporal contract + retrieval
  overhaul (ablates the targeting front-end).
- **V3-TARGETING** — hypothesis targeting + temporal contract + deliberately thin
  current-style retrieval with simulated fetch-drops (ablates the retrieval overhaul).

Judge results (same rubric as the audit):

| metric (n=12) | original | V1 | V2 | V3 |
|---|---|---|---|---|
| agree | 2 | 8 | **9** | **9** |
| partial | 4 | 3 | 2 | 2 |
| disagree | 4 | 1 | 1 | 1 |
| no_result | 2 | 0 | 0 | 0 |
| **mean note-parity (0–3)** | 0.67 | **2.08** | 1.75 | 1.67 |
| temporal_ok | 11/12 | 12/12 | 12/12 | 12/12 |
| beat_note | 1 | 5 | 5 | 5 |

Reading the ablation:

- **Both axes matter.** V2 (retrieval-only) still committed the wealth-vs-GDP category
  error and led with "TRUE" on the cherry-picked gas-price post; V3 (targeting-only)
  caught fabrications even under thin retrieval but couldn't reach the note's numbers.
- **V1 wins where it counts for this project** — note-parity: it found the *exact EIA
  series page the community note cites*, delivered the note's numbers ($2.81 → $4.03,
  +44%, "highest since 2022"), the Vienna Convention context, the wealth≠GDP
  correction, and the Tesla attribution correction. ⚠️ *Contamination caveat (see
  §2.5): the V1 playbook's note-parity example contained the gas note's numbers, so
  the gas result specifically cannot count as clean; the consulate, wealth≠GDP, Tesla,
  and fabrication results had no such leakage.*
- All variants: 100% temporal discipline under the contract (original: anachronisms).

### V1.1 patch iteration

Four residual failure modes → four rules, retested on the failing posts (+1 control):

- **P-A fabricated-quote protocol** (name the originating account; "no record exists"
  instead of "unverifiable")
- **P-B implied-claim check** (state and check the insinuated claim behind dunk posts)
- **P-C literal-vs-context balance guard** (deliver both; attribute disputed
  characterizations to their sides)
- **P-D causal-attribution rule** (verify the mechanism, not the outcome)

**V1.1 results (5 retested posts):** 3 of 4 targeted gaps fixed (Epstein implied-claim
→ agree/2 with the note's named individuals; Goetz disagree → agree/2; fake-quote
template → agree/3 naming the network), 0 disagrees — but two rule-conflict
regressions appeared: the recent-peak framing displaced the note's January baseline on
gas prices (parity 3→1), and one reply asserted specifics traceable only to
post-cutoff sources.

### V1.2 — final iteration (freeze candidate)

Three lint rules + one hypothesis type added:

- **R-1 baseline retention** — when misleadingness turns on a trend, the longest
  decision-relevant baseline must LEAD the reply and may never be displaced by
  recent-peak framing.
- **R-2 cutoff-consistency lint** — every specific fact in the reply must trace to a
  pre-cutoff evidence row; facts learned from post-cutoff pointers are re-established
  pre-cutoff, generalized, or dropped (substitution, not deletion).
- **R-3 evidence-consistency lint** — every figure/characterization in the reply must
  match the evidence log; contested characterizations stay in attributed form
  ("four of the six reviewing specialists").
- **Exculpatory-context hypothesis** — when a post assigns blame/culpability, search
  specifically for record context that cuts against the blame framing.

**V1.2 results on the 3 regression posts** (judge, same rubric):

| post | V1 | V1.1 | **V1.2** |
|---|---|---|---|
| Gas prices | agree/3 | partial/1 | **agree/3, beat_note** — leads $2.79→$4.02 (+44%), keeps peak context |
| Noviello ICE death | partial/1 | partial/0 | **agree/2** — first reply in any round to carry the note's exculpatory core |
| Fake celebrity quote | partial/1 | agree/3, temporal ✗ | **agree/3, beat_note, temporal ✓** — names the impersonation account |

**V1.2 totals: 3/3 agree, mean parity 2.67, 3/3 temporal, 2 beat-notes, zero
regressions.** Judge recommendation: freeze as the final design (with a full 12-post
confirmation run as part of implementation validation).

Known limitation of the experiment: subagents could not see attached images/videos, so
image-subject mismatches (IOC post) were uncatchable in simulation — the real pipeline
*has* image access (Stage 1.5) and gets an explicit image-subject-verification step in
the design below.

## 2.5 Validation integrity + clean held-out validation

An overfitting review after the iteration rounds found three contamination vectors:

1. **Answer leakage in playbook examples.** The V1/V1.1/V1.2 prompts contained
   study-derived examples — including the gas note's own numbers ("$2.78 → $4.02,
   +45%"), the Epstein post's implied claim, and the Noviello/Goetz charged words
   ("preventable", "unarmed", "impeach"). The V1.1/V1.2 retest "fixes" on those posts
   are therefore not usable as evidence.
2. **Adaptive overfitting.** Patch rules were derived from, and retested on, the same
   posts; the 12-post set was selected as the current system's worst failures.
3. **Judge circularity.** The round 1–3 judge knew variant identities and graded
   whether its own patch recommendations worked.

**Decontamination protocol.** The playbook was sanitized (every study-derived example
replaced with synthetic ones → "V1.2-S", `V12S_playbook.md`); 15 posts were sampled
(seed 42) from the 96 never used for tuning; replies were produced blind to notes;
grading used a fresh history-free judge with per-post randomized A/B labels
(assignment key withheld), plus a second cross-family judge (gpt-5 on Azure; 1 of 15
posts lost to its content filter).

**Clean held-out results (original production reply vs V1.2-S, same posts, blinded):**

| judge | metric | original | V1.2-S |
|---|---|---|---|
| primary (blinded Claude, n=15) | agree / partial / disagree | 11 / 3 / 1 | 12 / 2 / 1 |
| | mean note-parity | 1.20 | **1.80** |
| | temporal_ok | 14/15 | **15/15** |
| | head-to-head better | 2 | **11** (2 ties) |
| secondary (gpt-5, n=14) | agree / partial / disagree | 5 / 6 / 3 | 8 / 3 / 3 |
| | mean note-parity | 1.21 | **1.57** |
| | head-to-head better | 3 | **9** (2 ties) |

Honest reading: on a random held-out sample the effect is **clearly positive but more
modest than the tuning-set numbers** — as expected once worst-case selection and
contamination are removed. V1.2-S reached full note-parity (3/3) on 8/15 posts and won
head-to-head ~2:1 to 5:1 depending on judge; both judges independently ranked it
ahead. Its two losses were posts whose checkable core lived in attached media the
simulation couldn't see (e.g. the Powell/Arlington image) — the production pipeline
has image access, and video access is the P2 item. One shared miss (both systems
endorsed the nurses-strike post, missing the note's nonprofit/no-shareholders point)
shows subtle entity-property errors remain an open failure mode.

**Rules going forward:** the production prompts must carry no study-derived examples;
the design is frozen before the full-108 regeneration; the 12 tuning posts are
reported separately from the 96 held-out posts in any evaluation; and generalization
claims for the paper require a fresh sample from the CN snapshot outside the 108.

---

## 3. The proposed design (stage by stage)

### Stage 0 (new) — Temporal context block
Built once in `pipeline.py`, injected into **every** LLM call and both search-backend
system prompts:

```
TODAY = <invocation date>. POST_DATE = <tweet posted_at>. The post is <N days> old.
Evaluate every claim AS OF POST_DATE. Time-indexed claims (prices, standings, counts,
"today/now/this week") refer to POST_DATE, not today.
[study mode] EVIDENCE_CUTOFF = POST_DATE + 48h: cite only sources published on/before
the cutoff; later sources may only serve as pointers to contemporaneous primary data.
Write the reply as if posted within hours of the post.
```

- `run_pipeline` gains `as_of`/`evidence_cutoff` params (live mode: cutoff off).
- `_fetch_clean_page` already parses pages — extract `trafilatura` metadata dates and
  stamp `published_at` on every Evidence row.
- **Structural hindsight partition (study mode):** post-cutoff rows are visible to the
  search controller only (as pointers to contemporaneous primary data). They are
  STRIPPED before the reconcile prompt is built, and `verdict.py` counts only
  pre-cutoff rows. The verdict is thereby provably derived from pre-cutoff evidence —
  auditable from the freeze, not dependent on prompt obedience. The prompt instruction
  ("reason at POST_DATE; if pre-cutoff evidence is insufficient, hedge even if you
  know the outcome") is kept as defense in depth.

### Stage 2+3 (redesigned) — Misleadingness-hypothesis targeting
Replaces "extract atomic propositions → pick THE central claim → pick action" with:

1. Enumerate 2–4 **misleadingness hypotheses** (fabricated quote/media, AI/recycled/
   misattributed media, cherry-picked window, missing denominator/base rate, category
   error, false causal attribution, true-but-decontextualized, stale-as-breaking,
   **exculpatory context** for blame-assigning posts).
2. **Provenance-first** for any quoted statement/screenshot/video.
3. **Implied-claim extraction** for insinuation posts (P-B).
4. Select the **check target**: the hypothesis that, if confirmed, most changes a
   reader's understanding. Claim decomposition and the action vocabulary
   (verify/provide_context/challenge_opinion/surface_perspectives/decline) are kept —
   but the action now follows from the chosen hypothesis, and `provide_context`
   becomes the default for true-but-framed claims.
5. `reasoning_effort`: low → **medium** (this stage steers everything downstream).

Schema additions: `hypotheses[]`, `target_hypothesis`, `implied_claim` on
`ExtractionOutput` (freeze-visible for auditability).

### Stage 4 (redesigned) — Query plan + resilient retrieval
- **First wave (parallel, 4–6 queries)** generated from the target hypothesis:
  keywords+explicit month/year · verbatim quote in quotes · primary-data targeting
  (EIA/BLS/BEA/CDC/dockets/transcripts/official results) · fact-checker sweep ·
  media-provenance. Then ≤2 adaptive follow-ups (the existing controller loop, kept).
- **Fetch the post's `expanded_urls`** through `_fetch_clean_page` and add them as
  first-class Evidence rows tagged `origin=post_link`.
- **Stop dropping fetch-failed sources**: annotation-stamped URLs that 403/timeout are
  kept as snippet-only evidence (`body_markdown=""`, flag `fetch_failed=true`); one
  archive.org fallback attempt for the worst offenders (the `waf_block` log already
  identifies them).
- top_k 3 → **5**; wall clock 60s → 120s (batch/study runs are not latency-bound).
- Search-backend system prompts get the temporal block ("prefer coverage published
  near POST_DATE").

### Stage 4.5 (redesigned) — Reconcile with gates and parity check
Same single structured call, plus:
- The four patch rules **P-A/P-B/P-C/P-D** as prompt sections.
- The three V1.2 lint rules: **R-1 baseline retention** (prompt rule),
  **R-2 cutoff-consistency** (structural: reply facts cross-checked against evidence
  rows' `published_at`; enforced again at render time), **R-3 evidence-consistency**
  (structural: figures/characterizations in the payload must appear in evidence rows;
  contested ones must be attributed).
- **Devil's-advocate gate** (structural, in `pipeline.py`): if the derived outcome is
  `verified_supported` (or `challenge/context _unavailable`), run one extra search
  wave for the strongest counter-framing + a second reconcile pass before accepting.
  This single gate addresses the 11/11 `verified_supported` failure rate.
- **Note-parity self-critique**: reconcile must draft `hypothetical_note` (the note a
  top CN contributor would write) and confirm the payload carries its load-bearing
  facts; quantitative claims require the actual counter-numbers.
- **Temporal epistemics fields** (same call, structured output):
  `knowledge_state_at_post_date` (what was verifiably knowable / still unsettled at
  POST_DATE); `verdict_derivation` (chain from evidence-row indices to verdict — any
  step that cannot cite a row is a hindsight leak); `hindsight_check` (would the
  verdict survive on these rows alone? if not, weaken it); and an
  **unresolved-at-post-time** verdict framing for claims about in-progress events —
  the contemporaneous fact-check is the claim's *prematurity* ("declares as final
  something still undecided when posted"), not the later-known outcome.
- Central-claim evidence binding: stances audited against findings (existing audit),
  but audit failures now **repair** (drop the offending URL / re-stamp the stance)
  instead of collapsing to NEI; only unrepairable results collapse.

### Verdict derivation — weighted sufficiency
Replace the flat "≥2 reliable URLs" rule:
- A `primary-source`-tier source whose evidence row **directly quantifies/settles** the
  target (reconcile marks `on_point=true`) counts as 2.
- `fact-checker` tier addressing the exact claim counts as 2.
- Wikipedia reclassified `background` (counts 0.5, citable for context, never
  load-bearing); aggregators inherit the tier of the wire service when identifiable.
- Threshold stays 2 — but reachable by one decisive source, matching how community
  notes actually cite.

### Renderer + coherence
- Renderer state derived **from the payload**, not the outcome label: if a substantive
  payload exists, state=actionable; NEI phrasing only when the payload is genuinely
  empty. Freeze keeps both, coherently.
- Study mode: contemporaneous voice (no "as of July", no post-cutoff references) —
  enforced by a render-time lint that greps for dates > cutoff.

### Multimodal (scoped)
- Keep Stage 1.5. Add **image-subject verification** when a post names a person and
  carries a photo (VLM: "is the pictured person plausibly {name}?" + provenance hits).
- **Video (new, study-critical)**: 41/108 posts are videos the pipeline can't see. The
  study already stores the files (`study/data/media/`). Minimal viable path: sample
  N keyframes → VLM describe/OCR (existing `multimodal.py` machinery) + transcribe
  audio; feed as image_evidence. Without this, a third of study posts are checked
  blind against exactly the content the note addresses.

---

## 4. What stays unchanged
- The 5-action vocabulary, freeze schema spine, three-tone renderer, invariance
  boundary (render separate from pipeline), source-tier lists infrastructure,
  stub/test backends, Papelo-style adaptive follow-ups (now after the plan wave).

## 5. Implementation plan

| Phase | Change | Files | Size |
|---|---|---|---|
| **P0 (1 day)** | Temporal context block into all prompts + `as_of` param | pipeline.py, extract.py, verify.py, reconcile.py, search.py, llm.py | ~120 LOC |
| P0 | Stop dropping fetch-failed annotated URLs; keep snippet-only | search.py | ~30 LOC |
| P0 | Fetch expanded_urls as evidence | pipeline.py, search.py | ~40 LOC |
| P0 | Renderer/outcome coherence fix | pipeline.py, render.py | ~40 LOC |
| P0 | top_k 5, wall clock 120s, extract effort→medium | verify.py, extract.py | ~10 LOC |
| **P1 (2–3 days)** | Hypothesis-first extraction (new prompt + schema fields) | extract.py, schema.py | ~150 LOC |
| P1 | Query-plan first wave (parallel) + P-A/P-B provenance searches | verify.py | ~120 LOC |
| P1 | Reconcile patch rules P-A–P-D + lints R-1–R-3 + note-parity self-critique | reconcile.py, render.py | ~110 LOC |
| P1 | Devil's-advocate gate on supported/unavailable | pipeline.py | ~60 LOC |
| P1 | Weighted sufficiency + wikipedia/aggregator tiers | verdict.py, sources.py, source_lists.json | ~80 LOC |
| P1 | Audit repair-not-nuke | audit.py, pipeline.py | ~50 LOC |
| P1 | Evidence publication-date extraction + structural hindsight partition (reconcile/verdict see only pre-cutoff rows) + temporal-epistemics fields in reconcile output | search.py, pipeline.py, reconcile.py, verdict.py, schema.py | ~110 LOC |
| **P2 (2–4 days)** | Video keyframes + transcript → Stage 1.5 | multimodal.py, new video.py | ~250 LOC |
| P2 | Image-subject verification | multimodal.py, reconcile.py | ~60 LOC |
| P2 | archive.org fallback for WAF-blocked domains | search.py | ~50 LOC |

Cost note: +1 reconcile-scale call on supported/unavailable paths (the gate), ~2–4
extra searches per run (plan wave), extract at medium effort. Everything else is
prompt/logic. Rough estimate: +30–50% tokens per invocation — worth it against a 33%
clearly-worse-than-note rate.

Validation plan after implementation: regenerate the 108 replies with the new
pipeline (study mode, cutoff = post_date+48h), rerun the same 6-grader × judge
harness, target: ≥85% agree, 0 endorsements, 0 no-results-with-findable-evidence,
mean note-parity ≥ 2.0, 100% temporal_ok.
