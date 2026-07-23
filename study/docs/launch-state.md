# Study launch state — living checklist (updated 2026-07-23)

Single source of truth for the Prolific launch. Update this file as steps complete.

## DONE (verified)

- **Stimuli FINAL + committed**: 108 posts × 3 tones, v0.8 verdicts, final satirical register
  (spec `docs/superpowers/specs/2026-07-21-satirical-register-final-design.md`), tone-invariant
  generation params (effort=high/90s all tones). Blinded QA: 0 severity-2, register-ID
  neutral 108/108, satirical 98/108, agreeable 75/108. Two flagged posts fixed via re-runs
  (`2e6e383`). Decisions log: `study/docs/stimulus-decisions.md` (DMCA post KEPT).
- **Interface LIVE**: `https://postpanel-study.azurewebsites.net` (App Service, rg-derad-agent,
  shared B2 plan; image `derad-study-interface:latest` in ACR `azacrspzdzrbtv3v4o`; rebuild via
  `bash study/interface/build_image.sh` then `az webapp config container set … && az webapp restart`
  — plain restart may keep serving the old container until pull completes).
- **Pool**: 600 profiles live (300 D / 300 R; 150 templates, seed 20260722; 75 per party×condition;
  every post exactly 50×/condition). Tables: studyprofiles/studyassignments/studyexposures/
  studypartymap @ `https://azsaspzdzrbtv3v4o.table.core.windows.net`.
- **Server-side party**: `/api/session` resolves party from the studypartymap invite map
  (600 real PIDs + TESTPREVIEW01/D + TESTPREVIEW02/R); unknown pid → 403; token gate
  (`DERAD_SESSION_TOKEN` app setting; also in `~/.claude/jobs/2f82dc68/tmp/session_token.txt`);
  response includes flat `code1..code12` for Qualtrics mapping.
- **Compact embed mode**: `?compact=1` hides the right column (for the survey iframe).
- **Recruitment selection**: 600 invitees (stratified gender×age common targets, X-usage ranked,
  seed 20260722) from 958 unique screeners. Allowlists + selection log:
  `~/.claude/jobs/2f82dc68/tmp/recruitment/` (NOT in git). Aggregates in stimulus-decisions.md.
- **Prolific (token `~/.prolific_token`, `$PROLIFIC_API_TOKEN`; Cloudflare blocks python-urllib UA — use curl)**:
  workspace 68ec976d96b79700f5c9cd1e; sequential/longitudinal project "Satirical AI Final"
  6a602b52aa2666561f53a1b7. Group "Agonistic X Final Pool" 6a602bc0f03d6ca1add00ec8 = the 600.
  Progression groups: Pre 6a615099c713a1afa68065ec, Day1 6a61509ad40705ada2aabe8c,
  Day2 6a61509a57200d5f2416fd93, Day3 6a61509a0ff0b2426a5db259.
  Draft studies (UNPUBLISHED, placeholder URLs + placeholder rewards):
  pre 6a61512afcdcb1038a424262 (code PPPRE1), day1 6a615148c6598b4de98f15b5 (PPDAY1OK),
  day2 6a615149551e0d37e3fd113e (PPDAY2OK), day3 6a61514aee8ba42381e67058 (PPDAY3OK),
  post 6a61514c83400e84846c1e68 (PPPOSTOK).
- **Qualtrics (UW: uwashington.qualtrics.com, datacenter sjc1; API NOT enabled — UW-IT ticket sent)**:
  wired QSFs built (flow embedded data PROLIFIC_PID/day/cc, web service with flat code map,
  L&M rows `${e://Field/codeN}`, EOS redirect `…complete?cc=${e://Field/cc}`, attention check
  moved OUT of loop to once/day, page break = read page then questions page, wide-layout +
  logo-hiding header CSS). Ready-to-import (REAL token): `~/.claude/jobs/2f82dc68/tmp/qualtrics_ready/`;
  repo copies have `<SESSION_TOKEN>` placeholder. User is iterating iframe/CSS in the editor —
  CURRENT iframe snippet uses `&compact=1`, width:100%;max-width:960px;height:700px.

## PENDING (in order)

1. User finishes Qualtrics editor polish → publishes 3 surveys → sends the three SV_ ids.
2. Me: PATCH the 5 Prolific drafts' `external_study_url` to
   `https://uwashington.qualtrics.com/jfe/form/<SV_id>?PROLIFIC_PID={{%PROLIFIC_PID%}}[&day=N]&cc=<code>`.
3. User (Prolific UI): sequential-project wizard — wave order/schedule, project eligibility =
   Agonistic X Final Pool, device = desktop only; confirm rewards (placeholders: pre $1.60/8min,
   daily $5.00/25min, post $3.00/15min).
4. End-to-end preview QA (TESTPREVIEW01, day 1→2→3; check exposure rows, dwell).
5. PRE-LAUNCH CLEANUP: release TESTPREVIEW claims + delete their assignments/exposures;
   REMOVE TESTPREVIEW01/02 from studypartymap; restart app; verify 600 profiles 0 claims.
6. Publish wave 1 (user click — spends money).
7. Post-launch: monitor claims/exposures; differential-attrition checks (plan in
   allocation doc addendum).
8. Housekeeping: merge video-path-t9 → main (user call); rotate Prolific token + Qualtrics
   session token after study; UW-IT ticket for Qualtrics API access pending.

## Key commands

- Release a claim: clear `claimed_by` on studyprofiles row + delete studyassignments row (PK "assign", RK pid).
- QA judge: `python3 ~/.claude/jobs/2f82dc68/tmp/qa_tone_judge.py study/data/replies.csv <out.jsonl>`
- Artifacts: D3 review https://claude.ai/code/artifact/c2c5c329-1d48-422f-bcd8-4f0c904e820a ·
  satire 3-way https://claude.ai/code/artifact/10932dc2-09d5-4a1d-876c-865410237810 ·
  round-2 https://claude.ai/code/artifact/bff30c82-eeda-4155-8f6e-219e4fbef911
