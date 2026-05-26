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
import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

INGEST_MODE = os.getenv("DERAD_INGEST_MODE", "streaming").lower()

# Delay before the streamer attempts its FIRST connect. After a deploy
# restart, X's filtered-stream rate-limiter can hold the previous
# worker's slot open for several minutes (X considers half-dead TCP
# connections still occupying the "1 concurrent stream per app" quota).
# A pre-connect delay gives X time to release before we provoke a 429.
# Override via DERAD_STREAMER_STARTUP_DELAY_S; default 60s on a fresh
# start (cheap), bump to 300+ when chaining redeploys.
STREAMER_STARTUP_DELAY_S = float(os.getenv("DERAD_STREAMER_STARTUP_DELAY_S", "60"))

# Per X's filtered-stream docs:
# "Every HTTP 429 received increases the time you must wait until rate
# limiting will no longer be in effect."
# So we deliberately keep retries SLOW — each one we make pushes X's edge
# lockout further out. TooManyConnections (slot held by stale edge state)
# is empirically much slower to release than the request-rate 429, so it
# gets a much longer initial wait.
_TOO_MANY_CONNECTIONS_INITIAL_S = 1800.0   # 30 min — X edge slot release
_RATE_LIMIT_INITIAL_S = 60.0               # X docs: "starting with a 1 minute wait"

_connected = threading.Event()

# Graceful-shutdown state. When the worker is restarted, we want to actively
# close the stream connection so X frees the per-app slot immediately —
# otherwise the next worker's connect gets a 429 for several minutes while
# X times out the stale TCP socket on its side.
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
    """atexit / SIGTERM hook: signal the streamer loop and force a real TCP
    FIN on any open response.

    requests.Response.close() only RELEASES the connection back to urllib3's
    pool — it doesn't actually close the socket. To tell X "we're gone" at
    the TCP layer we need to reach into the urllib3 internals and close the
    underlying HTTPConnection. Empirically X's filtered-stream edge ignores
    TCP state for slot-release decisions, but doing this correctly removes
    one variable from the debugging picture.
    """
    _shutting_down.set()
    with _active_resp_lock:
        resp = _active_resp
    if resp is None:
        return
    try:
        # Force a real socket close via urllib3's internals.
        raw = getattr(resp, "raw", None)
        if raw is not None:
            try:
                connection = getattr(raw, "_connection", None) or getattr(raw, "connection", None)
                if connection is not None:
                    connection.close()
            except Exception:
                pass
            try:
                raw.close()
            except Exception:
                pass
        resp.close()
        logger.info("Closed active stream response on shutdown")
    except Exception:
        logger.warning("Error closing stream response on shutdown", exc_info=True)


def _install_shutdown_hook() -> None:
    """Register cleanup paths that close the active X-stream response on
    worker shutdown. Two signals are wired:

    1. atexit — fires on normal interpreter exit.
    2. SIGTERM — fires under App Service / Container Apps graceful restart,
       *before* the SIGKILL deadline. atexit alone is unreliable here
       because gunicorn's worker SIGTERM path can exit the process before
       atexit handlers run, leaving the X-side TCP socket half-dead and
       holding the rate-limit slot.

    The SIGTERM handler chains to the previous handler (typically
    gunicorn's) so normal shutdown still proceeds.
    """
    global _shutdown_hook_installed
    if _shutdown_hook_installed:
        return
    atexit.register(_request_shutdown)
    try:
        prev_handler = signal.getsignal(signal.SIGTERM)

        def _sigterm_handler(signum, frame):
            logger.info("SIGTERM received — closing X stream before exit")
            _request_shutdown()
            # Chain to whatever was registered before (gunicorn's worker
            # shutdown). If nothing, the default exit semantics apply.
            if callable(prev_handler) and prev_handler not in (signal.SIG_DFL, signal.SIG_IGN):
                prev_handler(signum, frame)

        signal.signal(signal.SIGTERM, _sigterm_handler)
    except (ValueError, OSError) as exc:
        # signal.signal can only be called from the main thread; under
        # gunicorn's worker the streamer is started from main, so this
        # path normally succeeds. Log and continue if it fails.
        logger.warning("Could not install SIGTERM hook: %s; relying on atexit only", exc)
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


