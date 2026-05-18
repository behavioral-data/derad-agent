"""Background polling worker — fallback when the X Account Activity webhook
subscription lapses (token expiry, AAA outage, etc.).

Activated by DERAD_INGEST_MODE=polling. When mode is "webhooks" (the default)
this module is imported but start_poller() is a noop — no thread is created.
The two modes are mutually exclusive: in polling mode the /mentions POST
endpoint accepts deliveries to avoid X marking the URL unhealthy, but does
not process them.

Cost math (X PPU, 2026):
  DERAD_POLL_INTERVAL_SEC=60, 1 page/bot/cycle (typical):
    3 bots × 1 call/min × 43 200 min/mo ≈ $21/mo
  Worst-case, _MAX_PAGES_PER_POLL=10 pages/bot/cycle (backfill burst):
    3 bots × 10 calls/cycle × 1 cycle/min × 43 200 min/mo ≈ $210/mo
  Never set interval < 30 s without checking the current PPU rate card.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

INGEST_MODE = os.getenv("DERAD_INGEST_MODE", "webhooks").lower()
_POLL_INTERVAL_SEC = int(os.getenv("DERAD_POLL_INTERVAL_SEC", "60"))
_MAX_RESULTS = 5
_MAX_PAGES_PER_POLL = 10


def _v2_to_v1_tweet(
    t: dict,
    *,
    user_id: str,
    users_by_id: dict[str, str] | None = None,
) -> dict:
    """Reshape an xdk v2 mention dict into the v1-shaped dict that _dispatch_tweet expects.

    Fields mapped:
      v2 id                               → v1 id_str
      v2 author_id                        → v1 user.id_str  (falls back to bot's user_id)
      v2 text                             → v1 text
      referenced_tweets[replied_to].id    → v1 in_reply_to_status_id_str
      users_by_id[author_id]              → v1 user.username (from page.includes.users)
    """
    refs = t.get("referenced_tweets") or []
    parent_id = next(
        (r["id"] for r in refs if isinstance(r, dict) and r.get("type") == "replied_to"),
        None,
    )
    author_id = t.get("author_id") or user_id
    user: dict = {"id_str": author_id}
    username = (users_by_id or {}).get(author_id)
    if username:
        user["username"] = username
    return {
        "id_str": t.get("id"),
        "in_reply_to_status_id_str": parent_id,
        "text": t.get("text", ""),
        "user": user,
    }


def _poll_one(
    tone: str,
    user_id: str,
    dispatch_fn: Callable,
    cursor_store: Any,
    *,
    x_client_factory: Callable | None = None,
) -> None:
    """Fetch and dispatch new mentions for one bot.

    On the first call with no cursor (bootstrap), the newest tweet ID is
    recorded as the watermark but no tweets are dispatched — pre-study
    mentions would corrupt research event logs.

    Args:
        x_client_factory: callable(tone) → xdk Client. Defaults to
            get_x_client from derad_agent.llm.config. Injected in tests
            to avoid real API calls.
    """
    if x_client_factory is None:
        from derad_agent.llm.config import get_x_client
        x_client_factory = get_x_client

    cursor_key = f"poll_cursor:{tone}"
    current_cursor = cursor_store.get(cursor_key)
    is_bootstrap = current_cursor is None

    try:
        client = x_client_factory(tone)
        newest_id: str | None = None
        pages_seen = 0

        for page in client.users.get_mentions(
            id=user_id,
            since_id=current_cursor or None,
            max_results=_MAX_RESULTS,
            tweet_fields=["author_id", "referenced_tweets"],
            expansions=["author_id"],
            user_fields=["username"],
        ):
            if not page.data:
                break
            pages_seen += 1
            # get_mentions returns newest-first; newest_id is from the first page.
            if newest_id is None and page.meta and page.meta.newest_id:
                newest_id = page.meta.newest_id

            if is_bootstrap:
                # Record the watermark without dispatching — pre-study mentions
                # would corrupt research event logs on first boot.
                break

            # Build author_id → username lookup from page.includes.users so
            # MentionEvent.author_username is populated in polling mode.
            users_by_id: dict[str, str] = {}
            if page.includes and page.includes.users:
                for u in page.includes.users:
                    if isinstance(u, dict) and u.get("id"):
                        users_by_id[u["id"]] = u.get("username", "")

            received_at_utc = datetime.now(timezone.utc)
            for tweet_dict in page.data:
                normalized = _v2_to_v1_tweet(
                    tweet_dict, user_id=user_id, users_by_id=users_by_id
                )
                dispatch_fn(tone, normalized, received_at_utc)

            if pages_seen >= _MAX_PAGES_PER_POLL:
                logger.warning(
                    "Page cap reached for tone=%s — some mentions deferred to next cycle", tone
                )
                break

        if newest_id:
            cursor_store.set(cursor_key, newest_id)
            if is_bootstrap:
                logger.info(
                    "Bootstrap: cursor for tone=%s set to %s; pre-study mentions skipped",
                    tone, newest_id,
                )
            else:
                logger.debug(
                    "Poll done: tone=%s pages=%d cursor→%s", tone, pages_seen, newest_id
                )

    except Exception:
        logger.exception("Poll cycle failed for tone=%s user_id=%s", tone, user_id)


def _poll_loop(
    dispatch_fn: Callable,
    bots: list[tuple[str, str]],
    cursor_store: Any,
    *,
    x_client_factory: Callable | None = None,
    interval: int = _POLL_INTERVAL_SEC,
) -> None:
    logger.info(
        "Polling worker started (interval=%ds, tones=%s)",
        interval,
        [tone for tone, _ in bots],
    )
    while True:
        for tone, user_id in bots:
            _poll_one(tone, user_id, dispatch_fn, cursor_store, x_client_factory=x_client_factory)
        time.sleep(interval)


def start_poller(dispatch_fn: Callable) -> None:
    """Start the background polling thread.

    Noop unless DERAD_INGEST_MODE=polling AND we're not under pytest.
    The PYTEST_CURRENT_TEST guard prevents stray threads in the test suite even
    if DERAD_INGEST_MODE is accidentally set in the environment.
    """
    if INGEST_MODE != "polling":
        return
    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    from derad_agent.app.cursors import get_cursor_store

    bots = [
        (tone, uid)
        for tone, uid in {
            "agreeable": os.getenv("BOT_USER_ID_AGREEABLE"),
            "neutral": os.getenv("BOT_USER_ID_NEUTRAL"),
            "satirical": os.getenv("BOT_USER_ID_SATIRICAL"),
        }.items()
        if uid
    ]

    if not bots:
        logger.warning(
            "DERAD_INGEST_MODE=polling but no BOT_USER_ID_* env vars set — poller not started"
        )
        return

    cursor_store = get_cursor_store()
    t = threading.Thread(
        target=_poll_loop,
        args=(dispatch_fn, bots, cursor_store),
        daemon=True,
        name="poller",
    )
    t.start()
    logger.info("Polling worker thread started (tones=%s)", [tone for tone, _ in bots])
