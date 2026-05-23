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
from derad_agent.llm.config import _parse_bool_env, _require_env

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

POST_SOURCES_TWEET = _parse_bool_env("DERAD_POST_SOURCES_TWEET")
DRY_RUN = _parse_bool_env("DERAD_DRY_RUN")
RATE_LIMIT_PER_SEC = int(os.getenv("DERAD_RATE_LIMIT_PER_SEC", "3"))
USER_DAILY_CAP = int(os.getenv("DERAD_USER_DAILY_CAP", "20"))  # mentions/UTC-day/user; 0 disables
TWEET_LIMIT = 280

# Participant metadata loaded for study tracking only — no longer gates bot access.
_participants_store = _participants.get_store()
_PARTICIPANTS_BY_ID: dict[str, _participants.Participant] = {
    p.author_id: p for p in _participants_store.list_all()
}
logger.info("Loaded %d registered participants (metadata only)", len(_PARTICIPANTS_BY_ID))


# Info-URL store: token → {tone, reply_text, reasons, parent_id}
# In-memory cache for fast reads; Azure Tables for durable persistence across restarts.
_INFO_STORE: dict[str, dict] = {}
_INFO_STORE_LOCK = threading.Lock()
_INFO_STORE_TTL = 86400  # 24 h memory cache; Tables holds tokens permanently

_INFO_TABLE_UNINIT = object()  # sentinel: init not yet attempted
_info_table_client = _INFO_TABLE_UNINIT
_info_table_init_lock = threading.Lock()


def _get_info_table():
    """Return an Azure Table client for InfoTokens, or None if Tables not configured."""
    global _info_table_client
    if _info_table_client is not _INFO_TABLE_UNINIT:
        return _info_table_client  # None means init was tried and failed
    with _info_table_init_lock:
        if _info_table_client is not _INFO_TABLE_UNINIT:
            return _info_table_client
        if os.getenv("DERAD_EVENTS_BACKEND", "memory").lower() != "tables":
            _info_table_client = None
            return None
        endpoint = os.getenv("DERAD_TABLES_ENDPOINT")
        if not endpoint:
            _info_table_client = None
            return None
        try:
            from azure.core.exceptions import ResourceExistsError
            from azure.data.tables import TableServiceClient
            from azure.identity import DefaultAzureCredential
            svc = TableServiceClient(endpoint=endpoint, credential=DefaultAzureCredential())
            try:
                svc.create_table("InfoTokens")
                logger.info("Created InfoTokens table")
            except ResourceExistsError:
                pass
            _info_table_client = svc.get_table_client("InfoTokens")
        except Exception:
            logger.exception("InfoTokens table init failed; tokens will be in-memory only")
            _info_table_client = None
    return _info_table_client


def _make_info_token(
    tone: str,
    reply_text: str,
    reasons: list,
    *,
    parent_id: str = "",
    parent_author_username: str = "",
) -> str:
    token = secrets.token_urlsafe(6)
    payload = {
        "tone": tone,
        "reply_text": reply_text,
        "reasons": reasons,
        "parent_id": parent_id,
        "parent_author_username": parent_author_username,
        "_ts": time.monotonic(),
    }
    with _INFO_STORE_LOCK:
        _INFO_STORE[token] = payload

    def _persist():
        table = _get_info_table()
        if table is None:
            return
        # Azure Tables enforces a 64 KiB cap per string property. Verbose notes
        # can blow past this and silently lose the persisted token. Stay well
        # under the limit; truncate the longest field first, then bail out
        # entirely if it's still too large.
        reasons_json = json.dumps(reasons, ensure_ascii=False)
        _LIMIT = 60_000
        if len(reasons_json.encode("utf-8")) > _LIMIT:
            logger.warning(
                "reasons_json for token %s exceeds %d bytes; truncating note_text fields",
                token, _LIMIT,
            )
            truncated = []
            for r in reasons or []:
                if isinstance(r, dict):
                    r2 = dict(r)
                    nt = r2.get("note_text")
                    if isinstance(nt, str) and len(nt) > 500:
                        r2["note_text"] = nt[:500]
                    truncated.append(r2)
                else:
                    truncated.append(r)
            reasons_json = json.dumps(truncated, ensure_ascii=False)
            if len(reasons_json.encode("utf-8")) > _LIMIT:
                logger.error(
                    "reasons_json for token %s still exceeds %d bytes after truncation; "
                    "persisting empty reasons list",
                    token, _LIMIT,
                )
                reasons_json = "[]"
        try:
            table.upsert_entity({
                "PartitionKey": "info",
                "RowKey": token,
                "tone": tone,
                "reply_text": reply_text,
                "reasons_json": reasons_json,
                "parent_id": parent_id,
                "parent_author_username": parent_author_username,
                "created_at": datetime.now(timezone.utc),
            })
        except Exception:
            logger.exception("Failed to persist info token %s to Azure Tables", token)

    threading.Thread(target=_persist, daemon=False, name=f"info-persist-{token}").start()
    return token


