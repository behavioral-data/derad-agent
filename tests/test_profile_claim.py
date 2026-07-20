from __future__ import annotations
import json
import re
from collections import Counter

import pytest

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

from study.interface import study_store as ss
from study.interface.study_store import (InMemoryStudyStore, StoredProfile,
                                         TablesStudyStore)

CONDS = ("neutral", "agreeable", "satirical", "control")


def _pool():
    profiles, orders = [], {"D": [], "R": []}
    pid = 0
    for party in ("D", "R"):
        for i in range(57):
            for c in CONDS:
                pid += 1
                name = f"P{pid:03d}"
                profiles.append(StoredProfile(name, c, party, [[f"{c}-{i}-post"]]))
                orders[party].append(name)
    return profiles, orders


def test_claim_binds_idempotent_and_balances_condition():
    s = InMemoryStudyStore()
    profs, orders = _pool()
    s.load_profiles(profs, orders)
    a1 = s.claim_profile("PROLIFIC1", "D")
    assert a1 is not None and a1.condition in CONDS
    # idempotent: same pid -> same assignment, no second claim
    assert s.claim_profile("PROLIFIC1", "D").condition == a1.condition
    # claim 8 fresh Dems -> first two blocks of 4 -> each condition twice
    got = [s.claim_profile(f"D{i}", "D").condition for i in range(8)]
    assert Counter(got) == {c: 2 for c in CONDS}


def test_pool_exhaustion_and_release():
    s = InMemoryStudyStore()
    profs, orders = _pool()
    s.load_profiles(profs, orders)
    claimed = [s.claim_profile(f"R{i}", "R") for i in range(228)]
    assert all(claimed) and s.claim_profile("Rextra", "R") is None      # exhausted
    s.release_profile("R0")                                             # frees one
    assert s.claim_profile("Rnew", "R") is not None


def test_tables_store_has_pool_methods():
    for m in ("load_profiles", "claim_profile", "release_profile"):
        assert callable(getattr(TablesStudyStore, m))


# ── I-3: silent InMemory fallback guard ────────────────────────────────────

def test_build_default_store_requires_tables_when_flagged(monkeypatch):
    monkeypatch.delenv("DERAD_STUDY_TABLES_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_TABLES_ENDPOINT", raising=False)
    monkeypatch.setenv("DERAD_REQUIRE_TABLES", "1")
    with pytest.raises(RuntimeError):
        ss._build_default_store()
    # Without the flag, no endpoint -> in-memory dev store (regression).
    monkeypatch.delenv("DERAD_REQUIRE_TABLES", raising=False)
    assert isinstance(ss._build_default_store(), InMemoryStudyStore)


# ── Fake Table client: enough of azure.data.tables to exercise the store ────

class _Ent(dict):
    """A dict that carries a .metadata like azure's TableEntity."""


class _FakeTable:
    """Minimal in-memory stand-in for a TableClient (no ETag concurrency)."""

    def __init__(self):
        self.rows = {}                       # (PartitionKey, RowKey) -> dict

    @staticmethod
    def _key(e):
        return (e["PartitionKey"], e["RowKey"])

    def _wrap(self, props):
        e = _Ent(props)                      # a fresh copy, like a TableEntity
        e.metadata = {"etag": 'W/"0"'}
        return e

    def create_entity(self, entity):
        k = self._key(entity)
        if k in self.rows:
            raise ResourceExistsError("exists")
        self.rows[k] = dict(entity)

    def upsert_entity(self, entity):
        self.rows[self._key(entity)] = dict(entity)

    def get_entity(self, pk, rk):
        try:
            return self._wrap(self.rows[(pk, rk)])
        except KeyError:
            raise ResourceNotFoundError("missing")

    def delete_entity(self, pk, rk):
        if (pk, rk) not in self.rows:
            raise ResourceNotFoundError("missing")
        del self.rows[(pk, rk)]

    def list_entities(self, results_per_page=None):
        return [self._wrap(v) for v in list(self.rows.values())]

    def query_entities(self, query, results_per_page=None):
        m = re.match(r"PartitionKey eq '([^']*)' and claimed_by eq '([^']*)'", query)
        pk, claimed = m.group(1), m.group(2)
        return [self._wrap(v) for v in self.rows.values()
                if v["PartitionKey"] == pk and v.get("claimed_by", "") == claimed]

    def update_entity(self, entity, mode=None, etag=None, match_condition=None):
        self.rows.setdefault(self._key(entity), {}).update(entity)   # merge


