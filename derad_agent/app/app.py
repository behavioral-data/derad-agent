import base64
import hashlib
import hmac
import logging
import os
import threading
import time
from datetime import datetime

from flask import Flask, abort, jsonify, render_template, request, url_for
from markupsafe import escape

from derad_agent.app import events, metrics
from derad_agent.app.dedup import get_store
from derad_agent.app.events import MentionDrop, MentionEvent, log_mention_drop, log_mention_event
from derad_agent.app.utils import (
    fetch_tweet,
    generate_notes_html,
    generate_reply,
    get_index,
    index_loaded,
    post_reply,
    preload_index_async,
)
from derad_agent.app import poller as _poller
from derad_agent.llm.config import _require_env

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# App Insights: if the connection string is set (App Service injects it via
# Key Vault reference or app setting), wire up OpenTelemetry which auto-
# instruments Flask + logging + requests. Silent no-op when unset, so tests
# and local dev don't need an Azure backend. Also skipped under pytest so a
# stray .env doesn't leak global OTel state across tests.
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") and not os.getenv("PYTEST_CURRENT_TEST"):
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor()
        logger.info("Application Insights instrumentation enabled")
    except Exception:
        logger.exception("Application Insights init failed; continuing without telemetry")

app = Flask(__name__)

CONSUMER_SECRET = _require_env("X_API_SECRET")

# Required so url_for(_external=True) works from the background thread that
# builds the /info link. Setting this at boot surfaces deploy misconfiguration
# now instead of on the first successful reply.
app.config["SERVER_NAME"] = _require_env("SERVER_NAME")
app.config["PREFERRED_URL_SCHEME"] = os.getenv("PREFERRED_URL_SCHEME", "https")

BOT_HANDLE_BY_TONE = {
    "agreeable": os.getenv("BOT_HANDLE_AGREEABLE", "aggie_bot"),
    "neutral": os.getenv("BOT_HANDLE_NEUTRAL", "nellie_bot"),
    "satirical": os.getenv("BOT_HANDLE_SATIRICAL", "eddie_bot"),
}

# Self-reply loop guard: compare incoming tweet's author against the bot's own
# user id (resolved from env to avoid an X API call on every worker boot).
BOT_USER_ID_BY_TONE = {
    "agreeable": os.getenv("BOT_USER_ID_AGREEABLE"),
    "neutral": os.getenv("BOT_USER_ID_NEUTRAL"),
    "satirical": os.getenv("BOT_USER_ID_SATIRICAL"),
}

# Reverse lookup for /mentions routing. One X dev app under PPU caps webhooks
# at 1 URL + 3 subscriptions, so all three bots' events land on the same
# endpoint; we route by event.for_user_id → tone.
TONE_BY_USER_ID = {
    user_id: tone
    for tone, user_id in BOT_USER_ID_BY_TONE.items()
    if user_id
}

# Sources follow-up tweet defaults to OFF — URL posts cost $0.200 each on
# X PPU (13× the $0.015 plain-text rate). Turn on per study arm.
POST_SOURCES_TWEET = os.getenv("DERAD_POST_SOURCES_TWEET", "false").lower() == "true"

RESTRICT_TO_REGISTERED = os.getenv("DERAD_RESTRICT_TO_REGISTERED", "true").lower() == "true"
ALLOWED_AUTHOR_IDS = {
    a.strip() for a in os.getenv("DERAD_ALLOWED_AUTHOR_IDS", "").split(",") if a.strip()
}
RATE_LIMIT_PER_SEC = int(os.getenv("DERAD_RATE_LIMIT_PER_SEC", "3"))
TWEET_LIMIT = 280
INGEST_MODE = _poller.INGEST_MODE


