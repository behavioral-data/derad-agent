"""Tests for agent.cli.collect_replies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agent.app.events import BotReplyReply, InMemoryEventsStore
from agent.app import events as events_module
from agent.cli.collect_replies import _collect_one, main


def _utc(days_ago=0, **kwargs):
    base = datetime(2026, 5, 20, tzinfo=timezone.utc) - timedelta(days=days_ago)
    return base.replace(**kwargs) if kwargs else base


def _make_response(tweets: list[dict], users: list[dict] | None = None):
    """Build a fake X API response object."""
    resp = MagicMock()
    resp.data = tweets
    resp.includes = {"users": users or []}
    return resp


@pytest.fixture(autouse=True)
def fresh_events_store(monkeypatch):
    store = InMemoryEventsStore()
    monkeypatch.setattr(events_module, "_default_store", store)
    return store


class TestCollectOne:
    def test_happy_path_direct_reply(self, monkeypatch):
        """Direct reply tweet is written as BotReplyReply."""
        tweet = {
            "id": "rr1",
            "author_id": "user42",
            "text": "interesting take",
            "in_reply_to_tweet_id": "bot99",
            "public_metrics": {"like_count": 3},
        }
        user = {"id": "user42", "username": "alice"}

        fake_client = MagicMock()
        fake_client.tweets.search_recent.return_value = _make_response([tweet], [user])
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )

        written_replies: list[BotReplyReply] = []
        monkeypatch.setattr(
            "agent.cli.collect_replies.log_reply_reply",
            lambda r: written_replies.append(r),
        )

        count = _collect_one("bot99", "neutral", mention_id="m1", parent_id="p1")
        assert count == 1
        assert written_replies[0].reply_tweet_id == "rr1"
        assert written_replies[0].author_username == "alice"
        assert written_replies[0].like_count == 3
        assert written_replies[0].tone == "neutral"
        assert written_replies[0].mention_id == "m1"

    def test_non_direct_reply_is_filtered(self, monkeypatch):
        """A tweet in the conversation that doesn't reply to the bot tweet is skipped."""
        tweet = {
            "id": "other1",
            "author_id": "user99",
            "text": "replying to someone else",
            "in_reply_to_tweet_id": "some_other_tweet",
            "public_metrics": {"like_count": 0},
        }

        fake_client = MagicMock()
        fake_client.tweets.search_recent.return_value = _make_response([tweet])
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )
        monkeypatch.setattr("agent.cli.collect_replies.log_reply_reply", MagicMock())

        count = _collect_one("bot99", "neutral", mention_id=None, parent_id="p1")
        assert count == 0

    def test_api_exception_returns_zero(self, monkeypatch):
        """Network error returns 0 without raising."""
        fake_client = MagicMock()
        fake_client.tweets.search_recent.side_effect = RuntimeError("timeout")
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )

        count = _collect_one("bot99", "neutral", mention_id=None, parent_id=None)
        assert count == 0

    def test_falls_back_to_in_reply_to_query_when_no_parent(self, monkeypatch):
        """Without parent_id the query uses in_reply_to_tweet_id:."""
        captured_queries: list[str] = []

        fake_client = MagicMock()

        def _search(**kwargs):
            captured_queries.append(kwargs.get("query", ""))
            return _make_response([])

        fake_client.tweets.search_recent.side_effect = _search
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )

        _collect_one("bot99", "neutral", mention_id=None, parent_id=None)
        assert captured_queries[0].startswith("in_reply_to_tweet_id:")

    def test_uses_conversation_query_when_parent_present(self, monkeypatch):
        """With parent_id the query uses conversation_id:."""
        captured: list[str] = []

        fake_client = MagicMock()
        fake_client.tweets.search_recent.side_effect = lambda **kw: (
            captured.append(kw.get("query", "")) or _make_response([])
        )
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )

        _collect_one("bot99", "neutral", mention_id=None, parent_id="parent123")
        assert captured[0] == "conversation_id:parent123"


class TestCollectRepliesMain:
    def test_no_candidates_exits_early(self, monkeypatch, fresh_events_store):
        """With no replies in the 3-day window main() logs and returns."""
        # Store has no events so iter_reply_ids() is empty
        monkeypatch.setattr(events_module, "_default_store", fresh_events_store)
        log_calls: list[str] = []
        monkeypatch.setattr(
            "agent.cli.collect_replies.logger",
            MagicMock(info=lambda msg, *a: log_calls.append(msg % a if a else msg)),
        )
        main()
        assert any("nothing to collect" in m for m in log_calls)