def _parse_rl_reset(raw: str) -> float | None:
    """Parse the `x-rate-limit-reset` header (unix epoch in seconds) into
    a float. Returns None on missing / unparseable values."""
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _diagnose_429(resp) -> dict:
    """Pick apart an X 429 response into a diagnostic dict.

    Two distinct 429 variants matter:
      - TooManyConnections — X's edge thinks the slot is held by a stale
        previous connection. Per X docs, only escape is to wait it out;
        retries push the lockout further. No x-rate-limit-* headers.
      - Request-rate (50/15min on Basic) — standard rate-limit response
        WITH x-rate-limit-remaining / x-rate-limit-reset headers.
    """
    info = {
        "rl_limit": resp.headers.get("x-rate-limit-limit") or "",
        "rl_remaining": resp.headers.get("x-rate-limit-remaining") or "",
        "rl_reset": resp.headers.get("x-rate-limit-reset") or "",
        "body_head": (resp.text or "")[:400],
        "connection_issue": "",
        "title": "",
        "is_slot_held": False,
    }
    try:
        body = resp.json()
        if isinstance(body, dict):
            info["connection_issue"] = body.get("connection_issue") or ""
            info["title"] = body.get("title") or ""
            detail = body.get("detail") or ""
            info["is_slot_held"] = (
                info["connection_issue"] == "TooManyConnections"
                or "maximum allowed connection" in detail.lower()
            )
    except Exception:
        pass
    return info


def _stream_loop(dispatch_fn: Callable, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    backoff = 1.0
    # Pre-connect delay — gives X time to release any stale per-app slot
    # left over from a recently-killed previous worker. Skipped only when
    # explicitly zeroed (e.g. local dev with no prior connection).
    if STREAMER_STARTUP_DELAY_S > 0:
        logger.info(
            "Streamer pre-connect delay: %.0f s (lets X release any stale per-app slot)",
            STREAMER_STARTUP_DELAY_S,
        )
        if _shutting_down.wait(timeout=STREAMER_STARTUP_DELAY_S):
            logger.info("Stream loop exited during pre-connect delay")
            return
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
                    # Pick apart the 429 — slot-held vs request-rate need
                    # different backoff strategies and we want the body
                    # logged so we can tell them apart in production.
                    diag = _diagnose_429(resp)
                    if diag["is_slot_held"]:
                        # X edge holds a stale per-app slot. Per X docs,
                        # the slot can take "many minutes to hours" to
                        # release and EVERY retry extends the lockout —
                        # so we wait long and retry sparingly.
                        wait = max(backoff, _TOO_MANY_CONNECTIONS_INITIAL_S)
                        logger.warning(
                            "Stream 429 TooManyConnections (X edge holds a stale slot) — "
                            "backing off %.0f s. body=%r",
                            wait, diag["body_head"][:200],
                        )
                    else:
                        # Standard request-rate 429. X recommends:
                        # (a) read `x-rate-limit-reset` (unix epoch) for the
                        #     actual window-end and wait until then, OR
                        # (b) exponential backoff from a 1-min floor.
                        # Use the header when it's a sensible value; else fall
                        # back to the doubling-backoff strategy.
                        reset_at = _parse_rl_reset(diag["rl_reset"])
                        if reset_at is not None:
                            now = time.time()
                            until_reset = max(0.0, reset_at - now) + 5.0  # +5s buffer
                            wait = max(min(until_reset, 1800.0), _RATE_LIMIT_INITIAL_S)
                            source = "rl_reset"
                        else:
                            wait = max(backoff, _RATE_LIMIT_INITIAL_S)
                            source = "exp_backoff"
                        logger.warning(
                            "Stream 429 rate-limited — backing off %.0f s (via %s). "
                            "rl_remaining=%s rl_reset=%s body=%r",
                            wait, source, diag["rl_remaining"], diag["rl_reset"],
                            diag["body_head"][:200],
                        )
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
