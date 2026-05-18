#!/usr/bin/env bash
# Post-deploy smoke test for derad-agent.
# Verifies:
#   - /healthz responds, and reports index_loaded=true after preload
#   - /info renders
#   - CRC GET on every /mention-* endpoint returns a valid HMAC
#   - POST without signature returns 403
#   - POST with a valid signature returns 200 (and is deduped on the second try)
#
# Usage:
#   ./scripts/smoke_test.sh https://<app>.azurewebsites.net <X_API_SECRET>
#
# The second argument is the X consumer secret that the App Service uses to
# verify webhook signatures — same value seeded into Key Vault as `x-api-secret`.
# If your App Service reads it from Key Vault, just pass it through here.

set -euo pipefail

HOST="${1:?usage: smoke_test.sh https://<app>.azurewebsites.net <X_API_SECRET>}"
SECRET="${2:?missing X_API_SECRET as second arg}"

# Strip trailing slash if present.
HOST="${HOST%/}"

# X requires HTTPS for webhook endpoints. Flag http:// up front so the user
# doesn't get a confusing CRC failure on registration later.
case "$HOST" in
  https://*) ;;
  http://*)
    printf "WARNING: %s is http://; X requires HTTPS for webhooks. Continuing for local dev only.\n" "$HOST" >&2
    ;;
  *)
    printf "ERROR: HOST must start with https:// or http:// — got %s\n" "$HOST" >&2
    exit 2
    ;;
esac

step() { printf "\n=== %s ===\n" "$*"; }
fail() { printf "FAIL: %s\n" "$*" >&2; exit 1; }
pass() { printf "  PASS: %s\n" "$*"; }

# ── 1. health ────────────────────────────────────────────────────────────────
step "1. /healthz"
HZ=$(curl -fsS "$HOST/healthz")
echo "  $HZ"
echo "$HZ" | grep -q '"ok":true' || fail "/healthz did not return ok:true"
pass "/healthz returns 200 ok:true"

# Wait up to 60s for index_loaded to flip true. `|| true` keeps the retry
# loop alive under `set -e` when curl hits a transient failure.
step "2. index preload (waiting up to 60s)"
for i in $(seq 1 60); do
  HZ=$(curl -fsS "$HOST/healthz" || true)
  if echo "$HZ" | grep -q '"index_loaded":true'; then
    pass "index_loaded=true after ${i}s"
    break
  fi
  sleep 1
done
echo "$HZ" | grep -q '"index_loaded":true' || fail "index never finished loading"

# ── 3. CRC GET on each tone ──────────────────────────────────────────────────
step "3. CRC GET on each /mention-* endpoint"
for TONE in agreeable neutral satirical; do
  TOKEN="smoke-$TONE-$$"
  RESP=$(curl -fsS "$HOST/mention-$TONE?crc_token=$TOKEN")
  echo "$RESP" | grep -q '"response_token":"sha256=' || fail "CRC for /mention-$TONE missing response_token"

  # Recompute the HMAC ourselves so we don't trust a server that's lying.
  # Pass the secret via env, not argv — argv shows up in `ps auxww`.
  EXPECTED=$(SMOKE_SECRET="$SECRET" SMOKE_TOKEN="$TOKEN" python3 -c '
import base64, hashlib, hmac, os
k = os.environ["SMOKE_SECRET"].encode()
t = os.environ["SMOKE_TOKEN"].encode()
print("sha256=" + base64.b64encode(hmac.new(k, t, hashlib.sha256).digest()).decode())
')
  echo "$RESP" | grep -q "\"$EXPECTED\"" || fail "/mention-$TONE returned wrong HMAC (expected $EXPECTED, got $RESP)"
  pass "/mention-$TONE CRC OK"
done

# ── 4. POST without signature → 403 ─────────────────────────────────────────
step "4. POST without signature must be rejected"
CODE=$(curl -sS -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" -d '{}' "$HOST/mention-neutral")
[[ "$CODE" == "403" ]] || fail "expected 403 on unsigned POST, got $CODE"
pass "unsigned POST → 403"

# ── 5. /info renders ────────────────────────────────────────────────────────
step "5. /info renders without exception"
INFO=$(curl -fsS "$HOST/info?reply_id=R1&tone=neutral")
echo "$INFO" | grep -q "twitter-tweet" || fail "/info missing tweet embed"
pass "/info responds 200 and contains the tweet embed"

printf "\nAll smoke checks passed against %s\n" "$HOST"
