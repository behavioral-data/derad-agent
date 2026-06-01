"""Research-grade append-only event log for derad-agent.

Two writers:

  ``log_mention_event(MentionEvent)``  one row per accepted mention with the
                                       full pipeline state (parent text,
                                       cited notes, reply ids, timings).

  ``log_mention_drop(MentionDrop)``    one row per mention we DIDN'T process
                                       (dedup hit, rate-limit, self-reply,
                                       unregistered, no-parent, invalid).

Two backends, same Protocol-based selection as ``dedup.py``:

  - ``InMemoryEventsStore`` (default; used in tests and local dev). Rows
    accumulate in a list; introspectable for assertions.
  - ``TablesEventsStore``  (Azure Table Storage). Auth via
    ``DefaultAzureCredential`` — App Service UAMI in prod, az-cli/env locally.

Selection via ``DERAD_EVENTS_BACKEND``. Endpoint reused from
``DERAD_TABLES_ENDPOINT``.

Failures are LOGGED, not raised. The bot must keep replying even if event
capture has a bad day — losing analytics is preferable to losing replies.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a datetime to tz-aware UTC at the storage boundary.

    Field names promise ``_utc``, but the dataclasses don't enforce tz-awareness.
    A naive datetime fed to ``.strftime("%Y-%m")`` / ``.timestamp()`` would
    silently use the host's local timezone, scrambling month-partition placement
    and RowKey sort order. We treat naive as UTC (most conservative given the
    naming convention) and convert any other tz-aware value to UTC.
    """
    if dt.tzinfo is None:
        logger.debug("Normalizing naive datetime to UTC at storage boundary")
        return dt.replace(tzinfo=timezone.utc)
    if dt.utcoffset() != timedelta(0):
        return dt.astimezone(timezone.utc)
    return dt


def parse_iso_utc(value) -> datetime:
    """Parse an ISO-8601 timestamp (or datetime) to a tz-aware UTC datetime.

    Backends emit timestamps in subtly different forms — ``InMemoryEventsStore``
    via ``datetime.isoformat()`` (``...+00:00``) and ``TablesEventsStore`` via
    the Azure SDK's datetime isoformat (which may render as ``...Z``). Mixed
    suffixes break the ``lex == chron`` assumption when comparing strings, so
    callers that order timestamps from both backends must parse before compare.

    Missing / unparseable values sort last (``datetime.min`` in UTC).
    """
    _MIN = datetime.min.replace(tzinfo=timezone.utc)
    if value is None or value == "":
        return _MIN
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return _MIN
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return _MIN


# ── Event types ─────────────────────────────────────────────────────────────

