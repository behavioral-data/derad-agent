# Deploying the study interface to Azure (Qualtrics + Prolific)

Runbook for hosting the **mock-X study interface** (`study/interface/`) on Azure so it can be
embedded in a Qualtrics survey and run on Prolific. Written to be executed later by another
agent/person. Nothing here has been provisioned yet.

> **Scope.** This deploys the *interface* (stimulus + participant assignment + exposure logging).
> It is **separate** from the fact-check *agent* deployment (`agent/app/app.py`), which already
> lives in the same resource group. Qualtrics owns all surveys; the interface owns the stimulus,
> the assignment engine, and exposure logging.

---

## 1. What gets deployed & how it's used

```
Prolific (two party-filtered studies: D / R) ──(PROLIFIC_PID + party)──▶ Qualtrics survey
  Day 1: pre-survey
  Each day D (1..3):
    Qualtrics "Web Service" ──▶ GET /api/session?pid=<PID>&party=<D|R>&day=<D>
                              ──▶ { "pid", "day", "codes": [12 opaque codes] }
    Loop & Merge over the 12 codes:
       ┌ <iframe src="https://<APP_HOST>/?v=<code>&pid=<PID>&day=<D>"> ┐   ← full-chrome X thread
       └ + that post's reaction questions (Qualtrics)                  ┘
       (interface beacons exposure → Azure Table: pid, post, condition, day, dwell)
  Day 3: post-survey ──▶ Prolific completion code
```

Key properties already implemented in code:
- **Condition never leaves the server.** `/api/session` returns only opaque 12-char codes; no
  `condition`/`post_id` in any client-visible URL or response. Researchers join condition later by
  `pid` from the `studyassignments` table.
- **Recruitment is party-filtered.** Run two Prolific studies (or one study with a screener /
  embedded-data field) so each participant's `party` (Democrat/Republican) is known before their
  first `/api/session` call — the endpoint requires `party` and does not infer it.
- **Claiming** (`/api/session`, backed by `study/interface/profiles.py` +
  `StudyStore.claim_profile`) is **idempotent per `pid`**: the first call for a new `pid` claims
  the next unused profile for that participant's party, walking a permuted-block order across the
  4 conditions (balance holds after every complete block of 4 claims), drawn from the pre-generated
  pool committed at `study/data/profiles/profiles.json` — **456 profiles** (114 matched templates ×
  4 conditions, **228 Democrat / 228 Republican**), each **36 posts** laid out as **3 days × 12**.
  Repeat calls for the same `pid` return that same claimed profile. The pool is generated offline
  (`python3 -m study.scripts.generate_profiles`) and committed — nothing is computed on the fly at
  claim time. When a party's pool is exhausted, `/api/session` returns **409**.
- **Attrition**: `StudyStore.release_profile(pid)` frees a claimed profile back to the pool. There
  is no HTTP endpoint for it yet — call it from a Python shell/operator script against the same
  store if a participant needs to be released.
- **The old on-the-fly `study/interface/assignment.py::assign()`** (54 posts, 6 days × 9, no
  party) is **legacy** — still in the repo and still covered by its own tests, but the server no
  longer calls it.
- **Writable state** (`study/interface/study_store.py`) → **Azure Table Storage** in prod
  (`studyassignments`, `studyexposures`, `studyprofiles`), auto-created on first write. The profile
  pool itself is loaded into `studyprofiles` once at app startup (idempotent no-op if the table
  already holds rows).
- **Embeddable**: `Content-Security-Policy: frame-ancestors` allows the Qualtrics iframe.

