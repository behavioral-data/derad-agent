"""Regression test: the pipeline-queue-timeout drop must actually get written.

_process_mention_throttled built its MentionDrop with kwargs the dataclass
doesn't have (tweet_id=..., reason=...) instead of (mention_id=...,
drop_reason=...). The TypeError was swallowed by the surrounding try/except,
so the drop was silently lost — a queued-out mention left no trace at all.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402
from agent.app import events as events_module  # noqa: E402


class _NeverAcquiredSemaphore:
    """Stand-in for _PIPELINE_SEMAPHORE that always times out on acquire."""

    def acquire(self, timeout=None):
        return False

    def release(self):  # pragma: no cover - should not be called on this path
        raise AssertionError("release() should not be called when acquire() failed")


@pytest.fixture
def fake_events_store():
    store = events_module.InMemoryEventsStore()
    events_module.reset_store(store)
    yield store
    events_module.reset_store(None)


def test_queue_timeout_writes_a_drop_event(monkeypatch, fake_events_store):
    monkeypatch.setattr(app_module, "_PIPELINE_SEMAPHORE", _NeverAcquiredSemaphore())

    received_at = datetime.now(timezone.utc)
    tweet = {"id_str": "T123", "in_reply_to_status_id_str": "P1", "user": {"id_str": "111"}}

    # Must not raise — the fixed construction should build a valid MentionDrop.
    app_module._process_mention_throttled("neutral", tweet, received_at)

    assert len(fake_events_store.drops) == 1
    drop = fake_events_store.drops[0]
    assert drop.drop_reason == "pipeline_queue_timeout"
    assert drop.mention_id == "T123"
    assert drop.tone == "neutral"
    assert drop.received_at_utc == received_at
