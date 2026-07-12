# Engineering review — "Note-Parity" design v0.6 (design-note-parity-v0.6.md + v12s-playbook.md)

Reviewer stance: independent staff engineer, did not write the design. Reviewed against the
existing implementation in `agent/factcheck/` (pipeline, extract, verify, reconcile, verdict,
audit, search, render, sources, schema, llm, freeze) and the live/study invocation paths
(`agent/app/utils.py`, `agent/app/app.py`, `study/scripts/batch_generate_replies.py`).

## TL;DR verdict

**Implement-with-changes for P0; different-architecture for P1.**

The P0 block (temporal context, keep fetch-failed sources, fetch `expanded_urls`, renderer/outcome
coherence, `top_k`/effort bumps) is architecture-independent, low-risk, and high-value — ship it as
mapped. The P1 block is where the design goes wrong: **it was validated as a single agentic loop
(the playbook), but it is being shipped as a fixed DAG of single-shot, schema-validated JSON calls.
The behaviors that produced the validation numbers — read-then-re-search, draft-then-revise,
lint-then-substitute, iterative devil's-advocate — are exactly the behaviors a fixed DAG of
single-shot calls does not have.** The v1.2-S held-out result (mean parity 1.20 → 1.80, 11/15 head-
to-head) is evidence for the loop, not for the DAG. Implementing P1 as mapped risks spending a full
108-post regeneration (money + hours + the grader harness) to discover the DAG misses the ≥85%-agree
/ parity-≥2.0 target — and you won't know until after the spend.

Recommendation: implement the playbook **as an agentic loop** (Claude tool-use loop over
search+fetch tools → structured finalization → separate cheap verifier), keeping the
freeze/render/source-tier/search-tool machinery that is the operationally hard, already-built part.

---

## Q1 — Translation fidelity: playbook (validated) vs staged DAG (§3/§5)

### CRITICAL-1. The validation does not transfer to the architecture being shipped.
The playbook (`v12s-playbook.md`) is a *procedure an agent follows with judgment*: §4 "identify
which official series answers this and search it directly" (requires reading a page, then searching),
§6 devil's advocate "run one additional search wave … before finalizing," §7 note-parity "If not,
**revise**," §9 lints "apply to your draft reply **before finalizing**" with §R-2 "re-established
pre-cutoff, generalized, or **dropped (substitution, not deletion)**." Every one of those verbs
(revise, substitute, re-establish, dig where it matters) is an *iteration over intermediate output*.
The DAG replaces them with fixed single-shot calls. The design acknowledges the validation was run by
agentic-loop agents but never argues that the mapped DAG reproduces the loop's behavior — it assumes
it. It does not.

### CRITICAL-2. "Re-search after reading" is structurally impossible in the current loop.
The design keeps the "existing controller loop" for ≤2 adaptive follow-ups (§3 Stage 4). But the
controller (`verify.py:_make_user_payload`, lines 141–160) feeds history as `{question, hits:[{url,
title, snippet}]}` — **it never sees `body_markdown`.** `body_markdown` is only attached when the
final `Evidence` rows are built for reconcile (`verify.py:279`), after the loop is over. So the
adaptive follow-up decides its next query from search snippets and titles, never from the fetched
article. The playbook's core retrieval move — read the fetched page, discover the EIA/BLS series it
references, then go search that series directly — cannot happen. This is the single biggest source of
the `missing_key_source` (n=40) failure the design is trying to fix, and the mapped design does not
close it. Fixing it means feeding fetched bodies back into the controller, i.e. a read→search loop —
which is the agentic loop.

