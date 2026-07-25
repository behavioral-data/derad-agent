# Cleanup / simplification plan (pre-finalization)

*Synthesis of three independent reviews — dead-code/artifacts, prompt/rule sprawl,
architecture/duplication. Full sub-reports in `.claude/cleanup-{deadcode,prompts,architecture}.md`.
All three cross-validate; nothing here changes validated behavior except where explicitly flagged.*

## The shape of the bloat

The loop pipeline is the validated, debiased product; the **staged pipeline is dead
weight** kept alive by two threads — it's still the default engine, and `pipeline_loop`
imports four helpers *from* it. Sever those and ~1,600 lines become deletable. On top of
that: the render rewrite left orphaned transform machinery, several schema fields are
write-only/dual-tracked, and the prompt's ~20 named rules collapse to ~8.

Tiered by value × safety. Tier 1 is behavior-preserving; Tiers 2–3 are structural and
need your go-ahead.

---

## Tier 1 — Dead code, safe now (no runtime behavior change)

| ID | Action | Evidence |
|---|---|---|
| DC-1/DC-2 | Delete `_transform_register` (render.py:519-567) + `prompts/render_transform.md` | Zero call sites (both reviewers); the render rewrite replaced it; no test touches it |
| DC-3 | Delete `lint_cross_tone` + `_fact_in` (render_lint.py:58-74); trim import + 2 tests | Now test-only — R-5 is reimplemented inline (headline-numeral majority) in render.py |
| DC-4 | Rename `test_v07_render_transform.py` → `test_v07_render_all_tones.py` | File tests the new function; name references the dead approach |
| DC-fix | Fix `render_lint.py:4-5` R-5 docstring (says "every fact", code does headline majority); fix `pipeline.py:1` "design v0.5" string | Doc drift |

Risk: none (dead code / tests / docs). Verify: `pytest tests/test_v07_render_lint.py tests/test_v07_render_all_tones.py`.

## Tier 2 — Structural core: make the loop the product (low risk, reversible)

| ID | Action | Note |
|---|---|---|
| AR-1 | Flip default engine `staged` → `loop` in `utils.py:304` and `batch_generate_replies.py` | The loop is what was validated + debiased; staged is pre-debias. Flag stays for one-env-var rollback. **Behavior change — your call.** |
| AR-2 | Move the 4 shared helpers (`_run_multimodal`, `_attached_image_records`, `_resolve_modality`, `_thread_context`) out of `pipeline.py` into a new `multimodal_prep.py`; repoint both pipelines | Severs the loop→staged import leak — the enabling step for AR-3 |
| AR-9 | Repoint `__main__.py` CLI at `run_factcheck` (it hard-wires `run_pipeline` today) | Unblocks AR-3 |

Risk: low, reversible. Verify: full suite + one live smoke on the loop default.

## Tier 3 — Irreversible / coordinated / research decisions (explicit sign-off each)

| ID | Action | Why gated |
|---|---|---|
| AR-3 | Delete the staged pipeline: `pipeline.py`, `extract.py`, `verify.py`, `reconcile.py`, `audit.py` + their tests + the `reconcile_stance_drift` metric (~1,600 lines) | Irreversible; only after AR-1 ships clean |
| AR-4/AR-5 | `verdict_label` → `Optional`, stop producing it; drop `overall_state` from `RendererView` | Coordinated migration (schema + app fallback + fixtures); legacy readers kept |
| Prompt-consolidation | Collapse ~13 loop rules → ~8: merge §6a/§6b, de-dup §6c ≈ verifier #8, fold R-2 into §10 + R-3 into P-C, drop the R- prefix collision, fix false-sequence numbering | **Do NOT thin the 5× anti-"manufacture a correction" repetition** — it's the study bias control; may be load-bearing for adherence. Re-validate after, since it sits next to the study IV. |
| AR-6/AR-7 | Collapse the placeholder cross-modal schema (Lens2/Lens3 never get real content); trim write-only loop fields (`implied_claim`, `knowledge_state_at_post_date`) | Touches persisted schema → needs a `migrate_freezes` bump; do last |
| **AR-8** | **Loop drops pivot-disclosure** (`action_source` hard-coded "inferred"; `pivoted_from`/`invoker_instruction_text` never set) | **Research-validity decision, not cleanup** — wire it into the loop or formally remove the sub-system. Live-mode only (study posts carry no invoker text), so low urgency for the study. |

## Recommended ordering

Tier 1 → Tier 2 (AR-1, AR-2, AR-9) → confirm one clean run → Tier 3 (AR-3 first, then
AR-4/5, then prompt-consolidation with re-validation, then AR-6/7). AR-8 decided
separately with the study owner.

## Do-NOT-touch (verified load-bearing)

Staged pipeline until Tier 2 ships; `verdict_label`/`derive_verdict` (legacy readers);
`snapshot.py`, `replay.py`, `sources.py`, `verdict.py` (shared by both paths);
`verifier_report`/`as_of`/`evidence_cutoff`/`engine` (read by the eval harness); all live
v0.7 schema fields.
