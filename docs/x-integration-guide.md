# X Platform Integration Guide

**Service:** derad-agent — deployed on Azure App Service  
**Endpoint:** `https://azapplikxqqfjcgk72.azurewebsites.net`  
**Infra repo:** `derad-agent` (this repo) — Bicep in `infra/`

---

## Overview

The agent receives X (Twitter) mention events via webhooks, fetches the parent tweet being fact-checked, retrieves relevant Community Notes, and posts a reply. All three bot accounts (agreeable / neutral / satirical) share a single webhook URL; routing is by `for_user_id` in the event payload.

```
X Account Activity API
  └─ POST /mentions  (HMAC-signed webhook)
       ├─ verifies signature
       ├─ routes to tone by for_user_id
       ├─ fetches parent tweet (X API v2)
       ├─ runs LLM + embedding pipeline (~40 s)
       └─ posts reply (X API v2)
```

Results (reply text, outcome, source notes) are written asynchronously to the `MentionEvents` table in Azure Table Storage.

---

## Endpoints

| Route | Method | Purpose |
|-------|--------|---------|
| `/mentions` | `GET` | CRC handshake — responds with `response_token` |
| `/mentions` | `POST` | Webhook delivery — processes mention events |
| `/healthz` | `GET` | Readiness probe — returns `{"ok": true, "index_loaded": true/false}` |
| `/info` | `GET` | Human-readable reply page linked from bot tweets |

---

## X Developer App Setup

### 1. Create / configure the X app

- Tier: **Basic** (webhooks require Account Activity API, which needs PPU or higher)
- Permissions: **Read + Write** (needs to post replies)
- App type: **Web App / Bot** (generates both API key+secret and access tokens)

### 2. Register the webhook URL

```bash
# Replace <bearer_token> with your app's bearer token
curl -X POST "https://api.twitter.com/2/account_activity/webhooks" \
  -H "Authorization: Bearer <bearer_token>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://azapplikxqqfjcgk72.azurewebsites.net/mentions"}'
```

X will immediately send a GET CRC challenge to `/mentions?crc_token=…` — the
app handles it automatically and responds with the HMAC-SHA256 digest.

### 3. Subscribe each bot account

For each bot account, subscribe its user ID to the webhook:

```bash
# Must be authenticated as the bot user (use its access token)
curl -X POST "https://api.twitter.com/2/account_activity/webhooks/<webhook_id>/subscriptions" \
  -H "Authorization: OAuth ..."
```

---

## Key Vault Secrets to Populate

All credentials live in Azure Key Vault **`azkvlikxqqfjcgk72`**. Current values are `placeholder` — replace with real values before going live.

Run `bash scripts/seed_keyvault.sh` after updating the values in that script, or set individual secrets:

```bash
KV="azkvlikxqqfjcgk72"

# X app credentials (from developer.twitter.com → Keys and Tokens)
az keyvault secret set --vault-name $KV --name x-api-key           --value "<API_KEY>"
az keyvault secret set --vault-name $KV --name x-api-secret        --value "<API_SECRET>"

# Per-bot OAuth 1.0a access tokens (one set per bot account)
az keyvault secret set --vault-name $KV --name x-access-token-agreeable        --value "<TOKEN>"
az keyvault secret set --vault-name $KV --name x-access-token-secret-agreeable --value "<SECRET>"
az keyvault secret set --vault-name $KV --name x-access-token-neutral          --value "<TOKEN>"
az keyvault secret set --vault-name $KV --name x-access-token-secret-neutral   --value "<SECRET>"
az keyvault secret set --vault-name $KV --name x-access-token-satirical        --value "<TOKEN>"
az keyvault secret set --vault-name $KV --name x-access-token-secret-satirical --value "<SECRET>"

# Bot user IDs (numeric, not handles) — enables tone routing and self-reply guard
az keyvault secret set --vault-name $KV --name bot-user-id-agreeable --value "<NUMERIC_USER_ID>"
az keyvault secret set --vault-name $KV --name bot-user-id-neutral   --value "<NUMERIC_USER_ID>"
az keyvault secret set --vault-name $KV --name bot-user-id-satirical --value "<NUMERIC_USER_ID>"

# HMAC webhook secret — set this to the value from the X webhook registration response
az keyvault secret set --vault-name $KV --name x-api-secret --value "<CONSUMER_SECRET>"
```

After updating secrets, restart the App Service to pick them up:

```bash
az webapp restart --name azapplikxqqfjcgk72 --resource-group rg-derad-agent
```

---

## App Settings to Flip

| Setting | Current | Production value |
|---------|---------|-----------------|
| `DERAD_DRY_RUN` | `true` | `false` |
| `DERAD_ALLOWED_AUTHOR_IDS` | `111,222,333` (test IDs) | real bot author IDs |

Update via:

```bash
az webapp config appsettings set \
  --name azapplikxqqfjcgk72 \
  --resource-group rg-derad-agent \
  --settings DERAD_DRY_RUN=false
```

---

## Webhook Payload Format

The app expects the standard X Account Activity API v1.1 payload:

```json
{
  "for_user_id": "<bot_numeric_user_id>",
  "tweet_create_events": [{
    "id_str": "<mention_tweet_id>",
    "text": "@bot_handle <question or claim>",
    "user": {
      "id_str": "<author_user_id>",
      "screen_name": "<author_handle>"
    },
    "in_reply_to_status_id_str": "<parent_tweet_id_being_fact_checked>"
  }]
}
```

**Routing:** `for_user_id` maps to tone:
- agreeable bot's user ID → `agreeable` tone
- neutral bot's user ID → `neutral` tone  
- satirical bot's user ID → `satirical` tone

**Signature:** Every POST must include the header:
```
X-Twitter-Webhooks-Signature: sha256=<base64(HMAC-SHA256(body, consumer_secret))>
```

---

## Testing Without Real X Credentials

While X credentials are still `placeholder`, use dry-run mode:

```bash
# The app is currently running with DERAD_DRY_RUN=true
python scripts/test_webhook.py --text "vaccines cause autism — is this true?"
```

The script signs the request with the dev HMAC secret and POSTs to `/mentions`.
The pipeline runs (~40 s) but skips X API calls, using the mention text as the
statement instead.

**To see the generated reply** (wait ~40 s after sending):

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

| Bot handle | Tone | KV secret for user ID |
|-----------|------|----------------------|
| `@aggie_bot` | agreeable | `bot-user-id-agreeable` |
| `@nellie_bot` | neutral | `bot-user-id-neutral` |
| `@eddie_bot` | satirical | `bot-user-id-satirical` |

---

## Checklist: Going Live

- [ ] Create X developer app with Read + Write permissions
- [ ] Generate API key/secret and per-bot access token pairs
- [ ] Register webhook URL with X Account Activity API
- [ ] Subscribe each bot account to the webhook
- [ ] Populate all 10 X-related KV secrets (keys, tokens, user IDs)
- [ ] Set `DERAD_DRY_RUN=false` in App Service
- [ ] Update `DERAD_ALLOWED_AUTHOR_IDS` to real allowed author IDs
- [ ] Restart App Service and verify `/healthz` returns `index_loaded: true`
- [ ] Send a test mention from an allowed account and check `MentionEvents` table