@dataclass
class MentionEvent:
    """One row per accepted mention, written at the terminal point of process_mention."""
    mention_id: str
    parent_id: str
    author_id: str
    tone: str

    received_at_utc: datetime
    pipeline_start_utc: Optional[datetime] = None
    reply_posted_utc: Optional[datetime] = None
    pipeline_ms: Optional[int] = None

    # Webhook + X API enrichment
    author_username: Optional[str] = None
    parent_text: Optional[str] = None
    parent_author_id: Optional[str] = None
    parent_author_username: Optional[str] = None
    parent_like_count: Optional[int] = None
    parent_retweet_count: Optional[int] = None
    parent_reply_count: Optional[int] = None
    parent_quote_count: Optional[int] = None

    # Pipeline output
    queries: list[str] = field(default_factory=list)
    reply_text: Optional[str] = None
    reply_id: Optional[str] = None
    # Tweet id of the dossier-link self-reply posted under reply_id. None when
    # the link self-reply failed (in which case outcome == 'replied_no_link').
    link_reply_id: Optional[str] = None

    # Study tracking
    study_code: Optional[str] = None      # 4-letter code used in daily DM surveys
    participant_id: Optional[str] = None  # author_id, explicit FK to Participants table
    study_day: Optional[int] = None       # 1-based day number within the 5-day study
    bot_author_id: Optional[str] = None   # X user ID of the bot that replied
    bot_handle: Optional[str] = None      # @handle of the bot that replied

    # Join key to the InfoTokens table; pairs with the dossier link in the reply.
    # New schema: per-citation source lists live on InfoTokens.payload_json, keyed
    # by this token. Older rows pre-dating the schema split have this unset.
    info_token: Optional[str] = None

    # Outcome
    outcome: str = "replied"  # 'replied' | 'replied_no_link' | 'pipeline_error' | 'x_post_error' | 'parent_fetch_failed' | 'empty_reply'
    # 'factcheck' when the pipeline produced a structural verdict
    # (Supported/Refuted/Disputed), 'no_factcheck' when it landed on NEI.
    # None when no reply was sent. Legacy field — analytics should prefer
    # `action` + `action_outcome` below.
    reply_type: Optional[str] = None
    # New action-typed analytics (introduced with the multi-action pipeline).
    # action ∈ {verify, provide_context, challenge_opinion, surface_perspectives, decline}
    # action_outcome is the terminal ActionOutcome label.
    action: Optional[str] = None
    action_outcome: Optional[str] = None
    # Raw text the invoker wrote in the mention (after stripping the bot
    # handle). Empty string when invoker only tagged.
    invoker_instruction_text: Optional[str] = None
    error_class: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class MentionDrop:
    """One row per mention that bypassed the pipeline at a guard."""
    drop_reason: str  # 'duplicate' | 'rate_limit' | 'self_reply' | 'unregistered' | 'no_parent' | 'invalid_payload'
    received_at_utc: datetime

    mention_id: Optional[str] = None
    author_id: Optional[str] = None
    tone: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngagementSnapshot:
    """One row per engagement poll on a bot reply tweet."""
    reply_id: str
    tone: str
    polled_at_utc: datetime
    like_count: int = 0
    retweet_count: int = 0
    reply_count: int = 0          # raw X public_metrics, as reported
    quote_count: int = 0
    mention_id: Optional[str] = None  # FK back to MentionEvents
    parent_id: Optional[str] = None   # original post being fact-checked
    # reply_count minus the bot's own link self-reply (which X counts as a
    # reply to this tweet). Equals reply_count when no link reply was posted.
    # Use THIS for analysis; reply_count is kept raw for faithfulness.
    adjusted_reply_count: int = 0


@dataclass
class BotReplyReply:
    """Text of a reply to a bot post — collected for bystander NLP analysis.

    Written by the derad-collect-replies CLI (~3 days after each bot reply).
    """
    bot_reply_id: str        # the bot's reply tweet that was responded to
    reply_tweet_id: str      # ID of the bystander's reply
    author_id: str           # bystander author's X user ID
    text: str                # full text of the bystander reply
    collected_at_utc: datetime
    author_username: Optional[str] = None
    like_count: int = 0
    mention_id: Optional[str] = None  # FK back to MentionEvents
    tone: Optional[str] = None        # which bot posted the reply


@dataclass
class InfoView:
    """One row per GET on a /i/<token> dossier link — a click-through.

    The X link-card crawler and chat-app unfurlers hit the URL automatically,
    so `is_bot` flags those (by user-agent) for analysis-time filtering; the
    raw hit is still recorded. No IP is stored. `reply_id` is the join key
    back to MentionEvents (mention_id/participant_id are denormalized here for
    convenience but are also derivable via that join).
    """
    token: str
    viewed_at_utc: datetime
    reply_id: Optional[str] = None        # bot reply whose dossier was opened
    parent_id: Optional[str] = None       # original claim tweet
    mention_id: Optional[str] = None      # FK back to MentionEvents
    participant_id: Optional[str] = None  # mention author this dossier belongs to
    tone: Optional[str] = None
    user_agent: Optional[str] = None
    referrer: Optional[str] = None
    is_bot: bool = False                  # heuristic: UA matches a known crawler/unfurler


# ── Store interface ─────────────────────────────────────────────────────────

