# Satirical register — final design (2026-07-21)

Design record for the satirical tone register that generates the study's satirical-arm
stimuli (`agent/factcheck/render.py::_SATIRICAL_REGISTER` + the SATIRE DISCIPLINE hard
constraint). Supersedes the register shipped with v0.7/v0.8.

## Problem

The user found the shipped register's outputs hard to understand. Diagnosed causes
(user-confirmed): (1) verdict never stated plainly — sustained deadpan irony carried it
implicitly; (2) jokes presupposed the fact-check (punchlines that only land if the reader
already knows the correction); (3) long, nested sentences. Comprehension failure in a
satirical fact-check is a validity risk: misread satire reads as *endorsement* of the
misleading post (cf. LaMarre et al. 2009).

## Evidence trail (all live renders from real study freezes)

| Iteration | Change | Result (n=8 or 10 posts) |
|---|---|---|
| V2 | setup→punchline template (literal verdict first) | 23.6→19.4 w/sent; clear but formulaic; user wanted late-night voice kept |
| V3 | Oliver voice + NO-HOMEWORK/EASY-SENTENCES/EXIT-TEST rules | voice survived, accessible |
| V3.1 | V3 + two worked "joke moves" examples | 13.0 w/sent, sharper jokes, but same-shaped replies |
| F1 | user's rewrite, outcome rules, no examples | more structural variety (angle menu visibly used) |
| F2 | joke moves restored as a STRATEGIES MENU; fact-first/end-on-punchline template removed | 16.4 w/sent, FK 10.8 (vs 16.2 shipped); variety + accessibility both held |
| Final | F2 + 5 additions from a systematic 10-pair current-vs-F2 read | — |

Head-to-head on 10 unseen-topic posts (same frozen verdicts): F2 ~6, current ~2, ties 2.
Current register's residual strengths (concrete everyday analogies, crafted closers) and
shared weaknesses (mock-praise opener tic: ~half of 40 current replies open
Remarkable/Incredible/etc.; F2 grew "Breaking news" ×2 in one batch) drove the final
additions.

## Final register (implemented)

Base = user-authored front matter (accessibility as OUTCOME rules — no fixed
fact-then-joke template; ~30-word sentence cap; no minted numbers; exit test), the
original two-worlds reasoning process, and a five-shape STRATEGIES MENU explicitly
marked "not a formula". Additions from the systematic comparison:

1. Boundary: death/tragedy posts — acknowledge the loss plainly; joke targets only the
   claim's logic/framing/source.
2. STEP 4(d): prefer concrete everyday imagery over abstract irony.
3. STEP 4(e): the last sentence needs snap (soft closer guidance, not a mandate).
4. Accessibility: VARY THE VOICE — worn openers ("Remarkable…", "Incredible…",
   "Breaking news…", "Ah yes…") and "doing a lot of heavy lifting" banned.
5. Strategy: CONCEDE, THEN CORRECT — plain concession of the post's true parts first.

Boundary note (user decision): target line changed from "never the person whose image or
name appears" to "never the person who has posted the parent post" — named public
figures *in* posts may be satirized via their claims; the poster is protected; the
no-harassment clause still covers any named person.

## Related mechanical changes

- Satirical renders now run `reasoning_effort="high"` (was "medium"), timeout 90s —
  user request; the register's five-check rewrite loop benefits from the extra budget.
- R-5 cross-tone lint (2026-07-20, commit 8cb0afa): anchor = headline ∩ neutral
  numerals, injected into register prompts as a fact contract. Unchanged by this design.
- No study-derived examples in the prompt (overfitting rule): strategy examples use a
  fictional stadium claim and the moon-landing hoax.

## Validation plan

Production smoke test (4 posts, real `render_all_tones` path), then full 108-stimulus
generation; blinded LLM tone-invariance + register-identification QA on all 108×3
(method: docs/tone-invariance-check-2026-07-10.md) before fielding. Watch: register
identifiability (satirical must remain blind-identifiable), fallback-to-neutral rate
(was 0/15 after the R-5 fix), homework-joke recurrence.
