"""Tests for the /api/participants endpoints used by the dashboard."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402
from agent.app import participants as participants_module  # noqa: E402


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
            json={"username": "@bob", "tone": "neutral", "notes": "pilot"},
        )
        assert resp.status_code == 201, resp.get_json()
        body = resp.get_json()
        assert body["participant"]["author_id"] == "555"
        assert body["participant"]["author_username"] == "bob"
        assert body["participant"]["notes"] == "pilot"
        assert body["participant"]["tone"] == "neutral"
        # Stored in the participants store
        stored = fresh_store.get("555")
        assert stored.author_username == "bob"
        assert stored.tone == "neutral"
        # And in the in-process cache
        assert "555" in app_module._PARTICIPANTS_BY_ID

    def test_tone_stored_from_body(self, fresh_store, client, monkeypatch):
        _patch_x_lookup(monkeypatch, user_id="100")
        resp = client.post("/api/participants", json={"username": "carla", "tone": "agreeable"})
        assert resp.status_code == 201
        assert resp.get_json()["participant"]["tone"] == "agreeable"

    def test_missing_username(self, fresh_store, client):
        resp = client.post("/api/participants", json={})
        assert resp.status_code == 400
        assert "username" in resp.get_json()["error"]

    def test_missing_tone_returns_400(self, fresh_store, client, monkeypatch):
        _patch_x_lookup(monkeypatch, user_id="200")
        resp = client.post("/api/participants", json={"username": "dave"})
        assert resp.status_code == 400
        assert "tone" in resp.get_json()["error"]

    def test_invalid_tone_returns_400(self, fresh_store, client, monkeypatch):
        _patch_x_lookup(monkeypatch, user_id="201")
        resp = client.post("/api/participants", json={"username": "eve", "tone": "hostile"})
        assert resp.status_code == 400
        assert "hostile" in resp.get_json()["error"]

    def test_random_tone_resolves_to_valid_tone(self, fresh_store, client, monkeypatch):
        _patch_x_lookup(monkeypatch, user_id="300")
        resp = client.post("/api/participants", json={"username": "frank", "tone": "random"})
        assert resp.status_code == 201, resp.get_json()
        assigned = resp.get_json()["participant"]["tone"]
        assert assigned in participants_module.VALID_TONES
        # The string "random" should never be stored — it must be resolved.
        assert assigned != "random"

    def test_random_tone_picks_least_used_for_balance(self, fresh_store, client, monkeypatch):
        from datetime import datetime, timezone
        now = datetime(2026, 5, 23, tzinfo=timezone.utc)
        # Skew: 2 agreeable, 2 neutral, 0 satirical → "random" must pick satirical.
        for i, t in enumerate(["agreeable", "agreeable", "neutral", "neutral"]):
            fresh_store.register(participants_module.Participant(
                author_id=f"seed-{i}", author_username=f"seed{i}",
                tone=t, enrolled_at_utc=now,
            ))
        _patch_x_lookup(monkeypatch, user_id="400")
        resp = client.post("/api/participants", json={"username": "grace", "tone": "random"})
        assert resp.status_code == 201
        assert resp.get_json()["participant"]["tone"] == "satirical"

    def test_lookup_failure_returns_422(self, fresh_store, client, monkeypatch):
        def _boom(username, **kw):
            raise participants_module.ParticipantLookupError(f"@{username} not found on X")
        monkeypatch.setattr(participants_module, "lookup_author_id", _boom)
        resp = client.post("/api/participants", json={"username": "ghost", "tone": "neutral"})
        assert resp.status_code == 422
        assert "not found" in resp.get_json()["error"]


class TestApiReplies:
    @pytest.fixture
    def events_store(self, monkeypatch):
        from agent.app import events as events_module
        store = events_module.InMemoryEventsStore()
        events_module.reset_store(store)
        yield store
        events_module.reset_store(None)

    def _make_event(self, **overrides):
        from datetime import datetime, timezone
        from agent.app.events import MentionEvent
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
        assert r["bot_handle"]  # populated from BOT_HANDLE
        assert r["reply_url"].endswith("/status/9001")
        assert r["tone"] == "agreeable"

    def test_blank_bot_handle_yields_empty_url(self, events_store, client, monkeypatch):
        monkeypatch.setattr(app_module, "BOT_HANDLE", "")
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
