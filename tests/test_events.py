"""Tests for derad_agent.app.events + the drop/event wiring in app.py.

Every guard in mention() should produce a drop row with the right reason; a
successful process_mention should produce an event row with the right outcome.
SDK is fully stubbed; no Azure or X needed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# Env vars must be set BEFORE importing app.py (module-load _require_env).
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test_key")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.example/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_EMBED", "test-embed")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_CHAT", "test-chat")
os.environ.setdefault("DERAD_ALLOWED_AUTHOR_IDS", "111,222")
os.environ.setdefault("BOT_USER_ID_NEUTRAL", "999")

from derad_agent.app import app as app_module  # noqa: E402
from derad_agent.app import dedup as dedup_module  # noqa: E402
from derad_agent.app import events as events_module  # noqa: E402


SECRET = os.environ["X_API_SECRET"]


def _sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return "sha256=" + base64.b64encode(digest).decode("utf-8")


def _signed_post(client, path, payload):
    body = json.dumps(payload).encode()
    return client.post(
        path,
        data=body,
        headers={"X-Twitter-Webhooks-Signature": _sign(body),
                 "Content-Type": "application/json"},
    )


@pytest.fixture
def fake_events_store():
    store = events_module.InMemoryEventsStore()
    events_module.reset_store(store)
    yield store
    events_module.reset_store(None)


@pytest.fixture
def client(monkeypatch, fake_events_store):
    # Fresh dedup store per test.
    monkeypatch.setattr(dedup_module, "_default_store", dedup_module.InMemoryStore())

    # Don't actually start the pipeline; capture the thread args instead.
    started: list[tuple] = []

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=False, **_):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            started.append((self.target, self.args, self.kwargs))

    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    c._started_threads = started  # type: ignore[attr-defined]
    c._events = fake_events_store  # type: ignore[attr-defined]
    return c


# ─── InMemoryEventsStore round-trips ────────────────────────────────────────

class TestInMemoryEventsStore:
    def test_appends_event(self):
        store = events_module.InMemoryEventsStore()
        ev = events_module.MentionEvent(
            mention_id="m1", parent_id="p1", author_id="a1", tone="neutral",
            received_at_utc=datetime.now(timezone.utc),
        )
        store.write_event(ev)
        assert len(store.events) == 1
        assert store.events[0].mention_id == "m1"

    def test_appends_drop(self):
        store = events_module.InMemoryEventsStore()
        drop = events_module.MentionDrop(
            drop_reason="duplicate",
            received_at_utc=datetime.now(timezone.utc),
            mention_id="m1", author_id="a1", tone="neutral",
        )
        store.write_drop(drop)
        assert len(store.drops) == 1
        assert store.drops[0].drop_reason == "duplicate"

    def test_get_store_returns_in_memory_by_default(self, monkeypatch):
        monkeypatch.delenv("DERAD_EVENTS_BACKEND", raising=False)
        events_module.reset_store(None)
        s = events_module.get_store()
        assert isinstance(s, events_module.InMemoryEventsStore)


# ─── log_mention_drop wiring on every guard in mention() ────────────────────

class TestDropWiring:
    def _payload(self, **fields):
        base = {
            "id_str": "555",
            "in_reply_to_status_id_str": "444",
            "user": {"id_str": "111"},
        }
        base.update(fields)
        return {"tweet_create_events": [base]}

    def test_invalid_payload_event_not_dict_logs_drop(self, client):
        body = json.dumps("not-a-dict").encode()
        client.post("/mention-neutral", data=body,
                    headers={"X-Twitter-Webhooks-Signature": _sign(body),
                             "Content-Type": "application/json"})
        drops = client._events.drops
        assert len(drops) == 1
        assert drops[0].drop_reason == "invalid_payload"

    def test_invalid_payload_wrong_events_type_logs_drop(self, client):
        body = json.dumps({"tweet_create_events": "oops"}).encode()
        client.post("/mention-neutral", data=body,
                    headers={"X-Twitter-Webhooks-Signature": _sign(body),
                             "Content-Type": "application/json"})
        drops = client._events.drops
        assert len(drops) == 1
        assert drops[0].drop_reason == "invalid_payload"
        assert drops[0].extra.get("type") == "str"

    def test_no_parent_logs_drop(self, client):
        # mention with no parent reply id
        payload = self._payload()
        payload["tweet_create_events"][0].pop("in_reply_to_status_id_str")
        _signed_post(client, "/mention-neutral", payload)
        drops = client._events.drops
        assert len(drops) == 1
        assert drops[0].drop_reason == "no_parent"

    def test_self_reply_logs_drop(self, client, monkeypatch):
        # bot id matches user id_str
        monkeypatch.setitem(app_module.BOT_USER_ID_BY_TONE, "neutral", "999")
        payload = self._payload(user={"id_str": "999"})
        _signed_post(client, "/mention-neutral", payload)
        drops = client._events.drops
        assert any(d.drop_reason == "self_reply" for d in drops)

    def test_unregistered_logs_drop(self, client):
        # author 'nope' not in allow list
        payload = self._payload(user={"id_str": "nope"})
        _signed_post(client, "/mention-neutral", payload)
        drops = client._events.drops
        assert any(d.drop_reason == "unregistered" for d in drops)

    def test_duplicate_logs_drop(self, client):
        # Pre-claim the mention so the second delivery is the dup.
        app_module.ALLOWED_AUTHOR_IDS.add("111")
        payload = self._payload()
        _signed_post(client, "/mention-neutral", payload)
        _signed_post(client, "/mention-neutral", payload)
        drops = client._events.drops
        assert sum(1 for d in drops if d.drop_reason == "duplicate") == 1
        assert client._started_threads, "first delivery should have started a thread"

    def test_rate_limit_logs_drop(self, client):
        app_module.ALLOWED_AUTHOR_IDS.add("111")
        for i in range(5):
            payload = self._payload(id_str=f"m{i}")
            _signed_post(client, "/mention-neutral", payload)
        drops = [d for d in client._events.drops if d.drop_reason == "rate_limit"]
        # Default limit is 3/sec; at least one of the 5 must have been rate-limited.
        assert drops, "expected at least one rate_limit drop"
        # rate_limit extra carries the hit count
        assert drops[0].extra.get("hits", 0) > 0


# ─── log_mention_event wiring in process_mention ────────────────────────────

class TestEventWiring:
    def _run_process(self, *, fetch_snap, generate_reply_result, post_reply_returns,
                     monkeypatch, fake_events_store, received_at_utc=None):
        """Invoke process_mention directly with stubs and return the captured event."""
        from derad_agent.app import utils as utils_module
        monkeypatch.setattr(utils_module, "fetch_tweet", lambda *a, **kw: fetch_snap)
        monkeypatch.setattr(app_module, "fetch_tweet", lambda *a, **kw: fetch_snap)
        monkeypatch.setattr(app_module, "generate_reply", lambda **kw: generate_reply_result)

        call_count = {"n": 0}
        def _post(parent_id, reply_text, tone):
            call_count["n"] += 1
            return post_reply_returns[call_count["n"] - 1]
        monkeypatch.setattr(app_module, "post_reply", _post)

        tweet = {
            "id_str": "555",
            "in_reply_to_status_id_str": "444",
            "user": {"id_str": "111", "screen_name": "alice"},
        }
        ts = received_at_utc or events_module.utcnow()
        app_module.process_mention("neutral", tweet, ts)
        assert fake_events_store.events, "process_mention should write an event row"
        return fake_events_store.events[-1], ts

    def test_replied_outcome_captures_full_pipeline_state(self, monkeypatch, fake_events_store):
        from derad_agent.app.utils import TweetSnapshot
        snap = TweetSnapshot(
            text="Mail-in voting causes fraud.",
            author_id="999",
            author_username="parent_user",
        )
        gen = {
            "text": "Here are the facts.",
            "sources": ["https://a.example"],
            "tweets": ["t1"],
            "notes": ["n1"],
            "queries": ["query1", "query2"],
            "all_cited_tweet_ids": ["t1", "t2", "t3"],
            "all_cited_note_ids": ["n1", "n2", "n3"],
        }
        ts = events_module.utcnow()
        ev, ts_passed = self._run_process(
            fetch_snap=snap,
            generate_reply_result=gen,
            post_reply_returns=["REPLY_ID", "SOURCES_ID"],
            monkeypatch=monkeypatch,
            fake_events_store=fake_events_store,
            received_at_utc=ts,
        )
        assert ev.outcome == "replied"
        assert ev.received_at_utc == ts_passed, "received_at_utc must round-trip from the handler"
        assert ev.reply_id == "REPLY_ID"
        assert ev.sources_reply_id == "SOURCES_ID"
        assert ev.parent_text == "Mail-in voting causes fraud."
        assert ev.parent_author_id == "999"
        assert ev.parent_author_username == "parent_user"
        assert ev.author_username == "alice"
        assert ev.queries == ["query1", "query2"]
        assert ev.cited_tweet_ids == ["t1", "t2", "t3"]
        assert ev.cited_note_ids == ["n1", "n2", "n3"]
        assert ev.reply_text == "Here are the facts."
        assert ev.pipeline_ms is not None and ev.pipeline_ms >= 0

    def test_parent_fetch_failed_outcome(self, monkeypatch, fake_events_store):
        ev = self._run_process(
            fetch_snap=None,
            generate_reply_result={},
            post_reply_returns=[],
            monkeypatch=monkeypatch,
            fake_events_store=fake_events_store,
        )[0]
        assert ev.outcome == "parent_fetch_failed"
        assert ev.reply_id is None

    def test_empty_reply_outcome(self, monkeypatch, fake_events_store):
        from derad_agent.app.utils import TweetSnapshot
        snap = TweetSnapshot(text="claim", author_id="999", author_username="u")
        ev = self._run_process(
            fetch_snap=snap,
            generate_reply_result={"text": "", "sources": None, "tweets": None, "notes": None,
                                   "queries": [], "all_cited_tweet_ids": [], "all_cited_note_ids": []},
            post_reply_returns=[],
            monkeypatch=monkeypatch,
            fake_events_store=fake_events_store,
        )[0]
        assert ev.outcome == "empty_reply"
        assert ev.reply_id is None

    def test_replied_no_sources_when_sources_post_fails(self, monkeypatch, fake_events_store):
        """Main reply lands, sources follow-up fails — outcome must be
        distinguishable from a reply that had no sources to begin with.
        """
        from derad_agent.app.utils import TweetSnapshot
        snap = TweetSnapshot(text="claim", author_id="999", author_username="u")
        gen = {
            "text": "main reply", "sources": ["https://a.example"],
            "tweets": ["t1"], "notes": ["n1"],
            "queries": ["q"], "all_cited_tweet_ids": ["t1"], "all_cited_note_ids": ["n1"],
        }
        ev = self._run_process(
            fetch_snap=snap,
            generate_reply_result=gen,
            post_reply_returns=["REPLY_ID", None],  # sources tweet rejected
            monkeypatch=monkeypatch,
            fake_events_store=fake_events_store,
        )[0]
        assert ev.outcome == "replied_no_sources"
        assert ev.reply_id == "REPLY_ID"
        assert ev.sources_reply_id is None

    def test_x_post_error_outcome(self, monkeypatch, fake_events_store):
        from derad_agent.app.utils import TweetSnapshot
        snap = TweetSnapshot(text="claim", author_id="999", author_username="u")
        gen = {
            "text": "the response", "sources": None, "tweets": None, "notes": None,
            "queries": ["q"], "all_cited_tweet_ids": ["t1"], "all_cited_note_ids": ["n1"],
        }
        ev = self._run_process(
            fetch_snap=snap,
            generate_reply_result=gen,
            post_reply_returns=[None],  # X reject the reply
            monkeypatch=monkeypatch,
            fake_events_store=fake_events_store,
        )[0]
        assert ev.outcome == "x_post_error"
        assert ev.reply_text == "the response"
        assert ev.reply_id is None

    def test_pipeline_error_outcome(self, monkeypatch, fake_events_store):
        def _boom(*a, **kw):
            raise RuntimeError("synthetic explosion")
        from derad_agent.app import utils as utils_module
        monkeypatch.setattr(utils_module, "fetch_tweet", _boom)
        monkeypatch.setattr(app_module, "fetch_tweet", _boom)

        tweet = {"id_str": "555", "in_reply_to_status_id_str": "444",
                 "user": {"id_str": "111", "screen_name": "alice"}}
        app_module.process_mention("neutral", tweet, events_module.utcnow())
        ev = fake_events_store.events[-1]
        assert ev.outcome == "pipeline_error"
        assert ev.error_class == "RuntimeError"
        assert "synthetic explosion" in (ev.error_detail or "")


# ─── TablesEventsStore schema regression ────────────────────────────────────

class _FakeResourceExistsError(Exception):
    pass


def _patched_tables_store(monkeypatch):
    """Construct a TablesEventsStore with the SDK stubbed."""
    events_client = MagicMock()
    drops_client = MagicMock()
    service = MagicMock()
    service.create_table = MagicMock(side_effect=[None, None])
    service.get_table_client = MagicMock(side_effect=[events_client, drops_client])

    tables_mod = MagicMock()
    tables_mod.TableServiceClient = MagicMock(return_value=service)
    identity_mod = MagicMock()
    identity_mod.DefaultAzureCredential = MagicMock(return_value=MagicMock())
    exc_mod = MagicMock()
    exc_mod.ResourceExistsError = _FakeResourceExistsError

    monkeypatch.setitem(sys.modules, "azure.data.tables", tables_mod)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_mod)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", exc_mod)

    store = events_module.TablesEventsStore("https://example.table.core.windows.net")
    return store, events_client, drops_client


class TestTablesEventsStoreSchema:
    def test_event_row_keys_are_sortable(self, monkeypatch):
        store, events_client, _ = _patched_tables_store(monkeypatch)
        ev = events_module.MentionEvent(
            mention_id="abc", parent_id="p1", author_id="u1", tone="neutral",
            received_at_utc=datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc),
            queries=["q1"], cited_tweet_ids=["t1", "t2"], cited_note_ids=["n1", "n2"],
        )
        store.write_event(ev)
        entity = events_client.create_entity.call_args[0][0]
        assert entity["PartitionKey"] == "2026-05"
        assert entity["RowKey"].startswith("2026-05-18T12:00:00")
        assert entity["RowKey"].endswith("_abc")
        # Lists encoded as JSON strings
        assert json.loads(entity["queries_json"]) == ["q1"]
        assert json.loads(entity["cited_tweet_ids_json"]) == ["t1", "t2"]
        assert json.loads(entity["cited_note_ids_json"]) == ["n1", "n2"]

    def test_drop_row_handles_missing_mention_id(self, monkeypatch):
        store, _, drops_client = _patched_tables_store(monkeypatch)
        drop = events_module.MentionDrop(
            drop_reason="invalid_payload",
            received_at_utc=datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc),
            tone="neutral",
            extra={"why": "event_not_dict"},
        )
        store.write_drop(drop)
        entity = drops_client.create_entity.call_args[0][0]
        assert entity["PartitionKey"] == "2026-05"
        assert "nomid_" in entity["RowKey"]
        assert json.loads(entity["extra_json"]) == {"why": "event_not_dict"}

    def test_write_swallows_sdk_exception(self, monkeypatch):
        store, events_client, _ = _patched_tables_store(monkeypatch)
        events_client.create_entity = MagicMock(side_effect=RuntimeError("network"))
        ev = events_module.MentionEvent(
            mention_id="abc", parent_id="p", author_id="u", tone="neutral",
            received_at_utc=datetime.now(timezone.utc),
        )
        # Must not raise — logging the exception is fine, but bot must not crash.
        store.write_event(ev)

    def test_truncates_long_text_fields(self, monkeypatch):
        store, events_client, _ = _patched_tables_store(monkeypatch)
        big = "x" * 40_000
        ev = events_module.MentionEvent(
            mention_id="abc", parent_id="p", author_id="u", tone="neutral",
            received_at_utc=datetime.now(timezone.utc),
            parent_text=big, reply_text=big,
        )
        store.write_event(ev)
        entity = events_client.create_entity.call_args[0][0]
        assert len(entity["parent_text"]) <= 32_000
        assert len(entity["reply_text"]) <= 32_000
