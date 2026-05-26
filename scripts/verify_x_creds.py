#!/usr/bin/env python
"""Verify the X creds currently in KV are coherent AND identify the X app.

Three checks:
  1. OAuth 1.0a internal consistency (CK + CS + AT + ATS)
     → calls GET /2/users/me. 200 + user_id matches BOT_USER_ID means the four
     OAuth1 secrets are from the same app, bound to the right user. Does NOT
     prove they're from the *new* app vs the *old* app — same-user tokens from
     different apps will both return 200.
  2. OAuth 1.0a write-permission probe
     → attempts POST /2/tweets with a deliberately-empty body. The response
     code tells us about app permissions:
       400 → OAuth1 set has write permission AND is from a Read+Write app.
       403 → OAuth1 set authenticates but app lacks write permission (= a
             read-only / streaming-only app — most likely the OLD app).
       401 → OAuth1 set doesn't authenticate at all (broken pairing).
  3. App-only bearer round-trip
     → returns the bot user as a sanity check.

Cred-identity dump:
  Shows head + tail of each non-token-bound secret so the user can eyeball
  against the X Developer Portal to confirm the right app's creds are in KV.
  Token values (AT/ATS) are not dumped beyond a head — they identify the user,
  not the app, so portal comparison doesn't help.
"""
from __future__ import annotations

import os
import subprocess
import sys

import requests
from requests_oauthlib import OAuth1


KV = "azkvspzdzrbtv3v4o"
BOT_HANDLE = "eddiexbot"


def kv_get(name: str) -> str:
    out = subprocess.run(
        ["az", "keyvault", "secret", "show", "--vault-name", KV, "--name", name, "--query", "value", "-o", "tsv"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def check_oauth1():
    print("=" * 60)
    print("CHECK 1 — OAuth 1.0a user-context (CK + CS + AT + ATS)")
    print("=" * 60)
    ck = kv_get("x-api-key")
    cs = kv_get("x-api-secret")
    at = kv_get("x-access-token")
    ats = kv_get("x-access-token-secret")
    expected_user_id = kv_get("bot-user-id")
    print(f"  CK head:  {ck[:10]}...{ck[-4:]}")
    print(f"  AT head:  {at[:10]}...{at[-4:]}")
    print(f"  Expected bot-user-id: {expected_user_id}")

    auth = OAuth1(ck, cs, at, ats, signature_type="auth_header")
    resp = requests.get("https://api.x.com/2/users/me", auth=auth, timeout=20)
    print(f"  GET /2/users/me → HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json().get("data", {})
        actual_id = data.get("id")
        actual_uname = data.get("username")
        print(f"  Returned user: id={actual_id} username={actual_uname}")
        if actual_id == expected_user_id:
            print("  ✓ OAuth1 tokens are VALID for this app, bound to BOT_USER_ID.")
            return True
        else:
            print(f"  ✗ MISMATCH — tokens are valid but bound to user {actual_id}, not BOT_USER_ID {expected_user_id}.")
            return False
    else:
        print(f"  ✗ Tokens REJECTED. Body: {resp.text[:240]}")
        print("  Most likely: access token was minted for a different X app's CK/CS.")
        return False


def check_oauth1_write_permission():
    """Send a deliberately-malformed POST /2/tweets and look at the rejection
    code. 400 = app has write permission (probably new R+W app). 403 = app is
    read-only (probably the old streaming app). 401 = OAuth pairing broken."""
    print()
    print("=" * 60)
    print("CHECK 2 — OAuth1 write-permission probe (the decisive test)")
    print("=" * 60)
    ck = kv_get("x-api-key")
    cs = kv_get("x-api-secret")
    at = kv_get("x-access-token")
    ats = kv_get("x-access-token-secret")
    auth = OAuth1(ck, cs, at, ats, signature_type="auth_header")
    resp = requests.post(
        "https://api.x.com/2/tweets",
        auth=auth,
        json={"text": ""},  # deliberately invalid — we want the rejection signal
        timeout=20,
    )
    print(f"  POST /2/tweets (empty body) → HTTP {resp.status_code}")
    print(f"  Body: {resp.text[:300]}")
    if resp.status_code in (400, 422):
        print("  ✓ Write permission GRANTED. App is Read+Write — consistent with NEW app.")
        return True
    if resp.status_code == 403:
        print("  ✗ Write permission DENIED. App appears to be read-only — likely OLD streaming app.")
        return False
    if resp.status_code == 401:
        print("  ✗ Authentication failed. OAuth1 pairing is broken.")
        return False
    print(f"  ? Unexpected status — inspect body above.")
    return False


def check_bearer():
    print()
    print("=" * 60)
    print("CHECK 3 — App-only bearer round-trip")
    print("=" * 60)
    bearer = kv_get("x-bearer-token")
    # Mask middle so the bearer isn't fully exposed but a portal-side comparison is possible.
    print(f"  Bearer prefix: {bearer[:18]}…  suffix: …{bearer[-12:]}  (len={len(bearer)})")
    resp = requests.get(
        f"https://api.x.com/2/users/by/username/{BOT_HANDLE}",
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=20,
    )
    print(f"  GET /2/users/by/username/{BOT_HANDLE} → HTTP {resp.status_code}")
    if resp.status_code == 200:
        print(f"  ✓ Bearer is VALID. Returned: {resp.json().get('data')}")
        return True
    else:
        print(f"  ✗ Bearer REJECTED. Body: {resp.text[:240]}")
        return False


def dump_identity():
    """Print head+tail of all 4 OAuth1 secrets + the bearer so the user can
    compare each one against the new X app's portal. Each secret is shown
    with first 6 + last 6 chars + length — enough to confirm identity
    against the portal without revealing the full value in transcripts."""
    print()
    print("=" * 60)
    print("APP-IDENTITY DUMP (compare each line against the new app's portal)")
    print("=" * 60)
    fields = [
        ("x-api-key",             "API Key (Consumer Key)"),
        ("x-api-secret",          "API Key Secret (Consumer Secret)"),
        ("x-bearer-token",        "Bearer Token"),
        ("x-access-token",        "Access Token"),
        ("x-access-token-secret", "Access Token Secret"),
    ]
    for kv_name, portal_label in fields:
        v = kv_get(kv_name)
        head = v[:6]
        tail = v[-6:]
        print(f"  {kv_name:25}  head={head}…  tail=…{tail}  len={len(v):3}   "
              f"(portal: {portal_label})")
    print()
    print("  For each row: look at the new X app's 'Keys and tokens' tab. If the")
    print("  head and tail match what's shown there, that field is from the new")
    print("  app. If any row's head doesn't match, THAT specific secret in KV is")
    print("  from the wrong app (most likely the old one).")
    print()
    print("  Note: OAuth 1.0a signing requires CK+CS+AT+ATS to all be from the")
    print("  SAME app — Check 1 returning HTTP 200 means those four are mutually")
    print("  consistent. If you find any mismatch in the comparison above, it'd")
    print("  mean either (a) the X API is unexpectedly tolerant about cross-app")
    print("  tokens, or (b) the portal isn't showing what you expect for that")
    print("  field — worth re-verifying.")


def main() -> int:
    ok1 = check_oauth1()
    ok2 = check_oauth1_write_permission()
    ok3 = check_bearer()
    dump_identity()
    print()
    if ok1 and ok2 and ok3:
        print("All checks passed — KV creds are coherent, OAuth1 set has write permission, bearer works.")
        return 0
    print("One or more checks did not confirm new-app identity. See above + compare against the portal.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
