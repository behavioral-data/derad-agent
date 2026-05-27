import hashlib
import json
import logging
import os
import random
import secrets
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, jsonify, render_template, request, stream_with_context, url_for
from markupsafe import escape

from agent.app import events, metrics
from agent.app.dedup import get_store
from agent.app.events import (
    InfoView,
    MentionDrop,
    MentionEvent,
    log_info_view,
    log_mention_drop,
    log_mention_event,
)
from agent.app import participants as _participants
from agent.app.participants import VALID_TONES
from agent.app.utils import (
    fetch_tweet,
    generate_reply,
    post_reply,
)
from agent.app import streamer as _streamer
from agent.llm.config import _parse_bool_env, _require_env

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
# Exporter export errors (e.g. network timeouts) log at ERROR — suppress them too
logging.getLogger("azure.monitor.opentelemetry.exporter").setLevel(logging.CRITICAL)
logger = logging.getLogger(__name__)

if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") and not os.getenv("PYTEST_CURRENT_TEST"):
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        # Use a short timeout so a blocked network path fails fast instead of
        # holding an exporter thread for the default 300 s.
        configure_azure_monitor(connection_timeout=5, read_timeout=10)
        logger.info("Application Insights instrumentation enabled")
    except Exception:
        logger.exception("Application Insights init failed; continuing without telemetry")

app = Flask(__name__)

app.config["SERVER_NAME"] = _require_env("SERVER_NAME")
app.config["PREFERRED_URL_SCHEME"] = os.getenv("PREFERRED_URL_SCHEME", "https")

BOT_HANDLE = os.getenv("BOT_HANDLE", "eddiexbot")
BOT_USER_ID = os.getenv("BOT_USER_ID") or None

DRY_RUN = _parse_bool_env("DERAD_DRY_RUN")
RATE_LIMIT_PER_SEC = int(os.getenv("DERAD_RATE_LIMIT_PER_SEC", "3"))
USER_DAILY_CAP = int(os.getenv("DERAD_USER_DAILY_CAP", "20"))  # mentions/UTC-day/user; 0 disables

# Cap concurrent fact-check pipelines so a mention burst can't blow through
# Foundry/Claude rate limits. Threads beyond the cap block on acquire; if a
# slot doesn't open in DERAD_PIPELINE_QUEUE_TIMEOUT_S the mention is dropped.
PIPELINE_MAX_CONCURRENT = int(os.getenv("DERAD_PIPELINE_MAX_CONCURRENT", "5"))
PIPELINE_QUEUE_TIMEOUT_S = float(os.getenv("DERAD_PIPELINE_QUEUE_TIMEOUT_S", "600"))
_PIPELINE_SEMAPHORE = threading.BoundedSemaphore(PIPELINE_MAX_CONCURRENT)

# Hard cap on the finalize-persist write so a degraded Tables backend can't
# hold a pipeline-semaphore slot forever.
_FINALIZE_TIMEOUT_S = float(os.getenv("DERAD_FINALIZE_TIMEOUT_S", "15"))

# Participant metadata loaded lazily via _lookup_participant's write-through
# cache (lines below). No eager preload — every gunicorn worker would
# otherwise repeat the same Tables scan at fork.
_participants_store = _participants.get_store()
_PARTICIPANTS_BY_ID: dict[str, _participants.Participant] = {}


def _warm_search_backend() -> None:
    """Pre-build the search-backend client at startup so the first mention
    doesn't pay DefaultAzureCredential chain resolution + AIProjectClient
    init on its critical path.
    """
    try:
        from agent.factcheck.search import build_default_backend
        backend = build_default_backend()
        # _ensure_client is the slow part; calling .search would trigger the
        # web_search tool which we don't want at startup.
        ensure = getattr(backend, "_ensure_client", None)
        if callable(ensure):
            ensure()
            logger.info("Search backend warmed up: %s", backend.name)
    except Exception:
        logger.warning("Search-backend warmup failed; first mention will pay the cost.", exc_info=True)


