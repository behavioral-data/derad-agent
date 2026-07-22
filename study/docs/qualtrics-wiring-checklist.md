# Qualtrics wiring checklist — Study 2 (mock-X interface)

Step-by-step wiring for the three imported surveys in `study/qualtrics_survey/`
(`Agonistic Humor - Pre-Survey / Daily Survey / Post-Survey`). Companion to
`docs/interface_azure_deployment.md` §7–8. Everything here is Qualtrics-UI work;
paste-ready snippets are marked with ▶.

Deployed host: **`postpanel-study.azurewebsites.net`** — replace `<APP_HOST>` with it everywhere
below. (Named participant-neutrally on purpose; participants can see this domain in the iframe.)

---

## 0. How the pieces fit

- **Prolific → Qualtrics:** two party-filtered Prolific studies (Democrat / Republican).
  Every survey URL carries `PROLIFIC_PID={{%PROLIFIC_PID%}}` plus a hardcoded
  `party=D` or `party=R`. The Daily Survey is ONE survey reused for all 3 days —
  the day arrives as a `day=1|2|3` URL parameter on each day's link.
- **Qualtrics → interface:** a Survey Flow **Web Service** element calls
  `/api/session` (server-side, no CORS needed), which claims the participant's
  profile (first call) and returns that day's **12 opaque codes**. The codes feed
  the Loop & Merge; each loop iteration shows one post in an iframe.
- Condition never appears client-side; researchers join it offline from the
  `studyassignments` table by `PROLIFIC_PID`.

## 1. Daily Survey (do this first — it's the load-bearing one)

The imported survey already has: a 12-iteration **static Loop & Merge** on the
Default Question Block (Field 1 currently holds the literals `1..12`), a
Descriptive "Title" question (`Post ${lm://Field/1}`), three sliders
(enjoyed / informative / satisfied), and an attention check. The old placeholder
Q2 sits in Trash — leave it there.

1. **Survey Flow → Add Embedded Data** (drag to the very TOP):
   - `PROLIFIC_PID` — Value: *set from URL parameter* (leave value blank; Qualtrics
     auto-fills from the URL param of the same name)
   - `party` — blank (from URL)
   - `day` — blank (from URL)
2. **Survey Flow → Add a Web Service element** (directly below the Embedded Data,
   ABOVE the question block):
   - Method: `GET`
   - ▶ URL:
     ```
     https://<APP_HOST>/api/session?pid=${e://Field/PROLIFIC_PID}&party=${e://Field/party}&day=${e://Field/day}&token=<DERAD_SESSION_TOKEN value>
     ```
     Append `&token=` with the exact `DERAD_SESSION_TOKEN` you set on the App Service (deployment
     runbook §5). This Web Service call runs server-side, so the token never reaches the participant's
     browser; if the app has a token set and the call omits it, `/api/session` returns `401`.
   - "Set Embedded Data" from the JSON response — add 12 mappings
     (Qualtrics shows the parsed response after you click *Test*; pick the
     array entries):
     `code1 = codes.0`, `code2 = codes.1`, … `code12 = codes.11`
3. **Loop & Merge** (block menu → Loop & Merge): replace Field 1's literal values
   `1..12` with the piped codes, one per row:
   - ▶ Row 1 Field 1: `${e://Field/code1}` … Row 12 Field 1: `${e://Field/code12}`
   - Keep "Randomize loop order" OFF — day order is pre-balanced in the profile.
4. **Title question** (the Descriptive-text one): replace its HTML with the
   iframe (HTML view):
   - ▶
     ```html
     <iframe src="https://<APP_HOST>/?v=${lm://Field/1}&pid=${e://Field/PROLIFIC_PID}&day=${e://Field/day}"
             style="width:100%;height:820px;border:0;overflow:hidden" title="Post"></iframe>
     ```
   - Set the question/column width wide (~1000px+) — the interface is a
     full-chrome X thread and is desktop-only.
5. **End of survey → Prolific redirect:** Survey Options → End of Survey →
   Redirect to URL — paste the day's Prolific completion URL. (Each day is its
   own Prolific "part" with its own completion code; same Qualtrics survey.)
6. **Survey Options:** Anonymize responses OFF (keep PROLIFIC_PID),
   "Prevent multiple submissions" ON per day is handled by Prolific parts —
   in Qualtrics use *Prevent Ballot Box Stuffing* OFF (the same person returns
   on days 2–3) — dedupe offline by (PROLIFIC_PID, day).

## 2. Pre-Survey (Day 1, before the first daily block)

1. Same **Embedded Data** element at the top: `PROLIFIC_PID`, `party` (from URL).
2. No Web Service call — the pre-survey does not touch the interface
   (the profile is claimed on the first Daily Survey call; a pre-survey dropout
   therefore never burns a profile).
3. End-of-survey redirect → Prolific completion for part 1.

## 3. Post-Survey (after Day-3 daily block)

1. Embedded Data: `PROLIFIC_PID`, `party` (from URL).
2. End-of-survey redirect → Prolific completion for the final part.
3. This is where the CN-based debrief lives (decision D3) — confirm the debrief
   block text before launch.

## 4. Prolific setup (per deployment runbook §8)

- Two studies (prescreen: US political affiliation = Democrat / = Republican),
  228 places each; device screen: desktop only.
- Multi-part structure per study: Part 1 = Pre + Day 1 links (or Pre separately),
  Parts for Day 2 / Day 3 / Post, each pointing at the SAME Qualtrics surveys with
  ▶ `?PROLIFIC_PID={{%PROLIFIC_PID%}}&party=D&day=2` (adjust party/day per study/part).
- Completion codes: one per part, set in each part's end-of-survey redirect.

## 5. QA pass before opening (no Prolific needed)

1. `https://<APP_HOST>/healthz` → `ok`.
2. Preview the Daily Survey with a test URL:
   `...&PROLIFIC_PID=TESTQA1&party=D&day=1` → 12 posts render in iframes; sliders work.
3. Re-run the same URL → same 12 codes (idempotent claim).
4. `day=2` for the same PID → 12 different posts, no overlap with day 1.
5. Missing `party` → the Web Service gets a 400; confirm the survey surfaces a
   graceful error (add a Branch on `code1` being empty → End of Survey with an
   "please contact the researchers" message).
6. Check `studyassignments` / `studyexposures` tables for the TEST pid rows,
   then delete them (and `release_profile` the TEST pid via an operator script,
   or regenerate the table) BEFORE launch — test claims consume real profiles.

## 6. Known gaps to watch

- The Daily Survey has no "day" sanity check: a participant opening a day-3 link
  on day 1 will get day-3 posts (the interface serves whatever `day` says).
  Prolific part scheduling is the guard — release parts on the right days.
- `release_profile` has no HTTP endpoint (deliberate); attrition slots are
  reclaimed with an operator script against the Tables store.
