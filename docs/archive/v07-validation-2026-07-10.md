# v0.7 implemented-pipeline validation — real data, full pipeline

**Setup:** the 15 held-out posts (seed 42; never used for design tuning) run through the
ACTUAL implemented v0.7 pipeline (`batch_generate_replies --engine loop --study-mode
--no-db`): live Anthropic loop + web_search + page fetching + archive.org snapshots +
independent verifier + freeze + all three tone renders. Wall clock ≈ 87 min (~5.8
min/post). Replies graded against the gold community notes by a blinded judge
(randomized A/B per post, key withheld), head-to-head vs the original production
replies from the July 8 run.

## Head-to-head (n=15, blinded)

| metric | original pipeline | **v0.7 implemented** |
|---|---|---|
| agree with note's thrust | 8 | **12** |
| partial | 6 | 2 |
| disagree | 1 | 1 (same post — see below) |
| mean note-parity (0–3) | 1.33 | **2.07** |
| temporal_ok | 14/15 | **15/15** |
| beat the note | 4 | **9** |
| judged better overall | 2 | **12** (1 tie) |

**The simulation transferred.** The design-phase held-out numbers (agree 12/15, parity
1.80–2.07 depending on judge) are reproduced by the implemented code — the loop
architecture preserved what was validated, resolving the engineering review's central
concern.

Standout wins mirror the simulation: the Karmelo Anthony video misattribution (original
whiffed at parity 0; v0.7 nailed the NiqueAtNite provenance at parity 3), the Caitlin
Clark AI-spam template (v0.7 delivered the provenance; the original asserted an
unverified alternative), the Australia charges causal-attribution post (parity 3 vs 1).

**v0.7's two losses:** the Pretti post (its one verifier-flagged temporal-leak case —
leak-adjacent phrasing cost it) and the Tesla-tax post (led with confirmation framing;
parity 1 vs 2). **The one shared disagree:** the nurses-strike post — the
nonprofit/no-shareholders point every system (old pipeline, simulation, v0.7, both
2026-07-09 judges) has missed. It is the standing hard case for the eval harness.

## Mechanical stats (from the 15 freezes)

| property | value |
|---|---|
| substantive outcomes | 11/15 (6 refuted, 3 context, 2 supported) — 4 verified_nei |
| evidence rows with fetched bodies | 63/77 (82%) |
| evidence rows dated pre-cutoff | 63/77 (82%) |
| rows via archive.org snapshot | 10/77 (13% — archive.org rate-limited; CDX breaker cycled 5×) |
| verifier clean passes | 1/15 |
| advisory downgrades recorded | 14/15 (43 derivation gaps total — strict grounding audit) |
| verifier-flagged temporal leaks | 2 (one marginal: source published 1 day past cutoff; one real: context_note alluding to post-cutoff legislation) |
| empty replies | 0 |

## Post-approval commits in this validation cycle

Landed after the final review's READY TO MERGE (each tested, within the review's own
adjudications): `13885eb` advisory downgrade + mechanical-revision retry (cures the
outcome-collapse the second smoke exposed), `3d5966e` CDX circuit breaker (archive.org
rate limits were burning the loop's wall clock).

## Before the 108-post regeneration (follow-up ticket, updated)

1. **Temporal-leak payload softening** — on verifier-confirmed temporal leaks, strip or
   generalize the offending payload element before freeze (the one real leak in this
   batch reached a reply-facing context_note).
2. Video path (T9) — unchanged blocker for the 41 video posts.
3. Verifier calibration pass on the derivation-gap strictness (14/15 downgraded;
   gaps average 2.9/post — decide the acceptable grounding bar and tune verifier.md).
4. archive.org: authenticated/higher-limit access or nightly snapshot pre-fetch for
   the 108 posts' likely sources, to lift the 13% snapshot rate.
5. The nurses-strike-class blind spot (entity-property claims) — flag for human review
   rather than more rule-patching.
