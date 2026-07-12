# Independent Architecture Review — Fact-Check Reply Bot

Reviewer: independent senior architect (did not write the design). Objective: decide staged pipeline vs agentic loop vs hybrid, and whether a redesign is warranted, for a system that (a) replies live to misleading X posts and (b) regenerates replies for 108 historical posts for a research study, matching/beating Community Notes.

---

## MY DESIGN (written BEFORE reading their proposal)

### First-principles constraints that drive the shape

1. **Freeze + invariance boundary is the dominant architectural fact.** The research requires one frozen, auditable JSON verdict from which three tones (neutral/satirical/agreeable) render *without re-running evidence*. This forces a hard cut between **evidence+verdict** (expensive, stochastic, uses search) and **rendering** (cheap, tone-only, no new facts). This cut is non-negotiable and is the top-level structure regardless of what the inner engine looks like.
2. **Temporal correctness is the hardest correctness property.** STUDY mode requires evidence cutoff = post_date + 48h; a reply must be plausible as written at post time. Search must be date-bounded, and the model's parametric (post-cutoff) knowledge must be actively suppressed and *verified out*. This is where most naive designs fail.
3. **Community-Notes-grade quality means variable-depth evidence work.** Some claims settle in one search; some need five, with follow-ups triggered by what was found. Real fact-checking interleaves reason→search→reason. A fixed DAG with a single "search stage" under-serves hard claims.
4. **Research reproducibility/auditability.** Every run must freeze: claims, evidence (URL + publisher + pub_date + verbatim quote + supports/refutes), verdict, confidence, cutoff, queries used, model/tool versions, hash.
5. **Two modes, one engine.** LIVE and STUDY must run the *same* evidence engine; only ingest + cutoff source differ. Otherwise the study doesn't validate the deployed system.

### The shape I would build: **hybrid — deterministic scaffolding around a bounded agentic evidence core, then a pure renderer**

**Phase 0 — Ingest & normalize (deterministic).** Fetch post text, author, timestamp, media, embedded links (trafilatura). STUDY: load from frozen historical dataset with known post_date; cutoff = post_date+48h. LIVE: cutoff = now. Resolve the post's own cited links so they're on the table.

**Phase 1 — Claim extraction (one cheap structured LLM call).** Emit atomic check-worthy claims, the primary claim, claim type, and "what evidence would settle it." Purpose: constrain + log the target, give the audit trail a clean "what are we checking" record. Lightweight guard, not a heavy stage.

**Phase 2 — Evidence & verdict engine (BOUNDED AGENTIC LOOP).** One Sonnet-class model; tools = `web_search` (hard date filter ≤ cutoff) + `fetch_page` (trafilatura). System prompt = the fact-checking playbook/procedure. The model plans searches, fetches primary sources, extracts dated quotes, reconciles conflicts, decides when it has enough — capped (e.g. ≤6–8 tool calls). Temporal control lives *in the tool* (search enforces `before:cutoff`) and in the prompt (cutoff stated, post-cutoff knowledge forbidden). **Forced structured final output**: verdict JSON schema as above.

**Phase 3 — Independent verifier (separate call; no fresh open-ended search).** Checks: every cited URL pub_date ≤ cutoff; each quote actually supports the claimed relation (misread guard); verdict label calibrated to evidence strength; URLs reachable and quote actually present (re-fetch → anti-hallucination for sourcing); no post-cutoff facts leaked into rationale. On fail: bounded loop-back to Phase 2, or downgrade to "insufficient evidence." **This verifier is what makes an agentic core trustworthy for research.**

**Phase 4 — FREEZE.** Immutable JSON record + hash + model/tool versions + cutoff. This is the invariance-boundary artifact.

**Phase 5 — Renderer (pure function of frozen JSON × tone).** Low temperature. Three tones from the SAME JSON. Prompt constraint: "use only facts present in the verdict JSON." Post-check: rendered reply's factual claims ⊆ frozen JSON (tone must not introduce facts). Enforce char limit + source link.

