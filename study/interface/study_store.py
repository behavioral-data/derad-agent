"""Writable study state: per-participant condition/post assignments and
per-post exposure events.

Two backends, same interface:
  - InMemoryStudyStore   — dev / tests (not durable across restarts)
  - TablesStudyStore      — Azure Table Storage (production; durable)

Selected by get_store(): Tables when DERAD_STUDY_TABLES_ENDPOINT (or the shared
AZURE_STORAGE_TABLES_ENDPOINT) is set, else in-memory. Auth is
DefaultAzureCredential (the App Service's managed identity in prod).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

log = logging.getLogger(__name__)

ASSIGN_TABLE = "studyassignments"
EXPOSURE_TABLE = "studyexposures"
_ASSIGN_PK = "assign"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Assignment:
    pid: str
    condition: str
    blocks: list[list[str]]        # blocks[day-1] = that day's post_ids
    created_at: str = field(default_factory=_now_iso)

    @property
    def post_ids(self) -> list[str]:
        return [pid for blk in self.blocks for pid in blk]


@dataclass
class Exposure:
    pid: str
    condition: str
    post_id: str
    code: str
    day: int
    dwell_ms: int = 0
    viewed_at: str = field(default_factory=_now_iso)


class StudyStore(Protocol):
    def get_assignment(self, pid: str) -> Optional[Assignment]: ...
    def put_assignment(self, a: Assignment) -> None: ...
    def all_assignments(self) -> list[Assignment]: ...
    def log_exposure(self, e: Exposure) -> None: ...


class InMemoryStudyStore:
    def __init__(self) -> None:
        self._assign: dict[str, Assignment] = {}
        self._exposures: dict[tuple, Exposure] = {}
        self._lock = threading.Lock()

    def get_assignment(self, pid: str) -> Optional[Assignment]:
        with self._lock:
            return self._assign.get(pid)

    def put_assignment(self, a: Assignment) -> None:
        with self._lock:
            self._assign[a.pid] = a

    def all_assignments(self) -> list[Assignment]:
        with self._lock:
            return list(self._assign.values())

    def log_exposure(self, e: Exposure) -> None:
        # Upsert by (pid, code, day): the on-load write records the exposure,
        # a later pagehide write updates dwell — one row per participant-post.
        with self._lock:
            self._exposures[(e.pid, e.code, e.day)] = e

    # test helper
    def exposures(self) -> list[Exposure]:
        with self._lock:
            return list(self._exposures.values())


class TablesStudyStore:
    """Azure Table Storage backend. Assignments live in one partition (small,
    fully listable for balancing); exposures are partitioned by pid."""

    def __init__(self, endpoint: str) -> None:
        from azure.data.tables import TableServiceClient
        from azure.core.credentials import AzureNamedKeyCredential  # noqa: F401 (import guard)
        from azure.identity import DefaultAzureCredential
        svc = TableServiceClient(endpoint=endpoint, credential=DefaultAzureCredential())
        self._assign = svc.create_table_if_not_exists(ASSIGN_TABLE)
        self._expo = svc.create_table_if_not_exists(EXPOSURE_TABLE)

    def get_assignment(self, pid: str) -> Optional[Assignment]:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            e = self._assign.get_entity(_ASSIGN_PK, pid)
        except ResourceNotFoundError:
            return None
        return Assignment(pid=pid, condition=e["condition"],
                          blocks=json.loads(e["blocks"]), created_at=e.get("created_at", ""))

    def put_assignment(self, a: Assignment) -> None:
        self._assign.upsert_entity({
            "PartitionKey": _ASSIGN_PK, "RowKey": a.pid,
            "condition": a.condition, "blocks": json.dumps(a.blocks),
            "created_at": a.created_at,
        })

    def all_assignments(self) -> list[Assignment]:
        out = []
        for e in self._assign.list_entities():
            out.append(Assignment(pid=e["RowKey"], condition=e["condition"],
                                  blocks=json.loads(e["blocks"]), created_at=e.get("created_at", "")))
        return out

    def log_exposure(self, e: Exposure) -> None:
        # Deterministic RowKey + upsert: one row per (participant, post, day),
        # updated in place when the pagehide dwell arrives.
        self._expo.upsert_entity({
            "PartitionKey": e.pid,
            "RowKey": f"{e.code}_{e.day}",
            "condition": e.condition, "post_id": e.post_id, "code": e.code,
            "day": e.day, "dwell_ms": e.dwell_ms, "viewed_at": e.viewed_at,
        })


_STORE: Optional[StudyStore] = None
_STORE_LOCK = threading.Lock()


def _build_default_store() -> StudyStore:
    endpoint = os.environ.get("DERAD_STUDY_TABLES_ENDPOINT") or os.environ.get(
        "AZURE_STORAGE_TABLES_ENDPOINT")
    if endpoint:
        log.info("StudyStore: TablesStudyStore at %s", endpoint)
        return TablesStudyStore(endpoint)
    log.info("StudyStore: InMemoryStudyStore (no tables endpoint configured)")
    return InMemoryStudyStore()


def get_store() -> StudyStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = _build_default_store()
        return _STORE


def reset_store(new: Optional[StudyStore] = None) -> None:
    """Test hook."""
    global _STORE
    with _STORE_LOCK:
        _STORE = new
