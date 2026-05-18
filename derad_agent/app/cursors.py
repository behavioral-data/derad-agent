"""Poll-cursor store: persists the newest tweet ID seen per bot tone.

Two backends:
  - InMemoryCursorStore — default; used in tests and local dev. Loses state
    on restart, which causes the first poll after a restart to re-examine
    recent mentions. The dedup store guards against double-posting.
  - TablesCursorStore   — Azure Table Storage, authenticated with
    DefaultAzureCredential. Persists across restarts.

Backend: DERAD_CURSOR_BACKEND ("memory" | "tables", default "memory").
Endpoint: DERAD_TABLES_ENDPOINT (same table-storage endpoint as dedup/events).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Protocol

logger = logging.getLogger(__name__)


class CursorStore(Protocol):
    def get(self, key: str) -> str | None:
        """Return the stored cursor value, or None if not set."""
        ...

    def set(self, key: str, value: str) -> None:
        """Persist the cursor value."""
        ...


class InMemoryCursorStore:
    """Thread-safe in-memory cursor store. Loses state on restart."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value


class TablesCursorStore:
    """Azure Table Storage cursor store.

    PartitionKey is fixed ("cursors") so every cursor is a single PK+RK
    lookup — cheap and predictable. RowKey is the cursor key (e.g.
    "poll_cursor:neutral").

    Auth: DefaultAzureCredential (App Service UAMI in prod, az-cli locally).
    """

    PARTITION = "cursors"

    def __init__(
        self,
        endpoint: str,
        *,
        table: str = "Cursors",
        credential=None,
    ) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential

        self._ResourceNotFoundError: type
        from azure.core.exceptions import ResourceNotFoundError

        self._ResourceNotFoundError = ResourceNotFoundError
        cred = credential or DefaultAzureCredential()
        svc = TableServiceClient(endpoint=endpoint, credential=cred)
        try:
            svc.create_table(table)
            logger.info("Created Tables table %s", table)
        except ResourceExistsError:
            pass
        self._client = svc.get_table_client(table)

    def get(self, key: str) -> str | None:
        try:
            entity = self._client.get_entity(partition_key=self.PARTITION, row_key=key)
            return entity.get("cursor_value")
        except self._ResourceNotFoundError:
            return None

    def set(self, key: str, value: str) -> None:
        self._client.upsert_entity({
            "PartitionKey": self.PARTITION,
            "RowKey": key,
            "cursor_value": value,
        })


def _build_default_cursor_store() -> CursorStore:
    backend = os.getenv("DERAD_CURSOR_BACKEND", "memory").lower()
    if backend == "tables":
        endpoint = os.environ["DERAD_TABLES_ENDPOINT"]
        logger.info("Cursor store: TablesCursorStore at %s", endpoint)
        return TablesCursorStore(endpoint)
    logger.info("Cursor store: InMemoryCursorStore")
    return InMemoryCursorStore()


_cursor_store: CursorStore | None = None
_cursor_lock = threading.Lock()


def get_cursor_store() -> CursorStore:
    """Return the process-wide store, lazily constructed on first call."""
    global _cursor_store
    if _cursor_store is not None:
        return _cursor_store
    with _cursor_lock:
        if _cursor_store is None:
            _cursor_store = _build_default_cursor_store()
    return _cursor_store


def reset_cursor_store(new: CursorStore | None = None) -> None:
    """Test hook: replace the singleton."""
    global _cursor_store
    _cursor_store = new
