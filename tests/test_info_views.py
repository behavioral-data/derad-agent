"""Tests for /i/<token> dossier click tracking (InfoView events)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402
from agent.app import events as events_module  # noqa: E402

_BROWSER_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
               "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1")


@pytest.fixture
def store():
    s = events_module.InMemoryEventsStore()
    events_module.reset_store(s)
    yield s
    events_module.reset_store(None)


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _make_token():
    token = app_module._make_info_token(
        "neutral",
        "Here are the facts.",
        {"action": "verify", "action_outcome": "verified_refuted"},
        parent_id="PARENT_1",
        parent_author_username="claimant",
        bot_handle="eddiexbot",
        mention_id="MENTION_1",
        participant_id="AUTHOR_1",
    )
    app_module._update_info_token(token, reply_id="REPLY_1")
    return token


class TestInfoViewTracking:
    def test_human_click_is_logged_and_not_flagged_bot(self, store, client):
        token = _make_token()
        resp = client.get(f"/i/{token}", headers={
            "User-Agent": _BROWSER_UA,
            "Referer": "https://t.co/abc123",
        })
        assert resp.status_code == 200
        assert len(store.info_views) == 1
        v = store.info_views[0]
        assert v.token == token
        assert v.reply_id == "REPLY_1"
        assert v.mention_id == "MENTION_1"
        assert v.participant_id == "AUTHOR_1"
        assert v.parent_id == "PARENT_1"
        assert v.tone == "neutral"
        assert v.referrer == "https://t.co/abc123"
        assert v.is_bot is False

    def test_crawler_click_is_flagged_bot(self, store, client):
        token = _make_token()
        resp = client.get(f"/i/{token}", headers={"User-Agent": "Twitterbot/1.0"})
        assert resp.status_code == 200
        assert len(store.info_views) == 1
        assert store.info_views[0].is_bot is True

    def test_missing_user_agent_flagged_bot(self, store, client):
        token = _make_token()
        # Werkzeug test client sends no UA by default unless set.
        resp = client.get(f"/i/{token}", headers={"User-Agent": ""})
        assert resp.status_code == 200
        assert store.info_views[0].is_bot is True

    def test_invalid_token_logs_nothing(self, store, client):
        resp = client.get("/i/does-not-exist")
        assert resp.status_code == 404
        assert store.info_views == []

    def test_each_view_recorded_separately(self, store, client):
        token = _make_token()
        for _ in range(3):
            client.get(f"/i/{token}", headers={"User-Agent": _BROWSER_UA})
        assert len(store.info_views) == 3

    def test_evicted_token_reconstructs_fks_from_tables(self, store, client, monkeypatch):
        """After the 24h in-memory cache eviction, a click reconstructs the FK
        fields from the persisted Tables row (the cold path that runs in prod)."""
        token = _make_token()
        with app_module._INFO_STORE_LOCK:
            app_module._INFO_STORE.pop(token, None)  # simulate TTL eviction
        fake_table = MagicMock()
        fake_table.get_entity.return_value = {
            "tone": "neutral",
            "reply_text": "Here are the facts.",
            "payload_json": "{}",
            "parent_id": "PARENT_1",
            "parent_author_username": "claimant",
            "bot_handle": "eddiexbot",
            "reply_id": "REPLY_1",
            "mention_id": "MENTION_1",
            "participant_id": "AUTHOR_1",
        }
        monkeypatch.setattr(app_module, "_get_info_table", lambda: fake_table)
        resp = client.get(f"/i/{token}", headers={"User-Agent": _BROWSER_UA})
        assert resp.status_code == 200
        v = store.info_views[0]
        assert v.reply_id == "REPLY_1"
        assert v.mention_id == "MENTION_1"
        assert v.participant_id == "AUTHOR_1"
