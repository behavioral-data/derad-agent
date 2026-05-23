"""Tests for derad_agent.app.metrics counter wiring and the per-user daily cap.

The cap itself lives in app._dispatch_tweet (it counts via dedup.hit_and_count
rather than a separate in-memory counter), so its tests sit alongside the
mention-counter wiring rather than in metrics.py.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test_key")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.example/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_EMBED", "test-embed")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_CHAT", "test-chat")
os.environ.setdefault("BOT_USER_ID_NEUTRAL", "999")
os.environ.setdefault("BOT_USER_ID_AGREEABLE", "1000")

from derad_agent.app import app as app_module  # noqa: E402
from derad_agent.app import dedup as dedup_module  # noqa: E402
from derad_agent.app import metrics as metrics_module  # noqa: E402


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def dispatch_env(monkeypatch):
    """Fresh dedup store + thread capture for _dispatch_tweet tests."""
    monkeypatch.setattr(dedup_module, "_default_store", dedup_module.InMemoryStore())

    started: list[tuple] = []

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=False, **_):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            started.append((self.target, self.args, self.kwargs))

    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    return {"started": started}


# ---------------------------------------------------------------------------
# Integration tests — counter wiring through _dispatch_tweet
# ---------------------------------------------------------------------------

class TestMetricCounterWiring:
    def _spy(self, monkeypatch, counter):
        calls: list[dict] = []
        monkeypatch.setattr(counter, "add", lambda _n, attrs: calls.append(dict(attrs)))
        return calls

    def _tweet(self, id_str="r1", author="111"):
        return {"id_str": id_str, "in_reply_to_status_id_str": "p1", "user": {"id_str": author}}

    def test_mentions_received_increments_on_valid_post(self, dispatch_env, monkeypatch):
        received = self._spy(monkeypatch, metrics_module.mentions_received)
        app_module._dispatch_tweet("neutral", self._tweet(), _now())
        assert len(received) == 1
        assert received[0]["tone"] == "neutral"

    def test_mentions_accepted_increments_when_dispatched(self, dispatch_env, monkeypatch):
        accepted = self._spy(monkeypatch, metrics_module.mentions_accepted)
        app_module._dispatch_tweet("neutral", self._tweet("a1"), _now())
        assert len(accepted) == 1
        assert accepted[0]["tone"] == "neutral"

    def test_mentions_dropped_reason_duplicate(self, dispatch_env, monkeypatch):
        dropped = self._spy(monkeypatch, metrics_module.mentions_dropped)
        tweet = self._tweet("dup1")
        app_module._dispatch_tweet("neutral", tweet, _now())
        app_module._dispatch_tweet("neutral", tweet, _now())
        reasons = [c["reason"] for c in dropped]
        assert "duplicate" in reasons


# ---------------------------------------------------------------------------
# Per-user daily cap (in _dispatch_tweet, backed by dedup.hit_and_count)
# ---------------------------------------------------------------------------

class TestUserDailyCap:
    def test_blocks_after_cap_reached(self, dispatch_env, monkeypatch):
        # Cap = 2. Same author, distinct mention_ids → first two accepted, third dropped.
        monkeypatch.setattr(app_module, "USER_DAILY_CAP", 2)
        author = "user-a"
        for i in range(2):
            assert app_module._dispatch_tweet(
                "neutral",
                {"id_str": f"m{i}", "in_reply_to_status_id_str": "p1", "user": {"id_str": author}},
                _now(),
            ) is True
        # Third mention from same author: dropped.
        assert app_module._dispatch_tweet(
            "neutral",
            {"id_str": "m2", "in_reply_to_status_id_str": "p1", "user": {"id_str": author}},
            _now(),
        ) is False
        assert len(dispatch_env["started"]) == 2

    def test_cap_is_per_user_not_global(self, dispatch_env, monkeypatch):
        monkeypatch.setattr(app_module, "USER_DAILY_CAP", 1)
        # User A: 1 accepted, 2nd dropped
        assert app_module._dispatch_tweet(
            "neutral",
            {"id_str": "a1", "in_reply_to_status_id_str": "p1", "user": {"id_str": "user-a"}},
            _now(),
        ) is True
        assert app_module._dispatch_tweet(
            "neutral",
            {"id_str": "a2", "in_reply_to_status_id_str": "p1", "user": {"id_str": "user-a"}},
            _now(),
        ) is False
        # User B: still has full budget
        assert app_module._dispatch_tweet(
            "neutral",
            {"id_str": "b1", "in_reply_to_status_id_str": "p1", "user": {"id_str": "user-b"}},
            _now(),
        ) is True

    def test_cap_covers_all_bots(self, dispatch_env, monkeypatch):
        # User hits cap on agreeable; further calls to neutral or satirical also dropped.
        monkeypatch.setattr(app_module, "USER_DAILY_CAP", 1)
        author = "user-multi"
        assert app_module._dispatch_tweet(
            "agreeable",
            {"id_str": "x1", "in_reply_to_status_id_str": "p1", "user": {"id_str": author}},
            _now(),
        ) is True
        # Same author on a different tone — still counts against the same daily bucket.
        assert app_module._dispatch_tweet(
            "neutral",
            {"id_str": "x2", "in_reply_to_status_id_str": "p1", "user": {"id_str": author}},
            _now(),
        ) is False

    def test_disabled_when_cap_zero(self, dispatch_env, monkeypatch):
        monkeypatch.setattr(app_module, "USER_DAILY_CAP", 0)
        # Bypass the per-second burst limit so we can drive 5 mentions through quickly.
        monkeypatch.setattr(app_module, "RATE_LIMIT_PER_SEC", 1000)
        author = "user-unlimited"
        for i in range(5):
            assert app_module._dispatch_tweet(
                "neutral",
                {"id_str": f"u{i}", "in_reply_to_status_id_str": "p1", "user": {"id_str": author}},
                _now(),
            ) is True
        assert len(dispatch_env["started"]) == 5

    def test_drop_reason_and_extras(self, dispatch_env, monkeypatch):
        monkeypatch.setattr(app_module, "USER_DAILY_CAP", 1)
        dropped: list[dict] = []
        monkeypatch.setattr(
            metrics_module.mentions_dropped,
            "add",
            lambda _n, attrs: dropped.append(dict(attrs)),
        )
        author = "user-cap"
        app_module._dispatch_tweet(
            "neutral",
            {"id_str": "c1", "in_reply_to_status_id_str": "p1", "user": {"id_str": author}},
            _now(),
        )
        app_module._dispatch_tweet(
            "neutral",
            {"id_str": "c2", "in_reply_to_status_id_str": "p1", "user": {"id_str": author}},
            _now(),
        )
        cap_drops = [d for d in dropped if d.get("reason") == "daily_cap"]
        assert len(cap_drops) == 1
