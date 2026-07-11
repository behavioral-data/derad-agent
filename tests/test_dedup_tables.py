"""Tests for the Azure Tables backend and the store selector.

The TablesStore tests stub the Azure SDK at the import surface — we don't
need a real backend, just to verify that `claim` and `hit_and_count` make the
expected calls and translate ResourceExistsError correctly.
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate_dedup_singleton():
    """Ensure each test sees a fresh singleton, regardless of import order."""
    from agent.app import dedup
    dedup.reset_store(None)
    yield
    dedup.reset_store(None)


# ─── get_store() selector ───────────────────────────────────────────────────

def test_get_store_defaults_to_in_memory(monkeypatch):
    monkeypatch.delenv("DERAD_STORE_BACKEND", raising=False)
    from agent.app import dedup
    store = dedup.get_store()
    assert isinstance(store, dedup.InMemoryStore)


def test_get_store_returns_tables_when_selected(monkeypatch):
    monkeypatch.setenv("DERAD_STORE_BACKEND", "tables")
    monkeypatch.setenv("DERAD_TABLES_ENDPOINT", "https://example.table.core.windows.net")

    # Stub the SDK so TablesStore.__init__ doesn't hit Azure.
    fake_tables = MagicMock()
    fake_tables.TableServiceClient = MagicMock(return_value=MagicMock())
    fake_identity = MagicMock()
    fake_identity.DefaultAzureCredential = MagicMock(return_value=MagicMock())
    fake_exceptions = MagicMock()

    class _ResourceExistsError(Exception):
        pass
    fake_exceptions.ResourceExistsError = _ResourceExistsError

    monkeypatch.setitem(sys.modules, "azure.data.tables", fake_tables)
    monkeypatch.setitem(sys.modules, "azure.identity", fake_identity)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", fake_exceptions)

    from agent.app import dedup
    store = dedup.get_store()
    assert isinstance(store, dedup.TablesStore)
    fake_tables.TableServiceClient.assert_called_once()


def test_get_store_caches_singleton(monkeypatch):
    monkeypatch.delenv("DERAD_STORE_BACKEND", raising=False)
    from agent.app import dedup
    s1 = dedup.get_store()
    s2 = dedup.get_store()
    assert s1 is s2


# ─── TablesStore behavior with a stubbed SDK ────────────────────────────────

class _FakeResourceExistsError(Exception):
    pass


def _make_tables_store(monkeypatch):
    """Build a TablesStore with the SDK stubbed out; return (store, fakes)."""
    fake_dedup_client = MagicMock()
    fake_rate_client = MagicMock()
    fake_service = MagicMock()
    fake_service.create_table = MagicMock()
    fake_service.get_table_client = MagicMock(side_effect=[fake_dedup_client, fake_rate_client])

    fake_tables_mod = MagicMock()
    fake_tables_mod.TableServiceClient = MagicMock(return_value=fake_service)
    fake_identity_mod = MagicMock()
    fake_identity_mod.DefaultAzureCredential = MagicMock(return_value=MagicMock())
    fake_exc_mod = MagicMock()
    fake_exc_mod.ResourceExistsError = _FakeResourceExistsError

    monkeypatch.setitem(sys.modules, "azure.data.tables", fake_tables_mod)
    monkeypatch.setitem(sys.modules, "azure.identity", fake_identity_mod)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", fake_exc_mod)

    from agent.app import dedup
    store = dedup.TablesStore("https://example.table.core.windows.net")
    return store, {
        "dedup_client": fake_dedup_client,
        "rate_client": fake_rate_client,
        "service": fake_service,
    }


def test_tables_claim_new_key_returns_true(monkeypatch):
    store, fakes = _make_tables_store(monkeypatch)
    fakes["dedup_client"].create_entity = MagicMock()  # success

    assert store.claim("mention-1") is True
    fakes["dedup_client"].create_entity.assert_called_once()
    entity = fakes["dedup_client"].create_entity.call_args[0][0]
    assert entity["RowKey"] == "mention-1"
    # Fixed PartitionKey — no midnight-UTC race window.
    from agent.app.dedup import TablesStore
    assert entity["PartitionKey"] == TablesStore.DEDUP_PARTITION == "mentions"
    # ExpiresAtUtc stored as tz-aware datetime (Edm.DateTime).
    assert isinstance(entity["ExpiresAtUtc"], datetime)
    assert entity["ExpiresAtUtc"].tzinfo is not None


def test_tables_claim_duplicate_returns_false(monkeypatch):
    store, fakes = _make_tables_store(monkeypatch)
    fakes["dedup_client"].create_entity = MagicMock(side_effect=_FakeResourceExistsError())

    assert store.claim("mention-1") is False


def test_tables_hit_and_count_inserts_and_queries(monkeypatch):
    store, fakes = _make_tables_store(monkeypatch)
    fakes["rate_client"].create_entity = MagicMock()

    def fake_query(query_filter, **kwargs):
        if "AtUtc ge datetime'" in query_filter:
            return iter([{"foo": 1}, {"foo": 2}])
        return iter([])  # prune query — nothing to delete

    fakes["rate_client"].query_entities = MagicMock(side_effect=fake_query)

    n = store.hit_and_count("author:42", window_seconds=1)
    assert n == 2

    fakes["rate_client"].create_entity.assert_called_once()
    entity = fakes["rate_client"].create_entity.call_args[0][0]
    assert entity["PartitionKey"] == "author:42"
    # AtUtc stored as tz-aware datetime so the OData filter uses Edm.DateTime.
    assert isinstance(entity["AtUtc"], datetime)
    assert entity["AtUtc"].tzinfo is not None

    rolling_calls = [
        c for c in fakes["rate_client"].query_entities.call_args_list
        if "AtUtc ge datetime'" in c.kwargs["query_filter"]
    ]
    assert len(rolling_calls) == 1
    filter_q = rolling_calls[0].kwargs["query_filter"]
    assert "PartitionKey eq 'author:42'" in filter_q


def test_tables_hit_and_count_prunes_out_of_window_rows(monkeypatch):
    """Pruning deletes only this author's out-of-window rows; in-window survive."""
    store, fakes = _make_tables_store(monkeypatch)

    now = datetime.now(timezone.utc)
    in_window = [
        {"PartitionKey": "author:42", "RowKey": "rk-new-1", "AtUtc": now},
        {"PartitionKey": "author:42", "RowKey": "rk-new-2", "AtUtc": now},
    ]
    out_of_window = [
        {"PartitionKey": "author:42", "RowKey": f"rk-old-{i}", "AtUtc": now}
        for i in range(3)
    ]

    def fake_query(query_filter, **kwargs):
        # Two query shapes: rolling-window (ge) and prune (lt).
        if "AtUtc ge datetime'" in query_filter:
            return iter(in_window)
        if "AtUtc lt datetime'" in query_filter:
            return iter(out_of_window)
        return iter([])

    fakes["rate_client"].create_entity = MagicMock()
    fakes["rate_client"].query_entities = MagicMock(side_effect=fake_query)
    fakes["rate_client"].delete_entity = MagicMock()

    n = store.hit_and_count("author:42", window_seconds=60)
    assert n == len(in_window)

    deleted_keys = {
        call.kwargs.get("row_key", call.args[1] if len(call.args) > 1 else None)
        for call in fakes["rate_client"].delete_entity.call_args_list
    }
    assert deleted_keys == {row["RowKey"] for row in out_of_window}
    # In-window rows are NOT deleted.
    for row in in_window:
        assert row["RowKey"] not in deleted_keys