def _tables_store(prof=None, assign=None):
    s = TablesStudyStore.__new__(TablesStudyStore)   # bypass __init__ (needs Azure)
    s._prof = prof or _FakeTable()
    s._assign = assign or _FakeTable()
    s._expo = _FakeTable()
    return s


# ── I-4: load_profiles is resume-safe (insert-only, never touch existing) ────

def test_tables_load_profiles_resumes_without_clobbering_live_rows():
    prof = _FakeTable()
    # Simulate a partial first-boot: D/0000 already written AND claimed live.
    prof.rows[("D", "0000")] = {
        "PartitionKey": "D", "RowKey": "0000", "profile_id": "D0",
        "condition": "neutral", "blocks": json.dumps([["p1"]]), "claimed_by": "LIVE_PID"}
    store = _tables_store(prof=prof)

    profiles = [
        StoredProfile("D0", "neutral", "D", [["p1"]]),
        StoredProfile("D1", "agreeable", "D", [["p2"]]),
        StoredProfile("R0", "satirical", "R", [["p3"]]),
    ]
    orders = {"D": ["D0", "D1"], "R": ["R0"]}
    store.load_profiles(profiles, orders)

    # All 3 rows present; the live claim on D0 is untouched; missing ones inserted.
    assert len(prof.rows) == 3
    assert prof.rows[("D", "0000")]["claimed_by"] == "LIVE_PID"
    assert prof.rows[("D", "0001")]["profile_id"] == "D1"
    assert prof.rows[("R", "0000")]["profile_id"] == "R0"


# ── I-2: claim_profile is insert-only; same-pid TOCTOU converges ────────────

def test_tables_claim_happy_path_creates_assignment():
    prof = _FakeTable()
    prof.rows[("D", "0000")] = {
        "PartitionKey": "D", "RowKey": "0000", "profile_id": "D0",
        "condition": "neutral", "blocks": json.dumps([["p1"]]), "claimed_by": ""}
    store = _tables_store(prof=prof)
    a = store.claim_profile("PID1", "D")
    assert a is not None and a.condition == "neutral"
    assert prof.rows[("D", "0000")]["claimed_by"] == "PID1"      # profile bound
    assert store._assign.rows[("assign", "PID1")]["condition"] == "neutral"


def test_tables_claim_same_pid_toctou_converges():
    """Two racing FIRST claims for one pid: the create_entity conflict makes the
    loser release its profile and return the winner's assignment, so both hand
    back identical codes and no slot is orphaned as claimed."""
    prof = _FakeTable()
    prof.rows[("D", "0000")] = {
        "PartitionKey": "D", "RowKey": "0000", "profile_id": "D0",
        "condition": "satirical", "blocks": json.dumps([["p1"]]), "claimed_by": ""}
    store = _tables_store(prof=prof)

    # A racing winner already recorded a DIFFERENT-condition assignment for RACER.
    winner = {"PartitionKey": "assign", "RowKey": "RACER",
              "condition": "control", "blocks": json.dumps([["pX"]]), "created_at": ""}

    class _RacyAssign(_FakeTable):
        def __init__(self):
            super().__init__()
            self._revealed = False

        def get_entity(self, pk, rk):
            if self._revealed and (pk, rk) == ("assign", "RACER"):
                return self._wrap(winner)
            raise ResourceNotFoundError("missing")

        def create_entity(self, entity):        # racer already inserted this pid
            self._revealed = True
            raise ResourceExistsError("exists")

    store._assign = _RacyAssign()
    a = store.claim_profile("RACER", "D")
    # Returns the WINNER's assignment (control), not the one this call grabbed.
    assert a is not None and a.condition == "control"
    # The profile this call grabbed was released back to the pool (no orphan).
    assert prof.rows[("D", "0000")]["claimed_by"] == ""
