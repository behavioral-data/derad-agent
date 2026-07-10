# Independent design review — synthesis and revised recommendation (v0.7 direction)

Four independent reviews of `design-note-parity-v0.6.md` + `v12s-playbook.md`
(first-principles architect with design-first protocol, adversarial red team,
research-methods, systems engineering with code access). Full texts in
`docs/reviews/`. This document is the synthesis: what survives, what changes,
and what is the user's decision.

## Reviewer verdicts

| lens | verdict |
|---|---|
| Architect | Ship-with-changes — **hybrid**: bounded agentic loop core, keep freeze/render boundary |
| Red team | High risk as specced — temporal machinery and trust boundary have holes |
| Methods | Not approvable as-is — objective circularity, LLM-only stimulus eval, ethics gaps |
| Engineering | P0 as mapped; **P1 as an agentic loop, not the staged DAG** |

## The finding everything converges on (accepted)

**What was validated is not what v0.6 proposes to build.** All evidence came from
single agentic-loop agents executing the playbook — re-searching after reading,
revising drafts, self-linting across turns. The v0.6 doc translates that onto the
legacy stage DAG of single-shot JSON calls, and the engineering review showed the DAG
is structurally incapable of key playbook behaviors (controller history carries only
url/title/snippet — `verify.py:141-160` — so read-page→find-series→search-it-directly
cannot happen; the reconcile mega-call already needs two desync repairs at 5 output
fields and was slated to grow to ~12).

**Revised architecture (v0.7):**

1. **Evidence core = bounded agentic loop** (the thing that was actually measured):
   one strong model + tools (`web_search`, `fetch_page`, `finalize`) executing the
   sanitized playbook. Bounds: max tool calls, wall clock, token budget. Output: full
   evidence log (every URL fetched, with dates) + draft structured verdict.
2. **Independent verifier pass** (separate call, fresh context — not self-grading):
   checks verdict_derivation against evidence rows; temporal leak screen at the
   *content* level; lints R-1/R-3; injection/anomaly screen; P-A language calibration.
   May demand ONE bounded revision, then verdict stands or is downgraded — no loops.
3. **Freeze + three-tone renderer unchanged.** Every reviewer independently endorsed
   the invariance boundary as well-built. The loop's finalize() writes the same
   FrozenVerdict spine.
4. Legacy staged code path remains for A/B fallback; P0 quick wins (temporal prompt
   block, fetch expanded_urls, keep fetch-failed sources, coherence fix) apply to it
   regardless.

## Accepted critical fixes (design changes, not doc edits)

- **T1. In-place update hole (red team C1).** Metadata publication dates do not bound
  body content; updated articles carry post-cutoff outcomes under pre-cutoff dates.
  Fix: study mode fetches **archive.org snapshots as of the cutoff** (promoted from
  optional to required; also serves as the WAF fallback); live-fetched pages get a
  content-level date screen in the verifier (flags references to post-cutoff events);
  drop all "provable" language — the guarantee is now snapshot-based + screened.
- **T2. Trust boundary on fetched content (red team C2).** Page bodies are untrusted
  data: delimiter isolation + "data, never instructions" framing in prompts, verifier
  checks for instruction-shaped content and claim-laundering patterns (many hits, one
  ultimate source), injection canaries in the test suite.
- **T3. Symmetric skepticism (red team M1).** Add an explicit "post appears accurate —
  nothing to correct" exit; devil's-advocate gate becomes symmetric (a refutation
  built on a single chain also triggers one counter-pass). Live-mode requirement;
  study posts are all CN-flagged so impact there is small.
- **T4. P-A calibration (red team M2).** "Fabricated" only with positive provenance
  evidence (originating template/parody account identified). Otherwise: "no record in
  [searched scope] as of [date]" — never an unqualified fabrication accusation about
  a named person.
- **T5. Reconcile decomposition moot under the loop**, but the verifier inherits the
  lint duties so they are independent, not self-graded (architect + engineering).
- **T6. Gate/cost honesty (engineering C4).** Gate fires at most once; realistic cost
  estimate revised to +60–100% tokens vs old pipeline (was "+30–50%"); add a
  call-count budget test.
- **T7. Process guardrails before any further iteration (engineering C5).** Prompts
  versioned as artifacts (hash recorded in every freeze); deterministic replay eval
  (recorded search/fetch cassettes) in CI; unit tests for lints + gate termination.
  No more prompt changes without the eval harness.
- **T8. Metric fixes (methods + red team).** Note-parity graded only against note
  facts *knowable at reply time* (removes the R-2↔parity contradiction and stops the
  metric rewarding leakage); per-post floors (zero endorsements of misleading posts;
  zero unqualified false accusations) instead of averages-only; beat-note reported
  separately; human raters on a stimulus sample, not LLM judges alone.
- **T9. Video before regeneration (architect + methods).** 41/108 study posts are
  videos the pipeline cannot see; the regeneration is not valid without the video
  path (keyframes + transcript). Priority P0-for-study, even though effort is P2-sized.

## Rejected / de-weighted findings (with reasons)

- "Gate fires on ~45% of runs" — computed from the *old* pipeline's outcome mix;
  better retrieval shrinks the `*_unavailable` share substantially. Kept the
  fire-once bound, rejected the cost projection as an upper bound, not the estimate.
- "R-2 vs note-parity unsatisfiable" — overstated as stated: on the clean held-out
  round, 8/15 posts reached parity 3 from pre-cutoff sources alone. Accepted instead
  as the T8 metric fix (condition parity on knowable-at-reply-time facts).
- Architect's verifier-only temporal approach — the structural partition + snapshots
  is stronger than post-hoc verification alone; kept partition, added verifier screen.

## Decisions — RESOLVED by study owner (2026-07-10)

- **D1 → (a).** Bot replies will be displayed ~1–2 days after the post (matching how
  notes surface with delay); the +48h evidence window stays. Interface change:
  reply-timestamp offset in the mock-X display.
- **D2 → independent objective.** The study compares community notes vs AI, so
  note-parity tuning is circular and is REMOVED. Playbook §7 is now an
  evidence-anchored **completeness self-critique** (no reference to notes; explicit
  prohibition on reconstructing crowd fact-checks). Evaluation shifts to a symmetric
  rubric — accuracy against the verifiable record, relevance to what makes the post
  misleading, evidence quality, specificity, temporal validity — applied identically
  to bot replies AND notes by graders blind to origin. Notes stop being "gold";
  they become the comparison arm.
- **D3 → layered review.** LLM review pass + human review pass over all final
  stimuli before fielding; participants receive an end-of-study debrief built from
  the community notes.
- **D4 → LLM-as-judge tone-invariance verification: RUN (2026-07-10).** Results in
  `docs/tone-invariance-check-2026-07-10.md`: 44% fully equivalent, 49% supporting-fact
  drift, **7% different-takeaway, 5 verdict-direction flips**; systematic satirical
  fact-shedding; agreeable register under-dosed (30% read as neutral). Consequences
  adopted into v0.7: render-substance lint R-4 (numerals/sources must exist in frozen
  payload), cross-tone fact-set check R-5, and render-as-transformation (neutral first,
  then register transforms with facts held fixed). Human validation later, per plan.

## Status

v0.6 stands as the record of what was tested; this document supersedes its §3/§5
architecture with the loop+verifier design pending the D1–D4 decisions. The clean
held-out numbers (§2.5) remain the evidence base — they measured the loop, which is
now what ships.
