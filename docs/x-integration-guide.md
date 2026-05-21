# X Platform Integration Guide

**Service:** derad-agent — deployed on Azure App Service  
**Endpoint:** `https://azapplikxqqfjcgk72.azurewebsites.net`  
**Infra repo:** `derad-agent` (this repo) — Bicep in `infra/`

---

## Overview

The agent ingests X (Twitter) mentions via the **Filtered Stream API** (`GET /2/tweets/search/stream`). On startup, the app connects outbound to X and keeps the connection open permanently. X pushes matching tweets in real time; no public webhook URL or CRC handshake is required.

```
X Filtered Stream API (persistent SSE)
  └─ GET /2/tweets/search/stream
       ├─ rules synced at startup (one rule per bot, tagged by tone)
       ├─ stream events contain matching_rules[].tag → tone routing
       ├─ fetches parent tweet (X API v2)
       ├─ runs LLM + embedding pipeline (~40 s)
       └─ posts reply (X API v2, OAuth 1.0a per-bot)
```

Results (reply text, outcome, source notes) are written asynchronously to the `MentionEvents` table in Azure Table Storage.

---

## Endpoints

| Route      | Method | Purpose                                                              |
| ---------- | ------ | -------------------------------------------------------------------- |
| `/healthz` | `GET`  | Readiness probe — returns `{"ok": true, "index_loaded": true/false}` |
| `/info`    | `GET`  | Human-readable reply page linked from bot tweets                     |

There is no `/mentions` webhook endpoint. The stream runs as a background thread inside the app process.

---

## How Tone Routing Works

At startup, `streamer.py` syncs stream rules so each bot handle has exactly one tagged rule:

```json
[
  {"value": "@aggiexbot", "tag": "agreeable"},
  {"value": "@nelliexbot", "tag": "neutral"},
  {"value": "@eddiexbot",  "tag": "satirical"}
]
```

When a stream event arrives, `matching_rules[0].tag` identifies the target bot tone. The tweet is reshaped into the expected dict format and passed to `_dispatch_tweet(tone, tweet, received_at_utc)`.

---

## X Developer App Setup

### 1. Create / configure the X app

- Tier: **Basic** or higher (Filtered Stream requires PPU or Basic access)
- Permissions: **Read + Write** (needs to post replies)
- App type: **Web App / Bot** (generates API key+secret and per-bot access tokens)

### 2. No webhook registration needed

Filtered Stream is a pull-based API. The app connects to X, not the other way around. There is no URL to register and no CRC challenge to answer.

### 3. Bearer token

The Filtered Stream uses **Bearer token auth** (app-only). The bearer token is stored in Key Vault as `x-bearer-token`. Posting replies uses per-bot **OAuth 1.0a access tokens**.

---

## Key Vault Secrets to Populate

All credentials live in Azure Key Vault **`azkvlikxqqfjcgk72`**. Current values are `placeholder` — replace with real values before going live.

Run `bash scripts/seed_keyvault.sh` after updating the values in that script, or set individual secrets:

```bash
KV="azkvlikxqqfjcgk72"

# X app credentials (from developer.twitter.com → Keys and Tokens)
az keyvault secret set --vault-name $KV --name x-api-key           --value "<API_KEY>"
az keyvault secret set --vault-name $KV --name x-api-secret        --value "<API_SECRET>"

# Bearer token — for Filtered Stream (app-only auth)
az keyvault secret set --vault-name $KV --name x-bearer-token      --value "<BEARER_TOKEN>"

# Per-bot OAuth 1.0a access tokens (one set per bot account)
az keyvault secret set --vault-name $KV --name x-access-token-agreeable        --value "<TOKEN>"
az keyvault secret set --vault-name $KV --name x-access-token-secret-agreeable --value "<SECRET>"
az keyvault secret set --vault-name $KV --name x-access-token-neutral          --value "<TOKEN>"
az keyvault secret set --vault-name $KV --name x-access-token-secret-neutral   --value "<SECRET>"
az keyvault secret set --vault-name $KV --name x-access-token-satirical        --value "<TOKEN>"
az keyvault secret set --vault-name $KV --name x-access-token-secret-satirical --value "<SECRET>"

# Bot user IDs (numeric, not handles) — enables self-reply guard
az keyvault secret set --vault-name $KV --name bot-user-id-agreeable --value "<NUMERIC_USER_ID>"
az keyvault secret set --vault-name $KV --name bot-user-id-neutral   --value "<NUMERIC_USER_ID>"
az keyvault secret set --vault-name $KV --name bot-user-id-satirical --value "<NUMERIC_USER_ID>"
```

