"""Tests for derad-poll-engagement metric ingestion edge cases."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import events as events_module  # noqa: E402
from agent.cli import poll_engagement  # noqa: E402


class _FakePosts:
    def __init__(self, metrics):
        self._metrics = metrics

    def get_by_id(self, id, tweet_fields):  # noqa: A002 - match SDK signature
        return SimpleNamespace(data={"public_metrics": self._metrics})


class _FakeClient:
    def __init__(self, metrics):
        self.posts = _FakePosts(metrics)


def test_poll_one_coerces_none_metrics_to_zero():
    store = events_module.InMemoryEventsStore()
    events_module.reset_store(store)
    try:
        metrics = {
            "like_count": None,
            "retweet_count": 0,
            "reply_count": None,
            "quote_count": 0,
        }
        with patch.object(poll_engagement, "get_x_client", return_value=_FakeClient(metrics)):
            poll_engagement._poll_one("R1", "neutral")

        assert len(store.engagements) == 1
        snap = store.engagements[0]
        assert snap.like_count == 0
        assert snap.retweet_count == 0
        assert snap.reply_count == 0
        assert snap.quote_count == 0
    finally:
        events_module.reset_store(None)


def _poll_with(reply_count, link_reply_id):
    store = events_module.InMemoryEventsStore()
    events_module.reset_store(store)
    try:
        metrics = {"like_count": 0, "retweet_count": 0, "reply_count": reply_count, "quote_count": 0}
        with patch.object(poll_engagement, "get_x_client", return_value=_FakeClient(metrics)):
            poll_engagement._poll_one("R1", "neutral", link_reply_id=link_reply_id)
        return store.engagements[0]
    finally:
        events_module.reset_store(None)


def test_adjusted_reply_count_subtracts_link_self_reply():
    """When a link self-reply exists, adjusted = raw - 1 (the bot's own reply)."""
    snap = _poll_with(reply_count=5, link_reply_id="L1")
    assert snap.reply_count == 5            # raw preserved
    assert snap.adjusted_reply_count == 4   # bot's link reply removed


def test_adjusted_reply_count_equals_raw_without_link_reply():
    """No link reply posted (e.g. replied_no_link) → no adjustment."""
    snap = _poll_with(reply_count=3, link_reply_id=None)
    assert snap.reply_count == 3
    assert snap.adjusted_reply_count == 3


def test_adjusted_reply_count_clamped_at_zero():
    """If X reports 0 replies but we expected the link reply, don't go negative."""
    snap = _poll_with(reply_count=0, link_reply_id="L1")
    assert snap.adjusted_reply_count == 0