Endpoints (see `study/interface/server.py`):
| Route | Purpose |
|---|---|
| `GET /healthz` | health probe |
| `GET /api/session?pid=&party=&day=` | claims (idempotent) this pid's profile for `party` (`Democrat`\|`Republican`\|`D`\|`R`, required); `day` (1..3) validated **before** claiming; returns that day's 12 opaque codes; 409 if the party's pool is exhausted |
| `GET /?v=<code>&pid=&day=` | renders the thread (participant-facing); beacons exposure |
| `POST /api/exposure` | logs `{code,pid,day,dwell_ms}` (same-origin beacon from the thread page) |
| `GET /browse` | operator/demo gallery (all posts × conditions → `/?v=` links) — **not** for participants; returns **404 unless `DERAD_ENABLE_BROWSE` is truthy** |

---

## 2. Existing Azure infra to reuse

From `.azure/derad-agent/.env` (the agent deployment). **Reuse all of it** — do not create a new
resource group.

| Thing | Value |
|---|---|
| Subscription | `faac48db-165b-4928-a952-7d769267fe0b` |
| Resource group | `rg-derad-agent` (region `westus`) |
| Container registry (ACR) | `azacrspzdzrbtv3v4o` (`azacrspzdzrbtv3v4o.azurecr.io`) |
| User-assigned identity | `azidspzdzrbtv3v4o` — clientId `aba3d0e0-872f-4796-8582-45fdb188e50b` |
| Storage account | `azsaspzdzrbtv3v4o` |
| Table endpoint | `https://azsaspzdzrbtv3v4o.table.core.windows.net` |
| Existing agent app (reference) | `azappspzdzrbtv3v4o` (App Service plan SKU `B2`) |

**Why this matters:** the existing user-assigned identity already holds **AcrPull** on the ACR and
**Storage Table Data Contributor** on the storage account (see `infra/main.bicep` →
`acrPullAssignment`, `tableDataContributorAssignment`). If the interface App Service uses that
**same identity**, it needs **no new role assignments** — it can pull the image and read/write Tables
immediately.

---

## 3. Build artifacts to create

### 3a. `requirements-interface.txt` (lean — no agent/embedding deps)
```
Flask>=3.1.3
gunicorn>=21.2.0
azure-data-tables>=12.5
azure-identity>=1.18
```

### 3b. `study/interface/Dockerfile`
Bakes the read-only `study.db` (built from the committed CSVs + media) into the image, so the
container is self-contained (no runtime dependency on X or on rebuilding the DB).
```dockerfile
# syntax=docker/dockerfile:1.6
FROM python:3.11-slim
# DERAD_REQUIRE_TABLES=1: refuse to boot on the in-memory store when no Tables
# endpoint is set (guards the silent per-worker fallback).
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000 DERAD_REQUIRE_TABLES=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-interface.txt .
RUN pip install --no-cache-dir -r requirements-interface.txt

# study/ is a package (study/__init__.py, study/interface/__init__.py). Copy the
# interface code + data (posts/notes/media_index/replies CSVs + media/, ~160 MB).
COPY study/ ./study/

# Build the read-only study.db at image-build time (creates posts, interventions,
# and the access-code table). Requires the committed CSVs + media to be present.
RUN python -m study.interface.build_db \
      --selected study/data/posts.csv \
      --notes    study/data/notes.csv \
      --media    study/data/media_index.csv \
      --replies  study/data/replies.csv \
      --db       study/data/study.db

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost:${PORT}/healthz || exit 1

# Prod uses the Azure Tables store (shared across workers), so >1 worker is safe.
# gunicorn app-factory syntax calls create_app(). Shell form so ${PORT} expands
# (keeps the bind in lockstep with the HEALTHCHECK); exec so gunicorn is PID 1.
CMD exec gunicorn "study.interface.server:create_app()" \
     --workers 2 --threads 4 --timeout 60 \
     --bind "0.0.0.0:${PORT}" --access-logfile - --error-logfile -
```

> **`.dockerignore`:** make sure it does **not** exclude `study/data/media/` (the mp4s/images must
> be in the build context). The repo root `.dockerignore` was written for the agent image — review it
> and add an interface-specific one if needed so `study/data/**` is included.

