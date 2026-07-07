"""Tests for app._resolve_tone.

Registered participants → their assigned tone.
Unregistered users → uniformly random tone per call.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402
from agent.app.participants import (  # noqa: E402
    VALID_TONES,
    InMemoryParticipantsStore,
    Participant,
)


@pytest.fixture
def clean_participants():
    """Wipe and restore the in-process participant cache."""
    saved = dict(app_module._PARTICIPANTS_BY_ID)
    app_module._PARTICIPANTS_BY_ID.clear()
    yield app_module._PARTICIPANTS_BY_ID
    app_module._PARTICIPANTS_BY_ID.clear()
    app_module._PARTICIPANTS_BY_ID.update(saved)


def _make(author_id: str, tone: str) -> Participant:
    return Participant(
        author_id=author_id,
        author_username="x",
        tone=tone,
        enrolled_at_utc=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


def _cache_entry(author_id: str, tone: str) -> tuple[Participant, float]:
    """Build a (participant, cached_at) tuple — the cache's storage shape."""
    return (_make(author_id, tone), time.monotonic())


class TestResolveTone:
    def test_registered_participant_uses_assigned_tone(self, clean_participants):
        clean_participants["42"] = _cache_entry("42", "agreeable")
        assert app_module._resolve_tone("42") == "agreeable"

    def test_all_three_tones_supported_for_registered(self, clean_participants):
        for tone in VALID_TONES:
            aid = f"reg_{tone}"
            clean_participants[aid] = _cache_entry(aid, tone)
            assert app_module._resolve_tone(aid) == tone

    def test_unregistered_returns_valid_tone(self, clean_participants):
        # Sample many trials to confirm every draw is a valid tone.
        seen = {app_module._resolve_tone("unknown_user") for _ in range(200)}
        assert seen.issubset(set(VALID_TONES))
        # And with random sampling over 200 calls we should hit at least 2 of the 3 tones.
        assert len(seen) >= 2

    def test_empty_author_id_still_returns_valid_tone(self, clean_participants):
        assert app_module._resolve_tone("") in VALID_TONES

    def test_participant_with_invalid_tone_falls_back_to_random(self, clean_participants):
        clean_participants["77"] = _cache_entry("77", "")  # corrupted record
        assert app_module._resolve_tone("77") in VALID_TONES


class TestParticipantCacheTTL:
    """Regression coverage for the write-through cache's TTL (bug: a tone
    correction re-registered against the persistent store never reached other
    gunicorn workers, which kept serving the stale cached tone forever)."""

    def test_stale_cache_entry_self_heals_after_ttl(self, clean_participants, monkeypatch):
        fake_now = [1_000.0]
        monkeypatch.setattr(app_module.time, "monotonic", lambda: fake_now[0])

        store = InMemoryParticipantsStore()
        monkeypatch.setattr(app_module, "_participants_store", store)
        store.register(_make("55", "agreeable"))

        # First lookup is a cache miss — populates the cache at fake_now[0].
        assert app_module._lookup_participant("55").tone == "agreeable"

        # Simulate a correction landing in the persistent store (e.g. another
        # worker re-registered this participant). The cache still holds the
        # old value because it hasn't expired yet.
        store.register(_make("55", "satirical"))
        assert app_module._lookup_participant("55").tone == "agreeable"

        # Advance the clock past the TTL: the cached entry must now be
        # treated as a miss and re-read from the store.
        fake_now[0] += app_module._PARTICIPANTS_CACHE_TTL_S + 1
        assert app_module._lookup_participant("55").tone == "satirical"

    def test_fresh_cache_entry_is_not_re_read(self, clean_participants, monkeypatch):
        fake_now = [2_000.0]
        monkeypatch.setattr(app_module.time, "monotonic", lambda: fake_now[0])

        store = InMemoryParticipantsStore()
        monkeypatch.setattr(app_module, "_participants_store", store)
        store.register(_make("66", "neutral"))
        assert app_module._lookup_participant("66").tone == "neutral"

        # Correct the store but stay well inside the TTL — cache should win.
        store.register(_make("66", "agreeable"))
        fake_now[0] += 1  # far less than the TTL
        assert app_module._lookup_participant("66").tone == "neutral"
