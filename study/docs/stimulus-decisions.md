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

## 2026-07-22 — Recruitment selection (600 invitees from screening)

From 964 screening responses (958 unique participants after deduplication — 6 double
responders kept at first response, all party-consistent; 2 IDs cleaned of a stray
"@email" suffix, which merged them with their existing responses): willing pools of
361 Democrats and 331 Republicans. Selected **300 per party** by stratified sampling
(seed 20260722): common gender x age-bracket stratum targets for both parties
(proportional to the pooled distribution, capped by per-party availability; per-party
rank-based fill for the residual 25), ranking within stratum by X-usage frequency
(daily > weekly > monthly > rarely > never), tie-broken by party-identification
strength, then seeded random. Result: gender near-parity across parties (D 156M/141F/3NB,
R 163M/137F), age aligned within availability limits (largest gaps: 18-24 D34/R21,
55-64 D16/R38), X-usage-heavy samples (ever-users: D 89%, R 94%). Zero cross-list
overlap. Allowlists (Prolific IDs) are NOT committed to the repository; they live with
the study's restricted materials. Selection log: selection_log.csv alongside the lists.
