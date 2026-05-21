"""Real-time mention ingestion via X Filtered Stream API.

Activated by DERAD_INGEST_MODE=streaming. Opens a persistent connection to
GET /2/tweets/search/stream, receives matching tweets in real time, and
dispatches them to the shared _dispatch_tweet pipeline.

Rules are synced on startup: old rules are deleted and fresh rules are added,
one per bot handle, tagged with the bot's tone for routing. If a tweet mentions
multiple bots it may match multiple rules; the dedup store in _dispatch_tweet
drops the duplicate dispatches cleanly.

Reconnection uses exponential backoff capped at 5 minutes. A 429 response
backs off 60 s before retrying.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

INGEST_MODE = os.getenv("DERAD_INGEST_MODE", "streaming").lower()

_connected = threading.Event()


def is_connected() -> bool:
    return _connected.is_set()


_BASE = "https://api.twitter.com/2/tweets/search/stream"
_RULES_URL = _BASE + "/rules"

_STREAM_PARAMS = {
    "tweet.fields": "author_id,referenced_tweets",
    "expansions": "author_id",
    "user.fields": "username",
}


def _token() -> str:
    val = os.getenv("X_BEARER_TOKEN")
    if not val:
        raise RuntimeError("X_BEARER_TOKEN env var not set")
    return val


def _bot_rules() -> list[dict]:
    """Build one rule per configured bot handle, tagged with its tone."""
    mapping = {
        "agreeable": os.getenv("BOT_HANDLE_AGREEABLE", ""),
        "neutral": os.getenv("BOT_HANDLE_NEUTRAL", ""),
        "satirical": os.getenv("BOT_HANDLE_SATIRICAL", ""),
    }
    return [
        {"value": f"@{handle.lstrip('@')}", "tag": tone}
        for tone, handle in mapping.items()
        if handle
    ]


def _sync_rules(token: str) -> None:
    """Replace any existing stream rules with fresh bot-mention rules."""
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.get(_RULES_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    existing = resp.json().get("data") or []
    if existing:
        ids = [r["id"] for r in existing]
        del_resp = requests.post(
            _RULES_URL,
            headers=headers,
            json={"delete": {"ids": ids}},
            timeout=30,
        )
        del_resp.raise_for_status()
        logger.info("Deleted %d stale stream rules", len(ids))

    new_rules = _bot_rules()
    if not new_rules:
        logger.warning("No BOT_HANDLE_* env vars set — stream will receive no events")
        return
    add_resp = requests.post(
        _RULES_URL,
        headers=headers,
        json={"add": new_rules},
        timeout=30,
    )
    add_resp.raise_for_status()
    logger.info("Installed %d stream rules: %s", len(new_rules), [r["tag"] for r in new_rules])


def _reshape(data: dict, includes: dict) -> dict:
    """Reshape a v2 stream tweet into the v1-shaped dict _dispatch_tweet expects."""
    refs = data.get("referenced_tweets") or []
    parent_id = next(
        (r["id"] for r in refs if isinstance(r, dict) and r.get("type") == "replied_to"),
        None,
    )
    author_id = data.get("author_id", "")
    users = (includes or {}).get("users") or []
    users_by_id = {u["id"]: u.get("username", "") for u in users if u.get("id")}
    user: dict = {"id_str": author_id}
    if author_id in users_by_id:
        user["username"] = users_by_id[author_id]
    return {
        "id_str": data.get("id"),
        "in_reply_to_status_id_str": parent_id,
        "text": data.get("text", ""),
        "user": user,
    }


def _stream_loop(dispatch_fn: Callable, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    backoff = 1.0
    while True:
        try:
            logger.info("Connecting to filtered stream ...")
            with requests.get(
                _BASE,
                headers=headers,
                params=_STREAM_PARAMS,
                stream=True,
                timeout=(10, 30),
            ) as resp:
                if resp.status_code == 429:
                    logger.warning("Stream rate-limited (429); backing off 60 s")
                    time.sleep(60)
                    continue
                resp.raise_for_status()
                backoff = 1.0
                _connected.set()
                logger.info("Filtered stream connected")

                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue  # heartbeat keep-alive
                    try:
                        event = json.loads(raw_line)
                    except Exception:
                        logger.warning("Unparseable stream line: %r", raw_line[:200])
                        continue

                    data = event.get("data")
                    if not isinstance(data, dict):
                        continue

                    matching = event.get("matching_rules") or []
                    tone = next(
                        (r.get("tag") for r in matching if r.get("tag")),
                        None,
                    )
                    if tone is None:
                        logger.debug("Stream event with no tone tag; skipping")
                        continue

                    tweet = _reshape(data, event.get("includes") or {})
                    dispatch_fn(tone, tweet, datetime.now(timezone.utc))

        except requests.exceptions.RequestException as exc:
            logger.warning("Stream lost: %s — reconnecting in %.0f s", exc, backoff)
        except Exception:
            logger.exception("Unexpected stream error — reconnecting in %.0f s", backoff)
        _connected.clear()

        time.sleep(backoff)
        backoff = min(backoff * 2, 300)


def start_streamer(dispatch_fn: Callable) -> None:
    """Start the background streaming thread.

    Noop unless DERAD_INGEST_MODE=streaming and not under pytest.
    """
    if INGEST_MODE != "streaming":
        return
    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    try:
        token = _token()
        _sync_rules(token)
    except Exception:
        logger.exception("Stream rule sync failed — streamer not started")
        return

    t = threading.Thread(
        target=_stream_loop,
        args=(dispatch_fn, token),
        daemon=True,
        name="streamer",
    )
    t.start()
    logger.info("Filtered stream worker started")
