"""Tests for derad_agent.app.metrics: daily kill switch and counter wiring.

These tests cover:
  - daily_cap_reached() logic in isolation (unit)
  - The webhook handler dropping with reason='daily_cap' when the limit is hit
  - mentions_received / mentions_accepted / mentions_dropped counter call sites

All counter objects are monkeypatched at the instance level (setattr on the
OTel no-op counter) — no real Azure backend needed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import date

import pytest

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
from derad_agent.app import metrics as metrics_module  # noqa: E402

SECRET = os.environ["X_API_SECRET"]


def _sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return "sha256=" + base64.b64encode(digest).decode("utf-8")


def _signed_post(client, payload: dict):
    if "for_user_id" not in payload:
        payload = {"for_user_id": "999", **payload}
    body = json.dumps(payload).encode()
    return client.post(
        "/mentions",
        data=body,
        headers={
            "X-Twitter-Webhooks-Signature": _sign(body),
            "Content-Type": "application/json",
        },
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dedup_module, "_default_store", dedup_module.InMemoryStore())

    def _no_pipeline(*a, **kw):
        raise AssertionError("process_mention must not run in these tests")

    monkeypatch.setattr(app_module, "process_mention", _no_pipeline)

    started = []

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=False, **_):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.args, self.kwargs))

    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    c._started_threads = started  # type: ignore[attr-defined]
    return c


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
        metrics_module.daily_cap_reached("neutral")  # at limit (not yet True)
        assert metrics_module.daily_cap_reached("neutral") is True
        assert metrics_module.daily_cap_reached("neutral") is True

    def test_per_tone_independence(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 1)
        # agreeable exhausts its limit
        metrics_module.daily_cap_reached("agreeable")
        assert metrics_module.daily_cap_reached("agreeable") is True
        # neutral is unaffected
        assert metrics_module.daily_cap_reached("neutral") is False

    def test_resets_on_date_rollover(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 1)

        day1 = date(2026, 5, 18)
        monkeypatch.setattr(metrics_module, "_utc_today", lambda: day1)
        metrics_module.daily_cap_reached("neutral")          # at limit
        assert metrics_module.daily_cap_reached("neutral") is True  # blocked

        day2 = date(2026, 5, 19)
        monkeypatch.setattr(metrics_module, "_utc_today", lambda: day2)
        assert metrics_module.daily_cap_reached("neutral") is False  # fresh day

    def test_no_reset_same_day(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 1)
        today = date(2026, 5, 18)
        monkeypatch.setattr(metrics_module, "_utc_today", lambda: today)
        metrics_module.daily_cap_reached("neutral")
        assert metrics_module.daily_cap_reached("neutral") is True  # persists


# ---------------------------------------------------------------------------
# Integration tests — counter wiring through the webhook handler
# ---------------------------------------------------------------------------

class TestMetricCounterWiring:
    """Verify that the right counters are incremented at the right call sites."""

    def setup_method(self):
        metrics_module._reset_counts_for_test()

    def _spy(self, monkeypatch, counter):
        """Return a call-log list and patch counter.add to append to it."""
        calls: list[dict] = []
        monkeypatch.setattr(counter, "add", lambda _n, attrs: calls.append(dict(attrs)))
        return calls

    def test_mentions_received_increments_on_valid_post(self, client, monkeypatch):
        received = self._spy(monkeypatch, metrics_module.mentions_received)
        app_module.ALLOWED_AUTHOR_IDS.add("111")
        try:
            payload = {"tweet_create_events": [
                {"id_str": "r1", "in_reply_to_status_id_str": "p1", "user": {"id_str": "111"}}
            ]}
            _signed_post(client, payload)
            assert len(received) == 1
            assert received[0]["tone"] == "neutral"
        finally:
            app_module.ALLOWED_AUTHOR_IDS.discard("111")

    def test_mentions_accepted_increments_when_dispatched(self, client, monkeypatch):
        accepted = self._spy(monkeypatch, metrics_module.mentions_accepted)
        app_module.ALLOWED_AUTHOR_IDS.add("111")
        try:
            payload = {"tweet_create_events": [
                {"id_str": "a1", "in_reply_to_status_id_str": "p1", "user": {"id_str": "111"}}
            ]}
            _signed_post(client, payload)
            assert len(accepted) == 1
            assert accepted[0]["tone"] == "neutral"
        finally:
            app_module.ALLOWED_AUTHOR_IDS.discard("111")

    def test_mentions_dropped_reason_unregistered(self, client, monkeypatch):
        dropped = self._spy(monkeypatch, metrics_module.mentions_dropped)
        # no-allow-listed author, RESTRICT_TO_REGISTERED=True (default in tests)
        payload = {"tweet_create_events": [
            {"id_str": "d1", "in_reply_to_status_id_str": "p1", "user": {"id_str": "not-in-list"}}
        ]}
        _signed_post(client, payload)
        reasons = [c["reason"] for c in dropped]
        assert "unregistered" in reasons

    def test_mentions_dropped_reason_duplicate(self, client, monkeypatch):
        dropped = self._spy(monkeypatch, metrics_module.mentions_dropped)
        app_module.ALLOWED_AUTHOR_IDS.add("111")
        try:
            payload = {"tweet_create_events": [
                {"id_str": "dup1", "in_reply_to_status_id_str": "p1", "user": {"id_str": "111"}}
            ]}
            _signed_post(client, payload)  # first — accepted
            _signed_post(client, payload)  # second — duplicate
            reasons = [c["reason"] for c in dropped]
            assert "duplicate" in reasons
        finally:
            app_module.ALLOWED_AUTHOR_IDS.discard("111")

    def test_daily_cap_drop_via_webhook(self, client, monkeypatch):
        monkeypatch.setattr(metrics_module, "_MAX_PER_DAY", 1)
        dropped = self._spy(monkeypatch, metrics_module.mentions_dropped)

        app_module.ALLOWED_AUTHOR_IDS.add("111")
        try:
            p1 = {"tweet_create_events": [
                {"id_str": "cap1", "in_reply_to_status_id_str": "pp1", "user": {"id_str": "111"}}
            ]}
            p2 = {"tweet_create_events": [
                {"id_str": "cap2", "in_reply_to_status_id_str": "pp2", "user": {"id_str": "111"}}
            ]}
            _signed_post(client, p1)  # accepted (count=1 == limit — not yet over)
            _signed_post(client, p2)  # dropped (count=2 > limit)

            # Only first mention should have started a thread.
            assert len(client._started_threads) == 1  # type: ignore[attr-defined]
            cap_drops = [c for c in dropped if c.get("reason") == "daily_cap"]
            assert len(cap_drops) >= 1, f"expected daily_cap drop; got {dropped}"
        finally:
            app_module.ALLOWED_AUTHOR_IDS.discard("111")
