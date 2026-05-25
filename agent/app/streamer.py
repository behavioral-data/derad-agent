"""Real-time mention ingestion via X Filtered Stream API.

Activated by DERAD_INGEST_MODE=streaming. Opens a persistent connection to
GET /2/tweets/search/stream, receives matching tweets in real time, and
dispatches them to the shared _dispatch_tweet pipeline.

A single rule is synced on startup matching the configured BOT_HANDLE.
Tone is resolved downstream in _dispatch_tweet from the participant table
(or random for unregistered users), not from the stream rule.

Reconnection uses exponential backoff capped at 30 minutes. A 429 response
backs off at least 120 s before retrying.
"""
from __future__ import annotations

import atexit
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

# Graceful-shutdown state. When the worker is restarted, we want to actively
# close the stream connection so X frees the per-app slot immediately —
# otherwise the next worker's connect gets a 429 and waits 120 s.
_shutting_down = threading.Event()
_active_resp_lock = threading.Lock()
_active_resp: requests.Response | None = None
_shutdown_hook_installed = False


def is_connected() -> bool:
    return _connected.is_set()


def _set_active_resp(resp: requests.Response | None) -> None:
    global _active_resp
    with _active_resp_lock:
        _active_resp = resp


def _request_shutdown() -> None:
    """atexit hook: signal the streamer loop and force-close any open response.

    Closing the Response unblocks the in-flight `iter_lines()` read in the
    streamer thread (raising a RequestException) and tells X our end of the
    connection is gone, so the next worker's connect doesn't 429.
    """
    _shutting_down.set()
    with _active_resp_lock:
        resp = _active_resp
    if resp is None:
        return
    try:
        resp.close()
        logger.info("Closed active stream response on shutdown")
    except Exception:
        logger.warning("Error closing stream response on shutdown", exc_info=True)


def _install_shutdown_hook() -> None:
    global _shutdown_hook_installed
    if _shutdown_hook_installed:
        return
    atexit.register(_request_shutdown)
    _shutdown_hook_installed = True


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
    """Build a single stream rule matching the configured BOT_HANDLE."""
    handle = os.getenv("BOT_HANDLE", "").lstrip("@")
    if not handle:
        return []
    return [{"value": f"@{handle}"}]


def _sync_rules(token: str) -> None:
    """Sync stream rules: skip if already correct, otherwise replace."""
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.get(_RULES_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    existing = resp.json().get("data") or []

    new_rules = _bot_rules()
    if not new_rules:
        logger.warning("BOT_HANDLE env var not set — stream will receive no events")
        return

    # Skip if existing rules already match desired rules exactly (avoid unnecessary churn).
    existing_values = {r.get("value") for r in existing}
    desired_values = {r["value"] for r in new_rules}
    if existing_values == desired_values:
        logger.info("Stream rules already up to date: %s", sorted(desired_values))
        return

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

    add_resp = requests.post(
        _RULES_URL,
        headers=headers,
        json={"add": new_rules},
        timeout=30,
    )
    add_resp.raise_for_status()
    logger.info("Installed %d stream rule(s): %s", len(new_rules), [r["value"] for r in new_rules])


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
    while not _shutting_down.is_set():
        try:
            logger.info("Connecting to filtered stream ...")
            with requests.get(
                _BASE,
                headers=headers,
                params=_STREAM_PARAMS,
                stream=True,
                timeout=(10, 90),
            ) as resp:
                if resp.status_code == 429:
                    # X rate-limits repeated connection attempts; back off at
                    # least 120 s and grow exponentially up to 30 minutes.
                    wait = max(backoff, 120.0)
                    logger.warning("Stream rate-limited (429); backing off %.0f s", wait)
                    _connected.clear()
                    if _shutting_down.wait(timeout=wait):
                        break
                    backoff = min(wait * 2, 1800)
                    continue
                resp.raise_for_status()
                _set_active_resp(resp)
                backoff = 1.0
                _connected.set()
                logger.info("Filtered stream connected")

                try:
                    for raw_line in resp.iter_lines():
                        if _shutting_down.is_set():
                            break
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

                        tweet = _reshape(data, event.get("includes") or {})
                        dispatch_fn(tweet, datetime.now(timezone.utc))
                finally:
                    _set_active_resp(None)

        except requests.exceptions.RequestException as exc:
            if _shutting_down.is_set():
                logger.info("Stream closed during shutdown")
                break
            logger.warning("Stream lost: %s — reconnecting in %.0f s", exc, backoff)
        except Exception:
            logger.exception("Unexpected stream error — reconnecting in %.0f s", backoff)
        _connected.clear()

        if _shutting_down.wait(timeout=backoff):
            break
        backoff = min(backoff * 2, 1800)

    logger.info("Stream loop exiting")


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

    _install_shutdown_hook()

    t = threading.Thread(
        target=_stream_loop,
        args=(dispatch_fn, token),
        daemon=True,
        name="streamer",
    )
    t.start()
    logger.info("Filtered stream worker started")