def _get_info_params(token: str) -> dict | None:
    """Look up a token: memory first, then Azure Tables on cache miss."""
    with _INFO_STORE_LOCK:
        params = _INFO_STORE.get(token)
    if params is not None:
        return params
    table = _get_info_table()
    if table is None:
        return None
    try:
        entity = table.get_entity("info", token)
        params = {
            "tone": entity.get("tone", ""),
            "reply_text": entity.get("reply_text", ""),
            "reasons": json.loads(entity.get("reasons_json", "[]")),
            "parent_id": entity.get("parent_id", ""),
            "parent_author_username": entity.get("parent_author_username", ""),
            "reply_id": entity.get("reply_id", ""),
            "_ts": time.monotonic(),
        }
        with _INFO_STORE_LOCK:
            _INFO_STORE[token] = params
        return params
    except Exception as exc:
        from azure.core.exceptions import ResourceNotFoundError
        if isinstance(exc, ResourceNotFoundError):
            logger.debug("Info token %s not found in Azure Tables", token)
        else:
            logger.warning("Azure Tables lookup failed for token %s: %s", token, exc)
        return None


def _update_info_token(token: str, **fields) -> None:
    """Merge extra fields into an existing token (e.g. reply_id after posting)."""
    with _INFO_STORE_LOCK:
        if token in _INFO_STORE:
            _INFO_STORE[token].update(fields)

    def _persist_update():
        table = _get_info_table()
        if table is None:
            return
        try:
            # upsert (merge) so this is safe even if the initial persist thread
            # hasn't completed yet — whichever thread wins, the other merges in.
            table.upsert_entity({"PartitionKey": "info", "RowKey": token, **fields})
        except Exception:
            logger.exception("Failed to update info token %s in Azure Tables", token)

    threading.Thread(target=_persist_update, daemon=False, name=f"info-update-{token}").start()


def _evict_info_store() -> None:
    while True:
        time.sleep(600)
        cutoff = time.monotonic() - _INFO_STORE_TTL
        with _INFO_STORE_LOCK:
            stale = [k for k, v in _INFO_STORE.items() if v.get("_ts", 0) < cutoff]
            for k in stale:
                del _INFO_STORE[k]
        if stale:
            logger.debug("Evicted %d stale info tokens from memory cache", len(stale))


# Gunicorn forks workers AFTER module import, and threads do not survive fork.
# Starting the evictor at module level means forked workers never evict — the
# in-memory _INFO_STORE grows unbounded. Defer the start to first request via
# a before_request hook so each worker gets its own evictor.
_evictor_started = False
_evictor_start_lock = threading.Lock()


def _ensure_evictor_started() -> None:
    global _evictor_started
    if _evictor_started:
        return
    with _evictor_start_lock:
        if _evictor_started:
            return
        # Double-check by name in case another path already started it in this process.
        if any(t.name == "info-store-evictor" for t in threading.enumerate()):
            _evictor_started = True
            return
        threading.Thread(
            target=_evict_info_store,
            daemon=True,
            name="info-store-evictor",
        ).start()
        _evictor_started = True


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


def _is_self_reply(tweet: dict, tone: str) -> tuple[bool, str]:
    """Return ``(should_drop, reason)``.

    ``reason`` is ``"self_reply"`` when the mention author matches the bot's
    configured user id, or ``"self_reply_unconfigured"`` when the bot id env
    var is unset (we still fail closed but tag the drop so analytics can
    distinguish misconfiguration from genuine self-replies).
    """
    bot_id = BOT_USER_ID_BY_TONE.get(tone)
    if not bot_id:
        logger.warning(
            "BOT_USER_ID for tone=%s is unset — skipping mention as self-reply fail-closed",
            tone,
        )
        return True, "self_reply_unconfigured"
    if (tweet.get("user") or {}).get("id_str") == bot_id:
        return True, "self_reply"
    return False, "self_reply"


# Startup warning for missing BOT_USER_ID env vars — surfaces misconfiguration
# before the first mention silently fails closed.
for _tone, _bot_id in BOT_USER_ID_BY_TONE.items():
    if not _bot_id:
        logger.warning(
            "BOT_USER_ID_%s is unset at import time — all mentions for tone=%s "
            "will be dropped with reason=self_reply_unconfigured",
            _tone.upper(), _tone,
        )


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
    """Append url on its own line, truncating text with ellipsis if needed to stay within limit."""
    budget = limit - _X_URL_LEN - 1  # -1 for the newline
    if len(text) > budget:
        text = text[:budget - 1] + "…"
    return f"{text}\n{url}"


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

        # Distinguish grounded factcheck replies from no-factcheck fallbacks for
        # downstream research analysis. reasons_detail is populated only when the
        # reply was grounded in Community Notes.
        ev.reply_type = "factcheck" if reply.get("reasons_detail") else "no_factcheck"

        token = _make_info_token(
            tone,
            reply["text"],
            reply.get("reasons_detail") or [],
            parent_id=parent_id,
            parent_author_username=ev.parent_author_username or "",
        )
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
        _update_info_token(token, reply_id=reply_id)
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

    is_self, self_reason = _is_self_reply(tweet, tone)
    if is_self:
        logger.info("Skipping self-reply %s (tone=%s reason=%s)", mention_id, tone, self_reason)
        _drop(self_reason, mention_id=mention_id, author_id=author_id)
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

    if USER_DAILY_CAP > 0:
        day = datetime.now(timezone.utc).date().isoformat()
        day_hits = store.hit_and_count(f"author_day:{author_id}:{day}", window_seconds=86400)
        if day_hits > USER_DAILY_CAP:
            logger.info(
                "User daily cap reached for author %s on %s (hits=%d, cap=%d)",
                author_id, day, day_hits, USER_DAILY_CAP,
            )
            _drop(
                "daily_cap", mention_id=mention_id, author_id=author_id,
                extra={"day_hits": day_hits, "cap": USER_DAILY_CAP},
            )
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


