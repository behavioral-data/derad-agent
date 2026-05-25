"""Tests for agent.app.streamer.

Verifies that the single-bot rule is produced from BOT_HANDLE and that the
filtered-stream worker dispatches each tweet with (tweet, timestamp) — tone is
no longer carried on the stream event.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test_key")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.example/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_EMBED", "test-embed")

from agent.app import streamer as streamer_module  # noqa: E402


class TestBotRules:
    def test_emits_single_rule_from_bot_handle(self, monkeypatch):
        monkeypatch.setenv("BOT_HANDLE", "eddiexbot")
        rules = streamer_module._bot_rules()
        assert rules == [{"value": "@eddiexbot"}]
        # No tag — tone is resolved downstream from the participant table.
        assert "tag" not in rules[0]

    def test_handles_leading_at_sign(self, monkeypatch):
        monkeypatch.setenv("BOT_HANDLE", "@eddiexbot")
        rules = streamer_module._bot_rules()
        assert rules == [{"value": "@eddiexbot"}]

    def test_empty_handle_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("BOT_HANDLE", "")
        assert streamer_module._bot_rules() == []


class TestReshape:
    def test_extracts_parent_id_from_replied_to_ref(self):
        data = {
            "id": "555",
            "text": "@eddiexbot please check this",
            "author_id": "111",
            "referenced_tweets": [{"type": "replied_to", "id": "444"}],
        }
        includes = {"users": [{"id": "111", "username": "alice"}]}
        out = streamer_module._reshape(data, includes)
        assert out["id_str"] == "555"
        assert out["in_reply_to_status_id_str"] == "444"
        assert out["user"]["id_str"] == "111"
        assert out["user"]["username"] == "alice"


class TestStreamLoopDispatchShape:
    """The _stream_loop calls dispatch_fn(tweet, ts) — never with a tone arg."""

    def test_dispatch_called_with_tweet_and_timestamp_only(self, monkeypatch):
        event_payload = {
            "data": {
                "id": "555",
                "text": "@eddiexbot what about this",
                "author_id": "111",
                "referenced_tweets": [{"type": "replied_to", "id": "444"}],
            },
            "includes": {"users": [{"id": "111", "username": "alice"}]},
        }

        class _FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def iter_lines(self):
                yield json.dumps(event_payload).encode("utf-8")

        monkeypatch.setattr(streamer_module.requests, "get", lambda *a, **kw: _FakeResp())

        captured: list[tuple] = []

        # The loop runs until _shutting_down is set; signal shutdown from
        # inside the dispatch handler so the loop exits cleanly after the
        # first event.
        def dispatch(tweet, ts):
            captured.append((tweet, ts))
            streamer_module._shutting_down.set()

        try:
            streamer_module._stream_loop(dispatch, token="dummy")
        finally:
            streamer_module._shutting_down.clear()

        assert len(captured) == 1
        tweet, ts = captured[0]
        assert tweet["id_str"] == "555"
        assert tweet["in_reply_to_status_id_str"] == "444"
        assert tweet["user"]["id_str"] == "111"
        assert isinstance(ts, datetime)
