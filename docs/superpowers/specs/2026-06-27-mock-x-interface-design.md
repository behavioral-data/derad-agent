# Mock X Study Interface — Part 2 (interface infra)

**Date:** 2026-06-27
**Status:** Approved design (pending spec review)
**Scope:** Part 2 of 2. Part 1 (study infra: participant flow, condition assignment, event
logging, Prolific handshake) is explicitly **out of scope** here and deferred.

## Context

We are evaluating the fact-check intervention in a controlled experiment with Prolific
participants inside a mock X (Twitter) interface, rather than in the live deployment (sample
size + the need to test cleanly before scaling). The mock interface is the measurement
instrument; its results go in the grant's final report.

This spec covers making the **mock interface render correctly given a post and a condition**,
backed by a database table. It does **not** cover participant logging, randomization, or the
survey — those are Part 1.

## Goal / acceptance test

Visiting `/?post_id=<tweetId>&condition=<c>` with `c ∈ {neutral, agreeable, satirical, control}`
renders a mock-X thread showing the original post + **exactly one** intervention, with all
content read from a SQLite table:

- `neutral` / `agreeable` / `satirical` → one bot reply from **@eddiexbot** (tone differs only
  in the reply body).
- `control` → the post's real community note, rendered as X's native "Readers added context"
  card attached under the post (not as a reply).

## Conditions

Four conditions. Each renders the post + one intervention; nothing else in the thread (no
organic replies). The three bot conditions are structurally identical (a reply from
@eddiexbot); the control condition is structurally distinct (an attached context card), as
community notes actually appear on X.

## Data source summary

- **Posts:** `tsv_generation/selected_posts.csv` — 180 rows = **170 unique `tweetId`s** (10 posts
  are double-listed across two `topic_condition` buckets; multi-line `text` fields inflate the
  raw line count). Every selected post has `communityFlagged = TRUE`. Study factors:
  `polarity_condition` (negative/positive/center, 60 each) × `topic_condition` (6 topics × 30).
  The CSV has tweet text + id + created_at, but **no author identity and no note text**.
- **Community notes (control):** joined from `tsv_generation/cn_data/notes-00000.tsv`
  (`summary` = note text, `classification`) by `tweetId`. The shown note is selected via
  `cn_data/noteStatusHistory-00000.tsv` `currentStatus == CURRENTLY_RATED_HELPFUL`. Coverage
  verified: **all 170/170 posts have a CRH note.** Some have several CRH notes → tie-break on
  most recent `timestampMillisOfCurrentStatus`.
- **Bot replies (3 tones):** **do not exist yet.** Generated later by the `agent/factcheck`
  pipeline (tones confirmed: `neutral`/`agreeable`/`satirical`). For now they are **stubs**.

## Architecture

Approach **A — JSON API + client renderer** (reuse the existing `app.js` renderer): Flask
serves the page + a JSON endpoint; the client fetches and renders. Chosen over server-side
Jinja to reuse the existing polished renderer with minimal rewrite.

```
selected_posts.csv ─┐
notes-00000.tsv ────┤── build_db.py ──> study.db (read-only) ──> server.py ──> /api/thread (JSON)
noteStatusHistory ──┘                                                              │
                                                              index.html + app.js + api.js (renderer)
```

### 1. Database — `mock-x/study.db` (SQLite, read-only at runtime)

**`posts`** — one row per unique `tweetId` (170 rows)

| column | source / notes |
|---|---|
| `post_id` TEXT PK | `tweetId` |
| `content` TEXT | `text` |
| `created_at` TEXT | `created_at` |
| `author_name` TEXT | **synthetic**, deterministic from `post_id` (anonymized — never real handles) |
| `author_handle` TEXT | synthetic, deterministic |
| `author_verified` INT | synthetic, mostly 0 |
| `likes` / `reposts` / `views` INT | **fabricated**, deterministic (seeded by `post_id`), realistic ranges, fixed across conditions |
| `polarity_condition` TEXT | CSV metadata |
| `topic_condition` TEXT | CSV metadata (comma-joined for the 10 dual-topic posts) |

**`interventions`** — one row per (post_id, condition); 4 per post (680 rows)

