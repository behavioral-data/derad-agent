# Stimulus decisions log (Study 2)

Decisions made during the final stimulus QA (D3), for the Methods section.

- **2026-07-22 — DMCA'd video post KEPT.** Post `2024103395024392545` (Amber Glenn):
  the attached video was removed from X via copyright takedown before the July 7
  media snapshot, so the interface shows X's takedown placeholder still — exactly
  what a live X user sees today. Kept because the rendering is identical across all
  four conditions (no between-arm confound), the fact-check rests on the post text,
  and exclusion would break the 108-post allocation arithmetic (exact 38x exposure).
- **2026-07-22 — Two QA-flagged posts re-run, not excluded.** `2022107827532443811`
  (COVID/carcinogen) and `2016222191239758090` (Anadith) had verdicts that collapsed
  to the NEI template despite admissible pre-post evidence; fresh pipeline runs
  produced correct verdicts (verified_refuted; context with the Community Note's
  2023/CBP corrections). Both re-graded severity-0 by the blinded judge. No posts
  excluded; the 108-post set is fielded intact.
- **Register/parameter provenance:** all 324 replies rendered from frozen v0.8
  verdicts with the final satirical register (commit 20fd64f, spec
  docs/superpowers/specs/2026-07-21-satirical-register-final-design.md) under
  tone-invariant generation parameters (commit 3f9183a).