class EventsStore(Protocol):
    def write_event(self, ev: MentionEvent) -> None: ...
    def write_drop(self, drop: MentionDrop) -> None: ...
    def write_engagement(self, snap: EngagementSnapshot) -> None: ...
    def write_reply_reply(self, reply: BotReplyReply) -> None: ...
    def write_info_view(self, view: InfoView) -> None: ...
    def iter_reply_ids(self) -> list[tuple[str, str, datetime | None, str | None, str | None, str | None]]: ...
    def snapshotted_reply_ids(self) -> set[str]: ...
    def latest_snapshot_times(self) -> dict[str, datetime]: ...
    def collected_reply_ids(self) -> set[str]: ...
    def list_recent(self, limit: int = 50) -> list[dict]: ...
    def list_recent_drops(self, limit: int = 20) -> list[dict]: ...
    def list_recent_engagements(self, limit: int = 500) -> list[dict]: ...
    def list_recent_reply_replies(self, limit: int = 500) -> list[dict]: ...
    def list_recent_info_views(self, limit: int = 500) -> list[dict]: ...


# ── In-memory backend (tests + local dev) ───────────────────────────────────

class InMemoryEventsStore:
    """Single-process append-only list. Inspect via the .events / .drops attrs."""

    def __init__(self) -> None:
        self.events: list[MentionEvent] = []
        self.drops: list[MentionDrop] = []
        self.engagements: list[EngagementSnapshot] = []
        self.reply_replies: list[BotReplyReply] = []
        self.info_views: list[InfoView] = []
        self._lock = threading.Lock()

    def write_event(self, ev: MentionEvent) -> None:
        with self._lock:
            self.events.append(ev)

    def write_drop(self, drop: MentionDrop) -> None:
        with self._lock:
            self.drops.append(drop)

    def write_engagement(self, snap: EngagementSnapshot) -> None:
        with self._lock:
            self.engagements.append(snap)

    def write_reply_reply(self, reply: BotReplyReply) -> None:
        with self._lock:
            self.reply_replies.append(reply)

    def write_info_view(self, view: InfoView) -> None:
        with self._lock:
            self.info_views.append(view)

    def iter_reply_ids(self) -> list[tuple[str, str, datetime | None, str | None, str | None, str | None]]:
        with self._lock:
            return [
                (ev.reply_id, ev.tone, ev.reply_posted_utc, ev.mention_id, ev.parent_id, ev.link_reply_id)
                for ev in self.events
                if ev.reply_id
            ]

    def snapshotted_reply_ids(self) -> set[str]:
        with self._lock:
            return {s.reply_id for s in self.engagements}

    def latest_snapshot_times(self) -> dict[str, datetime]:
        with self._lock:
            result: dict[str, datetime] = {}
            for s in self.engagements:
                prev = result.get(s.reply_id)
                if prev is None or s.polled_at_utc > prev:
                    result[s.reply_id] = s.polled_at_utc
            return result

    def collected_reply_ids(self) -> set[str]:
        with self._lock:
            return {r.bot_reply_id for r in self.reply_replies}

    def list_recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            events = list(reversed(self.events[-limit:]))
        return [
            {
                "mention_id": e.mention_id,
                "author_id": e.author_id,
                "author_username": e.author_username,
                "tone": e.tone,
                "outcome": e.outcome,
                "reply_id": e.reply_id,
                "received_at_utc": e.received_at_utc.isoformat() if e.received_at_utc else None,
                "pipeline_ms": e.pipeline_ms,
                "study_day": e.study_day,
                "study_code": e.study_code,
                "reply_text": (e.reply_text or "")[:120],
                "parent_text": (e.parent_text or "")[:120],
                "bot_handle": e.bot_handle,
            }
            for e in events
        ]

    def list_recent_drops(self, limit: int = 20) -> list[dict]:
        with self._lock:
            drops = list(reversed(self.drops[-limit:]))
        return [
            {
                "mention_id": d.mention_id,
                "author_id": d.author_id,
                "tone": d.tone,
                "drop_reason": d.drop_reason,
                "received_at_utc": d.received_at_utc.isoformat() if d.received_at_utc else None,
            }
            for d in drops
        ]

    def list_recent_engagements(self, limit: int = 500) -> list[dict]:
        with self._lock:
            snaps = sorted(self.engagements, key=lambda s: s.polled_at_utc, reverse=True)[:limit]
        return [
            {
                "reply_id": s.reply_id,
                "tone": s.tone,
                "polled_at_utc": s.polled_at_utc.isoformat() if s.polled_at_utc else None,
                "like_count": s.like_count,
                "retweet_count": s.retweet_count,
                "reply_count": s.reply_count,
                "adjusted_reply_count": s.adjusted_reply_count,
                "quote_count": s.quote_count,
                "mention_id": s.mention_id,
                "parent_id": s.parent_id,
            }
            for s in snaps
        ]

    def list_recent_reply_replies(self, limit: int = 500) -> list[dict]:
        with self._lock:
            replies = sorted(self.reply_replies, key=lambda r: r.collected_at_utc, reverse=True)[:limit]
        return [
            {
                "bot_reply_id": r.bot_reply_id,
                "reply_tweet_id": r.reply_tweet_id,
                "author_id": r.author_id,
                "author_username": r.author_username,
                "text": (r.text or "")[:280],
                "like_count": r.like_count,
                "collected_at_utc": r.collected_at_utc.isoformat() if r.collected_at_utc else None,
                "mention_id": r.mention_id,
                "tone": r.tone,
            }
            for r in replies
        ]

    def list_recent_info_views(self, limit: int = 500) -> list[dict]:
        with self._lock:
            views = sorted(self.info_views, key=lambda v: v.viewed_at_utc, reverse=True)[:limit]
        return [
            {
                "token": v.token,
                "viewed_at_utc": v.viewed_at_utc.isoformat() if v.viewed_at_utc else None,
                "reply_id": v.reply_id,
                "parent_id": v.parent_id,
                "mention_id": v.mention_id,
                "participant_id": v.participant_id,
                "tone": v.tone,
                "user_agent": v.user_agent,
                "referrer": v.referrer,
                "is_bot": v.is_bot,
            }
            for v in views
        ]