> ⚠️ **Multiple workers require the Tables store.** The in-memory store is per-process; with
> `--workers 2` it would give inconsistent assignments. Always set the Table endpoint env (§5) in
> prod. (Alternatively run `--workers 1` if Tables is somehow unavailable — not recommended.)

---

## 4. Deploy

### Option A — bicep + azd (recommended; reuses the proven identity/ACR wiring)
1. In `infra/main.bicep`, add a **second `Microsoft.Web/sites`** resource for the interface,
   copying the existing `appService` block (lines ~198–281) with these changes:
   - a distinct `name` (e.g. `${abbrs...}-study` or a fixed unique string),
   - `serverFarmId`: reuse `appServicePlan.id` **or** add a smaller `Microsoft.Web/serverfarms`
     (SKU `B1`) for isolation,
   - `identity`: the **same** `uami` (so AcrPull + Table Data Contributor already apply),
   - `linuxFxVersion`/container image: `azacrspzdzrbtv3v4o.azurecr.io/derad-study-interface:latest`,
   - `appSettings`: the env in §5 (drop the agent-only secrets),
   - `acrUseManagedIdentityCreds: true` + `acrUserManagedIdentityID: uami.properties.clientId`.
   Optionally declare the three tables (`studyassignments`, `studyexposures`, `studyprofiles`) like the existing
   `tableServices/tables` resources (otherwise they're auto-created at first write).
2. `azd provision` (creates the new app; existing resources are idempotent).
3. Build & push the image, then restart (see §4c). *`azd deploy` does not rebuild the image — use
   `az acr build`.*

### Option B — pure `az` CLI (no bicep edits)
```bash
# 0. Auth + subscription
az login                      # or: azd auth login
az account set --subscription faac48db-165b-4928-a952-7d769267fe0b

RG=rg-derad-agent
ACR=azacrspzdzrbtv3v4o
UAMI_ID=$(az identity show -g $RG -n azidspzdzrbtv3v4o --query id -o tsv)
UAMI_CLIENT=$(az identity show -g $RG -n azidspzdzrbtv3v4o --query clientId -o tsv)
APP=postpanel-study           # deployed 2026-07-22; participant-neutral name (participants see the domain).
                              # NOTE: new-plan quota was 0 in westus — the app runs on the agent's existing B2 plan.

# 1. Build + push the image server-side in ACR (no local Docker needed)
az acr build --registry $ACR --image derad-study-interface:latest \
  --file study/interface/Dockerfile .

# 2. App Service plan — reuse the agent's, or make a small dedicated one:
az appservice plan create -g $RG -n plan-derad-study --is-linux --sku B1
#   (reuse instead:) PLAN=$(az webapp show -g $RG -n azappspzdzrbtv3v4o --query appServicePlanId -o tsv)

# 3. Create the container web app
az webapp create -g $RG -p plan-derad-study -n $APP \
  --deployment-container-image-name $ACR.azurecr.io/derad-study-interface:latest

# 4. Attach the existing user-assigned identity + use it for ACR pull
az webapp identity assign -g $RG -n $APP --identities "$UAMI_ID"
az webapp config set -g $RG -n $APP \
  --generic-configurations '{"acrUseManagedIdentityCreds": true}'
az resource update --ids "$(az webapp show -g $RG -n $APP --query id -o tsv)/config/web" \
  --set properties.acrUserManagedIdentityID="$UAMI_CLIENT"

# 5. App settings (env) — see §5
az webapp config appsettings set -g $RG -n $APP --settings \
  WEBSITES_PORT=8000 \
  AZURE_STORAGE_TABLES_ENDPOINT=https://azsaspzdzrbtv3v4o.table.core.windows.net \
  AZURE_CLIENT_ID=$UAMI_CLIENT \
  DERAD_FRAME_ANCESTORS="'self' https://*.qualtrics.com"

# 6. Restart to pull the image
az webapp restart -g $RG -n $APP
echo "App: https://$APP.azurewebsites.net"
```

### 4c. Redeploying after code/data changes
```bash
az acr build --registry azacrspzdzrbtv3v4o --image derad-study-interface:latest \
  --file study/interface/Dockerfile .
az webapp restart -g rg-derad-agent -n $APP     # App Service re-pulls :latest on restart
```
> Rebuild the image whenever `replies.csv`, `notes.csv`, `posts.csv`, media, or interface code
> changes — `study.db` is baked at build time.

---

## 5. Configuration (App Service settings)

| Setting | Value | Why |
|---|---|---|
| `WEBSITES_PORT` | `8000` | container listens on 8000 |
| `AZURE_STORAGE_TABLES_ENDPOINT` | `https://azsaspzdzrbtv3v4o.table.core.windows.net` | **activates the Tables store** (required for >1 worker) |
| `AZURE_CLIENT_ID` | `aba3d0e0-872f-4796-8582-45fdb188e50b` | tells `DefaultAzureCredential` to use the user-assigned identity |
| `DERAD_FRAME_ANCESTORS` | `'self' https://*.qualtrics.com` | who may iframe the interface — add your exact Qualtrics host if it's outside `*.qualtrics.com` |
| `MOCKX_DB` | *(unset)* | defaults to the baked `study/data/study.db`; only set to override |
| `DERAD_PROFILES` | *(unset)* | defaults to the baked `study/data/profiles/profiles.json`; only set to override the pool file path |
| `DERAD_REQUIRE_TABLES` | `1` | **baked into the image** — refuse to boot on the in-memory store when no Tables endpoint is set (guards the silent per-worker fallback). Leave it; only unset for a deliberate single-worker in-memory run |
| `DERAD_SESSION_TOKEN` | *(set a long random string)* | when set, `/api/session` requires a matching `?token=` (constant-time compared); the Qualtrics Web Service adds it server-side so it never reaches participants. Unset → no auth (dev only) |
| `DERAD_ENABLE_BROWSE` | *(unset)* | operator gallery gate: `/browse` returns **404** unless this is truthy (`1`/`true`/`yes`). Leave unset in production so participants can't enumerate all conditions |

Data-plane env alternative: `DERAD_STUDY_TABLES_ENDPOINT` also works and takes precedence over
`AZURE_STORAGE_TABLES_ENDPOINT` (see `study_store._build_default_store`).

---

## 6. Verify after deploy
```bash
H=https://$APP.azurewebsites.net
curl -s $H/healthz                                          # -> ok
curl -s "$H/api/session?pid=TESTPID&party=D&day=1" | jq .   # -> {pid,day,codes:[12 …]}, NO "condition"
CODE=$(curl -s "$H/api/session?pid=TESTPID&party=D&day=1" | jq -r .codes[0])  # same claim (idempotent per pid)
curl -s -o /dev/null -w '%{http_code}\n' "$H/?v=$CODE&pid=TESTPID&day=1"   # -> 200
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"code\":\"$CODE\",\"pid\":\"TESTPID\",\"day\":1,\"dwell_ms\":1234}" $H/api/exposure  # -> {"ok":true}
curl -s -D- -o /dev/null "$H/?v=$CODE" | grep -i content-security-policy   # frame-ancestors … qualtrics.com
curl -s "$H/api/session?pid=TESTPID&party=D&day=4" | jq .   # -> 400 day out of range 1..3
```
> If you set `DERAD_SESSION_TOKEN` (§5), append `&token=<value>` to every `/api/session` call above —
> without it they return `401`. `/browse` returns `404` unless `DERAD_ENABLE_BROWSE` is set truthy.

Then confirm rows appear in the `studyassignments` + `studyexposures` tables, and a `claimed_by`
row in `studyprofiles` (Portal → Storage account → Storage browser → Tables). To sanity-check the
stimuli via `$H/browse`, temporarily set `DERAD_ENABLE_BROWSE=1` (and unset it before opening to
participants). Clean up the `TESTPID` test rows (including releasing its `studyprofiles` claim —
see `release_profile`) before going live.

**Staging check (pending).** Before enrollment opens, run the concurrency check from a session
with `DERAD_STUDY_TABLES_ENDPOINT` pointed at the Tables emulator/staging (see the implementation
plan's Task 10 Step 4 — deferred to the deployment session, not yet executed as of this doc
update). It must fire **both**: (a) ~50 concurrent `/api/session?...&party=D` calls for **distinct**
pids, asserting no two pids share a `profile_id` and that condition counts stay within one block of
balanced; **and** (b) duplicate near-simultaneous first claims for the **same** pid, to validate
convergence in `TablesStudyStore.claim_profile`. `claim_profile` still reads `get_assignment(pid)`
before claiming, so two racing first-time calls for one pid can both see "no existing assignment,"
and each go on to claim a *different* free profile via its own ETag-guarded write (which only guards
against two claimers taking the *same* profile, not two claims by the same pid). What no longer
happens is a silent overwrite: the assignment itself is written with an insert-only `create_entity`,
so only one of the two racing writes can land. The loser's `create_entity` raises
`ResourceExistsError`; it then releases the profile it had just claimed back to the pool (clearing
`claimed_by`) and returns the winner's assignment via `get_assignment(pid)`. Check (b) should assert
both concurrent responses come back with **identical** codes, and that no profile is left
`claimed_by` that pid without a matching `studyassignments` row — confirming the race converges
cleanly rather than measuring a leak rate.

---

## 7. Qualtrics wiring

1. **Capture the Prolific ID and party.** Prolific appends `?PROLIFIC_PID=...` to the study URL;
   capture it in Qualtrics **Survey Flow → Embedded Data** as `PROLIFIC_PID`. `party` needs to be
   known before the first `/api/session` call, so either:
   - run **two separate Prolific studies** — one for Democrat-registered participants, one for
     Republican — each with its own Qualtrics survey flow that sets embedded data `PARTY=D` /
     `PARTY=R` as a literal constant, or
   - use **one study** with a Prolific pre-screener for party affiliation and pass it through as a
     URL param / embedded data field (`?party=D|R`) that Qualtrics captures the same way as
     `PROLIFIC_PID`.
2. **Get the day's codes.** Add a **Web Service** element (Survey Flow), GET
   `https://<APP_HOST>/api/session?pid=${e://Field/PROLIFIC_PID}&party=${e://Field/PARTY}&day=<D>&token=<DERAD_SESSION_TOKEN value>`,
   and pipe the JSON `codes` array into embedded data. Append `&token=` with the exact
   `DERAD_SESSION_TOKEN` value you set in §5 — this Web Service call is server-side, so the token
   never reaches the participant's browser; without it the endpoint returns `401`. (`<D>` = the day for this Qualtrics survey;
   in a 3-day longitudinal Prolific study each day is typically its own Qualtrics survey, so `day`
   is a constant per survey.) A non-200 response (e.g. `409` if that party's pool is exhausted)
   should branch to an "unable to continue" end-of-survey message rather than looping over an
   empty `codes` array. Server-side Web Service calls need **no CORS**.
3. **Loop & Merge** over the 12 codes. In each loop iteration embed the thread and ask the reaction
   questions. Embed HTML (a "Text/Graphic" question):
   ```html
   <iframe src="https://<APP_HOST>/?v=${lm://Field/1}&pid=${e://Field/PROLIFIC_PID}&day=<D>"
           style="width:100%;height:820px;border:0;overflow:hidden" title="Post"></iframe>
   ```
   (Full X chrome wants a wide frame — set the Qualtrics question/column width wide, ~1000px+.)
4. **Pre-survey** (day 1) and **post-survey** (day 3) are ordinary Qualtrics blocks.
5. The interface logs exposure automatically (the iframe beacons on load + on pagehide).

> If you prefer a client-side fetch of `/api/session` instead of the Web Service element, the
> interface will need CORS headers added for your Qualtrics origin — currently it does not send them
> (Web Service is the recommended, CORS-free path).

---

## 8. Prolific wiring
- Set up a **longitudinal / multi-part study** (3 days). Each day points at that day's Qualtrics
  survey URL with `?PROLIFIC_PID={{%PROLIFIC_PID%}}` passthrough.
- Recruit via **two party-filtered studies** (Democrat / Republican), or one study with a party
  pre-screener — see §7 step 1. `/api/session` requires `party` and will 400 without it.
- Set each day's **completion URL/code** in Qualtrics' end-of-survey → Prolific redirect.
- Screen for **desktop** (the interface is desktop-only; full X chrome needs width).

---

## 9. Data & analysis
- **`studyassignments`** table: `RowKey = PROLIFIC_PID`, `condition`, `blocks` (JSON: 3×12
  post_ids), `created_at`. This is where condition lives — join to Qualtrics responses by
  `PROLIFIC_PID`. Party is known from recruitment (§7/§8), not stored on this row.
- **`studyprofiles`** table: the committed profile pool loaded at startup — `PartitionKey = party`
  (`D`/`R`), `RowKey` = claim-order index, `profile_id`, `condition`, `blocks`, `claimed_by`
  (empty until claimed, then the `PROLIFIC_PID` that holds it). Useful for auditing pool exhaustion
  and confirming party × condition balance.
- **`studyexposures`** table: `PartitionKey = PROLIFIC_PID`, `RowKey = <code>_<day>`, `post_id`,
  `condition`, `day`, `dwell_ms`, `viewed_at` (upserted — one row per participant-post).
- Export via Azure Storage Explorer / `az storage entity query` / the Tables SDK.

---

## 10. Notes / caveats
- **PII / IRB.** Prolific IDs + assignments + exposures are stored in Azure Tables (same storage
  account the agent already uses for research events). Confirm the IRB protocol covers this.
- **Claim concurrency.** `claim_profile()` guards each profile's claim with an ETag
  (`update_entity(..., match_condition=...)`), so two participants racing for the *same* profile
  can't both win it. Duplicate near-simultaneous first claims by the *same* `pid` can still each
  grab a *different* free profile (the ETag guard doesn't stop that), but the assignment write
  itself is insert-only (`create_entity`), so only one claim persists — the loser releases the
  profile it grabbed and hands back the winner's assignment, so both concurrent responses return
  identical codes. See the "Staging check (pending)" note under §6.
- **`:latest` tag.** App Service re-pulls on restart. For reproducibility, consider tagging images
  by date/commit and pinning the app to that tag.
- **Cost.** A `B1` Linux plan is ~\$13/mo; reusing the existing `B2` adds nothing. Set a budget alert
  (the bicep already defines one for the RG).
- **Teardown.** `az webapp delete -g rg-derad-agent -n $APP` (and the plan if dedicated). Leave the
  shared ACR/identity/storage — the agent uses them.

---

## 11. Pre-deploy checklist
- [ ] `study/data/replies.csv` is final (satirical/length changes done) — `study.db` is baked from it.
- [ ] `study/data/media/` (incl. mp4s) is in the Docker build context (check `.dockerignore`).
- [ ] `requirements-interface.txt` + `study/interface/Dockerfile` created (from §3).
- [ ] Global-unique web app name chosen.
- [ ] Confirm study parameters still match code: 4 conditions, 456 profiles (114 templates ×
      4 conditions, 228 Democrat / 228 Republican), 36 posts/profile as 3 days × 12, pool committed
      at `study/data/profiles/profiles.json`.
- [ ] Two party-filtered Prolific studies (or a party pre-screener field) are set up per §7/§8.
- [ ] Confirm the Qualtrics host is covered by `DERAD_FRAME_ANCESTORS`.
- [ ] Decide: reuse `B2` plan vs new `B1`.
- [ ] After deploy: run §6 verification (including the pending staging concurrency check once
      staging/Tables-emulator access is available); delete test rows; then open to Prolific.
```