After updating secrets, restart the App Service to pick them up:

```bash
az webapp restart --name azapplikxqqfjcgk72 --resource-group rg-derad-agent
```

---

## App Settings to Flip

| Setting                    | Current                  | Production value    |
| -------------------------- | ------------------------ | ------------------- |
| `DERAD_DRY_RUN`            | `true`                   | `false`             |
| `DERAD_ALLOWED_AUTHOR_IDS` | `111,222,333` (test IDs) | real bot author IDs |
| `DERAD_RESTRICT_TO_REGISTERED` | `true` | `false` (or keep `true` during supervised launch) |

Update via:

```bash
az webapp config appsettings set \
  --name azapplikxqqfjcgk72 \
  --resource-group rg-derad-agent \
  --settings DERAD_DRY_RUN=false
```

---

## Stream Event Format

The Filtered Stream delivers v2 tweet objects. The streamer reshapes each event into a v1-style dict for `_dispatch_tweet`:

```json
{
  "id_str": "<mention_tweet_id>",
  "text": "@bot_handle <question or claim>",
  "in_reply_to_status_id_str": "<parent_tweet_id_being_fact_checked>",
  "user": {
    "id_str": "<author_user_id>",
    "username": "<author_handle>"
  }
}
```

The tone is extracted from `matching_rules[0].tag`, not from the tweet payload.

---

## Testing Without Real X Credentials

While X credentials are still `placeholder`, use dry-run mode:

```bash
# DERAD_INGEST_MODE=streaming must be set; the streamer will connect and print events
# DERAD_DRY_RUN=true skips the actual X reply post

# In dry-run, the mention text itself is used as the statement (no parent fetch needed)
# Trigger a test by sending a mention from an allowed account to any of the three bot handles
```

**To see the generated reply** (wait ~40 s after the mention is ingested):

```python
from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential

svc = TableServiceClient(
    "https://azsalikxqqfjcgk72.table.core.windows.net",
    credential=DefaultAzureCredential()
)
rows = list(svc.get_table_client("MentionEvents").query_entities("PartitionKey eq '2026-05'"))
for r in sorted(rows, key=lambda r: r.get("Timestamp", ""), reverse=True)[:5]:
    print(f"outcome={r.get('outcome')} | reply={r.get('reply_text', '')[:300]}")
```

You need the `Storage Table Data Reader` role on `azsalikxqqfjcgk72` — ask @advaitmb to grant it.

---

## Tone Routing Reference

| Bot handle    | Tone      | KV secret for user ID   |
| ------------- | --------- | ----------------------- |
| `@aggiexbot`  | agreeable | `bot-user-id-agreeable` |
| `@nelliexbot` | neutral   | `bot-user-id-neutral`   |
| `@eddiexbot`  | satirical | `bot-user-id-satirical` |

---

## Checklist: Going Live

- [ ] Create X developer app with Read + Write permissions
- [ ] Generate API key/secret, bearer token, and per-bot access token pairs
- [ ] Populate all KV secrets (bearer token, keys, per-bot tokens, user IDs)
- [ ] Set `DERAD_DRY_RUN=false` in App Service
- [ ] Update `DERAD_ALLOWED_AUTHOR_IDS` to real allowed author IDs (or set `DERAD_RESTRICT_TO_REGISTERED=false`)
- [ ] Restart App Service and verify `/healthz` returns `index_loaded: true`
- [ ] Check App Service logs for "Filtered stream connected" — confirms the stream is live
- [ ] Send a test mention from an allowed account and check `MentionEvents` table