@app.before_request
def _before_request_start_evictor():
    """Lazily start the info-store evictor on the first request in this worker
    process. Module-level start does not survive gunicorn fork; this hook
    ensures every worker has exactly one evictor thread."""
    _ensure_evictor_started()


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
    params = _get_info_params(token)
    if params is None:
        return render_template("info.html"), 404
    tone = params.get("tone", "")
    return render_template(
        "info.html",
        headline=params.get("reply_text", ""),
        reasons=params.get("reasons", []),
        tone=tone,
        bot_handle=BOT_HANDLE_BY_TONE.get(tone, ""),
        parent_id=params.get("parent_id", ""),
        parent_author_username=params.get("parent_author_username", ""),
        reply_tweet_id=params.get("reply_id", ""),
    ), 200


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


@app.route("/api/replies", methods=["GET"])
def api_replies():
    """Recent bot replies, newest first. One row per posted reply.

    Each row carries enough context to render in the dashboard *and* to link
    to the live tweet on X. `reply_url` is built server-side from the bot
    handle for the event's tone + reply_id so the client doesn't need to
    duplicate the handle map.

    Query params: ?limit=N (default 200, max 1000).
    """
    try:
        limit = max(1, min(int(request.args.get("limit", "200")), 1000))
    except ValueError:
        limit = 200

    raw = events.get_store().list_recent(limit)
    out: list[dict] = []
    for e in raw:
        reply_id = e.get("reply_id")
        if not reply_id:
            continue  # not a successful reply — skip
        tone = (e.get("tone") or "").lower()
        bot_handle = BOT_HANDLE_BY_TONE.get(tone) or ""
        reply_url = (
            f"https://twitter.com/{bot_handle}/status/{reply_id}"
            if bot_handle else ""
        )
        out.append({
            "reply_id": reply_id,
            "reply_url": reply_url,
            "bot_handle": bot_handle,
            "tone": tone,
            "received_at_utc": e.get("received_at_utc"),
            "author_id": e.get("author_id"),
            "author_username": e.get("author_username"),
            "study_code": e.get("study_code"),
            "study_day": e.get("study_day"),
            "reply_text": e.get("reply_text"),
            "parent_text": e.get("parent_text"),
        })
    return jsonify({"replies": out}), 200


def _serialize_participant(p: _participants.Participant) -> dict:
    return {
        "author_id": p.author_id,
        "author_username": p.author_username,
        "tone": p.tone,
        "enrolled_at_utc": p.enrolled_at_utc.isoformat() if p.enrolled_at_utc else None,
        "notes": p.notes,
    }


@app.route("/api/participants", methods=["GET"])
def api_participants_list():
    parts = _participants.get_store().list_all()
    parts.sort(key=lambda p: p.enrolled_at_utc or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return jsonify({"participants": [_serialize_participant(p) for p in parts]}), 200


@app.route("/api/participants", methods=["POST"])
def api_participants_create():
    """Register a participant by @handle. Body: {username, notes?}.

    Looks up the X numeric user ID and persists the handle. Bot tone is
    assigned manually outside this system, so no tone is stored here.
    Updates the in-process cache so events from the newly-registered
    participant in *this worker* are tagged immediately; other gunicorn
    workers pick up the registration from Azure Tables when they next
    look up the author.
    """
    payload = request.get_json(silent=True) or {}
    username_raw = (payload.get("username") or "").strip()
    notes = (payload.get("notes") or "").strip()

    if not username_raw:
        return jsonify({"error": "username is required"}), 400

    clean_username = username_raw.lstrip("@")

    try:
        author_id = _participants.lookup_author_id(clean_username)
    except _participants.ParticipantLookupError as exc:
        return jsonify({"error": str(exc)}), 422

    registered_at = datetime.now(timezone.utc)

    p = _participants.Participant(
        author_id=author_id,
        author_username=clean_username,
        tone="",
        enrolled_at_utc=registered_at,
        notes=notes,
    )
    _participants.get_store().register(p)
    _PARTICIPANTS_BY_ID[author_id] = p
    logger.info(
        "Registered participant via dashboard: @%s id=%s",
        clean_username, author_id,
    )
    return jsonify({"participant": _serialize_participant(p)}), 201


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