def test_tables_hit_and_count_prune_failure_does_not_break_rate_check(monkeypatch):
    """Delete failures during pruning are swallowed; the count is still returned."""
    store, fakes = _make_tables_store(monkeypatch)
    now = datetime.now(timezone.utc)
    in_window = [{"PartitionKey": "a", "RowKey": "rk-new", "AtUtc": now}]
    out_of_window = [{"PartitionKey": "a", "RowKey": "rk-old", "AtUtc": now}]

    def fake_query(query_filter, **kwargs):
        if "AtUtc ge datetime'" in query_filter:
            return iter(in_window)
        return iter(out_of_window)

    fakes["rate_client"].create_entity = MagicMock()
    fakes["rate_client"].query_entities = MagicMock(side_effect=fake_query)
    fakes["rate_client"].delete_entity = MagicMock(side_effect=RuntimeError("transient"))

    n = store.hit_and_count("a", window_seconds=60)
    assert n == 1


# ─── InMemoryStore.hit_and_count ─────────────────────────────────────────────
#
# Regression coverage for a bug where every call pruned *all* keys using the
# *current* call's window as the cutoff. A per-second check (window=1) would
# then gut a daily-cap key's timestamps (window=86400), so USER_DAILY_CAP was
# never enforced on the in-memory backend.


