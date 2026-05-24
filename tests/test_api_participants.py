"""Tests for the /api/participants endpoints used by the dashboard."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

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
from derad_agent.app import participants as participants_module  # noqa: E402


@pytest.fixture
def fresh_store():
    store = participants_module.InMemoryParticipantsStore()
    participants_module.reset_store(store)
    # The app caches participants in a process-wide dict; reset it for isolation.
    app_module._PARTICIPANTS_BY_ID.clear()
    yield store
    participants_module.reset_store(None)
    app_module._PARTICIPANTS_BY_ID.clear()


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _patch_x_lookup(monkeypatch, *, user_id="12345"):
    fake_client = MagicMock()
    resp = MagicMock()
    resp.data = {"id": user_id, "username": "ignored"}
    fake_client.users.get_by_username.return_value = resp
    monkeypatch.setattr(
        participants_module,
        "lookup_author_id",
        lambda username, **kw: user_id,
    )
    return fake_client


class TestApiParticipantsList:
    def test_empty(self, fresh_store, client):
        resp = client.get("/api/participants")
        assert resp.status_code == 200
        assert resp.get_json() == {"participants": []}

    def test_returns_registered(self, fresh_store, client):
        from datetime import datetime, timezone
        fresh_store.register(participants_module.Participant(
            author_id="42", author_username="alice", tone="neutral",
            enrolled_at_utc=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ))
        resp = client.get("/api/participants")
        rows = resp.get_json()["participants"]
        assert len(rows) == 1
        assert rows[0]["author_id"] == "42"
        assert rows[0]["author_username"] == "alice"
        assert rows[0]["tone"] == "neutral"


class TestApiParticipantsCreate:
    def test_creates_participant(self, fresh_store, client, monkeypatch):
        _patch_x_lookup(monkeypatch, user_id="555")
        resp = client.post(
            "/api/participants",
            json={"username": "@bob", "notes": "pilot"},
        )
        assert resp.status_code == 201, resp.get_json()
        body = resp.get_json()
        assert body["participant"]["author_id"] == "555"
        assert body["participant"]["author_username"] == "bob"
        assert body["participant"]["notes"] == "pilot"
        # Tone is not assigned via the dashboard.
        assert body["participant"]["tone"] == ""
        # Stored in the participants store
        stored = fresh_store.get("555")
        assert stored.author_username == "bob"
        assert stored.tone == ""
        # And in the in-process cache
        assert "555" in app_module._PARTICIPANTS_BY_ID

    def test_tone_in_body_is_ignored(self, fresh_store, client, monkeypatch):
        _patch_x_lookup(monkeypatch, user_id="100")
        resp = client.post("/api/participants", json={"username": "carla", "tone": "agreeable"})
        assert resp.status_code == 201
        assert resp.get_json()["participant"]["tone"] == ""

    def test_missing_username(self, fresh_store, client):
        resp = client.post("/api/participants", json={})
        assert resp.status_code == 400
        assert "username" in resp.get_json()["error"]

    def test_lookup_failure_returns_422(self, fresh_store, client, monkeypatch):
        def _boom(username, **kw):
            raise participants_module.ParticipantLookupError(f"@{username} not found on X")
        monkeypatch.setattr(participants_module, "lookup_author_id", _boom)
        resp = client.post("/api/participants", json={"username": "ghost"})
        assert resp.status_code == 422
        assert "not found" in resp.get_json()["error"]


class TestApiReplies:
    @pytest.fixture
    def events_store(self, monkeypatch):
        from derad_agent.app import events as events_module
        store = events_module.InMemoryEventsStore()
        events_module.reset_store(store)
        yield store
        events_module.reset_store(None)

    def _make_event(self, **overrides):
        from datetime import datetime, timezone
        from derad_agent.app.events import MentionEvent
        base = dict(
            mention_id="m1",
            parent_id="p1",
            author_id="u1",
            tone="agreeable",
            received_at_utc=datetime(2026, 5, 22, 14, 32, 10, tzinfo=timezone.utc),
        )
        base.update(overrides)
        return MentionEvent(**base)

    def test_returns_only_events_with_reply_id(self, events_store, client):
        # Event 1: posted reply
        events_store.write_event(self._make_event(
            mention_id="m1", author_id="u1", author_username="alice",
            tone="agreeable", reply_id="9001", reply_text="Hi", outcome="replied",
        ))
        # Event 2: pipeline error, no reply_id — should be filtered out
        events_store.write_event(self._make_event(
            mention_id="m2", author_id="u2", tone="neutral",
            outcome="pipeline_error",
        ))
        resp = client.get("/api/replies")
        assert resp.status_code == 200
        replies = resp.get_json()["replies"]
        assert len(replies) == 1
        r = replies[0]
        assert r["reply_id"] == "9001"
        assert r["author_username"] == "alice"
        assert r["bot_handle"]  # populated from BOT_HANDLE_BY_TONE
        assert r["reply_url"].endswith("/status/9001")
        assert r["tone"] == "agreeable"

    def test_unknown_tone_yields_empty_url(self, events_store, client, monkeypatch):
        monkeypatch.setitem(app_module.BOT_HANDLE_BY_TONE, "agreeable", "")
        events_store.write_event(self._make_event(
            reply_id="42", tone="agreeable", outcome="replied",
        ))
        resp = client.get("/api/replies")
        assert resp.get_json()["replies"][0]["reply_url"] == ""

    def test_limit_clamped(self, events_store, client):
        resp = client.get("/api/replies?limit=0")
        assert resp.status_code == 200
        resp = client.get("/api/replies?limit=not-a-number")
        assert resp.status_code == 200