threading.Thread(target=_warm_search_backend, daemon=True, name="warm-search").start()


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
    info_payload: dict,
    *,
    parent_id: str = "",
    parent_author_username: str = "",
    bot_handle: str = "",
    mention_id: str = "",
    participant_id: str = "",
) -> str:
    """Create a /info token. `info_payload` is the rich projection of the
    FrozenVerdict (action + outcome + counterpoints + perspectives +
    sources + source_quality_table — see utils._info_payload_from_frozen).
    Persisted as one `payload_json` Tables column for read-back.
    """
    token = secrets.token_urlsafe(6)
    payload = {
        "tone": tone,
        "reply_text": reply_text,
        "info_payload": info_payload,
        "parent_id": parent_id,
        "parent_author_username": parent_author_username,
        "bot_handle": bot_handle,
        "mention_id": mention_id,
        "participant_id": participant_id,
        "_ts": time.monotonic(),
    }
    with _INFO_STORE_LOCK:
        _INFO_STORE[token] = payload

    def _persist():
        table = _get_info_table()
        if table is None:
            return
        # Tables enforces 64 KiB per string property. The structured payload
        # for an active mention is typically 4–10 KiB; trim source-quality
        # rationales first if we exceed the cap, then drop the source table
        # entirely as a last resort.
        payload_json = json.dumps(info_payload, ensure_ascii=False)
        _LIMIT = 60_000
        if len(payload_json.encode("utf-8")) > _LIMIT:
            logger.warning(
                "info_payload for token %s exceeds %d bytes; trimming rationales",
                token, _LIMIT,
            )
            trimmed = dict(info_payload)
            sqt = [
                {**row, "rationale": (row.get("rationale") or "")[:200]}
                for row in (trimmed.get("source_quality_table") or [])
            ]
            trimmed["source_quality_table"] = sqt
            payload_json = json.dumps(trimmed, ensure_ascii=False)
            if len(payload_json.encode("utf-8")) > _LIMIT:
                trimmed["source_quality_table"] = []
                payload_json = json.dumps(trimmed, ensure_ascii=False)
        try:
            table.upsert_entity({
                "PartitionKey": "info",
                "RowKey": token,
                "tone": tone,
                "reply_text": reply_text,
                "payload_json": payload_json,
                "parent_id": parent_id,
                "parent_author_username": parent_author_username,
                "bot_handle": bot_handle,
                "mention_id": mention_id,
                "participant_id": participant_id,
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
        # New rows carry payload_json; legacy rows carry reasons_json (a
        # plain list of URL strings). Synthesize a minimal info_payload
        # from the legacy field so old tokens still render something.
        if "payload_json" in entity:
            info_payload = json.loads(entity["payload_json"])
        else:
            legacy_urls = json.loads(entity.get("reasons_json", "[]"))
            # Pre-action-rewrite tokens were all verify-mode. Derive a
            # plausible action_outcome from the legacy verdict_label if
            # stored on the row, else fall back to verified_nei.
            legacy_verdict = entity.get("verdict_label") or ""
            legacy_outcome = {
                "Supported": "verified_supported",
                "Refuted": "verified_refuted",
                "Conflicting": "verified_conflicting",
                "NotEnoughEvidence": "verified_nei",
            }.get(legacy_verdict, "verified_nei")
            info_payload = {
                "action": "verify",
                "action_source": "inferred",
                "action_outcome": legacy_outcome,
                "primary_sources": [{"url": u, "display_name": u} for u in (legacy_urls or [])],
                "source_quality_table": [],
                "counterpoints": [],
                "perspectives": [],
            }
        params = {
            "tone": entity.get("tone", ""),
            "reply_text": entity.get("reply_text", ""),
            "info_payload": info_payload,
            "parent_id": entity.get("parent_id", ""),
            "parent_author_username": entity.get("parent_author_username", ""),
            "bot_handle": entity.get("bot_handle", ""),
            "reply_id": entity.get("reply_id", ""),
            "mention_id": entity.get("mention_id", ""),
            "participant_id": entity.get("participant_id", ""),
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


def _is_self_reply(tweet: dict) -> tuple[bool, str]:
    """Return ``(should_drop, reason)``.

    ``reason`` is ``"self_reply"`` when the mention author matches the bot's
    configured user id, or ``"self_reply_unconfigured"`` when the bot id env
    var is unset (we still fail closed but tag the drop so analytics can
    distinguish misconfiguration from genuine self-replies).
    """
    if not BOT_USER_ID:
        logger.warning(
            "BOT_USER_ID is unset — skipping mention as self-reply fail-closed",
        )
        return True, "self_reply_unconfigured"
    if (tweet.get("user") or {}).get("id_str") == BOT_USER_ID:
        return True, "self_reply"
    return False, "self_reply"


# Startup warning for missing BOT_USER_ID — surfaces misconfiguration
# before the first mention silently fails closed.
if not BOT_USER_ID:
    logger.warning(
        "BOT_USER_ID is unset at import time — all mentions will be dropped "
        "with reason=self_reply_unconfigured",
    )


def _lookup_participant(author_id: str) -> "_participants.Participant | None":
    """Look up a participant with a write-through cache.

    Checks the in-process dict first; on a miss, falls back to the persistent
    store (catches participants registered by CLI tools or other workers) and
    populates the cache for subsequent calls.
    """
    if not author_id:
        return None
    p = _PARTICIPANTS_BY_ID.get(author_id)
    if p is not None:
        return p
    p = _participants_store.get(author_id)
    if p is not None:
        _PARTICIPANTS_BY_ID[author_id] = p
    return p


_FORCE_TONE = (os.getenv("DERAD_FORCE_TONE") or "").strip().lower()


def _resolve_tone(author_id: str) -> str:
    """Pick the reply tone for an incoming mention.

    DERAD_FORCE_TONE, when set to one of VALID_TONES, overrides everything —
    useful for single-arm test rounds before the full pilot. Otherwise:
    registered participants use their assigned tone; unregistered users get a
    uniformly random tone per mention.
    """
    if _FORCE_TONE in VALID_TONES:
        return _FORCE_TONE
    p = _lookup_participant(author_id)
    if p and p.tone in VALID_TONES:
        return p.tone
    return random.choice(VALID_TONES)


def _build_info_url(tone: str, tweet_ids, note_ids, reply_id: str = "") -> str:
    return url_for(
        "info",
        reply_id=reply_id,
        tone=tone,
        tweet_id=tweet_ids,
        note_id=note_ids,
        _external=True,
    )


def _clean_invoker_text(text: str, bot_handle: str) -> str:
    """Strip the bot handle from the mention text and collapse whitespace.

    Returns whatever's left — typically the invoker's instruction (e.g.
    "fact check this", "what's the context", "push back on this nonsense").
    Empty when the invoker only tagged the bot and said nothing.
    """
    if not text:
        return ""
    handle = (bot_handle or "").lstrip("@").strip()
    if not handle:
        return " ".join(text.split())
    # Remove "@bothandle" tokens anywhere in the text, case-insensitive.
    # Use a regex that requires word boundaries so we don't chew into
    # legit handles that share a prefix.
    import re
    pattern = re.compile(rf"@{re.escape(handle)}\b", re.IGNORECASE)
    stripped = pattern.sub("", text)
    return " ".join(stripped.split())


def _author_username(tweet: dict) -> str | None:
    user = tweet.get("user") or {}
    return user.get("screen_name") or user.get("username")


def _process_mention_throttled(tone: str, tweet: dict, received_at_utc: datetime) -> None:
    """Cap concurrent pipelines via PIPELINE_MAX_CONCURRENT. Threads beyond
    the cap wait up to PIPELINE_QUEUE_TIMEOUT_S; over that we drop the
    mention, log a warning, and write a `pipeline_queue_timeout` drop event."""
    mention_id = tweet.get("id_str", "?")
    t_queued = time.monotonic()
    acquired = _PIPELINE_SEMAPHORE.acquire(timeout=PIPELINE_QUEUE_TIMEOUT_S)
    if not acquired:
        wait_s = time.monotonic() - t_queued
        logger.warning(
            "Pipeline queue timed out after %.0fs for mention %s (cap=%d) — dropping; consider scaling out",
            wait_s, mention_id, PIPELINE_MAX_CONCURRENT,
        )
        try:
            log_mention_drop(MentionDrop(
                received_at_utc=received_at_utc,
                tweet_id=str(mention_id),
                reason="pipeline_queue_timeout",
            ))
        except Exception:
            logger.exception("Failed to write drop event for queue-timeout mention %s", mention_id)
        return

    wait_s = time.monotonic() - t_queued
    if wait_s > 0.5:
        logger.info(
            "Pipeline slot acquired for mention %s after %.1fs wait (cap=%d)",
            mention_id, wait_s, PIPELINE_MAX_CONCURRENT,
        )
    try:
        process_mention(tone, tweet, received_at_utc)
    finally:
        _PIPELINE_SEMAPHORE.release()


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
        bot_author_id=BOT_USER_ID,
        bot_handle=BOT_HANDLE,
    )

    def _finalize(outcome: str, exc: BaseException | None = None) -> None:
        ev.outcome = outcome
        ev.pipeline_ms = int((time.monotonic() - t0) * 1000)
        if exc is not None:
            ev.error_class = type(exc).__name__
            ev.error_detail = str(exc)[:1000]
        # If Tables is degraded the write can stall indefinitely and hold the
        # pipeline-semaphore slot until PIPELINE_QUEUE_TIMEOUT_S (~10 min).
        # Run the persist on a daemon thread and bail after _FINALIZE_TIMEOUT_S.
        t = threading.Thread(
            target=log_mention_event, args=(ev,), daemon=True, name="finalize-persist",
        )
        t.start()
        t.join(timeout=_FINALIZE_TIMEOUT_S)
        if t.is_alive():
            logger.warning(
                "log_mention_event stalled for mention %s; abandoning persist (Tables degraded?)",
                ev.mention_id,
            )
        metrics.replies_posted.add(1, {"tone": tone, "outcome": outcome})
        metrics.pipeline_latency_ms.record(ev.pipeline_ms, {"tone": tone, "outcome": outcome})

    try:
        logger.info("process_mention[%s]: fetching parent %s", mention_id, parent_id)
        snap = fetch_tweet(parent_id)
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
        if DRY_RUN:
            logger.info("DRY_RUN: parent_text=%r", statement)

        parent_image_urls = list(snap.image_urls or [])
        # Extract the invoker's instruction from the mention tweet itself
        # (NOT the parent) — what they wrote alongside @eddiexbot is the
        # signal the extractor uses to choose the bot's action.
        invoker_instruction = _clean_invoker_text(tweet.get("text") or "", BOT_HANDLE)
        ev.invoker_instruction_text = invoker_instruction
        logger.info(
            "process_mention[%s]: parent fetched (author=@%s, text_chars=%d, images=%d, invoker=%r) — entering generate_reply",
            mention_id, snap.author_username or "?", len(statement), len(parent_image_urls),
            invoker_instruction[:60],
        )
        tweet_context = {
            "author_username": snap.author_username,
            "author_verified": snap.author_verified,
            "author_verified_type": snap.author_verified_type,
            "author_description": snap.author_description,
            "author_created_at": snap.author_created_at,
            "author_followers_count": snap.author_followers_count,
            "posted_at": snap.created_at,
            "lang": snap.lang,
            "possibly_sensitive": snap.possibly_sensitive,
            "expanded_urls": snap.expanded_urls or [],
            "referenced_tweets": snap.referenced_tweets or [],
            "public_metrics": {
                "like_count": snap.like_count,
                "retweet_count": snap.retweet_count,
                "reply_count": snap.reply_count,
                "quote_count": snap.quote_count,
            },
        }
        reply = generate_reply(
            statement=statement,
            exclude_tweet_id=parent_id,
            tone=tone,
            image_urls=parent_image_urls,
            tweet_context=tweet_context,
            invoker_instruction=invoker_instruction,
        )
        logger.info(
            "process_mention[%s]: generate_reply returned (action=%s outcome=%s reply_chars=%d sources=%s)",
            mention_id, reply.get("action"), reply.get("action_outcome"),
            len(reply.get("text") or ""), reply.get("sources"),
        )
        ev.queries = reply.get("queries") or []
        ev.action = reply.get("action")
        ev.action_outcome = reply.get("action_outcome")

        if not reply.get("text"):
            logger.info("Empty reply text for mention %s; skipping", mention_id)
            _finalize("empty_reply")
            return

        # Legacy reply_type, derived now from action_outcome — kept so older
        # analytics queries that group on this column keep working.
        # _unavailable / _insufficient / _nei / declined outcomes ⇒ no_factcheck.
        ao = (reply.get("action_outcome") or "") or ""
        no_factcheck_outcomes = {
            "verified_nei",
            "context_unavailable",
            "challenge_unavailable",
            "perspectives_insufficient",
            "declined",
        }
        ev.reply_type = "no_factcheck" if ao in no_factcheck_outcomes else "factcheck"

        token = _make_info_token(
            tone,
            reply["text"],
            reply.get("info_payload") or {},
            parent_id=parent_id,
            parent_author_username=ev.parent_author_username or "",
            bot_handle=BOT_HANDLE,
            mention_id=mention_id,
            participant_id=author_id,
        )

        with app.app_context():
            info_url = url_for("info_short", token=token, _external=True)
        reply_text = reply["text"]
        ev.reply_text = reply_text
        link_reply_text = f"Sources & reasoning: {info_url}"

        if DRY_RUN:
            logger.info("DRY_RUN reply (tone=%s): %s\n[link reply] %s", tone, reply_text, link_reply_text)
            _finalize("dry_run")
            return

        logger.info("Posting reply (tone=%s): (text suppressed)", tone)

        reply_id = post_reply(parent_id=mention_id, reply_text=reply_text)
        if reply_id is None:
            _finalize("x_post_error")
            return
        ev.reply_id = reply_id
        _update_info_token(token, reply_id=reply_id)
        ev.reply_posted_utc = events.utcnow()

        # Post the info link as a separate self-reply so the main fact-check
        # reply carries no URL — links suppress reach on X.
        link_reply_id = post_reply(parent_id=reply_id, reply_text=link_reply_text)
        if link_reply_id is None:
            logger.warning(
                "Link self-reply failed for mention %s (main reply %s already posted)",
                mention_id, reply_id,
            )

        ev.study_code = _make_study_code(reply_id)
        participant = _lookup_participant(author_id)
        if participant:
            ev.participant_id = author_id
            ev.study_day = max(1, (
                received_at_utc.date() - participant.enrolled_at_utc.date()
            ).days + 1)

        _finalize("replied")

    except Exception as exc:
        logger.exception("Pipeline failed for mention %s (tone=%s)", mention_id, tone)
        _finalize("pipeline_error", exc=exc)


def _dispatch_tweet(tweet: dict, received_at_utc: datetime) -> bool:
    """Apply the guard chain and, if accepted, start a pipeline thread.

    Tone is resolved here from the mention author's participant record
    (random for unregistered users) — see _resolve_tone.
    """
    mention_id = tweet.get("id_str")
    parent_id = tweet.get("in_reply_to_status_id_str")
    author_id = (tweet.get("user") or {}).get("id_str") or ""

    tone = _resolve_tone(author_id)
    metrics.mentions_received.add(1, {"tone": tone})

    def _drop(reason: str, **drop_kwargs):
        log_mention_drop(MentionDrop(
            drop_reason=reason, received_at_utc=received_at_utc, tone=tone, **drop_kwargs,
        ))
        metrics.mentions_dropped.add(1, {"tone": tone, "reason": reason})

    if not mention_id or not parent_id:
        _drop("no_parent", mention_id=mention_id, author_id=author_id,
              extra={"has_mention_id": bool(mention_id), "has_parent_id": bool(parent_id)})
        return False

    is_self, self_reason = _is_self_reply(tweet)
    if is_self:
        logger.info("Skipping self-reply %s (reason=%s)", mention_id, self_reason)
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
        target=_process_mention_throttled,
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
    bot_handle = BOT_HANDLE or "i"

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
    return render_template("info.html", reply=reply_html, notes=""), 200


# User-agent substrings that mark link-card crawlers / chat-app unfurlers.
# The dossier link lives in a tweet, so these hit /i/<token> automatically;
# flagging them lets analysis exclude phantom hits from real click-throughs.
_BOT_UA_MARKERS = (
    "bot", "crawler", "spider", "preview", "fetch", "scrap",
    "whatsapp", "facebookexternalhit", "embedly", "yandex", "pinterest",
    "vkshare", "headlesschrome", "python-requests", "curl", "wget",
    "go-http-client", "okhttp", "axios", "node-fetch",
)


def _is_bot_ua(user_agent: str) -> bool:
    ua = (user_agent or "").lower()
    if not ua:
        return True  # no UA header at all → almost certainly automated
    return any(marker in ua for marker in _BOT_UA_MARKERS)


@app.route("/i/<token>", methods=["GET"])
def info_short(token: str):
    params = _get_info_params(token)
    if params is None:
        return render_template("info.html"), 404
    tone = params.get("tone", "")

    ua = request.headers.get("User-Agent", "")
    log_info_view(InfoView(
        token=token,
        viewed_at_utc=events.utcnow(),
        reply_id=params.get("reply_id") or "",
        parent_id=params.get("parent_id") or "",
        mention_id=params.get("mention_id") or "",
        participant_id=params.get("participant_id") or "",
        tone=tone,
        user_agent=ua,
        referrer=request.headers.get("Referer", ""),
        is_bot=_is_bot_ua(ua),
    ))

    return render_template(
        "info.html",
        info=params.get("info_payload") or {},
        reply_text=params.get("reply_text", ""),
        tone=tone,
        bot_handle=params.get("bot_handle") or BOT_HANDLE,
        parent_id=params.get("parent_id", ""),
        parent_author_username=params.get("parent_author_username", ""),
        reply_tweet_id=params.get("reply_id", ""),
    ), 200


@app.route("/about", methods=["GET"])
def about():
    return render_template("about.html", bot_handle=BOT_HANDLE), 200


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True}), 200


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
    tone_counts: dict[str, int] = {t: 0 for t in _participants.VALID_TONES}
    action_counts: dict[str, int] = {}
    action_outcome_counts: dict[str, int] = {}
    latencies: list[int] = []
    for ev in recent_events:
        outcome = ev.get("outcome") or "unknown"
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        tone = _participants.canonical_tone(ev.get("tone") or "")
        if tone in tone_counts:
            tone_counts[tone] += 1
        action = ev.get("action") or ""
        if action:
            action_counts[action] = action_counts.get(action, 0) + 1
        ao = ev.get("action_outcome") or ""
        if ao:
            action_outcome_counts[ao] = action_outcome_counts.get(ao, 0) + 1
        ms = ev.get("pipeline_ms")
        if ms:
            latencies.append(int(ms))

    part_store = _participants.get_store()
    all_parts = part_store.list_all()
    participant_tone_counts = {t: 0 for t in _participants.VALID_TONES}
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
            "action_counts": action_counts,
            "action_outcome_counts": action_outcome_counts,
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
        bot_handle = e.get("bot_handle") or BOT_HANDLE
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


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _bot_tweet_url(reply_id: str) -> str:
    if not reply_id:
        return ""
    if BOT_HANDLE:
        return f"https://twitter.com/{BOT_HANDLE}/status/{reply_id}"
    return f"https://twitter.com/i/web/status/{reply_id}"


