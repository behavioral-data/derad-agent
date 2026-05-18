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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


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

    # Pipeline output
    queries: list[str] = field(default_factory=list)
    cited_note_ids: list[str] = field(default_factory=list)
    cited_tweet_ids: list[str] = field(default_factory=list)
    reply_text: Optional[str] = None
    reply_id: Optional[str] = None
    sources_reply_id: Optional[str] = None

    # Outcome
    outcome: str = "replied"  # 'replied' | 'pipeline_error' | 'x_post_error' | 'parent_fetch_failed' | 'empty_reply'
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


# ── Store interface ─────────────────────────────────────────────────────────

class EventsStore(Protocol):
    def write_event(self, ev: MentionEvent) -> None: ...
    def write_drop(self, drop: MentionDrop) -> None: ...


# ── In-memory backend (tests + local dev) ───────────────────────────────────

class InMemoryEventsStore:
    """Single-process append-only list. Inspect via the .events / .drops attrs."""

    def __init__(self) -> None:
        self.events: list[MentionEvent] = []
        self.drops: list[MentionDrop] = []
        self._lock = threading.Lock()

    def write_event(self, ev: MentionEvent) -> None:
        with self._lock:
            self.events.append(ev)

    def write_drop(self, drop: MentionDrop) -> None:
        with self._lock:
            self.drops.append(drop)


# ── Azure Tables backend ────────────────────────────────────────────────────

class TablesEventsStore:
    """Azure Table Storage backend.

    PartitionKey = YYYY-MM (cheap monthly export / cleanup).
    RowKey       = received_at ISO + mention_id (sortable, unique).

    Long fields (parent_text, reply_text, error_detail) are truncated to 32 kB
    each — the Tables row limit is ~1 MB total and we want headroom for the
    JSON-encoded lists. ``queries``, ``cited_note_ids``, ``cited_tweet_ids``,
    and ``extra`` are JSON-encoded as strings since Tables doesn't natively
    store lists/dicts.
    """

    _FIELD_CAP = 32_000  # bytes; rough char cap is fine for our text

    def __init__(
        self,
        endpoint: str,
        *,
        events_table: str = "MentionEvents",
        drops_table: str = "MentionDrops",
        credential=None,
    ) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential

        cred = credential or DefaultAzureCredential()
        self._service = TableServiceClient(endpoint=endpoint, credential=cred)
        for name in (events_table, drops_table):
            try:
                self._service.create_table(name)
                logger.info("Created events table %s", name)
            except ResourceExistsError:
                pass
        self._events = self._service.get_table_client(events_table)
        self._drops = self._service.get_table_client(drops_table)

    def write_event(self, ev: MentionEvent) -> None:
        entity = self._event_entity(ev)
        try:
            self._events.create_entity(entity)
        except Exception:
            logger.exception("write_event failed for mention %s; continuing", ev.mention_id)

    def write_drop(self, drop: MentionDrop) -> None:
        entity = self._drop_entity(drop)
        try:
            self._drops.create_entity(entity)
        except Exception:
            logger.exception(
                "write_drop failed for mention %s reason=%s; continuing",
                drop.mention_id, drop.drop_reason,
            )

    def _event_entity(self, ev: MentionEvent) -> dict[str, Any]:
        return {
            "PartitionKey": ev.received_at_utc.strftime("%Y-%m"),
            "RowKey": f"{ev.received_at_utc.isoformat()}_{ev.mention_id}",
            "mention_id": ev.mention_id,
            "parent_id": ev.parent_id,
            "author_id": ev.author_id,
            "author_username": ev.author_username,
            "tone": ev.tone,
            "parent_text": self._truncate(ev.parent_text),
            "parent_author_id": ev.parent_author_id,
            "parent_author_username": ev.parent_author_username,
            "queries_json": json.dumps(ev.queries, ensure_ascii=False),
            "cited_note_ids_json": json.dumps(ev.cited_note_ids),
            "cited_tweet_ids_json": json.dumps(ev.cited_tweet_ids),
            "reply_text": self._truncate(ev.reply_text),
            "reply_id": ev.reply_id,
            "sources_reply_id": ev.sources_reply_id,
            "received_at_utc": ev.received_at_utc,
            "pipeline_start_utc": ev.pipeline_start_utc,
            "reply_posted_utc": ev.reply_posted_utc,
            "pipeline_ms": ev.pipeline_ms,
            "outcome": ev.outcome,
            "error_class": ev.error_class,
            "error_detail": self._truncate(ev.error_detail, cap=1000),
        }

    def _drop_entity(self, drop: MentionDrop) -> dict[str, Any]:
        # PartitionKey by month; RowKey must be unique even when mention_id is
        # missing on an invalid_payload — fingerprint with the timestamp.
        rk_id = drop.mention_id or f"nomid_{drop.received_at_utc.timestamp():.6f}"
        return {
            "PartitionKey": drop.received_at_utc.strftime("%Y-%m"),
            "RowKey": f"{drop.received_at_utc.isoformat()}_{rk_id}",
            "mention_id": drop.mention_id,
            "author_id": drop.author_id,
            "tone": drop.tone,
            "drop_reason": drop.drop_reason,
            "received_at_utc": drop.received_at_utc,
            "extra_json": json.dumps(drop.extra, ensure_ascii=False, default=str),
        }

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
        endpoint = os.environ["DERAD_TABLES_ENDPOINT"]
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


def utcnow() -> datetime:
    """Single source of UTC-now so timings line up across the codebase."""
    return datetime.now(timezone.utc)
