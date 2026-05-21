"""Participant registry for derad-agent.

Stores the ~30 UW student invokers enrolled in the study.
Two backends — same pattern as dedup.py and events.py:

  - InMemoryParticipantsStore — default; tests + local dev
  - TablesParticipantsStore   — Azure Table Storage (production)

Backend selected by DERAD_PARTICIPANTS_BACKEND ("memory" | "tables").
Endpoint reused from DERAD_TABLES_ENDPOINT.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

_TABLE_NAME = "Participants"
_PARTITION = "participants"


@dataclass
class Participant:
    """One registered study participant."""
    author_id: str           # X numeric user ID — primary lookup key
    author_username: str     # @handle (human-readable, for DM composition)
    tone: str                # assigned bot: agreeable | neutral | satirical
    enrolled_at_utc: datetime
    notes: str = ""          # optional researcher notes


class ParticipantsStore(Protocol):
    def register(self, p: Participant) -> None: ...
    def get(self, author_id: str) -> Optional[Participant]: ...
    def list_all(self) -> list[Participant]: ...


class InMemoryParticipantsStore:
    """Thread-safe in-memory store — for tests and local dev."""

    def __init__(self) -> None:
        self._store: dict[str, Participant] = {}
        self._lock = threading.Lock()

    def register(self, p: Participant) -> None:
        with self._lock:
            self._store[p.author_id] = p

    def get(self, author_id: str) -> Optional[Participant]:
        with self._lock:
            return self._store.get(author_id)

    def list_all(self) -> list[Participant]:
        with self._lock:
            return list(self._store.values())


class TablesParticipantsStore:
    """Azure Table Storage backend.

    Single partition ('participants') — ~30 rows, no scan optimization needed.
    RowKey = author_id (X numeric user ID, globally unique and stable).
    Upsert semantics: re-registering a participant updates their record.
    """

    def __init__(self, endpoint: str, *, credential=None) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential

        cred = credential or DefaultAzureCredential()
        svc = TableServiceClient(endpoint=endpoint, credential=cred)
        try:
            svc.create_table(_TABLE_NAME)
            logger.info("Created table %s", _TABLE_NAME)
        except ResourceExistsError:
            pass
        self._tbl = svc.get_table_client(_TABLE_NAME)

    def register(self, p: Participant) -> None:
        entity = {
            "PartitionKey": _PARTITION,
            "RowKey": p.author_id,
            "author_username": p.author_username,
            "tone": p.tone,
            "enrolled_at_utc": p.enrolled_at_utc,
            "notes": p.notes,
        }
        try:
            self._tbl.upsert_entity(entity)
            logger.info("Registered participant author_id=%s username=%s tone=%s",
                        p.author_id, p.author_username, p.tone)
        except Exception:
            logger.exception("register failed for author_id=%s", p.author_id)

    def get(self, author_id: str) -> Optional[Participant]:
        try:
            ent = self._tbl.get_entity(_PARTITION, author_id)
            return _entity_to_participant(ent)
        except Exception:
            return None

    def list_all(self) -> list[Participant]:
        try:
            return [
                _entity_to_participant(ent)
                for ent in self._tbl.query_entities(
                    f"PartitionKey eq '{_PARTITION}'"
                )
            ]
        except Exception:
            logger.exception("list_all failed")
            return []


def _entity_to_participant(ent: dict) -> Participant:
    enrolled = ent.get("enrolled_at_utc")
    if isinstance(enrolled, str):
        enrolled = datetime.fromisoformat(enrolled)
    if enrolled and enrolled.tzinfo is None:
        enrolled = enrolled.replace(tzinfo=timezone.utc)
    return Participant(
        author_id=ent["RowKey"],
        author_username=ent.get("author_username", ""),
        tone=ent.get("tone", ""),
        enrolled_at_utc=enrolled or datetime.now(timezone.utc),
        notes=ent.get("notes", ""),
    )


# ── Singleton ────────────────────────────────────────────────────────────────

_default_store: Optional[ParticipantsStore] = None
_default_lock = threading.Lock()


def _build_default_store() -> ParticipantsStore:
    backend = os.getenv("DERAD_PARTICIPANTS_BACKEND", "memory").lower()
    if backend == "tables":
        endpoint = os.environ["DERAD_TABLES_ENDPOINT"]
        logger.info("Participants store: TablesParticipantsStore at %s", endpoint)
        return TablesParticipantsStore(endpoint)
    logger.info("Participants store: InMemoryParticipantsStore")
    return InMemoryParticipantsStore()


def get_store() -> ParticipantsStore:
    """Return the process-wide participants store, lazily constructed on first call."""
    global _default_store
    if _default_store is not None:
        return _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = _build_default_store()
    return _default_store


def reset_store(new: Optional[ParticipantsStore] = None) -> None:
    """Test hook: replace the singleton."""
    global _default_store
    _default_store = new