# ── Azure Tables backend ────────────────────────────────────────────────────

class TablesEventsStore:
    """Azure Table Storage backend.

    PartitionKey = YYYY-MM (cheap monthly export / cleanup).
    RowKey       = received_at ISO + mention_id (sortable, unique).

    Long fields (parent_text, reply_text, error_detail) are truncated to 32 kB
    each — the Tables row limit is ~1 MB total and we want headroom for the
    JSON-encoded lists. ``queries`` and ``extra`` are JSON-encoded as strings
    since Tables doesn't natively store lists/dicts.
    """

    _FIELD_CAP = 32_000  # bytes; rough char cap is fine for our text

    def __init__(
        self,
        endpoint: str,
        *,
        events_table: str = "MentionEvents",
        drops_table: str = "MentionDrops",
        engagements_table: str = "EngagementSnapshots",
        reply_replies_table: str = "BotReplyReplies",
        info_views_table: str = "InfoViews",
        credential=None,
    ) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential

        cred = credential or DefaultAzureCredential()
        # Short timeouts on the table service client. The Azure SDK default is
        # 300s, and the table-create loop below blocks app + streamer
        # initialization on a SINGLE slow network round-trip.
        self._service = TableServiceClient(
            endpoint=endpoint,
            credential=cred,
            connection_timeout=10,
            read_timeout=15,
        )
        for name in (events_table, drops_table, engagements_table, reply_replies_table, info_views_table):
            try:
                self._service.create_table(name)
                logger.info("Created events table %s", name)
            except ResourceExistsError:
                pass
            except Exception:
                # Tables already exist in production; any other failure here
                # (timeout, transient throttle) shouldn't abort startup. The
                # per-table client below works against a table whether or not
                # we just verified existence here.
                logger.warning("create_table(%s) failed — assuming the table exists.", name, exc_info=True)
        self._events = self._service.get_table_client(events_table)
        self._drops = self._service.get_table_client(drops_table)
        self._engagements = self._service.get_table_client(engagements_table)
        self._reply_replies = self._service.get_table_client(reply_replies_table)
        self._info_views = self._service.get_table_client(info_views_table)

    def write_event(self, ev: MentionEvent) -> None:
        entity = self._event_entity(ev)
        try:
            self._events.create_entity(entity)
            logger.info("Wrote event to tables (mention=%s outcome=%s)", ev.mention_id, ev.outcome)
        except Exception:
            logger.exception("write_event failed for mention %s; continuing", ev.mention_id)

    def write_drop(self, drop: MentionDrop) -> None:
        entity = self._drop_entity(drop)
        try:
            self._drops.create_entity(entity)
            logger.info("Wrote drop to tables (mention=%s reason=%s)", drop.mention_id, drop.drop_reason)
        except Exception:
            logger.exception(
                "write_drop failed for mention %s reason=%s; continuing",
                drop.mention_id, drop.drop_reason,
            )

    def _event_entity(self, ev: MentionEvent) -> dict[str, Any]:
        received_at = _ensure_utc(ev.received_at_utc)
        pipeline_start = _ensure_utc(ev.pipeline_start_utc) if ev.pipeline_start_utc else None
        reply_posted = _ensure_utc(ev.reply_posted_utc) if ev.reply_posted_utc else None
        return {
            "PartitionKey": received_at.strftime("%Y-%m"),
            "RowKey": f"{received_at.isoformat()}_{ev.mention_id}",
            "mention_id": ev.mention_id,
            "parent_id": ev.parent_id,
            "author_id": ev.author_id,
            "author_username": ev.author_username,
            "tone": ev.tone,
            "parent_text": self._truncate(ev.parent_text),
            "parent_author_id": ev.parent_author_id,
            "parent_author_username": ev.parent_author_username,
            "parent_like_count": ev.parent_like_count,
            "parent_retweet_count": ev.parent_retweet_count,
            "parent_reply_count": ev.parent_reply_count,
            "parent_quote_count": ev.parent_quote_count,
            "queries_json": json.dumps(ev.queries, ensure_ascii=False),
            "reply_text": self._truncate(ev.reply_text),
            "reply_id": ev.reply_id,
            "link_reply_id": ev.link_reply_id,
            "info_token": ev.info_token,
            "received_at_utc": received_at,
            "pipeline_start_utc": pipeline_start,
            "reply_posted_utc": reply_posted,
            "pipeline_ms": ev.pipeline_ms,
            "outcome": ev.outcome,
            "reply_type": ev.reply_type,
            "action": ev.action,
            "action_outcome": ev.action_outcome,
            "invoker_instruction_text": self._truncate(ev.invoker_instruction_text, cap=500),
            "error_class": ev.error_class,
            "error_detail": self._truncate(ev.error_detail, cap=1000),
            "study_code": ev.study_code,
            "participant_id": ev.participant_id,
            "study_day": ev.study_day,
            "bot_author_id": ev.bot_author_id,
            "bot_handle": ev.bot_handle,
        }

    def _drop_entity(self, drop: MentionDrop) -> dict[str, Any]:
        # PartitionKey by month; RowKey must be unique even when mention_id is
        # missing on an invalid_payload — fingerprint with the timestamp.
        received_at = _ensure_utc(drop.received_at_utc)
        rk_id = drop.mention_id or f"nomid_{received_at.timestamp():.6f}"
        return {
            "PartitionKey": received_at.strftime("%Y-%m"),
            "RowKey": f"{received_at.isoformat()}_{rk_id}",
            "mention_id": drop.mention_id,
            "author_id": drop.author_id,
            "tone": drop.tone,
            "drop_reason": drop.drop_reason,
            "received_at_utc": received_at,
            "extra_json": json.dumps(drop.extra, ensure_ascii=False, default=str),
        }

    def write_engagement(self, snap: EngagementSnapshot) -> None:
        polled_at = _ensure_utc(snap.polled_at_utc)
        entity = {
            "PartitionKey": polled_at.strftime("%Y-%m"),
            "RowKey": f"{polled_at.isoformat()}_{snap.reply_id}",
            "reply_id": snap.reply_id,
            "tone": snap.tone,
            "polled_at_utc": polled_at,
            "like_count": snap.like_count,
            "retweet_count": snap.retweet_count,
            "reply_count": snap.reply_count,
            "quote_count": snap.quote_count,
            "mention_id": snap.mention_id,
            "parent_id": snap.parent_id,
            "adjusted_reply_count": snap.adjusted_reply_count,
        }
        try:
            self._engagements.upsert_entity(entity)
        except Exception:
            logger.exception("write_engagement failed for reply %s; continuing", snap.reply_id)

    def write_reply_reply(self, reply: BotReplyReply) -> None:
        collected_at = _ensure_utc(reply.collected_at_utc)
        entity = {
            "PartitionKey": collected_at.strftime("%Y-%m"),
            "RowKey": f"{collected_at.isoformat()}_{reply.reply_tweet_id}",
            "bot_reply_id": reply.bot_reply_id,
            "reply_tweet_id": reply.reply_tweet_id,
            "author_id": reply.author_id,
            "author_username": reply.author_username,
            "text": self._truncate(reply.text),
            "like_count": reply.like_count,
            "collected_at_utc": collected_at,
            "mention_id": reply.mention_id,
            "tone": reply.tone,
        }
        try:
            self._reply_replies.upsert_entity(entity)
        except Exception:
            logger.exception(
                "write_reply_reply failed for reply_tweet_id=%s; continuing", reply.reply_tweet_id
            )

    def write_info_view(self, view: InfoView) -> None:
        # Same token can be viewed many times → RowKey needs a per-view nonce
        # on top of the timestamp so concurrent hits don't collide.
        viewed_at = _ensure_utc(view.viewed_at_utc)
        entity = {
            "PartitionKey": viewed_at.strftime("%Y-%m"),
            "RowKey": f"{viewed_at.isoformat()}_{view.token}_{uuid.uuid4().hex[:8]}",
            "token": view.token,
            "viewed_at_utc": viewed_at,
            "reply_id": view.reply_id,
            "parent_id": view.parent_id,
            "mention_id": view.mention_id,
            "participant_id": view.participant_id,
            "tone": view.tone,
            "user_agent": self._truncate(view.user_agent, 1024),
            "referrer": self._truncate(view.referrer, 1024),
            "is_bot": view.is_bot,
        }
        try:
            self._info_views.create_entity(entity)
        except Exception:
            logger.exception("write_info_view failed for token %s; continuing", view.token)

    def iter_reply_ids(self) -> list[tuple[str, str, datetime | None, str | None, str | None, str | None]]:
        result = []
        try:
            for entity in self._events.list_entities(
                select=["reply_id", "tone", "reply_posted_utc", "mention_id", "parent_id", "link_reply_id"]
            ):
                rid = entity.get("reply_id")
                tone = entity.get("tone", "")
                posted_at = entity.get("reply_posted_utc")
                mention_id = entity.get("mention_id")
                parent_id = entity.get("parent_id")
                link_reply_id = entity.get("link_reply_id")
                if rid:
                    result.append((rid, tone, posted_at, mention_id, parent_id, link_reply_id))
        except Exception:
            logger.exception("iter_reply_ids failed")
        return result

    def snapshotted_reply_ids(self) -> set[str]:
        result: set[str] = set()
        try:
            for entity in self._engagements.list_entities(select=["reply_id"]):
                rid = entity.get("reply_id")
                if rid:
                    result.add(rid)
        except Exception:
            logger.exception("snapshotted_reply_ids failed")
        return result

    def latest_snapshot_times(self) -> dict[str, datetime]:
        """Most recent poll time per bot reply — drives the 12h poll gap."""
        result: dict[str, datetime] = {}
        try:
            for entity in self._engagements.list_entities(select=["reply_id", "polled_at_utc"]):
                rid = entity.get("reply_id")
                ts = entity.get("polled_at_utc")
                if not rid or ts is None:
                    continue
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts)
                    except ValueError:
                        continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                prev = result.get(rid)
                if prev is None or ts > prev:
                    result[rid] = ts
        except Exception:
            logger.exception("latest_snapshot_times failed")
        return result

    def collected_reply_ids(self) -> set[str]:
        result: set[str] = set()
        try:
            for entity in self._reply_replies.list_entities(select=["bot_reply_id"]):
                rid = entity.get("bot_reply_id")
                if rid:
                    result.add(rid)
        except Exception:
            logger.exception("collected_reply_ids failed")
        return result

    _EVENT_SELECT = [
        "RowKey", "mention_id", "author_id", "author_username", "tone", "outcome",
        "received_at_utc", "pipeline_ms", "study_day", "study_code",
        "reply_text", "parent_text", "reply_id", "link_reply_id", "bot_author_id", "bot_handle",
        "action", "action_outcome", "invoker_instruction_text", "info_token",
    ]
    _DROP_SELECT = [
        "RowKey", "mention_id", "author_id", "tone", "drop_reason", "received_at_utc",
    ]
    _ENGAGEMENT_SELECT = [
        "RowKey", "reply_id", "tone", "polled_at_utc", "like_count",
        "retweet_count", "reply_count", "quote_count", "mention_id", "parent_id",
        "adjusted_reply_count",
    ]
    _REPLY_REPLY_SELECT = [
        "RowKey", "bot_reply_id", "reply_tweet_id", "author_id", "author_username",
        "text", "like_count", "collected_at_utc", "mention_id", "tone",
    ]
    _INFO_VIEW_SELECT = [
        "RowKey", "token", "viewed_at_utc", "reply_id", "parent_id", "mention_id",
        "participant_id", "tone", "user_agent", "referrer", "is_bot",
    ]

    @staticmethod
    def _normalize(d: dict) -> dict:
        d.pop("odata.etag", None)
        for k, v in list(d.items()):
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        return d

    def list_recent(self, limit: int = 50) -> list[dict]:
        now = datetime.now(timezone.utc)
        months = [now.strftime("%Y-%m")]
        if now.day <= 3:
            months.append((now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m"))
        result = []
        try:
            for month in months:
                for entity in self._events.query_entities(
                    f"PartitionKey eq '{month}'", select=self._EVENT_SELECT
                ):
                    d = self._normalize(dict(entity))
                    for fld in ("reply_text", "parent_text"):
                        if d.get(fld):
                            d[fld] = d[fld][:120]
                    result.append(d)
        except Exception:
            logger.exception("list_recent failed")
        result.sort(key=lambda e: e.get("RowKey", ""), reverse=True)
        return result[:limit]

    def list_recent_drops(self, limit: int = 20) -> list[dict]:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        result = []
        try:
            for entity in self._drops.query_entities(
                f"PartitionKey eq '{month}'", select=self._DROP_SELECT
            ):
                result.append(self._normalize(dict(entity)))
        except Exception:
            logger.exception("list_recent_drops failed")
        result.sort(key=lambda e: e.get("RowKey", ""), reverse=True)
        return result[:limit]

    def list_recent_engagements(self, limit: int = 500) -> list[dict]:
        # One snapshot per bot reply for the lifetime of the study — low volume,
        # so a full scan (no month partition filter) keeps the dashboard correct
        # across month boundaries. RowKey is polled_at-prefixed → newest first.
        result = []
        try:
            for entity in self._engagements.list_entities(select=self._ENGAGEMENT_SELECT):
                result.append(self._normalize(dict(entity)))
        except Exception:
            logger.exception("list_recent_engagements failed")
        result.sort(key=lambda e: e.get("RowKey", ""), reverse=True)
        return result[:limit]

    def list_recent_reply_replies(self, limit: int = 500) -> list[dict]:
        result = []
        try:
            for entity in self._reply_replies.list_entities(select=self._REPLY_REPLY_SELECT):
                d = self._normalize(dict(entity))
                if d.get("text"):
                    d["text"] = d["text"][:280]
                result.append(d)
        except Exception:
            logger.exception("list_recent_reply_replies failed")
        result.sort(key=lambda e: e.get("RowKey", ""), reverse=True)
        return result[:limit]

    def list_recent_info_views(self, limit: int = 500) -> list[dict]:
        result = []
        try:
            for entity in self._info_views.list_entities(select=self._INFO_VIEW_SELECT):
                result.append(self._normalize(dict(entity)))
        except Exception:
            logger.exception("list_recent_info_views failed")
        result.sort(key=lambda e: e.get("RowKey", ""), reverse=True)
        return result[:limit]

    def _truncate(self, value: Optional[str], cap: int = _FIELD_CAP) -> Optional[str]:
        if value is None:
            return None
        if len(value) <= cap:
            return value
        return value[: cap - 1] + "…"


# ── Singleton selector ──────────────────────────────────────────────────────

_default_store: Optional[EventsStore] = None
_default_lock = threading.Lock()


def _build_default_store() -> EventsStore:
    backend = os.getenv("DERAD_EVENTS_BACKEND", "memory").lower()
    if backend == "tables":
        endpoint = os.getenv("DERAD_TABLES_ENDPOINT")
        if not endpoint:
            logger.warning(
                "DERAD_EVENTS_BACKEND=tables but DERAD_TABLES_ENDPOINT is unset; "
                "falling back to InMemoryEventsStore"
            )
            return InMemoryEventsStore()
        logger.info("Events store: TablesEventsStore at %s", endpoint)
        return TablesEventsStore(endpoint)
    logger.info("Events store: InMemoryEventsStore")
    return InMemoryEventsStore()


def get_store() -> EventsStore:
    """Return the process-wide events store, lazily constructed on first call."""
    global _default_store
    if _default_store is not None:
        return _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = _build_default_store()
    return _default_store


def reset_store(new: Optional[EventsStore] = None) -> None:
    """Test hook: replace the singleton."""
    global _default_store
    _default_store = new


# ── Public writers used by app.py ───────────────────────────────────────────

def log_mention_event(ev: MentionEvent) -> None:
    """Best-effort write. Never raises; never blocks the bot."""
    try:
        get_store().write_event(ev)
    except Exception:
        logger.exception("log_mention_event swallowed exception for mention %s", ev.mention_id)


def log_mention_drop(drop: MentionDrop) -> None:
    """Best-effort write. Never raises; never blocks the bot."""
    try:
        get_store().write_drop(drop)
    except Exception:
        logger.exception("log_mention_drop swallowed exception for reason %s", drop.drop_reason)


def log_engagement_snapshot(snap: EngagementSnapshot) -> None:
    """Best-effort write. Never raises."""
    try:
        get_store().write_engagement(snap)
    except Exception:
        logger.exception("log_engagement_snapshot swallowed exception for reply %s", snap.reply_id)


def log_reply_reply(reply: BotReplyReply) -> None:
    """Best-effort write. Never raises."""
    try:
        get_store().write_reply_reply(reply)
    except Exception:
        logger.exception(
            "log_reply_reply swallowed exception for reply_tweet_id=%s", reply.reply_tweet_id
        )


def log_info_view(view: InfoView) -> None:
    """Best-effort write. Never raises; never blocks the page render."""
    try:
        get_store().write_info_view(view)
    except Exception:
        logger.exception("log_info_view swallowed exception for token %s", view.token)


def utcnow() -> datetime:
    """Single source of UTC-now so timings line up across the codebase."""
    return datetime.now(timezone.utc)


# Minimum age before a bot reply is eligible for engagement/bystander collection.
SNAPSHOT_MIN_AGE = timedelta(days=3)
