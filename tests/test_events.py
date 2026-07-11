"""Tests for agent.app.events + the drop/event wiring in app.py.

Every guard in _dispatch_tweet() should produce a drop row with the right
reason; a successful process_mention should produce an event row with the
right outcome. SDK is fully stubbed; no Azure or X needed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# Env vars must be set BEFORE importing app.py (module-load _require_env).
os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402
from agent.app import dedup as dedup_module  # noqa: E402
from agent.app import events as events_module  # noqa: E402


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def fake_events_store():
    store = events_module.InMemoryEventsStore()
    events_module.reset_store(store)
    yield store
    events_module.reset_store(None)


@pytest.fixture
def dispatch_env(monkeypatch, fake_events_store):
    """Fresh dedup store + thread capture for _dispatch_tweet tests.

    Pins _resolve_tone so drop/event tests don't depend on random tone selection.
    """
    monkeypatch.setattr(dedup_module, "_default_store", dedup_module.InMemoryStore())
    monkeypatch.setattr(app_module, "_resolve_tone", lambda _author_id: "neutral")

    started: list[tuple] = []

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=False, **_):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            started.append((self.target, self.args, self.kwargs))

    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    return {"started": started, "events": fake_events_store}


# ─── InMemoryEventsStore round-trips ────────────────────────────────────────

class TestInMemoryEventsStore:
    def test_appends_event(self):
        store = events_module.InMemoryEventsStore()
        ev = events_module.MentionEvent(
            mention_id="m1", parent_id="p1", author_id="a1", tone="neutral",
            received_at_utc=_now(),
        )
        store.write_event(ev)
        assert len(store.events) == 1
        assert store.events[0].mention_id == "m1"

    def test_conversation_ids_by_reply_only_includes_rows_with_both_fields(self):
        store = events_module.InMemoryEventsStore()
        store.write_event(events_module.MentionEvent(
            mention_id="m1", parent_id="p1", author_id="a1", tone="neutral",
            received_at_utc=_now(), reply_id="r1", conversation_id="c1",
        ))
        # Legacy row: reply posted, but no conversation_id was ever captured.
        store.write_event(events_module.MentionEvent(
            mention_id="m2", parent_id="p2", author_id="a2", tone="neutral",
            received_at_utc=_now(), reply_id="r2",
        ))
        # No reply posted at all — shouldn't appear regardless of conversation_id.
        store.write_event(events_module.MentionEvent(
            mention_id="m3", parent_id="p3", author_id="a3", tone="neutral",
            received_at_utc=_now(), conversation_id="c3",
        ))
        assert store.conversation_ids_by_reply() == {"r1": "c1"}

    def test_appends_drop(self):
        store = events_module.InMemoryEventsStore()
        drop = events_module.MentionDrop(
            drop_reason="duplicate",
            received_at_utc=_now(),
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


# ─── log_mention_drop wiring on every guard in _dispatch_tweet() ────────────

class TestDropWiring:
    def _tweet(self, **fields):
        base = {
            "id_str": "555",
            "in_reply_to_status_id_str": "444",
            "user": {"id_str": "111"},
        }
        base.update(fields)
        return base

    def test_no_parent_logs_drop(self, dispatch_env):
        tweet = self._tweet()
        del tweet["in_reply_to_status_id_str"]
        app_module._dispatch_tweet(tweet, _now())
        drops = dispatch_env["events"].drops
        assert len(drops) == 1
        assert drops[0].drop_reason == "no_parent"

    def test_self_reply_logs_drop(self, dispatch_env, monkeypatch):
        monkeypatch.setattr(app_module, "BOT_USER_ID", "999")
        tweet = self._tweet(user={"id_str": "999"})
        app_module._dispatch_tweet(tweet, _now())
        drops = dispatch_env["events"].drops
        assert any(d.drop_reason == "self_reply" for d in drops)

    def test_duplicate_logs_drop(self, dispatch_env):
        tweet = self._tweet()
        app_module._dispatch_tweet(tweet, _now())
        app_module._dispatch_tweet(tweet, _now())
        drops = dispatch_env["events"].drops
        assert sum(1 for d in drops if d.drop_reason == "duplicate") == 1
        assert dispatch_env["started"], "first delivery should have started a thread"

    def test_rate_limit_logs_drop(self, dispatch_env):
        for i in range(5):
            tweet = self._tweet(id_str=f"m{i}")
            app_module._dispatch_tweet(tweet, _now())
        drops = [d for d in dispatch_env["events"].drops if d.drop_reason == "rate_limit"]
        assert drops, "expected at least one rate_limit drop"
        assert drops[0].extra.get("hits", 0) > 0


# ─── log_mention_event wiring in process_mention ────────────────────────────

class TestEventWiring:
    def _run_process(self, *, fetch_snap, generate_reply_result, post_reply_returns,
                     monkeypatch, fake_events_store, received_at_utc=None):
        """Invoke process_mention directly with stubs and return the captured event."""
        monkeypatch.setattr(app_module, "DRY_RUN", False)
        from agent.app import utils as utils_module
        monkeypatch.setattr(utils_module, "fetch_tweet", lambda *a, **kw: fetch_snap)
        monkeypatch.setattr(app_module, "fetch_tweet", lambda *a, **kw: fetch_snap)
        monkeypatch.setattr(app_module, "generate_reply", lambda **kw: generate_reply_result)

        call_count = {"n": 0}
        def _post(parent_id, reply_text):
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
        from agent.app.utils import TweetSnapshot
        snap = TweetSnapshot(
            text="Mail-in voting causes fraud.",
            author_id="12345",
            author_username="parent_user",
        )
        gen = {
            "text": "Here are the facts.",
            "sources": ["https://a.example"],
            "verdict_label": "Refuted",
            "action": "verify",
            "action_outcome": "verified_refuted",
            "queries": ["query1", "query2"],
        }
        ts = events_module.utcnow()
        ev, ts_passed = self._run_process(
            fetch_snap=snap,
            generate_reply_result=gen,
            post_reply_returns=["REPLY_ID", "LINK_REPLY_ID"],
            monkeypatch=monkeypatch,
            fake_events_store=fake_events_store,
            received_at_utc=ts,
        )
        assert ev.outcome == "replied"
        assert ev.received_at_utc == ts_passed
        assert ev.reply_id == "REPLY_ID"
        assert ev.parent_text == "Mail-in voting causes fraud."
        assert ev.parent_author_id == "12345"
        assert ev.parent_author_username == "parent_user"
        assert ev.author_username == "alice"
        assert ev.queries == ["query1", "query2"]
        assert ev.reply_type == "factcheck"
        assert ev.reply_text.startswith("Here are the facts.")
        assert ev.pipeline_ms is not None and ev.pipeline_ms >= 0
        # Forward-join key to InfoTokens — must be set before the event is written.
        assert isinstance(ev.info_token, str) and ev.info_token

    def test_replied_outcome_captures_conversation_id(self, monkeypatch, fake_events_store):
        """conversation_id (the true thread root) must land on the event, not
        just parent_id — collect_replies needs it to search the whole thread
        for bystander replies instead of a narrower (sub-)conversation."""
        from agent.app.utils import TweetSnapshot
        snap = TweetSnapshot(
            text="Mail-in voting causes fraud.",
            author_id="12345",
            author_username="parent_user",
            conversation_id="99900",
        )
        gen = {
            "text": "Here are the facts.",
            "sources": ["https://a.example"],
            "verdict_label": "Refuted",
            "action": "verify",
            "action_outcome": "verified_refuted",
            "queries": ["query1"],
        }
        ev, _ts = self._run_process(
            fetch_snap=snap,
            generate_reply_result=gen,
            post_reply_returns=["REPLY_ID", "LINK_REPLY_ID"],
            monkeypatch=monkeypatch,
            fake_events_store=fake_events_store,
        )
        assert ev.conversation_id == "99900"

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
        from agent.app.utils import TweetSnapshot
        snap = TweetSnapshot(text="claim", author_id="12345", author_username="u")
        ev = self._run_process(
            fetch_snap=snap,
            generate_reply_result={"text": "", "sources": None, "verdict_label": "NotEnoughEvidence",
                                   "action": "verify", "action_outcome": "verified_nei",
                                   "queries": []},
            post_reply_returns=[],
            monkeypatch=monkeypatch,
            fake_events_store=fake_events_store,
        )[0]
        assert ev.outcome == "empty_reply"
        assert ev.reply_id is None

    def test_x_post_error_outcome(self, monkeypatch, fake_events_store):
        from agent.app.utils import TweetSnapshot
        snap = TweetSnapshot(text="claim", author_id="12345", author_username="u")
        gen = {
            "text": "the response", "sources": None, "verdict_label": "Supported",
            "action": "verify", "action_outcome": "verified_supported",
            "queries": ["q"],
        }
        ev = self._run_process(
            fetch_snap=snap,
            generate_reply_result=gen,
            post_reply_returns=[None],
            monkeypatch=monkeypatch,
            fake_events_store=fake_events_store,
        )[0]
        assert ev.outcome == "x_post_error"
        assert ev.reply_text.startswith("the response")
        assert ev.reply_id is None

    def test_pipeline_error_outcome(self, monkeypatch, fake_events_store):
        monkeypatch.setattr(app_module, "DRY_RUN", False)
        def _boom(*a, **kw):
            raise RuntimeError("synthetic explosion")
        from agent.app import utils as utils_module
        monkeypatch.setattr(utils_module, "fetch_tweet", _boom)
        monkeypatch.setattr(app_module, "fetch_tweet", _boom)

        tweet = {"id_str": "555", "in_reply_to_status_id_str": "444",
                 "user": {"id_str": "111", "screen_name": "alice"}}
        app_module.process_mention("neutral", tweet, events_module.utcnow())
        ev = fake_events_store.events[-1]
        assert ev.outcome == "pipeline_error"
        assert ev.error_class == "RuntimeError"
        assert "synthetic explosion" in (ev.error_detail or "")


# ─── Link self-reply threading ──────────────────────────────────────────────

class TestLinkSelfReplyThreading:
    """The info link is posted as a separate self-reply so the main fact-check
    reply carries no URL (links suppress reach on X). Verifies the thread shape:
    mention → main reply (no link) → link reply (URL, parented to the main reply).
    """

    def _setup(self, monkeypatch, fake_events_store, post_reply_returns):
        monkeypatch.setattr(app_module, "DRY_RUN", False)
        from agent.app.utils import TweetSnapshot
        from agent.app import utils as utils_module
        snap = TweetSnapshot(
            text="Mail-in voting causes fraud.",
            author_id="12345",
            author_username="parent_user",
        )
        monkeypatch.setattr(utils_module, "fetch_tweet", lambda *a, **kw: snap)
        monkeypatch.setattr(app_module, "fetch_tweet", lambda *a, **kw: snap)
        monkeypatch.setattr(app_module, "generate_reply", lambda **kw: {
            "text": "Here are the facts.",
            "sources": ["https://a.example"],
            "verdict_label": "Refuted",
            "action": "verify",
            "action_outcome": "verified_refuted",
            "queries": ["q1"],
        })
        calls: list[dict] = []
        def _post(parent_id, reply_text):
            calls.append({"parent_id": parent_id, "reply_text": reply_text})
            return post_reply_returns[len(calls) - 1]
        monkeypatch.setattr(app_module, "post_reply", _post)
        tweet = {"id_str": "555", "in_reply_to_status_id_str": "444",
                 "user": {"id_str": "111", "screen_name": "alice"}}
        app_module.process_mention("neutral", tweet, events_module.utcnow())
        return calls, fake_events_store.events[-1]

    def test_main_reply_has_no_url_and_link_threads_under_it(self, monkeypatch, fake_events_store):
        calls, ev = self._setup(monkeypatch, fake_events_store, ["MAIN_ID", "LINK_ID"])
        assert len(calls) == 2, "expected a main reply and a separate link self-reply"
        main, link = calls
        # Main reply: parented to the mention, body only, NO URL.
        assert main["parent_id"] == "555"
        assert main["reply_text"] == "Here are the facts."
        assert "http" not in main["reply_text"]
        # Link reply: parented to the MAIN reply id (a self-reply), carries /info URL.
        assert link["parent_id"] == "MAIN_ID"
        assert "http" in link["reply_text"]
        assert "/i/" in link["reply_text"]
        # Engagement tracking + dossier embed point at the main reply, not the link.
        assert ev.outcome == "replied"
        assert ev.reply_id == "MAIN_ID"
        assert ev.reply_text == "Here are the facts."

    def test_link_reply_failure_marks_replied_no_link(self, monkeypatch, fake_events_store):
        # Main reply posts; link self-reply returns None on both attempt and
        # retry — the fact-check went out, but with NO sources link. We surface
        # that gap with outcome='replied_no_link' so analytics can see it.
        monkeypatch.setattr(app_module.time, "sleep", lambda *_a, **_kw: None)
        calls, ev = self._setup(monkeypatch, fake_events_store, ["MAIN_ID", None, None])
        assert len(calls) == 3  # main reply + initial link attempt + 1 retry
        assert ev.outcome == "replied_no_link"
        assert ev.reply_id == "MAIN_ID"
        assert ev.link_reply_id is None


# ─── TablesEventsStore schema regression ────────────────────────────────────

class _FakeResourceExistsError(Exception):
    pass


def _patched_tables_store(monkeypatch):
    """Construct a TablesEventsStore with the SDK stubbed."""
    events_client = MagicMock()
    drops_client = MagicMock()
    engagements_client = MagicMock()
    reply_replies_client = MagicMock()
    info_views_client = MagicMock()
    service = MagicMock()
    # Use return_value (not side_effect list) to avoid StopIteration in Python 3.12+
    service.create_table = MagicMock(return_value=None)
    service.get_table_client = MagicMock(
        side_effect=[events_client, drops_client, engagements_client,
                     reply_replies_client, info_views_client]
    )

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
            queries=["q1"],
        )
        store.write_event(ev)
        entity = events_client.create_entity.call_args[0][0]
        assert entity["PartitionKey"] == "2026-05"
        assert entity["RowKey"].startswith("2026-05-18T12:00:00")
        assert entity["RowKey"].endswith("_abc")
        assert json.loads(entity["queries_json"]) == ["q1"]

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
            received_at_utc=_now(),
        )
        store.write_event(ev)

    def test_truncates_long_text_fields(self, monkeypatch):
        store, events_client, _ = _patched_tables_store(monkeypatch)
        big = "x" * 40_000
        ev = events_module.MentionEvent(
            mention_id="abc", parent_id="p", author_id="u", tone="neutral",
            received_at_utc=_now(),
            parent_text=big, reply_text=big,
        )
        store.write_event(ev)
        entity = events_client.create_entity.call_args[0][0]
        assert len(entity["parent_text"]) <= 32_000
        assert len(entity["reply_text"]) <= 32_000

    def test_drop_with_naive_datetime_normalizes_to_utc_month(self, monkeypatch):
        """A naive datetime must be treated as UTC at the storage boundary —
        otherwise ``.strftime("%Y-%m")`` and ``.timestamp()`` would silently use
        the host's local tz and could land the row in the wrong month partition.

        Construct a naive datetime at an instant that, in UTC, is firmly in May
        2026; the entity's PartitionKey must be "2026-05" regardless of the
        host's local tz.
        """
        store, _, drops_client = _patched_tables_store(monkeypatch)
        # Naive datetime — no tzinfo. In UTC this is mid-May 2026.
        naive = datetime(2026, 5, 18, 12, 0, 0)
        drop = events_module.MentionDrop(
            drop_reason="invalid_payload",
            received_at_utc=naive,
            tone="neutral",
        )
        store.write_drop(drop)
        entity = drops_client.create_entity.call_args[0][0]
        assert entity["PartitionKey"] == "2026-05"
        # RowKey is built from the same normalized timestamp.
        assert entity["RowKey"].startswith("2026-05-18T12:00:00")
        # The nomid_ fingerprint uses the UTC timestamp, not local time.
        expected_ts = naive.replace(tzinfo=timezone.utc).timestamp()
        assert f"nomid_{expected_ts:.6f}" in entity["RowKey"]

    def test_ensure_utc_helper_normalizes_naive_and_non_utc_tz(self):
        from datetime import timedelta as _td
        # Naive: treated as UTC.
        naive = datetime(2026, 5, 18, 12, 0, 0)
        out = events_module._ensure_utc(naive)
        assert out.tzinfo == timezone.utc
        assert out.replace(tzinfo=None) == naive
        # Tz-aware UTC: passes through unchanged.
        utc = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        assert events_module._ensure_utc(utc) == utc
        # Tz-aware non-UTC: converted to UTC.
        pacific = timezone(_td(hours=-8))
        non_utc = datetime(2026, 5, 18, 4, 0, 0, tzinfo=pacific)
        out = events_module._ensure_utc(non_utc)
        assert out.utcoffset() == _td(0)
        assert out.hour == 12  # 04:00 -08:00 == 12:00 UTC

    def test_info_view_rows_have_unique_sortable_keys(self, monkeypatch):
        store, _, _ = _patched_tables_store(monkeypatch)
        ts = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        view = events_module.InfoView(
            token="tok", viewed_at_utc=ts, reply_id="r1", mention_id="m1",
            participant_id="a1", tone="neutral", user_agent="Twitterbot/1.0",
            is_bot=True,
        )
        store.write_info_view(view)
        store.write_info_view(view)
        calls = store._info_views.create_entity.call_args_list
        e0, e1 = calls[0][0][0], calls[1][0][0]
        assert e0["PartitionKey"] == "2026-05"
        assert e0["RowKey"].startswith("2026-05-18T12:00:00")
        assert e0["reply_id"] == "r1"
        assert e0["is_bot"] is True
        # Same token viewed twice → distinct RowKeys (per-view nonce).
        assert e0["RowKey"] != e1["RowKey"]
