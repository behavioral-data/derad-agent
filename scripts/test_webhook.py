#!/usr/bin/env python3
"""Fire a signed test webhook at the deployed App Service.

Usage:
    python scripts/test_webhook.py [--tone neutral|agreeable|satirical] [--text "..."]

The script generates a valid HMAC-SHA256 signature using the X_API_SECRET
stored in Key Vault (seeded as 'placeholder' during dev setup), so the
app accepts it as a legitimate webhook.
"""
import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.request

BASE_URL = "https://azapplikxqqfjcgk72.azurewebsites.net"
CONSUMER_SECRET = "placeholder"  # matches what seed_keyvault.sh put in KV

# BOT_USER_ID_* are all "placeholder" in KV during dev — the app maps
# for_user_id → tone, so use "placeholder" to route to whichever tone wins.
DEV_FOR_USER_ID = "placeholder"


def sign(body: bytes) -> str:
    digest = hmac.new(
        CONSUMER_SECRET.encode(),
        body,
        hashlib.sha256,
    ).digest()
    return "sha256=" + base64.b64encode(digest).decode()


def send(payload: dict) -> None:
    body = json.dumps(payload).encode()
    sig = sign(body)
    url = f"{BASE_URL}/mentions"

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Twitter-Webhooks-Signature": sig,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print(f"POST {url} → {resp.status}")
            print(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"POST {url} → {e.code}")
        print(e.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tone", default="neutral",
                    choices=["neutral", "agreeable", "satirical"])
    ap.add_argument("--text", default="vaccines cause autism — is this true?")
    ap.add_argument("--author-id", default="111")  # in DERAD_ALLOWED_AUTHOR_IDS
    args = ap.parse_args()

    payload = {
        "for_user_id": DEV_FOR_USER_ID,  # routes to whichever tone KV maps to
        "tweet_create_events": [{
            "id_str": str(int(time.time() * 1000)),  # unique per run
            "user": {
                "id_str": args.author_id,
                "screen_name": "test_researcher",
            },
            "text": f"@nellie_bot {args.text}",
            "in_reply_to_status_id_str": "99999999999999999",  # parent claim tweet
        }],
    }

    print(f"Sending test mention (tone routed by for_user_id):")
    print(f"  text: {args.text}")
    print(f"  author_id: {args.author_id}")
    print()
    send(payload)

    # Check if index loaded
    print()
    with urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=10) as r:
        print("healthz:", r.read().decode())


if __name__ == "__main__":
    sys.exit(main())