def _crc_response(crc_token: str) -> dict:
    digest = hmac.new(
        CONSUMER_SECRET.encode("utf-8"),
        crc_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return {"response_token": "sha256=" + base64.b64encode(digest).decode("utf-8")}


def _verify_signature(raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = header[len("sha256="):]
    digest = hmac.new(
        CONSUMER_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, computed)


def _is_self_reply(tweet: dict, tone: str) -> bool:
    """Return True if the tweet should be skipped as a (possible) self-reply.

    Fails closed: if BOT_USER_ID_<TONE> is unset, we cannot verify, so we treat
    every mention as a potential self-reply and skip it. Misconfiguration is
    visible in logs as a steady stream of skips instead of an unbounded chain.
    """
    bot_id = BOT_USER_ID_BY_TONE.get(tone)
    if not bot_id:
        logger.warning("BOT_USER_ID for tone=%s is unset — skipping mention as self-reply fail-closed", tone)
        return True
    return (tweet.get("user") or {}).get("id_str") == bot_id


def _build_info_url(reply_id: str, tone: str, tweet_ids, note_ids) -> str:
    return url_for(
        "info",
        reply_id=reply_id,
        tone=tone,
        tweet_id=tweet_ids,
        note_id=note_ids,
        _external=True,
    )


def _build_sources_text(reply: dict, info_url: str) -> str:
    parts = ["Sources:"]
    parts.extend(reply["sources"] or [])
    parts.append(f"More Info: {info_url}")
    return "\n".join(parts)


def _fit_sources_text(sources: list[str], info_url: str, limit: int = TWEET_LIMIT) -> str:
    """Build the sources tweet, dropping URLs from the end until it fits.

    The More Info link is preserved — it's the link readers actually need.
    """
    kept = list(sources or [])
    while True:
        text = _build_sources_text({"sources": kept}, info_url)
        if len(text) <= limit or not kept:
            if len(text) > limit:
                logger.warning(
                    "Sources tweet still %d chars after dropping all URLs — X will reject",
                    len(text),
                )
            return text
        kept.pop()


def _author_username(tweet: dict) -> str | None:
    """Pull the mention author's username from the webhook payload.

    The X webhook payload carries ``user.screen_name`` (v1 style) or
    ``user.username`` (v2 style); we accept either rather than assume.
    """
    user = tweet.get("user") or {}
    return user.get("screen_name") or user.get("username")


def process_mention(tone: str, tweet: dict, received_at_utc: datetime) -> None:
    """Run the pipeline, post the reply, and write the event row.

    Runs in a background thread. The MentionEvent row is written at every
    terminal point so we can reconstruct what happened to every accepted
    mention.
    """
    mention_id = tweet.get("id_str") or ""
    parent_id = tweet.get("in_reply_to_status_id_str") or ""
    author_id = (tweet.get("user") or {}).get("id_str") or ""
    author_username = _author_username(tweet)

    pipeline_start_utc = events.utcnow()
    t0 = time.monotonic()

    ev = MentionEvent(
        mention_id=mention_id,
        parent_id=parent_id,
        author_id=author_id,
        tone=tone,
        received_at_utc=received_at_utc,
        pipeline_start_utc=pipeline_start_utc,
        author_username=author_username,
    )

    def _finalize(outcome: str, exc: BaseException | None = None) -> None:
        ev.outcome = outcome
        ev.pipeline_ms = int((time.monotonic() - t0) * 1000)
        if exc is not None:
            ev.error_class = type(exc).__name__
            ev.error_detail = str(exc)[:1000]
        log_mention_event(ev)
        metrics.replies_posted.add(1, {"tone": tone, "outcome": outcome})
        metrics.pipeline_latency_ms.record(ev.pipeline_ms, {"tone": tone, "outcome": outcome})

    try:
        snap = fetch_tweet(parent_id, tone=tone)
        if snap is None or not snap.text:
            logger.info("Parent tweet %s unreachable; skipping mention %s", parent_id, mention_id)
            _finalize("parent_fetch_failed")
            return
        ev.parent_text = snap.text
        ev.parent_author_id = snap.author_id
        ev.parent_author_username = snap.author_username

        reply = generate_reply(statement=snap.text, exclude_tweet_id=parent_id, tone=tone)
        ev.queries = reply.get("queries") or []
        ev.cited_tweet_ids = reply.get("all_cited_tweet_ids") or []
        ev.cited_note_ids = reply.get("all_cited_note_ids") or []

        if not reply.get("text"):
            logger.info("Empty reply text for mention %s; skipping", mention_id)
            _finalize("empty_reply")
            return
        ev.reply_text = reply["text"]

        reply_id = post_reply(parent_id=mention_id, reply_text=reply["text"], tone=tone)
        if reply_id is None:
            _finalize("x_post_error")
            return
        ev.reply_id = reply_id
        ev.reply_posted_utc = events.utcnow()

        sources_outcome = "replied"
        if POST_SOURCES_TWEET and reply.get("sources"):
            with app.app_context():
                info_url = _build_info_url(reply_id, tone, reply["tweets"], reply["notes"])
            sources_text = _fit_sources_text(reply["sources"], info_url)
            ev.sources_reply_id = post_reply(parent_id=reply_id, reply_text=sources_text, tone=tone)
            if ev.sources_reply_id is None:
                # Main reply went out fine; sources follow-up failed. Mark it so
                # we can tell this apart from a reply that had no sources at all.
                sources_outcome = "replied_no_sources"

        _finalize(sources_outcome)

    except Exception as exc:
        logger.exception("Pipeline failed for mention %s (tone=%s)", mention_id, tone)
        _finalize("pipeline_error", exc=exc)


def _route_tone_from_event(event: dict) -> str | None:
    """Map an Account Activity event to the bot tone it's destined for.

    The v2 webhook payload puts ``for_user_id`` at the top level alongside
    ``tweet_create_events``; it identifies which subscribed user the event
    is for. We translate to tone via TONE_BY_USER_ID.
    """
    for_user_id = event.get("for_user_id") if isinstance(event, dict) else None
    if for_user_id is None:
        return None
    return TONE_BY_USER_ID.get(str(for_user_id))


def _dispatch_tweet(tone: str, tweet: dict, received_at_utc: datetime) -> bool:
    """Apply the guard chain and, if accepted, start a pipeline thread.

    Called by the webhook handler (via mention()) and the polling worker.
    Returns True if the tweet was accepted and a pipeline thread started.

    All guard logic lives here so both code paths share identical filtering —
    no risk of the poller bypassing a guard that the webhook checks.
    """
    metrics.mentions_received.add(1, {"tone": tone})

    def _drop(reason: str, **drop_kwargs):
        log_mention_drop(MentionDrop(
            drop_reason=reason, received_at_utc=received_at_utc, tone=tone, **drop_kwargs,
        ))
        metrics.mentions_dropped.add(1, {"tone": tone, "reason": reason})

    mention_id = tweet.get("id_str")
    parent_id = tweet.get("in_reply_to_status_id_str")
    author_id = (tweet.get("user") or {}).get("id_str")

    if not mention_id or not parent_id:
        _drop("no_parent", mention_id=mention_id, author_id=author_id,
              extra={"has_mention_id": bool(mention_id), "has_parent_id": bool(parent_id)})
        return False

    if _is_self_reply(tweet, tone):
        logger.info("Skipping self-reply %s (tone=%s)", mention_id, tone)
        _drop("self_reply", mention_id=mention_id, author_id=author_id)
        return False

    if RESTRICT_TO_REGISTERED and author_id not in ALLOWED_AUTHOR_IDS:
        logger.info("Skipping mention %s from unregistered author %s", mention_id, author_id)
        _drop("unregistered", mention_id=mention_id, author_id=author_id)
        return False

    store = get_store()
    if not store.claim(mention_id, ttl_seconds=86400):
        logger.info("Duplicate mention %s ignored", mention_id)
        _drop("duplicate", mention_id=mention_id, author_id=author_id)
        return False

    hits = store.hit_and_count(f"author:{author_id}", window_seconds=1)
    if hits > RATE_LIMIT_PER_SEC:
        logger.info("Rate-limited mention %s from author %s (hits=%d)", mention_id, author_id, hits)
        _drop("rate_limit", mention_id=mention_id, author_id=author_id, extra={"hits": hits})
        return False

    # Daily cap: defense in depth against runaway loops. Counted in-memory
    # per tone; resets on UTC date rollover. Lose-on-restart is acceptable.
    if metrics.daily_cap_reached(tone):
        _drop("daily_cap", mention_id=mention_id, author_id=author_id)
        return False

    logger.info(
        "Accepted mention %s (tone=%s, author=%s, parent=%s)",
        mention_id, tone, author_id, parent_id,
    )
    metrics.mentions_accepted.add(1, {"tone": tone})
    # daemon=False: lets gunicorn's --graceful-timeout drain in-flight pipelines
    # on SIGTERM. We've already claimed mention_id in the dedup store, so X
    # won't redeliver — dropping the thread would silently lose the reply.
    threading.Thread(
        target=process_mention,
        args=(tone, tweet, received_at_utc),
        daemon=False,
    ).start()
    return True


def mention(tone: str, event: dict, received_at_utc: datetime):
    """Unwrap a webhook event and hand off to _dispatch_tweet."""

    def _drop(reason: str, **drop_kwargs):
        log_mention_drop(MentionDrop(
            drop_reason=reason, received_at_utc=received_at_utc, tone=tone, **drop_kwargs,
        ))
        metrics.mentions_dropped.add(1, {"tone": tone, "reason": reason})

    tweet_events = event.get("tweet_create_events")
    if not isinstance(tweet_events, list) or not tweet_events:
        # Account-activity event types other than tweet_create_events
        # (favorites, follows, etc.) land here. We don't log every one — too
        # noisy — but if the field is the wrong shape (a string, say), log it.
        if tweet_events is not None and not isinstance(tweet_events, list):
            _drop("invalid_payload", extra={
                "why": "tweet_create_events_not_list",
                "type": type(tweet_events).__name__,
            })
        return "", 200

    tweet = tweet_events[0]
    if not isinstance(tweet, dict):
        _drop("invalid_payload", extra={"why": "tweet_not_dict", "type": type(tweet).__name__})
        return "", 200

    _dispatch_tweet(tone, tweet, received_at_utc)
    return "", 200


@app.route("/mentions", methods=["GET", "POST"])
def mentions_endpoint():
    """Single Account Activity webhook for all three bots.

    GET serves the CRC handshake. POST verifies the signature, routes by
    ``for_user_id`` to the right tone, then hands off to ``mention()``.
    """
    if request.method == "GET":
        crc_token = request.args.get("crc_token")
        if not crc_token:
            return "Missing crc_token", 400
        return jsonify(_crc_response(crc_token)), 200

    raw = request.get_data()
    if not _verify_signature(raw, request.headers.get("X-Twitter-Webhooks-Signature")):
        logger.warning("Rejected POST to /mentions: invalid signature")
        abort(403)

    if INGEST_MODE != "webhooks":
        # Polling mode: accept the delivery so X doesn't mark the URL as
        # unhealthy, but don't process it — the poller handles ingestion.
        return "", 200

    received_at_utc = events.utcnow()
    event = request.get_json(silent=True) or {}

    if not isinstance(event, dict):
        log_mention_drop(MentionDrop(
            drop_reason="invalid_payload", received_at_utc=received_at_utc,
            extra={"why": "event_not_dict"},
        ))
        metrics.mentions_dropped.add(1, {"tone": "unknown", "reason": "invalid_payload"})
        return "", 200

    tone = _route_tone_from_event(event)
    if tone is None:
        # Either no for_user_id, or its value doesn't map to any configured
        # bot. Could also be a non-mention event type (follow, DM, etc.).
        # Quiet drop; the event delivery itself already cost us money.
        for_user_id = event.get("for_user_id")
        log_mention_drop(MentionDrop(
            drop_reason="unknown_target_user", received_at_utc=received_at_utc,
            extra={"for_user_id": str(for_user_id) if for_user_id is not None else None,
                   "event_keys": sorted(event.keys())},
        ))
        metrics.mentions_dropped.add(1, {"tone": "unknown", "reason": "unknown_target_user"})
        return "", 200

    return mention(tone, event, received_at_utc)


@app.route("/info", methods=["GET"])
def info():
    reply_id = request.args.get("reply_id", "")
    tweet_ids = request.args.getlist("tweet_id")
    note_ids = request.args.getlist("note_id")
    tone = request.args.get("tone", "neutral")
    bot_handle = BOT_HANDLE_BY_TONE.get(tone, "i")

    safe_reply_id = escape(reply_id)
    safe_handle = escape(bot_handle)
    reply_html = (
        '<blockquote class="twitter-tweet">'
        f'<a href="https://twitter.com/{safe_handle}/status/{safe_reply_id}"></a>'
        '</blockquote>'
    )
    # Degrade gracefully during the ~30 s startup preload: render the embed
    # without the notes carousel rather than blocking the request thread on
    # the index lock and risking a worker timeout.
    if index_loaded():
        notes_html = generate_notes_html(tweet_ids, note_ids, bot_handle=bot_handle)
    else:
        notes_html = ""
    return render_template("info.html", reply=reply_html, notes=notes_html), 200


@app.route("/healthz", methods=["GET"])
def healthz():
    """Cheap readiness probe — does not force the index to load."""
    return jsonify({"ok": True, "index_loaded": index_loaded()}), 200


preload_index_async()
_poller.start_poller(_dispatch_tweet)
