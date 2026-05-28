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