| column | notes |
|---|---|
| `post_id` TEXT (FK) | |
| `condition` TEXT | `neutral` \| `agreeable` \| `satirical` \| `control` |
| `kind` TEXT | `bot_reply` \| `community_note` |
| `body` TEXT | reply text (stub for now) *or* note `summary` |
| `bot_name` TEXT | bot rows: `"Eddie"` (configurable label) |
| `bot_handle` TEXT | bot rows: `"eddiexbot"` |
| `bot_avatar` TEXT | bot rows: initials/avatar token |
| `note_classification` TEXT | control rows only (e.g. `MISINFORMED_OR_POTENTIALLY_MISLEADING`) |
| `source_note_id` TEXT | control rows only (provenance) |
| `reply_likes`/`reply_reposts`/`reply_views` INT | bot rows: fabricated, deterministic |
| `is_stub` INT | **1** for the 3 bot replies now; flips to 0 when Part-1 generation overwrites the body |

### 2. Ingestion — `mock-x/build_db.py`

Idempotent build script (drop + recreate). Steps:

1. Read `selected_posts.csv`, dedupe to 170 unique `tweetId`s (preserve all topics for the 10
   dual-topic posts as a comma-joined `topic_condition`). Insert into `posts`.
2. Synthesize author identity deterministically from `post_id` (name pool → name + handle +
   avatar initials). Never surface real handles.
3. Fabricate engagement counts deterministically (seeded by `post_id`) in realistic ranges,
   fixed across conditions.
4. Community note (control): for each `tweetId`, select the CRH note (tie-break: most recent
   `timestampMillisOfCurrentStatus`); insert an `interventions` row with `kind=community_note`,
   `body=summary`, `note_classification`, `source_note_id`.
5. Bot replies: insert 3 `interventions` rows (`neutral`/`agreeable`/`satirical`,
   `kind=bot_reply`, `bot_handle=eddiexbot`, `is_stub=1`) with clearly-marked placeholder body
   text (e.g. `"[STUB — neutral reply pending generation]"`).

### 3. Server — `mock-x/server.py` (Flask + gunicorn)

- `GET /?post_id=&condition=` → serve `index.html`. Validate params; clear error page on
  unknown `post_id`/`condition`.
- `GET /api/thread?post_id=&condition=` → JSON `{ post, intervention }` read from `study.db`.
- Serve static assets (`style.css`, `app.js`, `api.js`).
- DB opened read-only.

### 4. Client refactor (`index.html`, `app.js`, `api.js`)

- **`api.js`:** remove the hardcoded `db`; `MockXAPI` fetches `/api/thread?post_id=&condition=`
  and returns the same shape the renderer expects.
- **`app.js`:** render a single-intervention thread.
  - `bot_reply` → reply card from @eddiexbot, **no tone badge** (tone is never surfaced in UI).
  - `community_note` → **new native "Readers added context" card** under the post (new component
    + CSS), not a reply.
- **`index.html`:** **remove** the "Study Info" panel and the "Who to follow" bot list (they
  leak the manipulation). Keep inert chrome (sidebar, search) for realism. Remove tone
  badges/colors and emoji-in-name styling.

## Deployment (Azure)

- Containerize `mock-x` (Flask + gunicorn) via a `Dockerfile`; `study.db` is **built into the
  image** (read-only) so no runtime DB provisioning is needed.
- Build/push to the existing **ACR**, run on its **own App Service (Linux)** separate from the
  production agent app, reusing the same resource group / ACR. Deploy per the existing runbook:
  `az acr build` then `az webapp restart` (note: `azd deploy` does **not** rebuild the image).
- **Part-1 caveat (documented, not built here):** participant *writes* must NOT go to the
  bundled SQLite — App Service container FS is ephemeral and SQLite on the `/home` Azure Files
  mount is flaky under concurrent writes. Part-1 logging will target a real store (Azure Table
  Storage / Postgres / blob append).

## Decisions locked

- Bot replies **stubbed** now; real generation deferred to Part 1.
- **Dedicated Flask + SQLite** app under `mock-x/`.
- Thread = **post + single intervention only** (no organic replies).
- Control note = **native "Readers added context" card**.
- **Synthetic anonymized authors** (IRB/ethics: never real handles).
- Bot persona = single account **@eddiexbot** across all three tones.
- Rendering = **approach A** (JSON API + client renderer).
- Host on **Azure App Service** via container + ACR.

## Out of scope (Part 1)

Participant flow, condition assignment/randomization, event logging, the DV/survey, Prolific
handshake (participant id in, completion code out), real bot-reply generation, engagement
counterbalancing, AI-disclosure design.
