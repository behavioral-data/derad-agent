"""3-legged OAuth 1.0a PIN flow for the X (Twitter) API — two-phase
variant so it can run from a non-interactive shell.

Phase A — request_token + authorize URL
  CONSUMER_KEY="..." CONSUMER_SECRET="..." python scripts/x-oauth-pin.py
  → prints the authorize URL + caches the request_token to /tmp.

Phase B — exchange PIN for access_token
  CONSUMER_KEY="..." CONSUMER_SECRET="..." PIN="1234567" \
      python scripts/x-oauth-pin.py
  → prints X_ACCESS_TOKEN + X_ACCESS_TOKEN_SECRET + BOT_USER_ID +
  BOT_HANDLE to stdout (sanity message to stderr).

The cached request_token survives between phases at
/tmp/x-oauth-rt-<consumer_key_hash>.json (mode 0600, deleted on
successful Phase B). Request tokens expire after ~15 min on X's side
— don't dawdle between phases.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def _rt_cache_path(ck: str) -> Path:
    h = hashlib.sha256(ck.encode()).hexdigest()[:12]
    return Path(f"/tmp/x-oauth-rt-{h}.json")


def main() -> int:
    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        print("error: requests-oauthlib not installed", file=sys.stderr)
        print("  pip install requests-oauthlib", file=sys.stderr)
        return 2

    ck = os.environ.get("CONSUMER_KEY")
    cs = os.environ.get("CONSUMER_SECRET")
    if not ck or not cs:
        print("error: CONSUMER_KEY and CONSUMER_SECRET env vars required", file=sys.stderr)
        return 2

    pin = os.environ.get("PIN", "").strip()
    cache = _rt_cache_path(ck)

    if not pin:
        # ── Phase A — request_token ──
        print("Phase A: fetching OAuth request_token from X...", file=sys.stderr)
        oauth = OAuth1Session(ck, client_secret=cs, callback_uri="oob")
        try:
            r = oauth.fetch_request_token("https://api.x.com/oauth/request_token")
        except Exception as exc:
            print(f"error: request_token failed: {exc}", file=sys.stderr)
            print("  Common causes: wrong CK/CS, App's OAuth callback is not 'oob'-compatible.",
                  file=sys.stderr)
            return 1
        rt = r["oauth_token"]
        rts = r["oauth_token_secret"]
        cache.write_text(json.dumps({"oauth_token": rt, "oauth_token_secret": rts}))
        cache.chmod(0o600)
        print(f"Cached request_token to {cache}", file=sys.stderr)
        print()
        print("─" * 70)
        print("OPEN THIS URL while logged in as @eddiexbot:")
        print(f"  https://api.x.com/oauth/authorize?oauth_token={rt}")
        print("Click 'Authorize app', copy the PIN, then re-run this script with")
        print("PIN=<the-pin> in env vars (along with CONSUMER_KEY + CONSUMER_SECRET).")
        print("─" * 70)
        return 0

    # ── Phase B — exchange PIN ──
    if not cache.exists():
        print(f"error: no cached request_token at {cache}", file=sys.stderr)
        print("  Run Phase A first (without PIN env var).", file=sys.stderr)
        return 1
    cached = json.loads(cache.read_text())
    rt = cached["oauth_token"]
    rts = cached["oauth_token_secret"]

    print("Phase B: exchanging PIN for access tokens...", file=sys.stderr)
    oauth = OAuth1Session(
        ck,
        client_secret=cs,
        resource_owner_key=rt,
        resource_owner_secret=rts,
        verifier=pin,
    )
    try:
        tok = oauth.fetch_access_token("https://api.x.com/oauth/access_token")
    except Exception as exc:
        print(f"error: access_token failed: {exc}", file=sys.stderr)
        print("  Common causes: PIN typo, request_token expired (>15 min old),", file=sys.stderr)
        print("                 or authorized as wrong account.", file=sys.stderr)
        return 1

    access_token = tok.get("oauth_token", "")
    access_secret = tok.get("oauth_token_secret", "")
    user_id = tok.get("user_id", "")
    screen_name = tok.get("screen_name", "")

    if not access_token or not access_secret:
        print("error: access_token response missing token/secret", file=sys.stderr)
        return 1

    # stdout: env-file-friendly key=value lines
    print()
    print(f"X_ACCESS_TOKEN={access_token}")
    print(f"X_ACCESS_TOKEN_SECRET={access_secret}")
    print(f"BOT_USER_ID={user_id}")
    print(f"BOT_HANDLE={screen_name}")
    print()
    print("─" * 70, file=sys.stderr)
    print("Sanity-check:", file=sys.stderr)
    print(f"  BOT_USER_ID = {user_id}  ← must match existing KV secret bot-user-id",
          file=sys.stderr)
    print(f"  BOT_HANDLE  = {screen_name}  ← must be 'eddiexbot'", file=sys.stderr)
    print("If either is wrong, you authorized the wrong account. Abort + redo.",
          file=sys.stderr)
    print("─" * 70, file=sys.stderr)

    # Single-use request_token — clean up.
    try:
        cache.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
