"""Tests for _make_study_code and the participant allow-list in app.py."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402
from agent.app.participants import Participant  # noqa: E402


_VALID_ALPHABET = set("ABCDEFGHJKLMNPQRSTUVWXYZ")


# ── _make_study_code ──────────────────────────────────────────────────────────


class TestMakeStudyCode:
    def test_length_is_four(self):
        assert len(app_module._make_study_code("12345678")) == 4

    def test_deterministic(self):
        code = app_module._make_study_code("12345678")
        assert app_module._make_study_code("12345678") == code

    def test_different_ids_differ(self):
        assert app_module._make_study_code("111") != app_module._make_study_code("222")

    def test_only_valid_alphabet_chars(self):
        for reply_id in ["1", "999", "1234567890123456789"]:
            code = app_module._make_study_code(reply_id)
            assert set(code).issubset(_VALID_ALPHABET), f"bad chars in {code!r}"

    def test_no_i_or_o(self):
        # Run a few hundred IDs to stress the alphabet exclusion
        for i in range(500):
            code = app_module._make_study_code(str(i))
            assert "I" not in code and "O" not in code, f"found I/O in {code!r} for id={i}"


# ── study_day calculation ─────────────────────────────────────────────────────


class TestStudyDay:
    def test_enrolled_today_is_day_one(self):
        enrolled = datetime(2026, 5, 20, tzinfo=timezone.utc)
        received = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
        participant = Participant(
            author_id="1", author_username="u", tone="neutral", enrolled_at_utc=enrolled
        )
        study_day = (received.date() - participant.enrolled_at_utc.date()).days + 1
        assert study_day == 1

    def test_enrolled_five_days_ago_is_day_six(self):
        enrolled = datetime(2026, 5, 14, tzinfo=timezone.utc)
        received = datetime(2026, 5, 20, tzinfo=timezone.utc)
        participant = Participant(
            author_id="1", author_username="u", tone="neutral", enrolled_at_utc=enrolled
        )
        study_day = (received.date() - participant.enrolled_at_utc.date()).days + 1
        assert study_day == 7