### CRITICAL-3. One reconcile call cannot reliably do findings + payload + stances + note-parity draft + temporal epistemics + 3 lints.
Today `ReconciliationOutput` has **5 fields** (`reconcile.py:88–93`) and the code already carries two
workarounds because the model desyncs even *that*: stance-count drift repair (`reconcile.py:271–282`)
and the `consolidated_findings.perspectives` vs `presentation_payload.perspectives` desync repair
(`reconcile.py:294–297`). The design (§3 Stage 4.5) adds to the **same call**: P-A/P-B/P-C/P-D
conduct rules, R-1 baseline rule, `hypothetical_note`, `knowledge_state_at_post_date`,
`verdict_derivation`, `hindsight_check`, an unresolved-at-post-time framing, and a
"confirm the payload carries the note's load-bearing facts" step. That is ~7 more interdependent
fields on a model that already under-populates a 5-field output. Two consequences:
- **More required fields ⇒ higher schema-failure rate ⇒ more NEI.** `call_claude_json` raises on
  validation failure (`llm.py:103–106`) and reconcile degrades to a no-sources NEI
  (`reconcile.py:246–266`). Expanding the schema directly *increases* the no-result rate the design
  exists to reduce. This is a self-defeating change.
- **`verdict_derivation` and `hindsight_check` are self-graded fields the model will confabulate to
  look consistent.** A "chain from evidence-row indices to verdict" emitted by the same call that
  produced the verdict is not an audit; it is a rationalization. Its stated purpose ("any step that
  cannot cite a row is a hindsight leak") requires an *independent* checker.

### MAJOR-4. Draft→revise self-lint is downgraded to post-hoc detection, losing "substitution not deletion."
The playbook applies R-1/R-2/R-3 to a *draft* and rewrites it. The design makes R-2/R-3 "structural"
(§3): "reply facts cross-checked against evidence rows' `published_at`; enforced again at render
time." A structural check over a finished payload can only **flag or drop** a fact — it cannot do
R-2's required behavior ("re-established pre-cutoff, generalized … substitution, not deletion") or
R-3's "use the attributed form." Dropping a fact when the playbook says substitute produces a
*weaker* reply — the opposite of note-parity. Also, "cross-check reply facts against evidence rows"
is an entity/number-matching NLP problem, not a `grep`; §5 budgets it as "structural … ~110 LOC" and
understates it badly.

### MAJOR-5. Hypothesis-first extraction is a large single call, pre-search, still one-shot.
§3 Stage 2+3 asks one `extract` call (raised to `reasoning_effort=medium`) to do: 2–4 hypotheses +
provenance-first + implied-claim + target selection + claim decomposition + action selection. Today
that call is `reasoning_effort=low`, `max_tokens=2048`, and does far less (`extract.py:206–215`). It
also still runs **before any evidence exists**, so hypothesis targeting is done blind — the playbook's
targeting benefits from the agent's ability to revisit the target after early searches. §5's "~150
LOC" is fine for the prompt; the risk is not LOC, it is one blind pre-search call carrying the whole
downstream steering load with no feedback path.

### §5 line items that are underspecified or underestimated
- **Devil's-advocate gate — "~60 LOC" (pipeline.py).** Needs a second search wave (reuse verify), a
  second full reconcile, re-derive outcome, re-audit, **and explicit termination.** See CRITICAL-8.
  60 LOC is optimistic and it omits the termination/idempotency logic entirely.
- **Weighted sufficiency — "~80 LOC."** This replaces integer counting (`verdict.py:34–36`,
  `_RELIABLE_THRESHOLD=2`) with weighted counting keyed on a **new model-emitted `on_point` flag per
  evidence row**. That is a schema + reconcile + verdict change, cross-cutting, and the flag is
  load-bearing (see CRITICAL-9). "aggregators inherit the tier of the wire service when identifiable"
  requires extracting the wire service from an aggregator page — a real extraction task, not a table
  edit.
- **Audit repair-not-nuke — "~50 LOC" (audit.py, pipeline.py).** `AuditResult.failures` is a
  `list[str]` (`audit.py:22–24`) and the audit is a shape/URL-containment check that its own comment
  calls a tautology on the outcome (`audit.py:119–124`). "Repair (drop the offending URL / re-stamp
  the stance)" needs structured failure objects that say *which* URL/stance, plus mutation of a frozen
  tree — more than 50 LOC and a schema/contract change.
