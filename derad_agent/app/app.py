import hashlib
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request, stream_with_context, url_for
from markupsafe import escape

from derad_agent.app import events, metrics
from derad_agent.app.dedup import get_store
from derad_agent.app.events import MentionDrop, MentionEvent, log_mention_drop, log_mention_event
from derad_agent.app import participants as _participants
from derad_agent.app.utils import (
    fetch_tweet,
    generate_notes_html,
    generate_reply,
    index_loaded,
    post_reply,
    preload_index_async,
)
from derad_agent.app import streamer as _streamer
from derad_agent.llm.config import _require_env

_LOG_FILE = os.getenv("DERAD_LOG_FILE", "/tmp/derad_stream.log")
_log_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
_root = logging.getLogger()
_root.setLevel(os.getenv("LOG_LEVEL", "INFO"))
_sh = logging.StreamHandler()
_sh.setFormatter(_log_fmt)
_root.addHandler(_sh)
try:
    _fh = logging.FileHandler(_LOG_FILE)
    _fh.setFormatter(_log_fmt)
    _root.addHandler(_fh)
except OSError:
    pass  # non-writable filesystem (e.g. read-only container layer) — stdout only
for _noisy in ("azure.core", "azure.monitor", "azure.identity"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") and not os.getenv("PYTEST_CURRENT_TEST"):
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor()
        logger.info("Application Insights instrumentation enabled")
    except Exception:
        logger.exception("Application Insights init failed; continuing without telemetry")

app = Flask(__name__)

app.config["SERVER_NAME"] = _require_env("SERVER_NAME")
app.config["PREFERRED_URL_SCHEME"] = os.getenv("PREFERRED_URL_SCHEME", "https")

BOT_HANDLE_BY_TONE = {
    "agreeable": os.getenv("BOT_HANDLE_AGREEABLE", "aggiexbot"),
    "neutral": os.getenv("BOT_HANDLE_NEUTRAL", "nelliexbot"),
    "satirical": os.getenv("BOT_HANDLE_SATIRICAL", "eddiexbot"),
}

BOT_USER_ID_BY_TONE = {
    "agreeable": os.getenv("BOT_USER_ID_AGREEABLE"),
    "neutral": os.getenv("BOT_USER_ID_NEUTRAL"),
    "satirical": os.getenv("BOT_USER_ID_SATIRICAL"),
}

POST_SOURCES_TWEET = os.getenv("DERAD_POST_SOURCES_TWEET", "false").lower() == "true"
DRY_RUN = os.getenv("DERAD_DRY_RUN", "false").lower() == "true"

RESTRICT_TO_REGISTERED = os.getenv("DERAD_RESTRICT_TO_REGISTERED", "true").lower() == "true"
# Dev/test escape hatch — production allow-list comes from the Participants table.
ALLOWED_AUTHOR_IDS = {
    a.strip() for a in os.getenv("DERAD_ALLOWED_AUTHOR_IDS", "").split(",") if a.strip()
}
RATE_LIMIT_PER_SEC = int(os.getenv("DERAD_RATE_LIMIT_PER_SEC", "3"))
TWEET_LIMIT = 280

# Load participants at startup so allow-list checks and study metadata lookups are
# pure in-memory. Restart the app to pick up newly registered participants.
_participants_store = _participants.get_store()
_PARTICIPANTS_BY_ID: dict[str, _participants.Participant] = {
    p.author_id: p for p in _participants_store.list_all()
}
_ALLOWED_IDS: set[str] = set(_PARTICIPANTS_BY_ID) | ALLOWED_AUTHOR_IDS
logger.info("Loaded %d registered participants", len(_PARTICIPANTS_BY_ID))


# Short info-URL store: token → {tone, tweet_ids, note_ids}
# Tokens are ephemeral (lost on restart); users click within minutes of receiving a reply.
_INFO_STORE: dict[str, dict] = {}
_INFO_STORE_LOCK = threading.Lock()


def _make_info_token(tone: str, tweet_ids: list, note_ids: list) -> str:
    token = secrets.token_urlsafe(6)  # 8-char URL-safe string
    with _INFO_STORE_LOCK:
        _INFO_STORE[token] = {"tone": tone, "tweet_ids": tweet_ids, "note_ids": note_ids}
    return token


# 4-letter study code derived deterministically from reply_id.
# Alphabet excludes I and O to avoid confusion when read aloud.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # 24 chars → 24^4 = 331,776 combinations


def _make_study_code(reply_id: str) -> str:
    n = int(hashlib.sha256(reply_id.encode()).hexdigest(), 16)
    base = len(_CODE_ALPHABET)
    code = []
    for _ in range(4):
        code.append(_CODE_ALPHABET[n % base])
        n //= base
    return "".join(code)


def _is_self_reply(tweet: dict, tone: str) -> bool:
    bot_id = BOT_USER_ID_BY_TONE.get(tone)
    if not bot_id:
        logger.warning("BOT_USER_ID for tone=%s is unset — skipping mention as self-reply fail-closed", tone)
        return True
    return (tweet.get("user") or {}).get("id_str") == bot_id


def _build_info_url(tone: str, tweet_ids, note_ids, reply_id: str = "") -> str:
    return url_for(
        "info",
        reply_id=reply_id,
        tone=tone,
        tweet_id=tweet_ids,
        note_id=note_ids,
        _external=True,
    )


# X shortens every URL to 23 chars via t.co regardless of actual length.
_X_URL_LEN = 23


def _append_url(text: str, url: str, limit: int = TWEET_LIMIT) -> str:
    """Append url to text, truncating text with ellipsis if needed to stay within limit."""
    budget = limit - _X_URL_LEN - 1  # -1 for the space
    if len(text) > budget:
        text = text[:budget - 1] + "…"
    return f"{text} {url}"


def _build_sources_text(reply: dict, info_url: str) -> str:
    parts = ["Sources:"]
    parts.extend(reply["sources"] or [])
    parts.append(f"More Info: {info_url}")
    return "\n".join(parts)


def _fit_sources_text(sources: list[str], info_url: str, limit: int = TWEET_LIMIT) -> str:
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
    user = tweet.get("user") or {}
    return user.get("screen_name") or user.get("username")


def process_mention(tone: str, tweet: dict, received_at_utc: datetime) -> None:
    """Run the pipeline, post the reply, and write the event row."""
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
        bot_author_id=BOT_USER_ID_BY_TONE.get(tone),
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
        if DRY_RUN:
            import re as _re
            raw_text = tweet.get("text", "")
            statement = _re.sub(r"@\w+\s*", "", raw_text).strip() or raw_text
            ev.parent_text = f"[dry-run] {statement}"
            logger.info("DRY_RUN: using mention text as statement: %r", statement)
        else:
            snap = fetch_tweet(parent_id, tone=tone)
            if snap is None or not snap.text:
                logger.info("Parent tweet %s unreachable; skipping mention %s", parent_id, mention_id)
                _finalize("parent_fetch_failed")
                return
            ev.parent_text = snap.text
            ev.parent_author_id = snap.author_id
            ev.parent_author_username = snap.author_username
            ev.parent_like_count = snap.like_count
            ev.parent_retweet_count = snap.retweet_count
            ev.parent_reply_count = snap.reply_count
            ev.parent_quote_count = snap.quote_count
            statement = snap.text

        reply = generate_reply(statement=statement, exclude_tweet_id=parent_id, tone=tone)
        ev.queries = reply.get("queries") or []
        ev.cited_tweet_ids = reply.get("all_cited_tweet_ids") or []
        ev.cited_note_ids = reply.get("all_cited_note_ids") or []

        if not reply.get("text"):
            logger.info("Empty reply text for mention %s; skipping", mention_id)
            _finalize("empty_reply")
            return

        tweet_ids = reply.get("tweets") or []
        note_ids = reply.get("notes") or []
        token = _make_info_token(tone, tweet_ids, note_ids)
        with app.app_context():
            info_url = url_for("info_short", token=token, _external=True)
        reply_text = _append_url(reply["text"], info_url)
        ev.reply_text = reply_text

        if DRY_RUN:
            logger.info("DRY_RUN reply (tone=%s): %s", tone, reply_text)
        else:
            logger.info("Posting reply (tone=%s): (text suppressed)", tone)

        if DRY_RUN:
            _finalize("dry_run")
            return

        reply_id = post_reply(parent_id=mention_id, reply_text=reply_text, tone=tone)
        if reply_id is None:
            _finalize("x_post_error")
            return
        ev.reply_id = reply_id
        ev.reply_posted_utc = events.utcnow()

        ev.study_code = _make_study_code(reply_id)
        participant = _PARTICIPANTS_BY_ID.get(author_id)
        if participant:
            ev.participant_id = author_id
            ev.study_day = (
                received_at_utc.date() - participant.enrolled_at_utc.date()
            ).days + 1

        sources_outcome = "replied"
        if POST_SOURCES_TWEET and reply.get("sources"):
            with app.app_context():
                info_url = _build_info_url(tone, reply.get("tweets") or [], reply.get("notes") or [], reply_id=reply_id)
            sources_text = _fit_sources_text(reply["sources"], info_url)
            ev.sources_reply_id = post_reply(parent_id=reply_id, reply_text=sources_text, tone=tone)
            if ev.sources_reply_id is None:
                sources_outcome = "replied_no_sources"

        _finalize(sources_outcome)

    except Exception as exc:
        logger.exception("Pipeline failed for mention %s (tone=%s)", mention_id, tone)
        _finalize("pipeline_error", exc=exc)


def _dispatch_tweet(tone: str, tweet: dict, received_at_utc: datetime) -> bool:
    """Apply the guard chain and, if accepted, start a pipeline thread."""
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

    if RESTRICT_TO_REGISTERED and author_id not in _ALLOWED_IDS:
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

    if metrics.daily_cap_reached(tone):
        _drop("daily_cap", mention_id=mention_id, author_id=author_id)
        return False

    logger.info(
        "Accepted mention %s (tone=%s, author=%s, parent=%s)",
        mention_id, tone, author_id, parent_id,
    )
    metrics.mentions_accepted.add(1, {"tone": tone})
    threading.Thread(
        target=process_mention,
        args=(tone, tweet, received_at_utc),
        daemon=False,
    ).start()
    return True


@app.route("/info", methods=["GET"])
def info():
    reply_id = request.args.get("reply_id", "")
    tweet_ids = request.args.getlist("tweet_id")
    note_ids = request.args.getlist("note_id")
    tone = request.args.get("tone", "neutral")
    bot_handle = BOT_HANDLE_BY_TONE.get(tone, "i")

    if reply_id:
        safe_reply_id = escape(reply_id)
        safe_handle = escape(bot_handle)
        reply_html = (
            '<blockquote class="twitter-tweet">'
            f'<a href="https://twitter.com/{safe_handle}/status/{safe_reply_id}"></a>'
            '</blockquote>'
        )
    else:
        reply_html = ""
    if index_loaded():
        notes_html = generate_notes_html(tweet_ids, note_ids, bot_handle=bot_handle)
    else:
        notes_html = ""
    return render_template("info.html", reply=reply_html, notes=notes_html), 200


@app.route("/i/<token>", methods=["GET"])
def info_short(token: str):
    with _INFO_STORE_LOCK:
        params = _INFO_STORE.get(token)
    if params is None:
        return render_template("info.html", reply="", notes=""), 404
    tone = params["tone"]
    tweet_ids = params["tweet_ids"]
    note_ids = params["note_ids"]
    bot_handle = BOT_HANDLE_BY_TONE.get(tone, "i")
    notes_html = generate_notes_html(tweet_ids, note_ids, bot_handle=bot_handle) if index_loaded() else ""
    return render_template("info.html", reply="", notes=notes_html), 200


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True, "index_loaded": index_loaded()}), 200


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/activity")
def api_activity():
    ev_store = events.get_store()
    recent_events = ev_store.list_recent(50)
    recent_drops = ev_store.list_recent_drops(20)

    outcome_counts: dict[str, int] = {}
    tone_counts: dict[str, int] = {"agreeable": 0, "neutral": 0, "satirical": 0}
    latencies: list[int] = []
    for ev in recent_events:
        outcome = ev.get("outcome") or "unknown"
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        tone = ev.get("tone") or ""
        if tone in tone_counts:
            tone_counts[tone] += 1
        ms = ev.get("pipeline_ms")
        if ms:
            latencies.append(int(ms))

    part_store = _participants.get_store()
    all_parts = part_store.list_all()
    participant_tone_counts = {"agreeable": 0, "neutral": 0, "satirical": 0}
    for p in all_parts:
        if p.tone in participant_tone_counts:
            participant_tone_counts[p.tone] += 1

    payload = {
        "events": recent_events,
        "drops": recent_drops,
        "metrics": {
            "total_events": len(recent_events),
            "outcome_counts": outcome_counts,
            "tone_counts": tone_counts,
            "avg_pipeline_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
            "participant_count": len(all_parts),
            "participant_tone_counts": participant_tone_counts,
            "dry_run": DRY_RUN,
            "stream_connected": _streamer.is_connected() if hasattr(_streamer, "is_connected") else None,
        },
    }
    return jsonify(payload), 200


_SSE_TTL = int(os.getenv("DERAD_SSE_TTL", "1800"))  # 30 min default; browser auto-reconnects


@app.route("/stream/logs")
def stream_logs():
    def _generate():
        deadline = time.monotonic() + _SSE_TTL
        # Backfill last 80 lines
        try:
            with open(_LOG_FILE, "r") as f:
                lines = f.readlines()
            for line in lines[-80:]:
                yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
        except FileNotFoundError:
            yield f"data: {json.dumps({'line': f'[log: {_LOG_FILE} not found — check DERAD_LOG_FILE]'})}\n\n"
            return

        # Tail new lines until TTL
        try:
            with open(_LOG_FILE, "r") as f:
                f.seek(0, 2)
                while time.monotonic() < deadline:
                    line = f.readline()
                    if line:
                        yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
                    else:
                        time.sleep(0.3)
                        yield ": keep-alive\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'line': f'[stream error: {exc}]'})}\n\n"

    resp = Response(stream_with_context(_generate()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


preload_index_async()
_streamer.start_streamer(_dispatch_tweet)
