"""Pluggable dedup + rate-limit store.

Two backends:
  - InMemoryStore — single-process, lock-protected. Default; used in tests and
    local dev. Loses state on restart, which is acceptable for research-scale
    traffic.
  - TablesStore — Azure Table Storage, authenticated with DefaultAzureCredential
    (App Service managed identity in prod, az-cli/env fallback locally).

Backend selection is via DERAD_STORE_BACKEND ("memory" | "tables"). The Tables
backend requires DERAD_TABLES_ENDPOINT.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol

logger = logging.getLogger(__name__)


class DedupRateStore(Protocol):
    def claim(self, key: str, ttl_seconds: int = 86400) -> bool:
        """Atomically reserve a key. Returns True if newly inserted, False if already present."""
        ...

    def hit_and_count(self, key: str, window_seconds: int) -> int:
        """Record a hit and return the count of hits within the rolling window."""
        ...


class InMemoryStore:
    """Thread-safe in-memory dedup + rate-limit store with opportunistic eviction."""

    def __init__(self) -> None:
        self._dedup: dict[str, float] = {}
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def claim(self, key: str, ttl_seconds: int = 86400) -> bool:
        now = time.time()
        with self._lock:
            self._evict_dedup(now)
            if self._dedup.get(key, 0) > now:
                return False
            self._dedup[key] = now + ttl_seconds
            return True

    def hit_and_count(self, key: str, window_seconds: int) -> int:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            # Sweep all keys: prune expired timestamps and drop empty entries.
            # Bounded by active-author count, runs once per hit — keeps memory
            # from leaking when authors stop sending mentions.
            for k in list(self._hits.keys()):
                self._hits[k] = [t for t in self._hits[k] if t >= cutoff]
                if not self._hits[k]:
                    del self._hits[k]
            timestamps = self._hits.setdefault(key, [])
            timestamps.append(now)
            return len(timestamps)

    def _evict_dedup(self, now: float) -> None:
        expired = [k for k, expiry in self._dedup.items() if expiry <= now]
        for k in expired:
            del self._dedup[k]


class TablesStore:
    """Azure Table Storage backend.

    Two tables. The dedup table uses a single, fixed PartitionKey
    (``DEDUP_PARTITION``) so a duplicate-event check is a single PK+RK lookup
    — no risk of a midnight-UTC straddle splitting one mention across two
    partitions. Tables are 5 GB free / 200 TB max per account, and our row
    rate is single-digit per second, well under the 2000 ops/sec single-
    partition limit. ``ExpiresAtUtc`` is stored for an out-of-band cleanup
    job; we don't currently delete rows.

    The rate-limit table partitions by the rate-limit key (author id) and
    stores ``AtUtc`` as a real ``Edm.DateTime`` (passing a tz-aware datetime
    to the SDK gives us that). OData comparisons then use ``datetime'...'``
    syntax with proper chronological semantics — no lex-vs-chrono surprises.

    Auth: DefaultAzureCredential, which picks the App Service UAMI in
    production and falls through to Azure CLI / env vars locally.
    """

    DEDUP_PARTITION = "mentions"

    def __init__(
        self,
        endpoint: str,
        *,
        dedup_table: str = "Mentions",
        rate_table: str = "RateLimits",
        credential=None,
    ) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential

        self._ResourceExistsError = ResourceExistsError
        cred = credential or DefaultAzureCredential()
        self._service = TableServiceClient(
            endpoint=endpoint,
            credential=cred,
            connection_timeout=10,
            read_timeout=15,
        )
        # Idempotent create — safe to call on every boot, and we don't let
        # a single slow round-trip block startup.
        for name in (dedup_table, rate_table):
            try:
                self._service.create_table(name)
                logger.info("Created Tables table %s", name)
            except ResourceExistsError:
                pass
            except Exception:
                logger.warning("create_table(%s) failed — assuming it exists.", name, exc_info=True)
        self._dedup = self._service.get_table_client(dedup_table)
        self._rate = self._service.get_table_client(rate_table)

    def claim(self, key: str, ttl_seconds: int = 86400) -> bool:
        now = datetime.now(timezone.utc)
        entity = {
            "PartitionKey": self.DEDUP_PARTITION,
            "RowKey": key,
            # Stored as Edm.DateTime when passed as datetime; useful for the
            # eventual cleanup pass that drops rows older than max(ttl_seconds).
            "ExpiresAtUtc": now + timedelta(seconds=ttl_seconds),
            "ClaimedAtUtc": now,
        }
        try:
            self._dedup.create_entity(entity)
            return True
        except self._ResourceExistsError:
            return False

    def hit_and_count(self, key: str, window_seconds: int) -> int:
        now = datetime.now(timezone.utc)
        # RowKey is monotonic-ish so range scans by time are cheap if we ever
        # need them; uuid suffix avoids collisions on simultaneous inserts.
        row_key = f"{now.timestamp():020.6f}-{uuid.uuid4().hex[:8]}"
        self._rate.create_entity(
            {
                "PartitionKey": key,
                "RowKey": row_key,
                # Passing a tz-aware datetime tells the SDK to write Edm.DateTime,
                # which makes the OData filter below a real chronological compare.
                "AtUtc": now,
            }
        )
        cutoff = now - timedelta(seconds=window_seconds)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        filter_q = f"PartitionKey eq '{key}' and AtUtc ge datetime'{cutoff_str}'"
        return sum(1 for _ in self._rate.query_entities(query_filter=filter_q))


def _build_default_store() -> DedupRateStore:
    backend = os.getenv("DERAD_STORE_BACKEND", "memory").lower()
    if backend == "tables":
        endpoint = os.environ["DERAD_TABLES_ENDPOINT"]
        logger.info("Dedup/rate-limit store: TablesStore at %s", endpoint)
        return TablesStore(endpoint)
    logger.info("Dedup/rate-limit store: InMemoryStore")
    return InMemoryStore()


_default_store: DedupRateStore | None = None
_default_lock = threading.Lock()


def get_store() -> DedupRateStore:
    """Return the process-wide store, lazily constructed on first call."""
    global _default_store
    if _default_store is not None:
        return _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = _build_default_store()
    return _default_store


def reset_store(new: DedupRateStore | None = None) -> None:
    """Test hook: replace the singleton."""
    global _default_store
    _default_store = new