### Why this shape, not the alternatives
- **Not a pure staged pipeline**: a rigid extract→search→reconcile→verdict DAG can't do the follow-up search that Community-Notes-grade claims require, and error propagates with no recovery. It over-fits the easy claims and under-serves the hard ones.
- **Not a pure agentic loop**: freeform trajectories are hard to freeze cleanly, drift temporally, and vary run-to-run. Unacceptable for an auditable study without scaffolding.
- **Hybrid wins**: agentic core for the variable-depth evidence work (which is what tool-trained models are good at), deterministic guards for extraction/verification/freeze/render (which is what research auditability needs). The invariance boundary is orthogonal to this and sits above it.

### Cost/latency
Extraction (cheap) + bounded agentic loop (main cost) + verifier (medium) + 3 renders (cheap). Predictable within "minutes." STUDY = same engine in batch over 108 posts.

---

## COMPARISON (after reading their design + auditing the code)

### Ground truth I verified in the codebase
- The staged pipeline is real: `pipeline.py` runs Stage 1.5 multimodal → Stage 2+3 extract → **Stage 4 "iterative verification (Papelo-style)"** → Stage 4.5 reconcile (single `call_claude_json`) → Stage 5 audit → Stage 6 freeze → Stage 7 render (invoked separately).
- **The invariance boundary is genuinely well-built**, not aspirational: `render.py` reads ONLY a `RendererView` from `freeze.py`; it imports no search/fetch/http; three tone registers (neutral/agreeable/satirical) compose with five action templates over the frozen payload. Their "render separate from pipeline" claim holds in code. This is the strongest part of the existing system and both designs keep it.
- **No stage currently knows the date**: `as_of`/`evidence_cutoff`/`post_date`/`published_at` appear nowhere in `pipeline.py` or `search.py`. The temporal contract is net-new, and the audit's "no date contract anywhere" is accurate.
- `reconcile()` is a **single** structured call with **no ability to search**. Stage 4 (`iterative_verify`) is the only place with an adaptive loop, and that loop is confined to search.

### Where we agree
Two-phase shape (evidence+verdict → freeze → pure tone renderer), the 5-action vocabulary, keeping the freeze schema spine and the three-tone renderer. My "invariance boundary above everything" is exactly their existing architecture. No disagreement there.