def test_hit_and_count_daily_cap_unaffected_by_interleaved_per_second_checks(monkeypatch):
    from agent.app import dedup as dedup_module

    store = dedup_module.InMemoryStore()
    day_key = "author_day:42:2026-07-07"
    sec_key = "author:42"

    fake_now = [0.0]
    monkeypatch.setattr(dedup_module.time, "time", lambda: fake_now[0])

    # First daily-cap hit at t=0s.
    assert store.hit_and_count(day_key, 86400) == 1

    # Two seconds later, a per-second rate check comes in (window=1s), as
    # happens on every mention.
    fake_now[0] = 2.0
    store.hit_and_count(sec_key, 1)

    # The daily-cap key must still remember the t=0 hit alongside this new
    # one — i.e. count grows to 2. Before the fix, the per-second call swept
    # *every* key (including day_key) using its own 1-second cutoff, which
    # discarded the t=0 timestamp and reset the daily count back to 1 on the
    # next daily check — meaning USER_DAILY_CAP could never be reached.
    assert store.hit_and_count(day_key, 86400) == 2


def test_hit_and_count_prunes_only_the_queried_key():
    from agent.app.dedup import InMemoryStore

    store = InMemoryStore()
    # Seed a long-lived key with an old timestamp directly.
    old_key = "author_day:1:2026-07-01"
    store._hits[old_key] = [0.0]  # far in the past

    # A hit on an unrelated key with a short window must not prune old_key.
    store.hit_and_count("author:2", 1)

    assert store._hits[old_key] == [0.0]


def test_tables_store_creates_tables_idempotently(monkeypatch):
    """First boot creates tables; subsequent boots tolerate ResourceExistsError."""
    fake_dedup_client = MagicMock()
    fake_rate_client = MagicMock()
    fake_service = MagicMock()
    # create_table raises on the SECOND call (table already exists for the rate table).
    fake_service.create_table = MagicMock(side_effect=[None, _FakeResourceExistsError()])
    fake_service.get_table_client = MagicMock(side_effect=[fake_dedup_client, fake_rate_client])

    fake_tables_mod = MagicMock()
    fake_tables_mod.TableServiceClient = MagicMock(return_value=fake_service)
    fake_identity_mod = MagicMock()
    fake_identity_mod.DefaultAzureCredential = MagicMock(return_value=MagicMock())
    fake_exc_mod = MagicMock()
    fake_exc_mod.ResourceExistsError = _FakeResourceExistsError

    monkeypatch.setitem(sys.modules, "azure.data.tables", fake_tables_mod)
    monkeypatch.setitem(sys.modules, "azure.identity", fake_identity_mod)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", fake_exc_mod)

    from agent.app import dedup
    store = dedup.TablesStore("https://example.table.core.windows.net")
    assert isinstance(store, dedup.TablesStore)
    assert fake_service.create_table.call_count == 2