@app.route("/api/engagement", methods=["GET"])
def api_engagement():
    """Downstream measurement data from the cron, for live monitoring.

    Each bot reply is polled every ~12 h for 10 days, so EngagementSnapshots
    holds a cumulative series (≈20 rows/reply). We collapse to the latest
    snapshot per reply for the table and totals, and build a 14-day daily
    cumulative time series for the growth chart. Bystander reply text
    (BotReplyReplies) is returned as-is.

    Tweet URLs are built server-side: bot replies post from BOT_HANDLE
    (single-app bot); bystander replies link via the bystander's own username.
    """
    store = events.get_store()
    # Full history — low volume (≤ ~20 rows per reply) so a generous cap is safe.
    snaps = store.list_recent_engagements(50000)
    replies = store.list_recent_reply_replies(2000)
    info_views = store.list_recent_info_views(50000)

    # ── Click aggregation (dossier /i/ link views) ─────────────────────────
    # Clicks land the moment a reply posts, but engagement snapshots only start
    # after the 3-day poll delay — so a reply can have clicks with no snapshot.
    # We count clicks independently and reconcile with by_reply below.
    # `is_bot` rows (X card crawler, chat unfurlers) are counted separately so
    # the table can show real (human) click-throughs.
    clicks_all: dict[str, int] = defaultdict(int)
    clicks_human: dict[str, int] = defaultdict(int)
    view_tone: dict[str, str] = {}
    view_mention: dict[str, str] = {}
    view_last: dict[str, str] = {}  # latest view ts per reply, for sorting click-only rows
    tone_clicks_all: dict[str, int] = defaultdict(int)
    tone_clicks_human: dict[str, int] = defaultdict(int)
    for v in info_views:
        rid = v.get("reply_id")
        is_bot = bool(v.get("is_bot"))
        ct = _participants.canonical_tone(v.get("tone") or "unknown")
        tone_clicks_all[ct] += 1
        if not is_bot:
            tone_clicks_human[ct] += 1
        if not rid:
            continue
        clicks_all[rid] += 1
        if not is_bot:
            clicks_human[rid] += 1
        view_tone.setdefault(rid, v.get("tone"))
        if v.get("mention_id"):
            view_mention.setdefault(rid, v.get("mention_id"))
        ts = v.get("viewed_at_utc") or ""
        if ts > view_last.get(rid, ""):
            view_last[rid] = ts

    for r in replies:
        tid = r.get("reply_tweet_id")
        uname = r.get("author_username")
        if tid and uname:
            r["reply_url"] = f"https://twitter.com/{uname}/status/{tid}"
        elif tid:
            r["reply_url"] = f"https://twitter.com/i/web/status/{tid}"
        else:
            r["reply_url"] = ""

    # ── Group snapshots per reply (sorted oldest→newest) ───────────────────
    per_reply: dict[str, list[tuple[datetime, dict]]] = {}
    for s in snaps:
        rid = s.get("reply_id")
        ts = _parse_iso(s.get("polled_at_utc"))
        if not rid or ts is None:
            continue
        per_reply.setdefault(rid, []).append((ts, s))
    for lst in per_reply.values():
        lst.sort(key=lambda t: t[0])

    # ── Collapse to the latest cumulative snapshot per reply ───────────────
    # `series` holds each metric's per-poll trajectory (oldest→newest) for
    # the in-cell sparklines; ≤ ~20 points per reply.
    def _ints(lst, key):
        return [int(s.get(key) or 0) for _, s in lst]

    by_reply = []
    for rid, lst in per_reply.items():
        latest = lst[-1][1]
        by_reply.append({
            "reply_id": rid,
            "reply_url": _bot_tweet_url(rid),
            "tone": latest.get("tone"),
            "like_count": int(latest.get("like_count") or 0),
            "retweet_count": int(latest.get("retweet_count") or 0),
            "reply_count": int(latest.get("reply_count") or 0),
            "quote_count": int(latest.get("quote_count") or 0),
            "click_count": clicks_all.get(rid, 0),
            "human_click_count": clicks_human.get(rid, 0),
            "poll_count": len(lst),
            "last_polled_utc": latest.get("polled_at_utc"),
            "mention_id": latest.get("mention_id"),
            "series": {
                "likes": _ints(lst, "like_count"),
                "retweets": _ints(lst, "retweet_count"),
                "replies": _ints(lst, "reply_count"),
                "quotes": _ints(lst, "quote_count"),
            },
        })

    # Replies clicked before their first engagement poll have no snapshot yet —
    # surface them so early click-throughs aren't hidden from the table.
    for rid in clicks_all:
        if rid in per_reply:
            continue
        by_reply.append({
            "reply_id": rid,
            "reply_url": _bot_tweet_url(rid),
            "tone": view_tone.get(rid),
            "like_count": 0, "retweet_count": 0, "reply_count": 0, "quote_count": 0,
            "click_count": clicks_all[rid],
            "human_click_count": clicks_human.get(rid, 0),
            "poll_count": 0,
            "last_polled_utc": None,
            "mention_id": view_mention.get(rid),
            "series": {"likes": [], "retweets": [], "replies": [], "quotes": []},
        })

    by_reply.sort(
        key=lambda r: r.get("last_polled_utc") or view_last.get(r["reply_id"], ""),
        reverse=True,
    )

    totals = {
        "reply_count": len(by_reply),
        "snapshot_count": len(snaps),
        "total_likes": sum(r["like_count"] for r in by_reply),
        "total_retweets": sum(r["retweet_count"] for r in by_reply),
        "total_replies": sum(r["reply_count"] for r in by_reply),
        "total_quotes": sum(r["quote_count"] for r in by_reply),
        "total_clicks": sum(clicks_all.values()),
        "total_human_clicks": sum(clicks_human.values()),
        "bystander_count": len(replies),
    }

    # ── Daily cumulative aggregation (14-day window) ───────────────────────
    # At each day's end, take each reply's latest snapshot at-or-before that
    # time (carried forward after polling stops) and sum across a reply set.
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    bucket_ends = [
        datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)
        for d in days
    ]

    def _latest_before(lst, bucket_end):
        latest = None
        for ts, s in lst:
            if ts < bucket_end:
                latest = s
            else:
                break
        return latest

    def _series_for(reply_ids):
        """Per-bucket cumulative totals + active-reply count, summed over reply_ids."""
        out = {"likes": [], "retweets": [], "replies": [], "quotes": [], "active": []}
        for be in bucket_ends:
            lk = rt = rp = qt = active = 0
            for rid in reply_ids:
                latest = _latest_before(per_reply[rid], be)
                if latest is not None:
                    active += 1
                    lk += int(latest.get("like_count") or 0)
                    rt += int(latest.get("retweet_count") or 0)
                    rp += int(latest.get("reply_count") or 0)
                    qt += int(latest.get("quote_count") or 0)
            out["likes"].append(lk); out["retweets"].append(rt)
            out["replies"].append(rp); out["quotes"].append(qt); out["active"].append(active)
        return out

    def _avg_series(totals_list, counts):
        return [round(v / c, 1) if c else 0 for v, c in zip(totals_list, counts)]

    overall = _series_for(list(per_reply.keys()))
    timeseries = {
        "labels": [d.strftime("%m-%d") for d in days],
        "likes": overall["likes"], "retweets": overall["retweets"],
        "replies": overall["replies"], "quotes": overall["quotes"],
    }

    # ── Per-condition (tone) breakdown ─────────────────────────────────────
    # Group by condition; legacy "agonistic" rows fold into the "satirical" label.
    reply_tone = {
        rid: _participants.canonical_tone(lst[-1][1].get("tone") or "unknown")
        for rid, lst in per_reply.items()
    }
    # Clicks may exist for a condition with no engagement snapshots yet, so the
    # tone set unions both sources; engagement averages stay over snapshot
    # replies (clean denominator), click figures are totals across all views.
    tones = sorted(set(reply_tone.values()) | set(tone_clicks_all.keys()))
    by_tone = []
    for tone in tones:
        rids = [rid for rid, t in reply_tone.items() if t == tone]
        n = len(rids)
        tl = tr = trp = tq = 0
        for rid in rids:
            latest = per_reply[rid][-1][1]
            tl += int(latest.get("like_count") or 0)
            tr += int(latest.get("retweet_count") or 0)
            trp += int(latest.get("reply_count") or 0)
            tq += int(latest.get("quote_count") or 0)
        ser = _series_for(rids)
        by_tone.append({
            "tone": tone,
            "reply_count": n,
            "total_likes": tl, "total_retweets": tr, "total_replies": trp, "total_quotes": tq,
            "total_clicks": tone_clicks_all.get(tone, 0),
            "total_human_clicks": tone_clicks_human.get(tone, 0),
            "avg_likes": round(tl / n, 1) if n else 0,
            "avg_retweets": round(tr / n, 1) if n else 0,
            "avg_replies": round(trp / n, 1) if n else 0,
            "avg_quotes": round(tq / n, 1) if n else 0,
            "avg_series": {
                "likes": _avg_series(ser["likes"], ser["active"]),
                "retweets": _avg_series(ser["retweets"], ser["active"]),
                "replies": _avg_series(ser["replies"], ser["active"]),
                "quotes": _avg_series(ser["quotes"], ser["active"]),
            },
        })
    # Conditions with data first, then the empty canonical ones; tone name within.
    by_tone.sort(key=lambda t: (t["reply_count"] == 0, t["tone"]))

    return jsonify({
        "by_reply": by_reply,
        "by_tone": by_tone,
        "totals": totals,
        "timeseries": timeseries,
        "replies": replies,
    }), 200


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
    """Register a participant by @handle. Body: {username, tone, notes?}.

    Looks up the X numeric user ID, validates the tone condition, and persists
    the record. Updates the in-process cache so events from this worker are
    tagged immediately; other workers pick it up via the write-through fallback
    in _lookup_participant on their next mention from this author.
    """
    payload = request.get_json(silent=True) or {}
    username_raw = (payload.get("username") or "").strip()
    tone_raw = (payload.get("tone") or "").strip().lower()
    notes = (payload.get("notes") or "").strip()

    if not username_raw:
        return jsonify({"error": "username is required"}), 400

    if not tone_raw:
        return jsonify({"error": f"tone is required; valid values: {', '.join(VALID_TONES)}, random"}), 400

    if tone_raw == "random":
        tone_raw = _participants.pick_balanced_tone()
    elif tone_raw not in VALID_TONES:
        return jsonify({"error": f"invalid tone {tone_raw!r}; valid values: {', '.join(VALID_TONES)}, random"}), 400

    clean_username = username_raw.lstrip("@")

    try:
        author_id = _participants.lookup_author_id(clean_username)
    except _participants.ParticipantLookupError as exc:
        return jsonify({"error": str(exc)}), 422

    registered_at = datetime.now(timezone.utc)

    p = _participants.Participant(
        author_id=author_id,
        author_username=clean_username,
        tone=tone_raw,
        enrolled_at_utc=registered_at,
        notes=notes,
    )
    _participants.get_store().register(p)
    _PARTICIPANTS_BY_ID[author_id] = p
    logger.info(
        "Registered participant via dashboard: @%s id=%s tone=%s",
        clean_username, author_id, tone_raw,
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


_streamer.start_streamer(_dispatch_tweet)
