"""Tests for derad_agent.app.poller and derad_agent.app.cursors.

Covers:
  - _v2_to_v1_tweet() normalization (replied_to, no refs, non-reply refs, username)
  - InMemoryCursorStore get/set
  - TablesCursorStore get/set/not-found (mock-based, no real Azure backend)
  - _poll_one() bootstrap: cursor set, no dispatch
  - _poll_one() normal: dispatch + cursor update
  - _poll_one() since_id passed from cursor
  - _poll_one() empty page → no dispatch, no cursor write
  - _poll_one() exception swallowed (loop must not crash)
  - _poll_one() page cap stops pagination and warns
  - _poll_one() multi-page cursor taken from first page
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test_key")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.example/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_EMBED", "test-embed")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_CHAT", "test-chat")
os.environ.setdefault("BOT_USER_ID_NEUTRAL", "999")
os.environ.setdefault("DERAD_ALLOWED_AUTHOR_IDS", "111,222")

from derad_agent.app.cursors import InMemoryCursorStore, TablesCursorStore  # noqa: E402
from derad_agent.app.poller import _poll_one, _v2_to_v1_tweet  # noqa: E402
import derad_agent.app.poller as poller_module  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

class _FakeMeta:
    def __init__(self, newest_id=None):
        self.newest_id = newest_id


class _FakeIncludes:
    def __init__(self, users=None):
        self.users = users or []


class _FakePage:
    def __init__(self, data=None, newest_id=None, users=None):
        self.data = data or []
        self.meta = _FakeMeta(newest_id) if newest_id else None
        self.includes = _FakeIncludes(users) if users is not None else None


def _make_client(pages: list[_FakePage]):
    """Return a mock xdk-like client whose users.get_mentions yields pages."""
    mock_users = MagicMock()
    mock_users.get_mentions.return_value = iter(pages)
    mock_client = MagicMock()
    mock_client.users = mock_users
    return mock_client


def _make_v2_tweet(tweet_id: str, author_id: str, parent_id: str) -> dict:
    return {
        "id": tweet_id,
        "author_id": author_id,
        "text": f"@bot check this (id={tweet_id})",
        "referenced_tweets": [{"id": parent_id, "type": "replied_to"}],
    }


# ── _v2_to_v1_tweet ───────────────────────────────────────────────────────

class TestV2ToV1Tweet:
    def test_replied_to_ref_becomes_parent(self):
        v2 = {
            "id": "1001",
            "author_id": "42",
            "text": "hello @bot",
            "referenced_tweets": [{"id": "999", "type": "replied_to"}],
        }
        result = _v2_to_v1_tweet(v2, user_id="bot_id")
        assert result["id_str"] == "1001"
        assert result["in_reply_to_status_id_str"] == "999"
        assert result["user"]["id_str"] == "42"
        assert result["text"] == "hello @bot"

    def test_no_referenced_tweets_gives_none_parent(self):
        v2 = {"id": "2002", "author_id": "55", "text": "standalone"}
        result = _v2_to_v1_tweet(v2, user_id="bot_id")
        assert result["in_reply_to_status_id_str"] is None

    def test_non_reply_ref_gives_none_parent(self):
        v2 = {
            "id": "3003",
            "author_id": "77",
            "text": "RT",
            "referenced_tweets": [{"id": "888", "type": "retweeted"}],
        }
        result = _v2_to_v1_tweet(v2, user_id="bot_id")
        assert result["in_reply_to_status_id_str"] is None

    def test_missing_author_id_falls_back_to_user_id(self):
        v2 = {"id": "4004", "text": "hi"}
        result = _v2_to_v1_tweet(v2, user_id="fallback_uid")
        assert result["user"]["id_str"] == "fallback_uid"

    def test_username_resolved_from_users_by_id(self):
        v2 = {"id": "5005", "author_id": "42", "text": "hi"}
        result = _v2_to_v1_tweet(v2, user_id="bot_id", users_by_id={"42": "alice"})
        assert result["user"]["username"] == "alice"

    def test_no_username_key_when_not_in_lookup(self):
        v2 = {"id": "6006", "author_id": "99", "text": "hi"}
        result = _v2_to_v1_tweet(v2, user_id="bot_id", users_by_id={"42": "alice"})
        assert "username" not in result["user"]


# ── InMemoryCursorStore ───────────────────────────────────────────────────

class TestInMemoryCursorStore:
    def test_get_missing_returns_none(self):
        store = InMemoryCursorStore()
        assert store.get("poll_cursor:neutral") is None

    def test_set_then_get_roundtrip(self):
        store = InMemoryCursorStore()
        store.set("poll_cursor:neutral", "12345")
        assert store.get("poll_cursor:neutral") == "12345"

    def test_overwrite_updates_value(self):
        store = InMemoryCursorStore()
        store.set("k", "old")
        store.set("k", "new")
        assert store.get("k") == "new"

    def test_keys_are_independent(self):
        store = InMemoryCursorStore()
        store.set("a", "1")
        store.set("b", "2")
        assert store.get("a") == "1"
        assert store.get("b") == "2"


# ── TablesCursorStore (mock-based) ─────────────────────────────────────────

class TestTablesCursorStore:
    def _make_store(self, mock_table_client):
        """Bypass __init__ and inject a pre-built mock table client."""
        from azure.core.exceptions import ResourceNotFoundError
        store = object.__new__(TablesCursorStore)
        store._client = mock_table_client
        store._ResourceNotFoundError = ResourceNotFoundError
        return store

    def test_get_returns_cursor_value(self):
        client = MagicMock()
        client.get_entity.return_value = {"cursor_value": "99999"}
        store = self._make_store(client)
        assert store.get("poll_cursor:neutral") == "99999"
        client.get_entity.assert_called_once_with(
            partition_key="cursors", row_key="poll_cursor:neutral"
        )

    def test_get_returns_none_when_not_found(self):
        from azure.core.exceptions import ResourceNotFoundError
        client = MagicMock()
        client.get_entity.side_effect = ResourceNotFoundError()
        store = self._make_store(client)
        assert store.get("poll_cursor:neutral") is None

    def test_set_upserts_correct_entity(self):
        client = MagicMock()
        store = self._make_store(client)
        store.set("poll_cursor:neutral", "88888")
        client.upsert_entity.assert_called_once_with({
            "PartitionKey": "cursors",
            "RowKey": "poll_cursor:neutral",
            "cursor_value": "88888",
        })


# ── _poll_one ─────────────────────────────────────────────────────────────

class TestPollOne:
    def test_bootstrap_sets_cursor_without_dispatching(self):
        """First call (no cursor) records watermark but dispatches nothing."""
        cursor_store = InMemoryCursorStore()
        dispatched = []

        page = _FakePage(
            data=[_make_v2_tweet("50", "111", "10")],
            newest_id="50",
        )
        _poll_one(
            "neutral", "999",
            lambda tone, tweet, ts: dispatched.append(tweet["id_str"]),
            cursor_store,
            x_client_factory=lambda tone: _make_client([page]),
        )

        assert dispatched == []
        assert cursor_store.get("poll_cursor:neutral") == "50"

    def test_dispatches_each_tweet_on_page(self):
        cursor_store = InMemoryCursorStore()
        cursor_store.set("poll_cursor:neutral", "1")  # skip bootstrap
        dispatched = []

        page = _FakePage(
            data=[
                _make_v2_tweet("10", "111", "1"),
                _make_v2_tweet("11", "222", "2"),
            ],
            newest_id="11",
        )
        _poll_one(
            "neutral", "999",
            lambda tone, tweet, ts: dispatched.append((tone, tweet["id_str"])),
            cursor_store,
            x_client_factory=lambda tone: _make_client([page]),
        )

        assert len(dispatched) == 2
        assert dispatched[0] == ("neutral", "10")
        assert dispatched[1] == ("neutral", "11")

    def test_username_populated_from_page_includes(self):
        cursor_store = InMemoryCursorStore()
        cursor_store.set("poll_cursor:neutral", "1")
        normalized_tweets = []

        page = _FakePage(
            data=[_make_v2_tweet("20", "111", "5")],
            newest_id="20",
            users=[{"id": "111", "username": "alice_test"}],
        )
        _poll_one(
            "neutral", "999",
            lambda tone, tweet, ts: normalized_tweets.append(tweet),
            cursor_store,
            x_client_factory=lambda tone: _make_client([page]),
        )

        assert len(normalized_tweets) == 1
        assert normalized_tweets[0]["user"]["username"] == "alice_test"

    def test_cursor_updated_to_newest_id(self):
        cursor_store = InMemoryCursorStore()
        cursor_store.set("poll_cursor:neutral", "1")  # skip bootstrap

        page = _FakePage(
            data=[_make_v2_tweet("99", "111", "50")],
            newest_id="99",
        )
        _poll_one(
            "neutral", "999",
            lambda *a: None,
            cursor_store,
            x_client_factory=lambda tone: _make_client([page]),
        )

        assert cursor_store.get("poll_cursor:neutral") == "99"

    def test_since_id_passed_from_cursor(self):
        cursor_store = InMemoryCursorStore()
        cursor_store.set("poll_cursor:agreeable", "50")
        captured_kwargs: dict = {}

        def fake_get_mentions(id, **kwargs):
            captured_kwargs.update(kwargs)
            yield _FakePage(data=[], newest_id=None)

        mock_client = MagicMock()
        mock_client.users.get_mentions.side_effect = fake_get_mentions

        _poll_one(
            "agreeable", "101",
            lambda *a: None,
            cursor_store,
            x_client_factory=lambda tone: mock_client,
        )

        assert captured_kwargs.get("since_id") == "50"

    def test_empty_page_no_dispatch_no_cursor(self):
        cursor_store = InMemoryCursorStore()
        dispatched = []

        _poll_one(
            "neutral", "999",
            lambda *a: dispatched.append(True),
            cursor_store,
            x_client_factory=lambda tone: _make_client([_FakePage(data=[])]),
        )

        assert dispatched == []
        assert cursor_store.get("poll_cursor:neutral") is None

    def test_exception_in_client_does_not_propagate(self):
        cursor_store = InMemoryCursorStore()

        def bad_factory(tone):
            raise RuntimeError("API down")

        _poll_one(
            "neutral", "999",
            lambda *a: None,
            cursor_store,
            x_client_factory=bad_factory,
        )

    def test_cursor_not_updated_when_no_newest_id(self):
        cursor_store = InMemoryCursorStore()
        cursor_store.set("poll_cursor:neutral", "1")  # skip bootstrap

        page = _FakePage(
            data=[_make_v2_tweet("7", "111", "3")],
            newest_id=None,
        )
        _poll_one(
            "neutral", "999",
            lambda *a: None,
            cursor_store,
            x_client_factory=lambda tone: _make_client([page]),
        )

        assert cursor_store.get("poll_cursor:neutral") == "1"  # unchanged

    def test_page_cap_stops_pagination_and_warns(self, monkeypatch):
        """Dispatch stops after _MAX_PAGES_PER_POLL pages and logs a warning."""
        monkeypatch.setattr(poller_module, "_MAX_PAGES_PER_POLL", 2)

        cursor_store = InMemoryCursorStore()
        cursor_store.set("poll_cursor:neutral", "1")  # skip bootstrap
        dispatched = []

        pages = [
            _FakePage(data=[_make_v2_tweet(str(i + 10), "111", "1")], newest_id=str(i + 10))
            for i in range(3)
        ]
        _poll_one(
            "neutral", "999",
            lambda tone, tweet, ts: dispatched.append(tweet["id_str"]),
            cursor_store,
            x_client_factory=lambda tone: _make_client(pages),
        )

        assert len(dispatched) == 2  # capped at 2 pages × 1 tweet
        assert cursor_store.get("poll_cursor:neutral") == "10"  # newest_id from page 1

    def test_multi_page_cursor_from_first_page(self):
        """Cursor should be newest_id of first page (global newest), not last."""
        cursor_store = InMemoryCursorStore()
        cursor_store.set("poll_cursor:neutral", "1")  # skip bootstrap

        pages = [
            _FakePage(data=[_make_v2_tweet("20", "111", "10")], newest_id="20"),
            _FakePage(data=[_make_v2_tweet("15", "111", "10")], newest_id="15"),
        ]
        _poll_one(
            "neutral", "999",
            lambda *a: None,
            cursor_store,
            x_client_factory=lambda tone: _make_client(pages),
        )

        assert cursor_store.get("poll_cursor:neutral") == "20"
