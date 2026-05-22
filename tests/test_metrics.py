"""Tests for derad_agent.app.metrics: daily kill switch and counter wiring.

These tests cover:
  - daily_cap_reached() logic in isolation (unit)
  - _dispatch_tweet dropping with reason='daily_cap' when the limit is hit
  - mentions_received / mentions_accepted / mentions_dropped counter call sites
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test_key")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.example/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_EMBED", "test-embed")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_CHAT", "test-chat")
os.environ.setdefault("BOT_USER_ID_NEUTRAL", "999")

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
# Unit tests — daily_cap_reached() in isolation
# ---------------------------------------------------------------------------

class TestDailyCapUnit:
    def setup_method(self):
        metrics_module._reset_counts_for_test()

    def test_no_cap_when_max_zero(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 0)
        for _ in range(10):
            assert metrics_module.daily_cap_reached("neutral") is False

    def test_cap_allows_up_to_limit(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 3)
        assert metrics_module.daily_cap_reached("neutral") is False  # 1
        assert metrics_module.daily_cap_reached("neutral") is False  # 2
        assert metrics_module.daily_cap_reached("neutral") is False  # 3 (at limit)
        assert metrics_module.daily_cap_reached("neutral") is True   # 4 (over)

    def test_cap_keeps_blocking_once_hit(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 1)
        metrics_module.daily_cap_reached("neutral")
        assert metrics_module.daily_cap_reached("neutral") is True
        assert metrics_module.daily_cap_reached("neutral") is True

    def test_per_tone_independence(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 1)
        metrics_module.daily_cap_reached("agreeable")
        assert metrics_module.daily_cap_reached("agreeable") is True
        assert metrics_module.daily_cap_reached("neutral") is False

    def test_resets_on_date_rollover(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 1)
        day1 = date(2026, 5, 18)
        monkeypatch.setattr(metrics_module, "_utc_today", lambda: day1)
        metrics_module.daily_cap_reached("neutral")
        assert metrics_module.daily_cap_reached("neutral") is True

        day2 = date(2026, 5, 19)
        monkeypatch.setattr(metrics_module, "_utc_today", lambda: day2)
        assert metrics_module.daily_cap_reached("neutral") is False

    def test_no_reset_same_day(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 1)
        today = date(2026, 5, 18)
        monkeypatch.setattr(metrics_module, "_utc_today", lambda: today)
        metrics_module.daily_cap_reached("neutral")
        assert metrics_module.daily_cap_reached("neutral") is True


# ---------------------------------------------------------------------------
# Integration tests — counter wiring through _dispatch_tweet
# ---------------------------------------------------------------------------

class TestMetricCounterWiring:
    def setup_method(self):
        metrics_module._reset_counts_for_test()

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

    def test_daily_cap_drop(self, dispatch_env, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 1)
        dropped = self._spy(monkeypatch, metrics_module.mentions_dropped)
        app_module._dispatch_tweet("neutral", self._tweet("cap1", "111"), _now())
        app_module._dispatch_tweet("neutral", self._tweet("cap2", "111"), _now())
        assert len(dispatch_env["started"]) == 1
        cap_drops = [c for c in dropped if c.get("reason") == "daily_cap"]
        assert len(cap_drops) >= 1, f"expected daily_cap drop; got {dropped}"