- **Evidence pub-date + structural hindsight partition — "~110 LOC."** trafilatura date extraction is
  available (`search.py:429–434` already parses metadata) but frequently returns nothing. The design
  says verdict/reconcile "see only pre-cutoff rows" — but **never says how `published_at == unknown`
  is treated.** If unknown counts as pre-cutoff you leak hindsight; if it counts as post-cutoff you
  discard good evidence. The entire temporal-discipline research claim rests on this undocumented rule.
- **Video — "~250 LOC" (P2).** Keyframe sampling + per-frame VLM + audio transcription is a subsystem
  with a new heavy dependency (Whisper/Azure Speech), new cost, new latency, new failure surface.
  250 LOC + "existing multimodal machinery" understates the operational weight (a new Azure resource,
  batching, and per-post minutes of latency for 41/108 posts).

---

## Q2 — Alternative architecture: implement the playbook AS an agentic loop

**Shape:** one Claude tool-use loop (Anthropic SDK `client.beta.messages.tool_runner`; `anthropic`
0.85.0 is installed) with two tools that already exist in this repo as functions —
`web_search` (the `ClaudeWebSearchBackend` server tool, `search.py:629`) and `fetch_page`
(`_fetch_clean_page`, `search.py:371`), optionally `classify_source` (`sources.build_quality_table`).
The loop system prompt *is essentially the playbook* (the validated artifact). Then:
1. a **structured-output finalization** call that emits the existing `PresentationPayload` +
   `ConsolidatedFindings` + `Evidence[]` (so the freeze/render boundary is unchanged), and
2. a **separate cheap verifier pass** (low-effort model call) that independently re-derives the
   outcome and runs the cutoff-consistency / evidence-consistency lints — the honest version of
   `verdict_derivation`/`hindsight_check`, done by a different call than the one being checked.

### Comparison (loop vs mapped DAG)

| Axis | Agentic loop | Mapped DAG |
|---|---|---|
| **Fidelity** | Runs what was validated. Read→search, draft→revise, iterative devil's-advocate are native. | Approximates it; loses the iterative behaviors (Q1). Validation numbers do not transfer. |
| **Cost** | Variable path, but one cached conversation. Prompt caching on system+tool defs + growing transcript is cheap. | No caching across stages; **the full JSON schema is re-sent in every call's system prompt** (`llm.py:63–69`) — and the reconcile schema is the largest in the tree. Many independent calls re-pay that every time. |
| **Latency (live)** | One streaming loop; naturally wall-clock-bounded; nondeterministic tail. | Sum of sequential stages + the gate's extra wave+reconcile roughly doubles the tail (Q3). |
| **Reliability** | Single loop can derail → bound with `max_iterations` + wall clock; coarse recovery. | Each stage degrades independently to NEI — predictable but the failure default is "no result." |
| **Auditability** | Freeze the transcript (every query, every fetched page, the actual reasoning) + finalization. The `verdict_derivation` the DAG fakes is **real** here. | Per-stage structured freeze — genuinely good for research, but the reasoning is post-hoc reconstructed, not the actual chain. |
| **Determinism** | Path nondeterministic; finalization schema fixed. Freeze transcript+temp for repro. | Structure fixed, content still nondeterministic (LLM). Not actually more reproducible in the way that matters. |
| **Testing** | Tools unit-tested; loop tested via recorded transcript cassettes (VCR-style); finalization schema-tested. | Each stage unit-testable with stubs — this is the DAG's real strength (existing tests do exactly this). |

### How much of the codebase survives each way
- **Survives either way (the hard, built part):** `schema.py` (freeze contract), `freeze.py`
  (invariance boundary), `render.py` (three-tone renderer + invariance enforcement), `sources.py`
  (tier lists + caching), `search.py` backends and `_fetch_clean_page`, `llm.py`, and the
  live/study wiring in `utils.py`. In the loop these become **tools + finalization + verifier**;
  `audit.py`/`verdict.py` become the verifier pass.
- **Replaced by the loop:** `pipeline.py` orchestration and the `extract.py` + `verify.py` +
  `reconcile.py` prompt trio collapse into the loop system prompt + finalization schema. That is a
  feature: those three prompts are three imperfect re-encodings of one procedure; the loop lets you
  keep **one** copy of the procedure — the playbook — which is the thing you actually validated.

