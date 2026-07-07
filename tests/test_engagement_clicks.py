"""Tests that /api/engagement folds dossier link clicks (InfoView) into the
engagement metrics table, totals, and per-condition breakdown."""

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

_TS = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


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


def _view(store, reply_id, tone, is_bot):
    store.write_info_view(events_module.InfoView(
        token="tok", viewed_at_utc=_TS, reply_id=reply_id, tone=tone, is_bot=is_bot,
    ))


class TestEngagementClicks:
    def test_clicks_attached_to_reply_with_snapshot(self, store, client):
        store.write_engagement(events_module.EngagementSnapshot(
            reply_id="R1", tone="neutral", polled_at_utc=_TS, like_count=3))
        _view(store, "R1", "neutral", is_bot=False)
        _view(store, "R1", "neutral", is_bot=False)
        _view(store, "R1", "neutral", is_bot=True)  # crawler

        data = client.get("/api/engagement").get_json()
        row = next(r for r in data["by_reply"] if r["reply_id"] == "R1")
        assert row["click_count"] == 3
        assert row["human_click_count"] == 2
        assert row["poll_count"] == 1

    def test_click_only_reply_surfaces_without_snapshot(self, store, client):
        # R2 clicked before its first engagement poll → no snapshot row exists.
        _view(store, "R2", "agreeable", is_bot=False)
        data = client.get("/api/engagement").get_json()
        row = next(r for r in data["by_reply"] if r["reply_id"] == "R2")
        assert row["poll_count"] == 0
        assert row["like_count"] == 0
        assert row["human_click_count"] == 1
        assert row["tone"] == "agreeable"

    def test_totals_count_all_and_human_clicks(self, store, client):
        _view(store, "R1", "neutral", is_bot=False)
        _view(store, "R1", "neutral", is_bot=True)
        _view(store, "R2", "agreeable", is_bot=False)
        totals = client.get("/api/engagement").get_json()["totals"]
        assert totals["total_clicks"] == 3
        assert totals["total_human_clicks"] == 2

    def test_by_tone_click_totals(self, store, client):
        _view(store, "R1", "neutral", is_bot=False)
        _view(store, "R1", "neutral", is_bot=True)
        _view(store, "R2", "agreeable", is_bot=False)
        by_tone = {t["tone"]: t for t in client.get("/api/engagement").get_json()["by_tone"]}
        assert by_tone["neutral"]["total_clicks"] == 2
        assert by_tone["neutral"]["total_human_clicks"] == 1
        assert by_tone["agreeable"]["total_clicks"] == 1

    def test_by_tone_totals_align_with_headline_when_a_view_lacks_reply_id(self, store, client):
        """Views without reply_id (e.g. a dossier token opened in the brief
        window before the reply's id was recorded) must be excluded from BOTH
        the headline total and the per-tone breakdown. Counting such a view
        only in the per-tone side makes the per-condition columns sum to more
        than the headline total.
        """
        _view(store, "R1", "neutral", is_bot=False)
        _view(store, "R1", "neutral", is_bot=False)
        _view(store, "R2", "agreeable", is_bot=False)
        # No reply_id yet — must not be counted anywhere.
        store.write_info_view(events_module.InfoView(
            token="tok-early", viewed_at_utc=_TS, reply_id=None, tone="neutral", is_bot=False,
        ))

        data = client.get("/api/engagement").get_json()
        totals = data["totals"]
        by_tone_sum = sum(t["total_clicks"] for t in data["by_tone"])
        assert by_tone_sum == totals["total_clicks"]
        assert totals["total_clicks"] == 3

    def test_no_views_yields_zero_clicks(self, store, client):
        store.write_engagement(events_module.EngagementSnapshot(
            reply_id="R1", tone="neutral", polled_at_utc=_TS, like_count=1))
        data = client.get("/api/engagement").get_json()
        assert data["totals"]["total_clicks"] == 0
        assert data["totals"]["total_human_clicks"] == 0
        row = next(r for r in data["by_reply"] if r["reply_id"] == "R1")
        assert row["click_count"] == 0
        assert row["human_click_count"] == 0
