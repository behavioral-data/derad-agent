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
    fakes["rate_client"].query_entities = MagicMock(return_value=iter([{"foo": 1}, {"foo": 2}]))

    n = store.hit_and_count("author:42", window_seconds=1)
    assert n == 2

    fakes["rate_client"].create_entity.assert_called_once()
    entity = fakes["rate_client"].create_entity.call_args[0][0]
    assert entity["PartitionKey"] == "author:42"
    # AtUtc stored as tz-aware datetime so the OData filter uses Edm.DateTime.
    assert isinstance(entity["AtUtc"], datetime)
    assert entity["AtUtc"].tzinfo is not None

    fakes["rate_client"].query_entities.assert_called_once()
    filter_q = fakes["rate_client"].query_entities.call_args.kwargs["query_filter"]
    assert "PartitionKey eq 'author:42'" in filter_q
    assert "AtUtc ge datetime'" in filter_q


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