Net: ~50–60% of the code (and ~100% of the operationally risky infra) survives the loop. The DAG
survives 100% of the code but ships the thing you didn't validate.

---

## Q3 — Operational concerns

### CRITICAL-6. The "+30–50% tokens" arithmetic is optimistic; expect +60–100% average, +100–150% on gated runs.
The cost note counts "+1 reconcile-scale call on supported/unavailable paths, ~2–4 extra searches,
extract at medium." It omits the two biggest drivers:
1. **Reconcile input scales with `top_k × query-count.`** `top_k` 3→5 and the first wave goes from
   the current single verify seed (`verify.py:117–123`) to 4–6 hypothesis-targeted queries + up to 2
   follow-ups. Each surviving hit carries up to 3 KB of body (`_RECONCILE_BODY_CAP=3000`,
   `reconcile.py:58`). Today: ~5–9 sources ≈ 15–27 KB into reconcile. New: less overlap across
   hypothesis-targeted queries ⇒ ~12–20 unique sources ≈ 36–60 KB — a **2–3× reconcile input on
   every run**, and reconcile is the most expensive call in the pipeline.
2. **The gate fires on a large fraction of runs, not a corner case.** It triggers on
   `verified_supported` **or** any `*_unavailable` outcome. Historically 38% of runs end NEI/unavailable
   (design §1) plus the supported cases — so the +1 reconcile + extra search wave lands on ~40–50% of
   runs. "On the supported/unavailable paths" reads like a rare tail; it is nearly half of traffic.

Combine a 2–3× reconcile input on 100% of runs with a doubled reconcile on ~45% of runs and the
average is well above +50%. State it honestly as **~+60–100% average, ~+100–150% on gated runs.**

### CRITICAL-7. Rate-limit cascade → NEI fleet-wide.
Live mode caps at 5 concurrent pipelines (`app.py:82–84`). The design raises per-run Claude calls
from ~10–12 to ~15–20 (plan wave + gate + second reconcile). A mention burst that today puts ~50–60
calls in flight against the Foundry Claude deployment now puts ~90–100. Every search backend swallows
exceptions and returns `[]` on error (`search.py:195–197`, `645–647`); zero hits → NEI. So the
failure mode of hitting Azure TPM/RPM limits is **silent NEI across the whole burst**, and the design
makes bursts hit the ceiling ~2× sooner. There is no backpressure between the search calls and the
stage calls (they share the deployment).

### CRITICAL-8. Devil's-advocate gate can double-cost unbounded / loop forever.
§3 says "run one extra search wave … + a second reconcile pass before accepting." If the second
reconcile *also* returns `verified_supported`/`*_unavailable` (very possible — thin evidence stays
thin), does the gate re-fire? The doc's wording ("before accepting") does not specify termination.
Implemented naively as `while outcome in {supported, *_unavailable}: re-gate` it loops until the
wall clock. This **must** be capped at exactly one re-gate, idempotent, with the gate result frozen
(fired: bool, outcome_before, outcome_after). §5's 60 LOC does not budget this.

### MAJOR-9. Weighted sufficiency + model-emitted `on_point` is a research-validity landmine.
"A primary-source-tier row that reconcile marks `on_point=true` counts as 2" and "threshold reachable
by one decisive source" means a **single** model-set boolean flips `verified_nei → verified_supported`.
The design's own headline failure is "every one of the 11 `verified_supported` outcomes is a failure."
Lowering the bar to one source, gated on a self-emitted flag, re-creates exactly that risk through a
different door — a confused reconcile that marks one mis-tiered "primary" source `on_point=true`
produces a confident endorsement. The gate is supposed to catch this, but the gate itself depends on
reconcile's honesty (CRITICAL-3). Keep the ≥2-independent-source floor for `supported`; let the
single-decisive-source rule apply only to `refuted`/`context`.

### MAJOR-10. Blast radius of the new fetch paths.
- Keeping fetch-failed sources as snippet-only (P0) is correct and low-risk — today they're dropped
  (`search.py:207`, `_classify_hit` returns reject on 403/429/451 at `515–525`).
