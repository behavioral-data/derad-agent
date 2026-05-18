#!/usr/bin/env python
"""Register and manage X (Twitter) Account Activity webhooks for the three bots.

This is the Phase 4 runbook tool. After ``azd up`` deploys the App Service
and you've seeded the bot OAuth1 credentials into Key Vault and the local
``.env``, you run this CLI once to:

  1. Register one webhook URL per tone against the bot's OAuth1 credentials.
     X immediately issues a CRC GET to each — the App Service must already
     be live and responding with the correct HMAC.
  2. Subscribe each bot user to its webhook so mentions are delivered.

The CLI also has read-only helpers for the runbook:

  ``derad-webhooks me --tone X``    print the authed user's id + username,
                                    so you can paste them into the
                                    ``BOT_USER_ID_*`` Key Vault secrets.
  ``derad-webhooks list``           show registered webhooks.
  ``derad-webhooks subscriptions``  show who's subscribed to each webhook.

The CLI uses the OAuth1 tokens already in your env (``X_API_KEY``,
``X_API_SECRET``, ``X_ACCESS_TOKEN_<TONE>``, ``X_ACCESS_TOKEN_SECRET_<TONE>``).
Locally these come from ``derad_agent/llm/.env``; in CI/azd contexts you can
export them yourself.

Examples:

    derad-webhooks me --tone neutral
    derad-webhooks register --tone agreeable --url https://derad-prod.azurewebsites.net/mention-agreeable
    derad-webhooks register --tone neutral   --url https://derad-prod.azurewebsites.net/mention-neutral
    derad-webhooks register --tone satirical --url https://derad-prod.azurewebsites.net/mention-satirical
    derad-webhooks list --tone neutral
    derad-webhooks subscribe --tone neutral --webhook-id 1234567890
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import requests

TONES = ("agreeable", "neutral", "satirical")


def _get_client(tone: str):
    """Return an xdk.Client bound to the bot's OAuth1 credentials."""
    from derad_agent.llm.config import get_x_client
    return get_x_client(tone=tone)


