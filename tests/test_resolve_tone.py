"""Tests for app._resolve_tone.

Registered participants → their assigned tone.
Unregistered users → uniformly random tone per call.
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
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402
from agent.app.participants import VALID_TONES, Participant  # noqa: E402


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


class TestResolveTone:
    def test_registered_participant_uses_assigned_tone(self, clean_participants):
        clean_participants["42"] = _make("42", "agreeable")
        assert app_module._resolve_tone("42") == "agreeable"

    def test_all_three_tones_supported_for_registered(self, clean_participants):
        for tone in VALID_TONES:
            aid = f"reg_{tone}"
            clean_participants[aid] = _make(aid, tone)
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
        clean_participants["77"] = _make("77", "")  # corrupted record
        assert app_module._resolve_tone("77") in VALID_TONES