- **archive.org fallback (P2)** adds a new external dependency that itself rate-limits/blocks under
  load — a new outage surface for the worst-offender domains, hit exactly when many domains are
  WAF-blocking (correlated failure). Budget a per-run cap and a circuit breaker.

### MAJOR-11. Observability gaps that will make a bad verdict undebuggable in prod.
Today the freeze captures per-evidence `question` (so seed/follow-up queries are partially recorded)
and logs `waf_block` and `reconcile_stance_drift`. The design adds `hypotheses`/`target`/
`implied_claim`/temporal fields to the freeze (good). Still missing, and needed to debug a bad
verdict after the fact:
- **Gate telemetry:** fired?, outcome-before/after, whether it flipped the verdict. (Nothing today.)
- **Per-wave query provenance + which rows were partitioned out by cutoff** (with their `published_at`
  and the unknown-date decision). Without this the temporal claim is unauditable in practice.
- **Per-stage token/cost and LLM-call count** — needed to detect CRITICAL-8 regressions mechanically.
- **Fetch-failure / snippet-only rate and weighted-sufficiency near-misses** (e.g. "supported on a
  single on_point primary"). These are the exact cases a reviewer will want to pull.

---

## Q4 — Complexity budget

### CRITICAL-12. ~20 named rules across inline prompt strings, no eval in CI, prompts unversioned, interactions known to regress.
The rule stack after this design: Stage-0 temporal contract, a 9-item hypothesis taxonomy, P-A/B/C/D,
R-1/2/3, weighted-sufficiency tiering, the devil's-advocate gate, note-parity, image-subject
verification, and the temporal-epistemics fields. These live as large inline string constants in
`extract.py`, `verify.py`, `reconcile.py`, `render.py`, `sources.py` (a rough grep already counts ~74
rule-ish tokens across the stage files). Problems:
- **No eval gate in CI.** `.github/workflows/tests.yml` runs `pytest -q` only. The real eval is the
  manual 108-post regen + 6-grader/judge harness, run offline, costing money and hours. So today the
  only way to know a prompt edit helped or hurt is a full paid regen.
- **Prompts are not versioned artifacts.** The freeze records `pipeline_commit` + source-list
  versions (`schema.py:81–88`, `pipeline.py:270–275`) but **not a prompt version.** You cannot tie a
  frozen verdict to the exact prompt text that produced it — fatal for a research pipeline whose
  output is the paper's data.
- **Rule interactions are known to regress and are untested.** The design itself documents two
  rule-conflict regressions (§2: R-1 baseline vs recent-peak displaced the note; cutoff-consistency vs
  specificity). There is no automated guard against re-introducing them when the next rule is edited.

### Minimum harness needed before this is safe to iterate on
1. **Prompt versioning:** move stage prompts to versioned templates and stamp a `prompt_version` into
   `BackendVersion`. Non-negotiable for research reproducibility.
2. **Deterministic replay eval in CI:** a small frozen set (the 12 tuning + ~20 held-out posts) with
   recorded search+fetch cassettes so the pipeline runs offline, free, deterministically. Assert on
   *structured* signals that need no LLM judge: outcome label, "no date > cutoff in the reply"
   (a real regex over the render), "contains a counter-number when the note is quantitative," and the
   cutoff-partition invariant. This catches CRITICAL-8/9/regression classes cheaply on every PR.
3. **Unit tests for the structural lints.** R-2/R-3 are advertised as structural — if they are not
   unit-tested (feed a payload with a post-cutoff-dated fact, assert it is caught/substituted) they
   are decorative. Same for the gate's one-re-gate termination and the `published_at == unknown` rule.
4. **A call-count/cost budget test:** assert LLM calls per run ≤ a fixed bound so the gate can never
   silently loop (CRITICAL-8).
5. **Judge eval as a pinned, separate job** (nightly / pre-merge, not per-commit): fixed judge model +
   rubric, tracked metrics (mean parity, agree%, endorsement count), gate = "no regression vs the last
   frozen baseline." Run the full 108 only at release, not per edit.

Without at least (1)–(4), every rule edit is a blind change to a system whose own designers recorded
that rules fight each other.

---

## Ranked findings

| # | Sev | Finding | Anchor |
|---|---|---|---|
| 1 | CRITICAL | Validation was done as an agentic loop; shipping a single-shot DAG. Numbers don't transfer. | design §2/§5 vs playbook §4/§6/§7/§9 |
| 2 | CRITICAL | Read-then-re-search impossible: controller never sees fetched bodies. | verify.py:141–160 vs :279 |
| 3 | CRITICAL | One reconcile call can't reliably emit findings+payload+stances+note+temporal+3 lints; more fields ⇒ more NEI; self-graded derivation is confabulation. | reconcile.py:88–93, 246–297; llm.py:103 |
| 4 | CRITICAL | "+30–50% tokens" undercounts; real ~+60–100% avg (reconcile input 2–3×; gate fires ~45% of runs). | design §5; reconcile.py:58; verify.py:117 |
| 5 | CRITICAL | Devil's-advocate gate has no specified termination → loop-forever / unbounded double-cost. | design §3 Stage 4.5; app.py:82 |
| 6 | CRITICAL | Weighted sufficiency: one model-emitted `on_point` flips NEI→supported — re-opens the 11/11 endorsement failure. | verdict.py:26–36; design §3 |
| 7 | CRITICAL | ~20 rules in inline prompts, no CI eval, prompts unversioned, interactions known to regress. | tests.yml; reconcile.py prompt; design §2 |
| 8 | MAJOR | Rate-limit cascade: ~2× calls/run → bursts hit Azure limits sooner → silent NEI fleet-wide. | search.py:195/645; app.py:82 |
| 9 | MAJOR | R-2/R-3 "structural" = detection only; loses playbook's substitution-not-deletion; matching understated as grep. | design §3; reconcile.py |
| 10 | MAJOR | §5 LOC underestimates: gate 60, weighted 80, audit-repair 50, hindsight-partition 110, video 250. | design §5 |
| 11 | MAJOR | `published_at == unknown` handling undefined — the whole temporal-discipline claim rests on it. | design §3 Stage 0; search.py:429 |
| 12 | MAJOR | Observability gaps: no gate telemetry, per-wave queries, cutoff-partition record, per-stage cost. | freeze.py; schema.py |
| 13 | MINOR | Audit "repair not nuke" needs structured failures + frozen-tree mutation, not `list[str]`. | audit.py:22–24, 119–124; pipeline.py:460 |
| 14 | MINOR | Renderer payload-derived state change touches the invariance boundary; keep freeze coherent. | render.py:61–77; freeze.py |

---

## Verdict

- **P0 (temporal block, keep fetch-failed snippet-only, fetch `expanded_urls`, renderer/outcome
  coherence, `top_k`/effort):** **implement-as-mapped.** Architecture-independent, low-risk, correct.
  Add: the `published_at == unknown` rule and a `prompt_version` stamp while you're in these files.
- **P1 (targeting + retrieval + reconcile mega-call + gate + weighted sufficiency + audit-repair):**
  **different-architecture.** Implement the playbook as a Claude tool-use loop (search+fetch tools) →
  structured finalization → separate cheap verifier, keeping the freeze/render/source-tier/search-tool
  machinery. This runs the artifact you validated and makes `verdict_derivation`/`hindsight_check`
  real instead of self-graded.
- **If P1 must stay a DAG (implement-with-changes, mandatory):** (a) feed fetched bodies to the
  follow-up controller; (b) split reconcile into reason→finalize→verify (not one mega-call); (c) keep
  ≥2-source floor for `supported`, single-source only for refuted/context; (d) cap the gate at one
  re-gate with frozen telemetry and a call-count budget test; (e) stand up the deterministic replay
  eval + structural-lint unit tests **before** the 108-post regen, and version the prompts.
- **Do not run the full 108-post regeneration until the deterministic held-out eval + prompt
  versioning are in place** — otherwise a failed target is an expensive, unattributable result.