def _dump(value: Any) -> Any:
    """Coerce SDK pydantic models into plain dicts; pass through everything else."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def _emit_response(resp) -> int:
    """Emit a response payload as JSON; return non-zero if it carries errors.

    The xdk response models are split: ``CreateResponse`` (used by
    ``webhooks.create``) is flat — its fields ARE the payload. Every other
    response we touch is nested as ``{data, errors}``. This helper handles
    both shapes uniformly: it prefers ``.data`` when present, falls back to
    dumping the whole model otherwise, and surfaces any non-empty
    ``.errors`` as a non-zero exit so the runbook fails loudly on a
    2xx-with-errors response.
    """
    errors = getattr(resp, "errors", None)
    if errors:
        print(f"ERROR: {json.dumps(_dump(errors), indent=2, default=str)}", file=sys.stderr)
        return 1
    # Prefer .data when the model exposes it (nested responses). Pydantic raises
    # AttributeError on undeclared fields, so hasattr correctly returns False on
    # the flat CreateResponse — we then fall back to dumping the whole model.
    payload = resp.data if hasattr(resp, "data") else resp
    print(json.dumps(_dump(payload), indent=2, default=str))
    return 0


def _call(label: str, fn, *args, **kwargs):
    """Invoke an SDK call, translating requests.HTTPError into a runbook-friendly
    one-line error written to stderr. Returns (resp, exit_code).
    """
    try:
        return fn(*args, **kwargs), 0
    except requests.HTTPError as exc:
        body = ""
        if exc.response is not None:
            body = (exc.response.text or "")[:1000]
            status = exc.response.status_code
        else:
            status = "?"
        print(f"ERROR: {label} failed: HTTP {status}: {body}", file=sys.stderr)
        return None, 1


# ── Subcommands ─────────────────────────────────────────────────────────────

def cmd_me(args) -> int:
    client = _get_client(args.tone)
    resp, code = _call("users.get_me", client.users.get_me)
    if code: return code
    return _emit_response(resp)


def cmd_register(args) -> int:
    from xdk.webhooks.models import CreateRequest
    client = _get_client(args.tone)
    body = CreateRequest(url=args.url)
    resp, code = _call("webhooks.create", client.webhooks.create, body=body)
    if code: return code
    return _emit_response(resp)


def cmd_list(args) -> int:
    client = _get_client(args.tone)
    resp, code = _call("webhooks.get", client.webhooks.get)
    if code: return code
    return _emit_response(resp)


def cmd_validate(args) -> int:
    client = _get_client(args.tone)
    resp, code = _call("webhooks.validate", client.webhooks.validate, webhook_id=args.webhook_id)
    if code: return code
    return _emit_response(resp)


def cmd_delete(args) -> int:
    client = _get_client(args.tone)
    resp, code = _call("webhooks.delete", client.webhooks.delete, webhook_id=args.webhook_id)
    if code: return code
    # Still emit any errors the response carries; on success, print a simple ack.
    if getattr(resp, "errors", None):
        return _emit_response(resp)
    print(json.dumps({"deleted": args.webhook_id}, indent=2))
    return 0


def cmd_subscribe(args) -> int:
    """Subscribe the authed (tone) user to a webhook so their mentions deliver."""
    client = _get_client(args.tone)
    resp, code = _call(
        "account_activity.create_subscription",
        client.account_activity.create_subscription,
        webhook_id=args.webhook_id,
    )
    if code: return code
    if getattr(resp, "errors", None):
        return _emit_response(resp)
    print(json.dumps({"subscribed_tone": args.tone, "webhook_id": args.webhook_id}, indent=2))
    return 0


def cmd_unsubscribe(args) -> int:
    client = _get_client(args.tone)
    resp, code = _call(
        "account_activity.delete_subscription",
        client.account_activity.delete_subscription,
        webhook_id=args.webhook_id, user_id=args.user_id,
    )
    if code: return code
    if getattr(resp, "errors", None):
        return _emit_response(resp)
    print(json.dumps({"unsubscribed_user_id": args.user_id, "webhook_id": args.webhook_id}, indent=2))
    return 0


def cmd_subscriptions(args) -> int:
    client = _get_client(args.tone)
    resp, code = _call(
        "account_activity.get_subscriptions",
        client.account_activity.get_subscriptions,
        webhook_id=args.webhook_id,
    )
    if code: return code
    return _emit_response(resp)


# ── argparse plumbing ───────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="derad-webhooks",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_tone(sp):
        sp.add_argument("--tone", required=True, choices=TONES, help="Which bot's credentials to use.")

    sp = sub.add_parser("me", help="Print authed user's id + username.")
    add_tone(sp); sp.set_defaults(func=cmd_me)

    sp = sub.add_parser("register", help="Register a webhook URL.")
    add_tone(sp)
    sp.add_argument("--url", required=True, help="Public HTTPS webhook URL.")
    sp.set_defaults(func=cmd_register)

    sp = sub.add_parser("list", help="List registered webhooks.")
    add_tone(sp); sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("validate", help="Trigger CRC verification on a webhook.")
    add_tone(sp)
    sp.add_argument("--webhook-id", required=True)
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("delete", help="Delete a webhook.")
    add_tone(sp)
    sp.add_argument("--webhook-id", required=True)
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("subscribe", help="Subscribe the bot user to a webhook.")
    add_tone(sp)
    sp.add_argument("--webhook-id", required=True)
    sp.set_defaults(func=cmd_subscribe)

    sp = sub.add_parser("unsubscribe", help="Unsubscribe a user.")
    add_tone(sp)
    sp.add_argument("--webhook-id", required=True)
    sp.add_argument("--user-id", required=True)
    sp.set_defaults(func=cmd_unsubscribe)

    sp = sub.add_parser("subscriptions", help="List subscriptions on a webhook.")
    add_tone(sp)
    sp.add_argument("--webhook-id", required=True)
    sp.set_defaults(func=cmd_subscriptions)

    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