### Where THEIR design is better than mine
1. **Empirical grounding.** Mine is armchair first-principles. Theirs is derived from a 108-post graded audit with a coded failure taxonomy, an ablation (V1/V2/V3) that isolates targeting vs retrieval, and — rare — a decontamination protocol (§2.5) after they caught their own answer-leakage, adaptive overfitting, and judge circularity, then re-ran blind on held-out data with a cross-family judge. That is better science than most design docs and it beats my ungrounded sketch on the things this corpus actually gets wrong.
2. **Structural hindsight partition > my verifier-checks-dates approach.** Making post-cutoff rows visible to the search controller only, STRIPPING them before reconcile, and having `verdict.py` count only pre-cutoff rows makes the verdict *provably* pre-cutoff-derived from the freeze — not dependent on prompt obedience. My design leaned on a verifier catching temporal leaks after the fact; theirs makes the leak structurally impossible for the load-bearing path. This is the single best idea in their doc.
3. **Video is a corpus fact I under-weighted.** 41/108 study posts are video the pipeline literally cannot see, and the note addresses exactly that content. My sketch hand-waved "fetch media." Their P2 (keyframes + transcript) is the right, concrete call and is study-critical.
4. **Traceability.** Every fix maps to a coded root cause (e.g. "11/11 `verified_supported` are failures" → devil's-advocate gate). That discipline is worth more than architectural elegance.
5. **Pragmatism.** Phased P0/P1/P2 reuse of a codebase whose invariance boundary already works, rather than my greenfield rebuild. Lower delivery risk.

### Where MY design is better / their weaknesses
1. **Independent adversarial verifier vs in-call self-critique.** Their R-1/R-2/R-3 lints, note-parity self-critique, and central-claim binding all live *inside the same reconcile call* that formed the verdict (I confirmed reconcile is one call). A model grading its own just-committed output is the weakest form of verification. Their only truly independent, adversarial check is the devil's-advocate gate — and it's **conditional** (fires only on `verified_supported`/`unavailable`). My unconditional separate verifier (re-fetch each cited URL, confirm the quote exists and supports the claimed relation, check calibration) is stronger and directly attacks their `factual_error_in_reply` and `missing_key_source` modes. The doc calls R-2/R-3 "structural" but the enforcement mechanism is underspecified — string-matching code can't judge whether a quote *supports* a claim, and if an LLM does it, it needs to be a separate call, which the doc doesn't budget.
2. **The overloaded reconcile call.** Stage 4.5 crams reconcile + P-A/P-B/P-C/P-D + R-1/R-2/R-3 + devil's-advocate framing + note-parity self-critique (draft `hypothetical_note`) + four temporal-epistemics fields + central-claim binding into ONE structured output. In the validated agentic runs these were ten *sequential* procedure steps, each getting the model's full attention across turns. Collapsing steps 5–9 of the playbook into a single call is exactly where I expect quality to bleed out.
3. **Note-parity loses its teeth in the port.** In the playbook (§7), if the draft doesn't carry the note's load-bearing numbers, the agent revises **and can search again** for them. In the staged design, note-parity is a field in reconcile, which cannot search. It becomes diagnostic ("I lack the numbers"), not corrective ("go get them"). The ablation credits note-parity for V1's win; the port defangs the mechanism it credits.

### The crux: validation fidelity (what was tested ≠ what ships)
The validated artifact is an **agentic loop**: Opus agents executing `v12s-playbook.md` end-to-end in one continuous context with live search and free loopback. The proposal reconstructs that behavior out of **separate staged calls** (extract → search → reconcile) with hard schema boundaries and information loss at each cut. That is not a translation; it is a re-implementation of a different computational structure. Concrete fidelity gaps introduced by staging:
- **Cross-stage loopback is lost.** The audit's own dominant failure chain — "wrong target → wrong queries → wrong/no evidence → wrong verdict" — is a *pipeline* pathology (error propagation, no recovery). The agentic loop doesn't have it because the same agent re-targets when evidence comes back thin. The staged port keeps the hard boundaries that cause the chain and adds only two partial patches: Stage 4's intra-search Papelo loop (survives — good) and the conditional devil's-advocate gate (one cross-stage loop, for one outcome).
- **Hypotheses freeze at extraction.** The loop refines its target hypothesis as evidence arrives; the staged extraction commits the target before any search and nothing downstream can revise it.
- **Self-grading replaces fresh attention.** Ten discrete reasoning steps become one call's worth of divided attention.

Most important: **the §5 validation plan compares the new pipeline only against the notes (and implicitly the old production system) — never against the validated agentic-loop artifact.** So the staged port could lose fidelity to the loop, still look "better than old production" on the notes rubric, and the paper would then quote the loop's held-out numbers (parity 1.80, 11/15 head-to-head) for a system that never achieved them. That is a research-validity hazard, not just an engineering one.

Tempering the critique honestly: the gap is narrower than "staged vs agentic" sounds, because (a) the existing Stage 4 already *is* an adaptive loop, (b) the freeze makes run-to-run agentic nondeterminism irrelevant for the study (you generate 108 once and freeze), and (c) the structural hindsight partition is actually *easier* to guarantee in the staged design than in a free loop. So this is a fidelity-risk-to-be-retired, not a proof the port fails.

### The strongest hybrid
Keep their whole top-and-bottom (freeze schema, three-tone renderer, structural hindsight partition, video, source tiers, empirical harness) and their P0 fixes wholesale. For the **evidence+verdict core**, make it a **bounded agentic loop that runs the playbook directly** — the exact validated artifact — emitting the structured `FrozenVerdict` as its forced final output, with `web_search` date-filtered to ≤ cutoff and post-cutoff rows partitioned structurally as they propose. Then add **my independent verifier** as an unconditional Stage 5.5 (re-fetch + quote-support + calibration + temporal lint) before freeze. This preserves cross-stage loopback and note-parity-driven re-search, keeps auditability (structured final output + tool log both freeze cleanly), keeps cost bounded (tool-call cap), and — critically — ships the thing that was measured.

---

## VERDICT

**SHIP-WITH-CHANGES.** The design is right far more than it is wrong, it is unusually well-evidenced, and the invariance boundary it builds on is real and sound. It does not need a redesign. But it must not be *frozen* on agentic-loop evidence without closing the validation-fidelity gap, and two of its quality gates are weaker than the doc claims.

**Architecture answer: HYBRID, and specifically resist the instinct to reconstruct the agentic loop as staged calls.** For both modes the two-phase cut (evidence+verdict → freeze → pure renderer) is mandatory and already present. Within Phase A, the evidence+verdict core should be a **bounded agentic loop executing the playbook** (what was validated), not a stack of independent structured calls — with deterministic scaffolding around it (structural cutoff partition + independent verifier + freeze). Reasoning across the axes the brief names:
- **Both modes:** identical engine; LIVE sets cutoff=now, STUDY sets cutoff=post_date+48h. A loop serves LIVE's variable claim difficulty better and serves STUDY's batch-of-108 fine.
- **Cost:** a *bounded* loop (tool-call cap) ≈ the staged first-wave + follow-ups + gate they already budget (+30–50% tokens); no material difference.
- **Auditability/freeze:** equal — force structured final output; freeze the tool-call log too. Staging is not required for a clean freeze.
- **Validation fidelity:** decisive for the loop. Ship what you measured; don't quote a loop's numbers for a re-implementation you didn't measure.

**Changes, ranked (most important first):**
1. **Close the validation-fidelity gap before freezing.** Add to §5 a head-to-head: staged-pipeline output vs the validated agentic-loop (V1.2-S) on the 15-post held-out set (and a fresh CN-snapshot sample), same blinded cross-family judges. Freeze only if the pipeline matches the loop within noise. If it doesn't, ship the loop for the core. This is the difference between a defensible paper and an indefensible one.
2. **Do not overload one reconcile call; or better, make the core a loop.** If staying staged, split Stage 4.5 so that verdict-formation and the lint/verify checks are *separate* calls, and make note-parity able to trigger one more search wave (mirror the devil's-advocate gate). If moving to the loop, this dissolves.
3. **Add an unconditional independent verifier (Stage 5.5) before freeze:** re-fetch every cited URL, confirm the quote exists and supports the claimed relation, check verdict calibration and temporal validity. Make the "structural" R-2/R-3 claims real by assigning them to this pass, not to reconcile's self-report. On fail: bounded loop-back, then downgrade.
4. **Ship P0 immediately, unconditionally.** Temporal context block, stop dropping fetch-failed annotated URLs, fetch `expanded_urls`, renderer/outcome coherence fix, top_k/wall-clock/effort bumps. These are pure wins with no fidelity question and fix real coded defects (the NEI-leak incoherence, the date blindness). Don't let them wait on the architecture debate.
5. **Land video (P2) before the 108-post study regeneration, not after.** A third of the study corpus is checked blind against exactly the content the note addresses; STUDY validity depends on it. It is mis-tiered as P2 — for the study it is P0-critical. Re-sequence it ahead of the final regeneration.

**What to keep, no changes:** freeze schema + invariance boundary + three-tone renderer (already excellent in code), structural hindsight partition, failure-taxonomy-driven fixes, weighted sufficiency, devil's-advocate gate, and the decontamination discipline in §2.5 — that honesty is the doc's best feature and should govern the final validation too.

