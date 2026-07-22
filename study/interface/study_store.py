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
PROFILE_TABLE = "studyprofiles"
PARTY_TABLE = "studypartymap"
_ASSIGN_PK = "assign"

try:                                    # optional import guard (azure-data-tables is dev-optional)
    from azure.core import MatchConditions as _MC
    _IF_MATCH = _MC.IfNotModified
except ImportError:                     # pragma: no cover - azure-core not installed
    _IF_MATCH = None


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
class StoredProfile:
    profile_id: str
    condition: str
    target_party: str
    blocks: list                       # list[list[str]] of post_ids
    claimed_by: Optional[str] = None


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
    def load_profiles(self, profiles: list[StoredProfile], claim_orders: dict[str, list[str]]) -> None: ...
    def claim_profile(self, pid: str, party: str) -> Optional[Assignment]: ...
    def release_profile(self, pid: str) -> None: ...


class InMemoryStudyStore:
    def __init__(self) -> None:
        self._assign: dict[str, Assignment] = {}
        self._exposures: dict[tuple, Exposure] = {}
        self._profiles: dict[str, StoredProfile] = {}
        self._party_map: dict[str, str] = {}
        self._claim_orders: dict[str, list[str]] = {}
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

    def load_party_map(self, mapping: dict[str, str]) -> None:
        with self._lock:
            self._party_map = dict(mapping)

    def get_party_map(self) -> dict[str, str]:
        with self._lock:
            return dict(self._party_map)

    def load_profiles(self, profiles: list[StoredProfile], claim_orders: dict[str, list[str]]) -> None:
        with self._lock:
            if self._profiles:                       # load once; don't wipe live claims
                return
            self._profiles = {p.profile_id: p for p in profiles}
            self._claim_orders = {k: list(v) for k, v in claim_orders.items()}

    def claim_profile(self, pid: str, party: str) -> Optional[Assignment]:
        with self._lock:
            existing = self._assign.get(pid)
            if existing is not None:
                return existing
            for prof_id in self._claim_orders.get(party, []):
                prof = self._profiles[prof_id]
                if prof.claimed_by is None:
                    prof.claimed_by = pid
                    a = Assignment(pid=pid, condition=prof.condition, blocks=prof.blocks)
                    self._assign[pid] = a
                    return a
            return None

    def release_profile(self, pid: str) -> None:
        with self._lock:
            self._assign.pop(pid, None)
            for prof in self._profiles.values():
                if prof.claimed_by == pid:
                    prof.claimed_by = None
                    break


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
        self._prof = svc.create_table_if_not_exists(PROFILE_TABLE)
        self._party = svc.create_table_if_not_exists(PARTY_TABLE)

    def load_party_map(self, mapping: dict[str, str]) -> None:
        """Upsert the pid->party invite map (idempotent; small, one partition)."""
        for pid, party in mapping.items():
            self._party.upsert_entity({"PartitionKey": "party", "RowKey": pid,
                                       "party": party})

    def get_party_map(self) -> dict[str, str]:
        return {e["RowKey"]: e["party"]
                for e in self._party.query_entities("PartitionKey eq 'party'")}

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

    def _create_assignment(self, a: Assignment) -> None:
        # Insert-only sibling of put_assignment: makes the first assignment for a
        # pid exactly-once. Raises ResourceExistsError if a racing first claim for
        # the same pid already wrote one. (put_assignment stays an upsert for any
        # other callers.)
        self._assign.create_entity({
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

    def load_profiles(self, profiles: list[StoredProfile], claim_orders: dict[str, list[str]]) -> None:
        # Resume-safe idempotent load. The old skip-if-any-row logic left a
        # permanently short pool if a first-boot crash interrupted the upserts
        # (some rows written, load then skipped forever). Instead: enumerate the
        # RowKeys already present per partition and insert ONLY the missing
        # entities with create_entity. Existing rows are never touched — they may
        # already hold a live `claimed_by`.
        from azure.core.exceptions import ResourceExistsError
        order_index = {pid: i for party in claim_orders for i, pid in enumerate(claim_orders[party])}
        by_id = {p.profile_id: p for p in profiles}
        existing: dict[str, set] = {}
        for e in self._prof.list_entities():
            existing.setdefault(e["PartitionKey"], set()).add(e["RowKey"])
        inserted, kept = 0, 0
        for pid, p in by_id.items():
            rk = f"{order_index[pid]:04d}"
            if rk in existing.get(p.target_party, set()):
                kept += 1
                continue
            try:
                self._prof.create_entity({
                    "PartitionKey": p.target_party,
                    "RowKey": rk,
                    "profile_id": pid, "condition": p.condition,
                    "blocks": json.dumps(p.blocks), "claimed_by": "",
                })
                inserted += 1
            except ResourceExistsError:               # created concurrently; leave it
                kept += 1
        log.info("TablesStudyStore.load_profiles: inserted %d, left %d existing untouched",
                 inserted, kept)

    def claim_profile(self, pid: str, party: str) -> Optional[Assignment]:
        from azure.core.exceptions import (ResourceExistsError,
                                           ResourceModifiedError,
                                           ResourceNotFoundError)
        existing = self.get_assignment(pid)
        if existing is not None:
            return existing
        # Walk claim order (RowKey ascending); take the first free profile, guarding
        # the write with the entity ETag so concurrent claims can't double-book.
        for _attempt in range(500):
            free = None
            for e in self._prof.query_entities(
                    f"PartitionKey eq '{party}' and claimed_by eq ''",
                    results_per_page=1):
                free = e
                break
            if free is None:
                return None
            free["claimed_by"] = pid
            try:
                self._prof.update_entity(free, mode="merge", etag=free.metadata["etag"],
                                         match_condition=_IF_MATCH)
            except (ResourceModifiedError, ResourceNotFoundError):
                continue                                  # someone else took it; retry
            a = Assignment(pid=pid, condition=free["condition"],
                           blocks=json.loads(free["blocks"]))
            # Record the assignment insert-only. The ETag guard above only stops
            # two claimers taking the SAME profile — it does NOT stop two racing
            # FIRST claims for the same pid from each claiming a DIFFERENT free
            # profile. create_entity makes the assignment exactly-once: the loser
            # releases the profile it just grabbed and returns the winner's
            # assignment, so both racers hand back identical codes.
            try:
                self._create_assignment(a)
            except ResourceExistsError:
                free["claimed_by"] = ""
                try:
                    self._prof.update_entity(free, mode="merge")   # unconditional release
                except (ResourceModifiedError, ResourceNotFoundError):
                    pass
                return self.get_assignment(pid)
            return a
        return None

    def release_profile(self, pid: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError
        a = self.get_assignment(pid)
        if a is None:
            return
        # Clear claimed_by on whichever profile this pid holds.
        for party in ("D", "R"):
            for e in self._prof.query_entities(f"PartitionKey eq '{party}' and claimed_by eq '{pid}'"):
                e["claimed_by"] = ""
                self._prof.update_entity(e, mode="merge")
        # Delete the assignment binding (already-gone is fine; anything else surfaces).
        try:
            self._assign.delete_entity(_ASSIGN_PK, pid)
        except ResourceNotFoundError:
            pass


_STORE: Optional[StudyStore] = None
_STORE_LOCK = threading.Lock()


def _build_default_store() -> StudyStore:
    endpoint = os.environ.get("DERAD_STUDY_TABLES_ENDPOINT") or os.environ.get(
        "AZURE_STORAGE_TABLES_ENDPOINT")
    if endpoint:
        log.info("StudyStore: TablesStudyStore at %s", endpoint)
        return TablesStudyStore(endpoint)
    # Guard against a silent InMemory fallback in production: a missing/typo'd
    # Tables endpoint would otherwise leave each gunicorn worker with its own
    # per-process store, so assignments diverge across workers and evaporate on
    # restart. When DERAD_REQUIRE_TABLES is truthy we refuse to start instead.
    if os.environ.get("DERAD_REQUIRE_TABLES", "").lower() in ("1", "true", "yes"):
        raise RuntimeError(
            "DERAD_REQUIRE_TABLES is set but no Tables endpoint is configured. "
            "Set DERAD_STUDY_TABLES_ENDPOINT (or AZURE_STORAGE_TABLES_ENDPOINT) "
            "to the storage account's table endpoint, or unset DERAD_REQUIRE_TABLES "
            "for the in-memory dev store.")
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
